import logging
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .pydantic_models import FriendRequest, FriendResponseRequest, ContactHashesRequest
from .config import db
from .auth import token_required
from .users import get_user_profiles_from_ids
from .notifications import send_notification
social_bp = Blueprint('social_bp', __name__)

# --- Transactional Helper ---
@firestore.transactional
def process_friend_request_transaction(transaction, current_user_ref, requester_ref):
    """
    Atomically adds users to each other's friends list and removes the corresponding
    friend requests from both user documents.
    """
    requester_id = requester_ref.id
    current_user_id = current_user_ref.id
    
    # Add to friends lists for both users
    transaction.update(current_user_ref, {'friends': firestore.ArrayUnion([requester_id])})
    transaction.update(requester_ref, {'friends': firestore.ArrayUnion([current_user_id])})
    
    # Remove from pending request lists for both users
    transaction.update(current_user_ref, {'friendRequestsReceived': firestore.ArrayRemove([requester_id])})
    transaction.update(requester_ref, {'friendRequestsSent': firestore.ArrayRemove([current_user_id])})

# --- Endpoints ---

@social_bp.route('/request', methods=['POST'])
@token_required
def send_friend_request(user_id):
    """Adds a user ID to the target's received requests and the sender's sent requests."""
    sender_id = user_id
    
    req_data = request.get_json()
    target_user_id = req_data.get('targetUserId')

    if not target_user_id:
        return jsonify({"error": "targetUserId is required"}), 400
    try:
        req_data = FriendRequest.model_validate(request.get_json())
        if user_id == req_data.targetUserId:
            return jsonify({"error": "Cannot add yourself as a friend."}), 400
        
        current_user_ref = db.collection('users').document(user_id)
        target_user_ref = db.collection('users').document(req_data.targetUserId)
        
        batch = db.batch()
        batch.update(current_user_ref, {'friendRequestsSent': firestore.ArrayUnion([req_data.targetUserId])})
        batch.update(target_user_ref, {'friendRequestsReceived': firestore.ArrayUnion([user_id])})
        batch.commit()
        sender_profile = get_user_profiles_from_ids([sender_id])
        if sender_profile:
            sender_name = sender_profile[0].displayName or "Someone"
            
            # Send notification to the person RECEIVING the request
            send_notification(
                user_id=target_user_id,
                title="New Friend Request!",
                body=f"{sender_name} sent you a friend request.",
                data={"type": "friend_request_received"}
            )
        return jsonify({"message": "Friend request sent."}), 200
    except Exception as e:
        logging.error(f"Error sending friend request from {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not send friend request."}), 500

@social_bp.route('/accept', methods=['POST'])
@token_required
def accept_friend_request(user_id):
    acceptor_id = user_id
    req_data = request.get_json()
    # This is the user who ORIGINALLY SENT the request
    requester_user_id = req_data.get('requesterUserId')
    """Accepts a friend request, adding users to each other's friend lists."""
    try:
        req_data = FriendResponseRequest.model_validate(request.get_json())
        current_user_ref = db.collection('users').document(user_id)
        requester_ref = db.collection('users').document(req_data.requesterUserId)
        
        # Use the atomic transaction to ensure data consistency
        process_friend_request_transaction(db.transaction(), current_user_ref, requester_ref)
        acceptor_profile = get_user_profiles_from_ids([acceptor_id])
        if acceptor_profile:
            acceptor_name = acceptor_profile[0].displayName or "Someone"

            # Send notification to the person who ORIGINALLY SENT the request
            send_notification(
                user_id=requester_user_id,
                title="Friend Request Accepted!",
                body=f"{acceptor_name} accepted your friend request.",
                data={"type": "friend_request_accepted"}
            ) 
        return jsonify({"message": "Friend request accepted."}), 200
    except Exception as e:
        logging.error(f"Error accepting friend request for {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not accept friend request."}), 500

@social_bp.route('/decline', methods=['POST'])
@token_required
def decline_friend_request(user_id):
    """Removes a friend request from both the sender's and receiver's lists."""
    try:
        req_data = FriendResponseRequest.model_validate(request.get_json())
        current_user_ref = db.collection('users').document(user_id)
        requester_ref = db.collection('users').document(req_data.requesterUserId)
        
        batch = db.batch()
        batch.update(current_user_ref, {'friendRequestsReceived': firestore.ArrayRemove([req_data.requesterUserId])})
        batch.update(requester_ref, {'friendRequestsSent': firestore.ArrayRemove([user_id])})
        batch.commit()
        
        return jsonify({"message": "Friend request declined."}), 200
    except Exception as e:
        logging.error(f"Error declining friend request for {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not decline friend request."}), 500

@social_bp.route('/remove', methods=['POST'])
@token_required
def remove_friend(user_id):
    """Removes a user from the current user's friends list, and vice-versa."""
    try:
        req_data = FriendRequest.model_validate(request.get_json())
        current_user_ref = db.collection('users').document(user_id)
        friend_ref = db.collection('users').document(req_data.targetUserId)
        
        batch = db.batch()
        batch.update(current_user_ref, {'friends': firestore.ArrayRemove([req_data.targetUserId])})
        batch.update(friend_ref, {'friends': firestore.ArrayRemove([user_id])})
        batch.commit()
        
        return jsonify({"message": "Friend removed."}), 200
    except Exception as e:
        logging.error(f"Error removing friend for {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not remove friend."}), 500
    
@social_bp.route('/find-by-emails', methods=['POST'])
@token_required
def find_by_emails(user_id):
    """Finds users by a list of email hashes from the user's contacts."""
    try:
        req_data = ContactHashesRequest.model_validate(request.get_json())
        if not req_data.hashes:
            return jsonify([]), 200
        
        matching_user_ids = set()
        # Firestore 'in' queries are limited to 30 items, so we process in chunks
        for i in range(0, len(req_data.hashes), 30):
            chunk = req_data.hashes[i:i+30]
            query = db.collection('email_hashes').where(filter=firestore.FieldFilter.from_document_id("in", chunk))
            for doc in query.stream():
                uid = doc.to_dict().get('userId')
                if uid and uid != user_id:
                    matching_user_ids.add(uid)

        if not matching_user_ids:
            return jsonify([]), 200

        # Import the helper here to avoid circular dependency at the top level
        from .users import get_user_profiles_from_ids
        matching_profiles = get_user_profiles_from_ids(list(matching_user_ids), user_id)
        
        return jsonify([p.model_dump() for p in matching_profiles]), 200
    except Exception as e:
        logging.error(f"Error finding by email for {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not perform search."}), 500
    
def health_check():
    """Performs a non-destructive health check for the social module."""
    try:
        # Checks if the prefix search query index is working.
        _ = list(db.collection('users').order_by('displayName').start_at(['a']).end_at(['a' + '\uf8ff']).limit(1).stream())
        _ = list(db.collection('contact_hashes').limit(1).stream())
        return {"status": "OK", "details": "Firestore collections and search index are accessible."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to query Firestore collections. Check indexes. Error: {str(e)}"}
    
@social_bp.route('/friends', methods=['GET'])
@token_required
def get_all_friend_data(user_id):
    """
    A single, efficient endpoint to get all friend-related data for the current user.
    """
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404

    user_data = user_doc.to_dict()
    
    friend_ids = user_data.get('friends', [])
    sent_request_ids = user_data.get('friendRequestsSent', [])
    received_request_ids = user_data.get('friendRequestsReceived', [])

    friends = [p.model_dump() for p in get_user_profiles_from_ids(friend_ids, user_id)]
    sent_requests = [p.model_dump() for p in get_user_profiles_from_ids(sent_request_ids, user_id)]
    received_requests = [p.model_dump() for p in get_user_profiles_from_ids(received_request_ids, user_id)]

    return jsonify({
        "friends": friends,
        "sentRequests": sent_requests,
        "receivedRequests": received_requests
    }), 200