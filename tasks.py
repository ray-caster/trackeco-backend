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
from firebase_admin import credentials, messaging
import pytz

from logging_config import setup_logging

# --- SETUP & CONFIG ---
setup_logging()
load_dotenv()
try:
    SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not SERVICE_ACCOUNT_FILE:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"Service account key not found at path: {SERVICE_ACCOUNT_FILE}")

    creds = credentials.Certificate(SERVICE_ACCOUNT_FILE)
    # Check if a Firebase app is already initialized to prevent errors
    if not firebase_admin._apps:
        firebase_admin.initialize_app(creds)
        logging.info("Firebase Admin SDK initialized for Celery worker.")

    # Pass the credentials object directly to the clients
    db = firestore.Client(credentials=creds.get_credential())
    storage_client = storage.Client(credentials=creds.get_credential())
    logging.info("Firestore and Storage clients initialized for Celery worker.")

except Exception as e:
    logging.critical(f"FATAL: Celery worker setup failed. Error: {e}", exc_info=True)
    # Exit if setup fails
    exit()


AI_ANALYSIS_PROMPT = """
<RoleAndGoal>
You are "Eco," an AI scoring judge. Your directive is to analyze a video and return a JSON object with a score, challenge verification, and progress tracking.
</RoleAndGoal>
<AnalysisProcess>
1.  **Assess & Score:** First, assess viability and score the video based on the `<ScoringRubric>`.
2.  **Track Progress:** Identify and count any items in the video that match a keyword from the `<ActiveChallenges>` list. For example, if you see two cans and "can" is a keyword for a progress challenge, you will report this.
3.  **Verify Simple Challenges:** Determine if the action in the video fully completes any *simple* challenges in `<ActiveChallenges>`.
4.  **Construct Final JSON:** Assemble the final JSON. `completedChallengeIds` should only contain IDs of *simple* challenges completed. `progressUpdate` should contain the item count for any relevant *progress* challenges. If multiple item types for progress challenges are found, report the one with the highest count.
</AnalysisProcess>
<ScoringRubric>
1. Environmental Impact & Proper Disposal (0–20 points)
Definition: The environmental correctness and positive impact of the disposal method.
Scoring Rules:
- High Impact (16-20 pts): Actions with significant positive impact. Examples: Correctly recycling electronics (e-waste), disposing of a used battery at a designated drop-off, composting a large amount of vegetable scraps, properly rinsing a jar before recycling.
- Medium Impact (6-15 pts): Standard, correct disposal actions. Examples: Placing a single clean plastic bottle/can in a recycling bin, composting a single piece of fruit, putting regular trash in a landfill bin.
- Low Impact (1-5 pts): Correct but trivial actions. Examples: Tossing a small, crumpled piece of paper into a trash or recycling bin.
- Incorrect Sorting (Mild: 3-7 pts): A recyclable or compostable item is placed in a landfill bin.
- Incorrect Sorting (Severe: 1-2 pts): An action that contaminates a waste stream. Examples: Throwing food waste into a recycling bin, putting a greasy pizza box in with clean paper.
- Harmful Disposal (0 pts): An actively harmful action. Examples: Littering on the ground, throwing trash into water, burning plastic, pouring chemicals down a drain.
2. Dangerousness / Risk Factor (0–10 points)
Definition: The immediate physical risk posed by the action.
Scoring Rules:
- Safe (8-10 pts): Action is careful and controlled. Item is placed, not thrown from a distance. No risk.
- Mildly Careless (4-7 pts): Action is not dangerous but is careless. Examples: Tossing a plastic bottle from a distance (but it goes in), dropping a bag with a bit too much force.
- Unsafe (0-3 pts): Action poses a real risk of harm or mess. Examples: Throwing a glass bottle that could shatter, leaving a sharp can lid exposed in a bag, dropping hazardous waste, creating a trip hazard.
3. Completeness (Penalty System)
Definition: Did all disposed items successfully land in the intended receptacle?
Rules:
- 100% In: No penalty. status is "Complete", penaltyApplied is 0.0.
- Partial Miss: One small piece misses, but the majority lands inside. status is "Partial", penaltyApplied is 0.25.
- Total Miss: The majority or all of the items miss the bin. status is "Fail", penaltyApplied is 1.0.
</ScoringRubric>
<CalculationLogic>
- Multiple Items: For videos with multiple distinct disposal actions, evaluate the single most environmentally impactful and positive action.
- rawScore: environmentalImpact + dangerousness.
- finalScore: rawScore * (1.0 - penaltyApplied). If status is "Fail", the finalScore is always 0.
</CalculationLogic>
<EdgeCases>
- Unassessable: { "error": "Unassessable video quality" }
- No Action: { "error": "No disposal action detected in the video." }
- Irrelevant: { "error": "Video content is irrelevant to waste disposal." }
</EdgeCases>
<ActiveChallenges>
{active_challenges_placeholder}
</ActiveChallenges>
<OutputFormat>
Your entire response MUST be a single, raw JSON object.```json
{
  "environmentalImpact": <integer>,
  "dangerousness": <integer>,
  "completeness": { "status": "<string>", "penaltyApplied": <float> },
  "rawScore": <integer>,
  "finalScore": <float>,
  "justification": "<string>",
  "completedChallengeIds": ["<string, id_of_completed_simple_challenge_1>"],
  "progressUpdate": { "keyword": "<string, keyword_of_item_counted>", "count": <integer> } | null,
  "error": <string | null>
}
```
</OutputFormat>
<Examples>
- Video: User recycles two cans.
- ActiveChallenges: `[{"challengeId": "c1", "description": "Recycle a can.", "keyword": "can", "type": "simple"}, {"challengeId": "c2", "description": "Recycle 5 cans.", "keyword": "can", "type": "progress"}]`
- Expected JSON Output:
```json
{
  "environmentalImpact": 19, "dangerousness": 9, "completeness": { "status": "Complete", "penaltyApplied": 0.0 },
  "rawScore": 28, "finalScore": 28.0, "justification": "Two aluminum cans were recycled correctly.",
  "completedChallengeIds": ["c1"],
  "progressUpdate": { "keyword": "can", "count": 2 },
  "error": null
}
```
</Examples>
"""

def send_fcm_data_notification(doc_snapshot):
    """Dynamically sends a data-only FCM message with the document's current state."""
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

@celery_app.task(name="award_bonus_points_task")
def award_bonus_points(user_id, amount, reason):
    """A simple, separate task to award points to a user."""
    try:
        user_ref = db.collection('users').document(user_id)
        user_ref.update({'totalPoints': firestore.Increment(amount)})
        logging.info(f"Awarded {amount} bonus points to {user_id} for: {reason}")
    except Exception as e:
        logging.error(f"Failed to award bonus points to {user_id}: {e}")

@firestore.transactional
def update_stats_and_upload_transaction(transaction, user_ref, upload_ref, ai_result_json_string, active_challenges, today_wib_date):
    """Atomically updates user's individual stats and the upload document."""
    ai_result = json.loads(ai_result_json_string)
    user_doc = user_ref.get(transaction=transaction)
    if not user_doc.exists:
        logging.warning(f"User {user_ref.id} not found. Only updating upload status.")
        transaction.update(upload_ref, {'status': 'COMPLETED', 'aiResult': ai_result_json_string})
        return (None, False)

    user_data = user_doc.to_dict()
    current_points, new_score = user_data.get('totalPoints', 0), ai_result.get('finalScore', 0.0)
    user_completed_ids = user_data.get('completedChallengeIds', [])
    newly_completed_ids, bonus_points = [], 0
    
    completed_simple_ids_from_ai = ai_result.get('completedChallengeIds', [])
    for challenge_id in completed_simple_ids_from_ai:
        if challenge_id not in user_completed_ids:
            challenge = next((c for c in active_challenges if c.get('challengeId') == challenge_id), None)
            if challenge and challenge.get('type') != 'progress':
                bonus_points += challenge.get('bonusPoints', 0)
                newly_completed_ids.append(challenge_id)

    progress_update = ai_result.get('progressUpdate')
    challenge_progress = user_data.get('challengeProgress', {})
    if progress_update and 'keyword' in progress_update and 'count' in progress_update:
        update_keyword, update_count = progress_update['keyword'], progress_update['count']
        for challenge in active_challenges:
            challenge_id = challenge.get('challengeId')
            if (challenge.get('type') == 'progress' and challenge.get('keyword') == update_keyword and challenge_id not in user_completed_ids):
                current_progress = challenge_progress.get(challenge_id, 0)
                new_progress = current_progress + update_count
                goal = challenge.get('progressGoal', 999)
                if new_progress >= goal:
                    bonus_points += challenge.get('bonusPoints', 0)
                    newly_completed_ids.append(challenge_id)
                    challenge_progress.pop(challenge_id, None)
                else:
                    challenge_progress[challenge_id] = new_progress
                break
    
    last_streak_timestamp, current_streak = user_data.get('lastStreakTimestamp'), user_data.get('currentStreak', 0)
    if last_streak_timestamp:
        last_streak_date_wib = last_streak_timestamp.astimezone(WIB_TZ).date()
        if last_streak_date_wib != today_wib_date:
            current_streak = current_streak + 1 if last_streak_date_wib == (today_wib_date - datetime.timedelta(days=1)) else 1
    else: current_streak = 1
    
    is_first_upload = not user_data.get('hasCompletedFirstUpload', False)
    referrer_id = user_data.get('referredBy') if is_first_upload else None
    
    user_update_data = {
        'totalPoints': current_points + new_score + bonus_points, 'currentStreak': current_streak,
        'lastStreakTimestamp': firestore.SERVER_TIMESTAMP, 'challengeProgress': challenge_progress
    }
    if is_first_upload:
        user_update_data['hasCompletedFirstUpload'] = True
    if newly_completed_ids:
        user_update_data['completedChallengeIds'] = firestore.ArrayUnion(newly_completed_ids)
    if current_streak > user_data.get('maxStreak', 0):
        user_update_data['maxStreak'] = current_streak
    
    transaction.update(user_ref, user_update_data)
    transaction.update(upload_ref, {'status': 'COMPLETED', 'aiResult': ai_result_json_string})
    return (referrer_id, is_first_upload)

def handle_team_challenge_progress(user_id, progress_update):
    """Handles updating shared progress on team challenges. Runs AFTER the main transaction."""
    if not progress_update: return
    user_doc = db.collection('users').document(user_id).get(['activeTeamChallenges'])
    if not user_doc.exists: return
    active_team_ids = user_doc.to_dict().get('activeTeamChallenges', [])
    if not active_team_ids: return

    update_keyword, update_count = progress_update.get('keyword'), progress_update.get('count', 0)
    
    for team_id in active_team_ids:
        team_challenge_ref = db.collection('teamChallenges').document(team_id)
        
        @firestore.transactional
        def update_progress_in_transaction(transaction, ref):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists: return None
            challenge = snapshot.to_dict()
            if challenge.get('status') == 'active' and challenge.get('keyword') == update_keyword:
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
            members = completed_challenge.get('members', [])
            bonus = completed_challenge.get('bonusPoints', 0)
            reason = f"Team Challenge '{completed_challenge.get('description')}'"
            batch = db.batch()
            for member_id in members:
                award_bonus_points.delay(member_id, bonus, reason)
                member_ref = db.collection('users').document(member_id)
                batch.update(member_ref, {'activeTeamChallenges': firestore.ArrayRemove([team_id])})
            batch.commit()
            break

@celery_app.task(bind=True, max_retries=2, default_retry_delay=300, acks_late=True)
def analyze_video_with_gemini(self, bucket_name, gcs_filename, upload_id, user_id):
    logging.info(f"[{gcs_filename}] -> START for Upload ID: {upload_id}")
    upload_ref = db.collection('uploads').document(upload_id)
    bucket = storage_client.bucket(bucket_name)
    source_blob = bucket.blob(gcs_filename)
    temp_local_path = f"/tmp/{os.path.basename(gcs_filename)}"
    gemini_file_resource, client_instance = None, None
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
        active_challenges_prompt = []
        for challenge in active_challenges_full:
            if challenge.get('challengeId') not in user_completed_ids:
                prompt_challenge = {"challengeId": challenge.get('challengeId'), "description": challenge.get('description'), "keyword": challenge.get('keyword'), "type": "progress" if challenge.get("progressGoal") else "simple"}
                active_challenges_prompt.append(prompt_challenge)
        
        prompt = AI_ANALYSIS_PROMPT.replace('{active_challenges_placeholder}', json.dumps(active_challenges_prompt))
        if not source_blob.exists(): raise FileNotFoundError(f"Blob '{gcs_filename}' not found.")
        source_blob.download_to_filename(temp_local_path)
        analysis_result_str = None
        for api_key in ACTIVE_GEMINI_KEYS:
            try:
                client_instance = genai.Client(api_key=api_key)
                gemini_file_resource = client_instance.files.upload(file=temp_local_path)
                while gemini_file_resource.state.name == "PROCESSING": time.sleep(10); gemini_file_resource = client_instance.files.get(name=gemini_file_resource.name)
                if gemini_file_resource.state.name == "FAILED": raise Exception("Gemini File API failed.")
                response = client_instance.models.generate_content("gemini-1.5-flash", [gemini_file_resource, prompt])
                analysis_result_str = response.text
                break
            except Exception as e:
                logging.warning(f"Gemini key failed: {e}. Trying next...")
                if gemini_file_resource: 
                    try: client_instance.files.delete(name=gemini_file_resource.name) 
                    except: pass
                gemini_file_resource = None; continue
        if not analysis_result_str: raise Exception("All Gemini API keys failed.")
        cleaned_json_string = analysis_result_str.strip().removeprefix("```json").removesuffix("```").strip()
        ai_result = json.loads(cleaned_json_string)
        if ai_result.get("error"):
            upload_ref.update({'status': 'COMPLETED', 'aiResult': cleaned_json_string})
        else:
            today_wib_date = datetime.datetime.now(WIB_TZ).date()
            transaction = db.transaction()
            referrer_id, is_first_upload = update_stats_and_upload_transaction(transaction, user_ref, upload_ref, cleaned_json_string, active_challenges_full, today_wib_date)
            if referrer_id and is_first_upload:
                award_bonus_points.delay(referrer_id, 50, "Successful Referral")
            handle_team_challenge_progress(user_id, ai_result.get('progressUpdate'))
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
        if gemini_file_resource: 
            try: client_instance.files.delete(name=gemini_file_resource.name) 
            except: pass
        if os.path.exists(temp_local_path): os.remove(temp_local_path)