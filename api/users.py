# In api/users.py
from flask import Blueprint, request, jsonify
import datetime
from .config import db,GCS_BUCKET_NAME,storage_client, tasks_client
from .auth import token_required # We still need the decorator
from .pydantic_models import FcmTokenUpdateRequest, AvatarUploadRequest, AvatarUploadCompleteRequest, PublicProfileResponse
from tasks import process_avatar_image # Import the new Celery task
import logging
users_bp = Blueprint('users_bp', __name__)


@users_bp.route('/<profile_user_id>', methods=['GET'])
@token_required
def get_public_profile(user_id, profile_user_id):
    """
    Fetches a limited, public version of another user's profile.
    """
    user_ref = db.collection('users').document(profile_user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404
    
    user_data = user_doc.to_dict()
    
    # Use the Pydantic model to select and validate the public fields
    public_profile = PublicProfileResponse(
        userId=user_data.get('userId'),
        displayName=user_data.get('displayName'),
        username=user_data.get('username'),
        avatarUrl=user_data.get('avatarUrl'),
        totalPoints=int(user_data.get('totalPoints', 0)),
        currentStreak=user_data.get('currentStreak', 0)
    )

    return public_profile.model_dump(), 200

@users_bp.route('/initiate-avatar-upload', methods=['POST'])
@token_required
def initiate_avatar_upload(user_id):
    """Generates a V4 signed URL for a user to upload their avatar directly to GCS."""
    req_data = AvatarUploadRequest.model_validate(request.get_json())
    gcs_filename = f"{user_id}.{req_data.fileExtension}"
    blob_path = f"avatars_original/{gcs_filename}"
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(blob_path)
    signed_url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=10),
        method="PUT",
        content_type=req_data.contentType
    )
    # Return the URL and the path, which the client will need to send back
    return jsonify({"upload_url": signed_url, "gcs_path": blob_path}), 200

@users_bp.route('/avatar-upload-complete', methods=['POST'])
@token_required
def avatar_upload_complete(user_id):
    """
    Notified by the client that a direct GCS upload is complete.
    This endpoint queues the image for processing.
    """
    req_data = AvatarUploadCompleteRequest.model_validate(request.get_json())
    # Queue the background task to process the image
    process_avatar_image.delay(req_data.gcsPath, user_id)
    return jsonify({"message": "Avatar processing queued."}), 202

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