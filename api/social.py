import logging
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .pydantic_models import FriendRequest, FriendResponseRequest, ContactHashesRequest
from .config import db
from .auth import token_required

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
        
        return jsonify({"message": "Friend request sent."}), 200
    except Exception as e:
        logging.error(f"Error sending friend request from {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not send friend request."}), 500

@social_bp.route('/accept', methods=['POST'])
@token_required
def accept_friend_request(user_id):
    """Accepts a friend request, adding users to each other's friend lists."""
    try:
        req_data = FriendResponseRequest.model_validate(request.get_json())
        current_user_ref = db.collection('users').document(user_id)
        requester_ref = db.collection('users').document(req_data.requesterUserId)
        
        # Use the atomic transaction to ensure data consistency
        process_friend_request_transaction(db.transaction(), current_user_ref, requester_ref)
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