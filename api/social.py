import hashlib
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
    # Add to friends lists
    transaction.update(current_user_ref, {'friends': firestore.ArrayUnion([requester_id])})
    transaction.update(requester_ref, {'friends': firestore.ArrayUnion([current_user_id])})
    # Remove from request lists
    transaction.update(current_user_ref, {'friendRequestsReceived': firestore.ArrayRemove([requester_id])})
    transaction.update(requester_ref, {'friendRequestsSent': firestore.ArrayRemove([current_user_id])})

# --- Endpoints ---
@social_bp.route('/users/search', methods=['GET'])
@token_required
def search_users(user_id):
    query_str = request.args.get('q', '').lower().strip()
    if not query_str or len(query_str) < 3: 
        return jsonify([]), 200
    
    results, found_ids = [], set()
    
    # 1. Exact username match (fastest)
    username_doc = db.collection('usernames').document(query_str).get()
    if username_doc.exists:
        match_user_id = username_doc.to_dict().get('userId')
        if match_user_id and match_user_id != user_id:
            user_res = db.collection('users').document(match_user_id).get()
            if user_res.exists:
                user = user_res.to_dict()
                results.append({"userId": user['userId'], "displayName": user.get('displayName'), "username": user.get('username')})
                found_ids.add(user['userId'])

    # 2. Prefix search on display name
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
    if user_id == req_data.targetUserId: return jsonify({"error": "Cannot add yourself"}), 400
    
    current_user_ref = db.collection('users').document(user_id)
    target_user_ref = db.collection('users').document(req_data.targetUserId)
    
    batch = db.batch()
    batch.update(current_user_ref, {'friendRequestsSent': firestore.ArrayUnion([req_data.targetUserId])})
    batch.update(target_user_ref, {'friendRequestsReceived': firestore.ArrayUnion([user_id])})
    batch.commit()
    
    return jsonify({"message": "Friend request sent"}), 200

@social_bp.route('/accept', methods=['POST'])
@token_required
def accept_friend_request(user_id):
    req_data = FriendResponseRequest.model_validate(request.get_json())
    current_user_ref = db.collection('users').document(user_id)
    requester_ref = db.collection('users').document(req_data.requesterUserId)
    
    process_friend_request_transaction(db.transaction(), current_user_ref, requester_ref)
    return jsonify({"message": "Friend request accepted"}), 200

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
    
    return jsonify({"message": "Friend request declined"}), 200

@social_bp.route('/find-by-contacts', methods=['POST'])
@token_required
def find_by_contacts(user_id):
    req_data = ContactHashesRequest.model_validate(request.get_json())
    if not req_data.hashes: return jsonify([]), 200
    
    matching_user_ids = set()
    # Firestore 'in' query is limited to 30 items, so we process in chunks
    for i in range(0, len(req_data.hashes), 30):
        chunk = req_data.hashes[i:i+30]
        # FIX: Use the correct syntax for an "IN" query
        query = db.collection('contact_hashes').where(filter=firestore.FieldFilter.from_document_id("in", chunk))
        for doc in query.stream():
            uid = doc.to_dict().get('userId')
            if uid and uid != user_id:
                matching_user_ids.add(uid)

    if not matching_user_ids:
        return jsonify([]), 200

    # Now fetch the profiles for the matched user IDs
    user_refs = [db.collection('users').document(uid) for uid in list(matching_user_ids)]
    user_docs = db.getAll(user_refs)
    
    results = []
    for doc in user_docs:
        if doc.exists:
            user = doc.to_dict()
            results.append({"userId": user.get('userId'), "displayName": user.get('displayName'), "username": user.get('username')})

    return jsonify(results), 200