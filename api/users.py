# In api/users.py
from flask import Blueprint, request, jsonify
import datetime
from .config import db,GCS_BUCKET_NAME,storage_client
from .auth import token_required # We still need the decorator
from .pydantic_models import FcmTokenUpdateRequest, AvatarUploadRequest
import logging
users_bp = Blueprint('users_bp', __name__)

@users_bp.route('/initiate-avatar-upload', methods=['POST'])
@token_required
def initiate_avatar_upload(user_id):
    """
    Generates a V4 signed URL for an authenticated user to upload their avatar
    directly to a private folder in Google Cloud Storage.
    """
    try:
        req_data = AvatarUploadRequest.model_validate(request.get_json())
        
        # A user's avatar filename is always their user_id to ensure they can only
        # have one, and to make it easy for the Cloud Function to find their document.
        # The extension is provided by the client.
        gcs_filename = f"{user_id}.{req_data.fileExtension}"
        blob_path = f"avatars_original/{gcs_filename}"
        
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_path)

        # Generate a secure, short-lived URL. The client must use the 'PUT' method
        # and provide the correct 'Content-Type' header for the upload to succeed.
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=10), # URL is valid for 10 minutes
            method="PUT",
            content_type=req_data.contentType
        )
        
        return jsonify({"upload_url": signed_url}), 200

    except Exception as e:
        logging.error(f"Error generating signed URL for avatar for user {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not prepare avatar upload."}), 500

@users_bp.route('/search', methods=['GET'])
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

    # 2. Prefix search on display name
    query = db.collection('users').order_by('displayName').start_at([query_str]).end_at([query_str + '\uf8ff']).limit(10)
    for doc in query.stream():
        user = doc.to_dict()
        user_id_from_doc = user.get('userId')
        if user_id_from_doc and user_id_from_doc not in found_ids and user_id_from_doc != user_id:
            results.append({"userId": user['userId'], "displayName": user.get('displayName'), "username": user.get('username')})
            found_ids.add(user_id_from_doc)
            
    return jsonify(results), 200

# You can also move the check-username endpoint here for consistency
@users_bp.route('/check-username', methods=['GET'])
def check_username():
    username = request.args.get('username', '').lower().strip()
    if not username: return jsonify({"error": "Username cannot be empty"}), 400
    return jsonify({"isAvailable": not db.collection('usernames').document(username).get().exists}), 200

@users_bp.route('/update-fcm-token', methods=['POST'])
@token_required
def update_fcm_token(user_id):
    """
    Updates the FCM token for the currently authenticated user.
    """
    try:
        req_data = FcmTokenUpdateRequest.model_validate(request.get_json())
        user_ref = db.collection('users').document(user_id)
        
        # We also update the fcmToken in the main user document now
        user_ref.update({
            "fcmToken": req_data.fcmToken
        })
        
        logging.info(f"Successfully updated FCM token for user {user_id}")
        return jsonify({"message": "Token updated successfully"}), 200
    except Exception as e:
        logging.error(f"Failed to update FCM token for user {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Server error while updating token"}), 500