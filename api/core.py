import logging
import json
import datetime
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .pydantic_models import InitiateUploadRequest, UploadCompleteRequest
from .config import db, storage_client, tasks_client, GCP_PROJECT_ID, GCP_QUEUE_LOCATION, GCP_QUEUE_ID, WORKER_TARGET_URL, GCS_BUCKET_NAME
from .auth import token_required
from .error_utils import create_error_response, handle_exception, not_found_error, server_error

core_bp = Blueprint('core_bp', __name__)

@core_bp.route('/logout', methods=['POST'])
@token_required
def logout(user_id):
    """
    Clears the FCM token from the user's profile to stop push notifications.
    """
    try:
        user_ref = db.collection('users').document(user_id)
        user_ref.update({"fcmToken": firestore.DELETE_FIELD})
        return jsonify({"message": "Logout successful"}), 200
    except Exception as e:
        return handle_exception(e, "logout endpoint")

@core_bp.route('/initiate-upload', methods=['POST'])
@token_required
def initiate_upload(user_id):
    # Check if user has completed onboarding
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get(['onboardingComplete'])
        if not user_doc.exists:
            return not_found_error("User not found")
        user_data = user_doc.to_dict()
        if not user_data.get('onboardingComplete', False):
            return create_error_response("ONBOARDING_REQUIRED", "Please complete onboarding before uploading videos", status_code=403)
        
        req_data = InitiateUploadRequest.model_validate(request.get_json())
        upload_id = req_data.upload_id
        gcs_filename = f"{user_id}/{upload_id}/{req_data.filename}"
        
        db.collection('uploads').document(upload_id).set({
            'uploadId': upload_id,
            'userId': user_id,
            'originalFilename': req_data.filename,
            'gcsFilename': gcs_filename,
            'fcmToken': req_data.fcm_token,
            'status': 'PENDING_UPLOAD',
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        
        try:
            blob = storage_client.bucket(GCS_BUCKET_NAME).blob(gcs_filename)
            # The origin header is important for CORS on the GCS signed URL
            resumable_url = blob.create_resumable_upload_session(content_type="video/mp4")
            return jsonify({"upload_url": resumable_url, "upload_id": upload_id}), 200
        except Exception as e:
            logging.error(f"Error creating GCS session for {gcs_filename}: {e}", exc_info=True)
            return create_error_response("STORAGE_ERROR", status_code=500)
    except Exception as e:
        return handle_exception(e, "initiate_upload endpoint")

@core_bp.route('/upload-complete', methods=['POST'])
@token_required
def upload_complete(user_id):
    # Check if user has completed onboarding
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get(['onboardingComplete'])
        if not user_doc.exists:
            return not_found_error("User not found")
        user_data = user_doc.to_dict()
        if not user_data.get('onboardingComplete', False):
            return create_error_response("ONBOARDING_REQUIRED", "Please complete onboarding before uploading videos", status_code=403)
        
        req_data = UploadCompleteRequest.model_validate(request.get_json())
        upload_ref = db.collection('uploads').document(req_data.upload_id)
        upload_doc = upload_ref.get(['userId', 'gcsFilename'])
        
        if not upload_doc.exists or upload_doc.to_dict().get('userId') != user_id:
            return create_error_response("NOT_FOUND_OR_UNAUTHORIZED", status_code=404)
            
        upload_ref.update({'status': 'PENDING_ANALYSIS'})
        
        try:
            parent = tasks_client.queue_path(GCP_PROJECT_ID, GCP_QUEUE_LOCATION, GCP_QUEUE_ID)
            task_payload = {
                "upload_id": req_data.upload_id,
                "gcs_filename": upload_doc.to_dict().get('gcsFilename'),
                "user_id": user_id
            }
            task = { "http_request": {
                    "http_method": "POST",
                    "url": f"{WORKER_TARGET_URL}/process-task",
                    "headers": {"Content-type": "application/json"},
                    "body": json.dumps(task_payload).encode()
            }}
            tasks_client.create_task(parent=parent, task=task)
            return jsonify({"status": "analysis_queued"}), 200
        except Exception as e:
            logging.error(f"Failed to queue task for {req_data.upload_id}: {e}", exc_info=True)
            upload_ref.update({'status': 'FAILED', 'errorMessage': 'Failed to queue for analysis.'})
            return create_error_response("TASK_QUEUE_ERROR", status_code=500)
    except Exception as e:
        return handle_exception(e, "upload_complete endpoint")

@core_bp.route('/process-task', methods=['POST'])
def process_task_endpoint():
    # Security check to ensure the request is from Cloud Tasks
    if 'X-CloudTasks-QueueName' not in request.headers:
        logging.warning("Unauthorized attempt to access /process-task")
        return "Unauthorized", 403
        
    try:
        from tasks import analyze_video_with_gemini # Local import to avoid circular dependencies
        task_body = request.get_json(force=True)
        analyze_video_with_gemini.delay(GCS_BUCKET_NAME, task_body['gcs_filename'], task_body['upload_id'], task_body['user_id'])
        return "Task dispatched", 200
    except Exception as e:
        return handle_exception(e, "process_task_endpoint")

@core_bp.route('/history', methods=['GET'])
@token_required
def get_history(user_id):
    try:
        # This query requires a composite index in Firestore on (userId, timestamp desc)
        query = db.collection('uploads').where(
            filter=firestore.FieldFilter("userId", "==", user_id)
        ).order_by(
            'timestamp', direction=firestore.Query.DESCENDING
        ).limit(20)
        
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            # Convert any datetime objects to ISO 8601 string format for JSON
            for key, value in data.items():
                if isinstance(value, datetime.datetime):
                    data[key] = value.isoformat() + "Z"
            results.append(data)
            
        return jsonify(results), 200
    except Exception as e:
        return handle_exception(e, "get_history endpoint")


    
def health_check():
    """
    Performs a non-destructive health check for the core module.
    """
    try:
        # This checks GCS bucket permissions and Cloud Tasks queue permissions
        _ = storage_client.get_bucket(GCS_BUCKET_NAME)
        _ = tasks_client.get_queue(name=f"projects/{GCP_PROJECT_ID}/locations/{GCP_QUEUE_LOCATION}/queues/{GCP_QUEUE_ID}")
        return {"status": "OK", "details": "GCS bucket and Cloud Tasks queue are accessible."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to access GCS or Cloud Tasks. Check permissions. Error: {str(e)}"}