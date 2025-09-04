import logging
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .pydantic_models import FriendRequest, FriendResponseRequest, ContactHashesRequest
from .config import db
from .auth import token_required

social_bp = Blueprint('social_bp', __name__)

# --- Helpers ---
@firestore.transactional
def process_friend_request_transaction(transaction, current_user_ref, requester_ref):
    """Atomically adds users to each other's friends list and removes requests."""
    requester_id = requester_ref.id
    current_user_id = current_user_ref.id
    
    # Add to friends lists for both users
    transaction.update(current_user_ref, {'friends': firestore.ArrayUnion([requester_id])})
    transaction.update(requester_ref, {'friends': firestore.ArrayUnion([current_user_id])})
    
    # Remove from pending request lists for both users
    transaction.update(current_user_ref, {'friendRequestsReceived': firestore.ArrayRemove([requester_id])})
    transaction.update(requester_ref, {'friendRequestsSent': firestore.ArrayRemove([current_user_id])})

def get_user_profiles_from_ids(user_ids):
    """
    Efficiently fetches multiple user profiles from a list of user IDs.
    Returns a list of public profile dictionaries.
    """
    if not user_ids:
        return []
    
    # Use a generator to create references
    refs = (db.collection('users').document(uid) for uid in user_ids)
    docs = db.get_all(refs)
    
    profiles = []
    for doc in docs:
        if doc.exists:
            user = doc.to_dict()
            profiles.append({
                "userId": user.get('userId'),
                "displayName": user.get('displayName'),
                "username": user.get('username')
                # Add avatarUrl here in the future
            })
    return profiles


# --- Endpoints ---
@social_bp.route('/', methods=['GET'])
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

    # Fetch the full profile data for each list of IDs
    friends = get_user_profiles_from_ids(friend_ids)
    sent_requests = get_user_profiles_from_ids(sent_request_ids)
    received_requests = get_user_profiles_from_ids(received_request_ids)

    return jsonify({
        "friends": friends,
        "sentRequests": sent_requests,
        "receivedRequests": received_requests
    }), 200

# --- Endpoints ---
@social_bp.route('/users/search', methods=['GET'])
@token_required
def search_users(user_id):
    query_str = request.args.get('q', '').lower().strip()
    if not query_str or len(query_str) < 3: 
        return jsonify([]), 200
    
    results, found_ids = [], set()
    
    # 1. Exact username match (fastest lookup)
    username_doc = db.collection('usernames').document(query_str).get()
    if username_doc.exists:
        match_user_id = username_doc.to_dict().get('userId')
        if match_user_id and match_user_id != user_id:
            user_res = db.collection('users').document(match_user_id).get()
            if user_res.exists:
                user = user_res.to_dict()
                results.append({"userId": user['userId'], "displayName": user.get('displayName'), "username": user.get('username')})
                found_ids.add(user['userId'])

    # 2. Prefix search on display name (slower, for discovery)
    # This requires a composite index on (displayName ASC)
    query = db.collection('users').order_by('displayName').start_at([query_str]).end_at([query_str + '\uf8ff']).limit(10)
    for doc in query.stream():
        user = doc.to_dict()
        user_id_from_doc = user.get('userId')
        if user_id_from_doc and user_id_from_doc not in found_ids and user_id_from_doc != user_id:
            results.append({"userId": user['userId'], "displayName": user.get('displayName'), "username": user.get('username')})
            found_ids.add(user_id_from_doc)
            
    return jsonify(results), 200

@social_bp.route('/request', methods=['POST'])
@token_required
def send_friend_request(user_id):
    req_data = FriendRequest.model_validate(request.get_json())
    if user_id == req_data.targetUserId: return jsonify({"error": "Cannot add yourself as a friend."}), 400
    
    current_user_ref = db.collection('users').document(user_id)
    target_user_ref = db.collection('users').document(req_data.targetUserId)
    
    batch = db.batch()
    batch.update(current_user_ref, {'friendRequestsSent': firestore.ArrayUnion([req_data.targetUserId])})
    batch.update(target_user_ref, {'friendRequestsReceived': firestore.ArrayUnion([user_id])})
    batch.commit()
    
    return jsonify({"message": "Friend request sent."}), 200

@social_bp.route('/accept', methods=['POST'])
@token_required
def accept_friend_request(user_id):
    req_data = FriendResponseRequest.model_validate(request.get_json())
    current_user_ref = db.collection('users').document(user_id)
    requester_ref = db.collection('users').document(req_data.requesterUserId)
    
    process_friend_request_transaction(db.transaction(), current_user_ref, requester_ref)
    return jsonify({"message": "Friend request accepted."}), 200

@social_bp.route('/decline', methods=['POST'])
@token_required
def decline_friend_request(user_id):
    req_data = FriendResponseRequest.model_validate(request.get_json())
    current_user_ref = db.collection('users').document(user_id)
    requester_ref = db.collection('users').document(req_data.requesterUserId)
    
    batch = db.batch()
    batch.update(current_user_ref, {'friendRequestsReceived': firestore.ArrayRemove([req_data.requesterUserId])})
    batch.update(requester_ref, {'friendRequestsSent': firestore.ArrayRemove([user_id])})
    batch.commit()
    
    return jsonify({"message": "Friend request declined."}), 200

@social_bp.route('/remove', methods=['POST'])
@token_required
def remove_friend(user_id):
    req_data = FriendRequest.model_validate(request.get_json()) # Re-use FriendRequest model
    current_user_ref = db.collection('users').document(user_id)
    friend_ref = db.collection('users').document(req_data.targetUserId)
    
    batch = db.batch()
    batch.update(current_user_ref, {'friends': firestore.ArrayRemove([req_data.targetUserId])})
    batch.update(friend_ref, {'friends': firestore.ArrayRemove([user_id])})
    batch.commit()
    
    return jsonify({"message": "Friend removed."}), 200
    
@social_bp.route('/find-by-contacts', methods=['POST'])
@token_required
def find_by_contacts(user_id):
    req_data = ContactHashesRequest.model_validate(request.get_json())
    if not req_data.hashes: return jsonify([]), 200
    
    matching_user_ids = set()
    # Firestore 'in' query is limited to 30 items, so we process in chunks
    for i in range(0, len(req_data.hashes), 30):
        chunk = req_data.hashes[i:i+30]
        query = db.collection('contact_hashes').where(filter=firestore.FieldFilter.from_document_id("in", chunk))
        for doc in query.stream():
            uid = doc.to_dict().get('userId')
            if uid and uid != user_id:
                matching_user_ids.add(uid)

    if not matching_user_ids:
        return jsonify([]), 200

    # Now fetch the profiles for the matched user IDs using another 'in' query
    # (also chunked for safety)
    final_results = []
    user_id_list = list(matching_user_ids)
    for i in range(0, len(user_id_list), 30):
        chunk = user_id_list[i:i+30]
        user_query = db.collection('users').where(filter=firestore.FieldFilter("userId", "in", chunk))
        for doc in user_query.stream():
            user = doc.to_dict()
            final_results.append({"userId": user.get('userId'), "displayName": user.get('displayName'), "username": user.get('username')})

    return jsonify(final_results), 200

# --- HEALTH CHECK ---
def health_check():
    """Performs a non-destructive health check for the social module."""
    try:
        # Checks if the prefix search query index is working.
        _ = list(db.collection('users').order_by('displayName').start_at(['a']).end_at(['a' + '\uf8ff']).limit(1).stream())
        _ = list(db.collection('contact_hashes').limit(1).stream())
        return {"status": "OK", "details": "Firestore collections and search index are accessible."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to query Firestore collections. Check indexes. Error: {str(e)}"}