# FILE: trackeco-backend/tasks.py

import os
import logging
import time
import datetime
import json
from celery import Celery
from google.cloud import storage, firestore
from google import genai
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import messaging
import pytz
import redis
from logging_config import setup_logging
from api.prompts import AI_ANALYSIS_PROMPT
from PIL import Image
from io import BytesIO
from api.cache_utils import invalidate_user_summary_cache # <-- IMPORT cache helper
from api.search_utils import sync_user_to_algolia

# --- SETUP & CONFIG ---
setup_logging()
load_dotenv()
celery_app = Celery('tasks', broker=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'), include=['tasks'])

# --- GLOBAL CONSTANTS ---
GEMINI_API_KEYS = [ os.environ.get(f"GEMINI_API_KEY_{i+1}") for i in range(4) ]
ACTIVE_GEMINI_KEYS = [key for key in GEMINI_API_KEYS if key]
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
WIB_TZ = pytz.timezone('Asia/Jakarta')

# --- LAZY INITIALIZED CLIENTS ---
_db, _storage_client, _firebase_app, _redis_client = None, None, None, None
def get_db(): global _db; _db = _db or firestore.Client(); return _db
def get_storage_client(): global _storage_client; _storage_client = _storage_client or storage.Client(); return _storage_client
def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try: _redis_client = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True); _redis_client.ping()
        except redis.exceptions.ConnectionError: _redis_client = None
    return _redis_client
def initialize_firebase():
    global _firebase_app
    if not firebase_admin._apps: _firebase_app = firebase_admin.initialize_app(); logging.info("Firebase Admin SDK initialized for Celery worker process.")

# --- HELPER FUNCTIONS ---
def send_fcm_data_notification(doc_snapshot):
    """Sends a data-only FCM message with the document's current state."""
    try:
        data = doc_snapshot.to_dict()
        fcm_token = data.get('fcmToken')
        if not fcm_token: return
        payload = {}
        for key, value in data.items():
            if isinstance(value, datetime.datetime):
                payload[key] = value.isoformat() + "Z"
            elif value is not None:
                payload[key] = str(value)
        message = messaging.Message(token=fcm_token, data=payload, android=messaging.AndroidConfig(priority="high"))
        messaging.send(message)
        logging.info(f"Successfully sent FCM for {doc_snapshot.id}")
    except Exception as e:
        logging.error(f"Failed to send FCM for {doc_snapshot.id}: {e}", exc_info=True)

@celery_app.task(name="sync_user_to_algolia_task", max_retries=3, default_retry_delay=60)
def sync_user_to_algolia_task(user_id):
    """Celery task to handle syncing a user to Algolia with retries."""
    sync_user_to_algolia(user_id)

@celery_app.task(name="process_avatar_image")
def process_avatar_image(gcs_path, user_id):
    logging.info(f"Processing avatar for user {user_id} from path: {gcs_path}")
    db = get_db()
    storage_client = get_storage_client()
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    source_blob = bucket.blob(gcs_path)

    try:
        if not source_blob.exists():
            logging.error(f"Original avatar not found at {gcs_path} for user {user_id}")
            return

        image_bytes = source_blob.download_as_bytes()
        
        with Image.open(BytesIO(image_bytes)) as img:
            if img.mode in ('RGBA', 'LA'):
                background = Image.new(img.mode[:-1], img.size, (255, 255, 255))
                background.paste(img, img.getchannel('A'))
                img = background
            
            img.thumbnail((256, 256))
            output_buffer = BytesIO()
            img.convert('RGB').save(output_buffer, "WEBP", quality=85)
            output_buffer.seek(0)
            
        processed_blob_name = f"avatars_processed/{user_id}.webp"
        dest_blob = bucket.blob(processed_blob_name)
        
        dest_blob.upload_from_file(output_buffer, content_type='image/webp')
        
        user_ref = db.collection('users').document(user_id)
        user_ref.update({'avatarUrl': dest_blob.public_url})
        sync_user_to_algolia_task.delay(user_id)
        invalidate_user_summary_cache(user_id) # <-- Invalidate cache on success
        
        logging.info(f"Successfully updated avatar for user: {user_id}")
        source_blob.delete()
    except Exception as e:
        logging.error(f"Failed to process avatar for user {user_id}: {e}", exc_info=True)

@celery_app.task(name="award_bonus_points_task")
def award_bonus_points(user_id, amount, reason):
    """A separate task to award points to a user."""
    try:
        db = get_db()
        user_ref = db.collection('users').document(user_id)
        sync_user_to_algolia_task.delay(user_id)
        user_ref.update({'totalPoints': firestore.Increment(amount)})
        invalidate_user_summary_cache(user_id) # <-- Invalidate cache on success
        logging.info(f"Awarded {amount} bonus points to {user_id} for: {reason}")
    except Exception as e:
        logging.error(f"Failed to award bonus points to {user_id}: {e}")

@firestore.transactional
def update_stats_and_upload_transaction(transaction, user_ref, upload_ref, ai_result_json_string, active_challenges, today_wib_date):
    """Atomically updates user stats based on the new AI result format."""
    ai_result = json.loads(ai_result_json_string)
    user_doc = user_ref.get(transaction=transaction)
    if not user_doc.exists:
        transaction.update(upload_ref, {'status': 'COMPLETED', 'aiResult': ai_result_json_string})
        return (None, False)

    user_data = user_doc.to_dict()
    current_points = user_data.get('totalPoints', 0)
    
    new_score = ai_result.get('finalScore', 0)
    
    user_completed_ids = user_data.get('completedChallengeIds', [])
    newly_completed_ids, bonus_points = [], 0
    challenge_progress = user_data.get('challengeProgress', {})
    
    challenge_updates = ai_result.get('challengeUpdates', [])
    challenge_map = {c.get('challengeId'): c for c in active_challenges}

    for update in challenge_updates:
        challenge_id = update.get('challengeId')
        if not challenge_id or challenge_id in user_completed_ids: continue
        challenge = challenge_map.get(challenge_id)
        if not challenge: continue

        if update.get('isCompleted') is True and challenge.get('progressGoal') is None:
            bonus_points += challenge.get('bonusPoints', 0)
            newly_completed_ids.append(challenge_id)
        
        elif 'progress' in update and challenge.get('progressGoal') is not None:
            current_prog = challenge_progress.get(challenge_id, 0)
            new_prog = current_prog + update.get('progress', 0)
            goal = challenge.get('progressGoal', 999)
            if new_prog >= goal:
                bonus_points += challenge.get('bonusPoints', 0)
                newly_completed_ids.append(challenge_id)
                challenge_progress.pop(challenge_id, None)
            else:
                challenge_progress[challenge_id] = new_prog
    
    if(new_score+bonus_points>0):
        last_streak_timestamp, current_streak = user_data.get('lastStreakTimestamp'), user_data.get('currentStreak', 0)
        if last_streak_timestamp:
            last_streak_date_wib = last_streak_timestamp.astimezone(WIB_TZ).date()
            if last_streak_date_wib != today_wib_date:
                current_streak = current_streak + 1 if last_streak_date_wib == (today_wib_date - datetime.timedelta(days=1)) else 1
        else: current_streak = 1
        if current_streak > user_data.get('maxStreak', 0): user_update_data['maxStreak'] = current_streak
    
    is_first_upload = not user_data.get('hasCompletedFirstUpload', False)
    referrer_id = user_data.get('referredBy') if is_first_upload else None
    
    user_update_data = {
        'totalPoints': current_points + new_score + bonus_points, 'currentStreak': current_streak,
        'lastStreakTimestamp': firestore.SERVER_TIMESTAMP, 'challengeProgress': challenge_progress
    }
    if is_first_upload: user_update_data['hasCompletedFirstUpload'] = True
    if newly_completed_ids: user_update_data['completedChallengeIds'] = firestore.ArrayUnion(newly_completed_ids)
    
    
    transaction.update(user_ref, user_update_data)
    transaction.update(upload_ref, {'status': 'COMPLETED', 'aiResult': ai_result_json_string})
    
    # Invalidate cache after the transaction commits
    invalidate_user_summary_cache(user_ref.id)
    
    return (referrer_id, is_first_upload)

def handle_team_challenge_progress(user_id, ai_result):
    db = get_db()
    challenge_updates = ai_result.get('challengeUpdates', [])
    if not challenge_updates: return
    
    user_doc = db.collection('users').document(user_id).get(['activeTeamChallenges'])
    if not user_doc.exists: return
    active_team_ids = user_doc.to_dict().get('activeTeamChallenges', [])
    if not active_team_ids: return

    progress_updates_map = {p['challengeId']: p['progress'] for p in challenge_updates if 'progress' in p}
    if not progress_updates_map: return

    for team_id in active_team_ids:
        team_challenge_ref = db.collection('teamChallenges').document(team_id)
        
        @firestore.transactional
        def update_progress_in_transaction(transaction, ref):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists: return None
            challenge = snapshot.to_dict()
            original_challenge_id = challenge.get("originalChallengeId")

            if challenge.get('status') == 'active' and original_challenge_id in progress_updates_map:
                update_count = progress_updates_map[original_challenge_id]
                new_progress = challenge.get('currentProgress', 0) + update_count
                goal = challenge.get('progressGoal', 999)
                if new_progress >= goal:
                    transaction.update(ref, {'currentProgress': new_progress, 'status': 'completed'})
                    return challenge
                else:
                    transaction.update(ref, {'currentProgress': new_progress})
            return None

        completed_challenge = update_progress_in_transaction(db.transaction(), team_challenge_ref)
        
        if completed_challenge:
            logging.info(f"Team challenge {team_id} completed!")
            members_map = completed_challenge.get('members', {})
            accepted_members = [uid for uid, status in members_map.items() if status == "accepted"]
            member_count = len(accepted_members)
            
            if member_count > 0:
                total_bonus = completed_challenge.get('bonusPoints', 0)
                points_per_member = total_bonus // member_count
                reason = f"Team Challenge '{completed_challenge.get('description')}'"
                
                batch = db.batch()
                for member_id in accepted_members:
                    award_bonus_points.delay(member_id, points_per_member, reason)
                    member_ref = db.collection('users').document(member_id)
                    batch.update(member_ref, {'activeTeamChallenges': firestore.ArrayRemove([team_id])})
                batch.commit()
            break

@celery_app.task(bind=True, max_retries=2, default_retry_delay=300, acks_late=True)
def analyze_video_with_gemini(self, bucket_name, gcs_filename, upload_id, user_id):
    logging.info(f"[{gcs_filename}] -> START for Upload ID: {upload_id}")
    initialize_firebase()
    db = get_db(); storage_client = get_storage_client(); redis_client = get_redis_client()
    
    upload_ref = db.collection('uploads').document(upload_id)
    bucket = storage_client.bucket(bucket_name)
    source_blob = bucket.blob(gcs_filename)
    temp_local_path = f"/tmp/{os.path.basename(gcs_filename)}"
    
    try:
        upload_doc = upload_ref.get()
        if not upload_doc.exists or upload_doc.to_dict().get('status') != 'PENDING_ANALYSIS':
            logging.warning(f"Task for {upload_id} invalid or already processed. Aborting.")
            return

        upload_ref.update({'status': 'PROCESSING_AI', 'processedTimestamp': firestore.SERVER_TIMESTAMP})
        
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        user_completed_ids = user_doc.to_dict().get('completedChallengeIds', []) if user_doc.exists else []
        
        challenge_query = db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True))
        active_challenges_full = [doc.to_dict() for doc in challenge_query.stream()]
        active_challenges_prompt = [c for c in active_challenges_full if c.get('challengeId') not in user_completed_ids]
        
        for challenge in active_challenges_prompt:
            for key, value in list(challenge.items()):
                if isinstance(value, datetime.datetime):
                    challenge[key] = value.isoformat() + "Z"

        prompt = AI_ANALYSIS_PROMPT.replace('{active_challenges_placeholder}', json.dumps(active_challenges_prompt))
        
        if not source_blob.exists(): raise FileNotFoundError(f"Blob '{gcs_filename}' not found.")
        source_blob.download_to_filename(temp_local_path)
        
        analysis_result_str = None
        if not redis_client: raise ConnectionError("Cannot connect to Redis for API key management.")
        start_index = int(redis_client.get("current_analysis_gemini_key_index") or 0)
        
        gemini_file_resource = None
        client_instance = None 

        for i in range(len(ACTIVE_GEMINI_KEYS)):
            current_index = (start_index + i) % len(ACTIVE_GEMINI_KEYS)
            api_key = ACTIVE_GEMINI_KEYS[current_index]
            try:
                logging.info(f"--> Trying Gemini API Key #{current_index + 1}")
                client_instance = genai.Client(api_key=api_key)
                
                logging.info(f"Uploading file '{temp_local_path}' to Gemini File API...")
                gemini_file_resource = client_instance.files.upload(file=temp_local_path)
                
                while gemini_file_resource.state.name == "PROCESSING":
                    time.sleep(10); gemini_file_resource = client_instance.files.get(name=gemini_file_resource.name)
                
                if gemini_file_resource.state.name == "FAILED": raise Exception("Gemini File API processing failed.")
                
                logging.info("File processed. Generating content...")
                response = client_instance.models.generate_content(model="gemini-2.5-pro", contents=[prompt, gemini_file_resource])
                analysis_result_str = response.text
                
                redis_client.set("current_analysis_gemini_key_index", current_index)
                logging.info(f"Gemini API Key #{current_index + 1} succeeded. Setting as active key.")
                break 
            except Exception as e:
                logging.warning(f"Gemini API Key #{current_index + 1} failed: {e}")
                if gemini_file_resource and client_instance:
                    try: client_instance.files.delete(name=gemini_file_resource.name)
                    except Exception: pass
                gemini_file_resource = None
                if i == len(ACTIVE_GEMINI_KEYS) - 1: raise
                continue
        
        if not analysis_result_str: raise Exception("All Gemini API keys failed.")
        cleaned_json_string = analysis_result_str.strip().removeprefix("```json").removesuffix("```").strip()
        is_low_confidence = ai_result.get('baseScore', 0) <= 2 and ai_result.get('effortScore', 0) <= 2
        if not ai_result.get("error") and is_low_confidence:
            logging.warning(f"Overriding low-confidence AI result for upload {upload_id}. Original: {cleaned_json_string}")
            ai_result["error"] = "No significant eco-friendly action was detected."
            ai_result["finalScore"] = 0
            # Zero out other score fields for consistency
            ai_result["baseScore"] = 0
            ai_result["effortScore"] = 0
            ai_result["creativityScore"] = 0
            ai_result["penaltyPoints"] = 0
            ai_result["challengeUpdates"] = []
            ai_result["suggestion"] = None
            cleaned_json_string = json.dumps(ai_result)
        ai_result = json.loads(cleaned_json_string)

        if ai_result.get("error"):
            upload_ref.update({'status': 'COMPLETED', 'aiResult': cleaned_json_string})
        else:
            today_wib_date = datetime.datetime.now(WIB_TZ).date()
            transaction = db.transaction()
            referrer_id, is_first_upload = update_stats_and_upload_transaction(transaction, user_ref, upload_ref, cleaned_json_string, active_challenges_full, today_wib_date)
            sync_user_to_algolia(user_id)
            if referrer_id and is_first_upload:
                award_bonus_points.delay(referrer_id, 50, "Successful Referral")
            
            handle_team_challenge_progress(user_id, ai_result)
        
        final_upload_doc = upload_ref.get()
        if final_upload_doc.exists: send_fcm_data_notification(final_upload_doc)
        
        destination_blob = bucket.blob(f"processed/{gcs_filename}")
        destination_blob.upload_from_string(source_blob.download_as_string(), content_type=source_blob.content_type)
        source_blob.delete()
        logging.info(f"[{gcs_filename}] -> SUCCESS.")
    except Exception as e:
        logging.error(f"Task failed for {upload_id}: {e}", exc_info=True)
        upload_ref.update({'status': 'FAILED', 'errorMessage': str(e)})
        failed_doc = upload_ref.get()
        if failed_doc.exists: send_fcm_data_notification(failed_doc)
        if source_blob.exists():
            try: 
                failed_blob = bucket.blob(f"failed/{gcs_filename}")
                failed_blob.upload_from_string(source_blob.download_as_string(), content_type=source_blob.content_type)
                source_blob.delete()
            except Exception as move_e: logging.error(f"Failed to move failed file: {move_e}")
        raise self.retry(exc=e)
    finally:
        if gemini_file_resource and client_instance:
            try:
                client_instance.files.delete(name=gemini_file_resource.name) 
                logging.info(f"Successfully deleted temporary file {gemini_file_resource.name} from Gemini.")
            except Exception as del_e:
                logging.error(f"Failed to delete temporary file from Gemini: {del_e}")
        if os.path.exists(temp_local_path):
            os.remove(temp_local_path)