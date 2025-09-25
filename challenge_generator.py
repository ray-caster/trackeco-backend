import os
import logging
import datetime
import json
import uuid
import argparse
from google.cloud import firestore
from google import genai
from google.oauth2 import service_account
from firebase_init import initialize_firebase
from dotenv import load_dotenv
from api.prompts import CHALLENGE_GENERATION_PROMPT
import pytz
import redis
# --- SETUP & CONFIG ---
try:
    from logging_config import setup_logging
    setup_logging()
    load_dotenv()

    # Use centralized Firebase initialization
    initialize_firebase()
    db = firestore.Client()
    logging.info("Successfully initialized Firestore client for challenge generator.")

    GEMINI_API_KEYS = [ os.environ.get(f"GEMINI_API_KEY_{i+1}") for i in range(4) ]
    ACTIVE_GEMINI_KEYS = [key for key in GEMINI_API_KEYS if key]
    if not ACTIVE_GEMINI_KEYS:
        raise ValueError("No GEMINI_API_KEY environment variables found.")
    redis_client = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    redis_client.ping()
except Exception as e:
    logging.critical(f"FATAL: Script setup failed. Error: {e}", exc_info=True)
    exit()
    
except Exception as e:
    logging.critical(f"FATAL: Challenge generator could not connect to Redis. Error: {e}")
    redis_client = None
    exit()


def generate_new_challenge_from_ai(timescale, challenge_type, previous_descriptions=[]):
    """Calls Gemini to generate a challenge based on specific criteria."""
    if not ACTIVE_GEMINI_KEYS:
        raise Exception("No active Gemini API keys found.")
    start_index = int(redis_client.get("current_challenge_gemini_key_index") or 0)
    logging.info(f"Attempting to generate a new '{challenge_type}' challenge for timescale '{timescale}' from Gemini API...")
    for i in range(len(ACTIVE_GEMINI_KEYS)):
        current_index = (start_index + i) % len(ACTIVE_GEMINI_KEYS)
        api_key = ACTIVE_GEMINI_KEYS[current_index]
        try:
            logging.info(f"--> Trying Gemini API Key #{i + 1}")
            client_instance = genai.Client(api_key=api_key)
        
            previous_list = "- " + "\n- ".join(previous_descriptions) if previous_descriptions else "N/A"
            prompt = CHALLENGE_GENERATION_PROMPT.replace('{timescale_placeholder}', timescale)
            prompt = prompt.replace('{challenge_type_placeholder}', challenge_type)
            prompt = prompt.replace('{previous_challenges_placeholder}', previous_list)

            response = client_instance.models.generate_content(model="gemini-2.5-pro", contents=[prompt])
            
            logging.info(f"Successfully received response from Gemini API Key #{i + 1}.")
            raw_text = response.text
            logging.debug(f"Raw Gemini response: {raw_text}")
            redis_client.set("current_challenge_gemini_key_index", current_index)
            cleaned_json_string = raw_text.strip().removeprefix("```json").removesuffix("```").strip()
            parsed_json = json.loads(cleaned_json_string)
            logging.info(f"Successfully parsed JSON from Gemini response: {parsed_json}")
            return parsed_json
            
        except Exception as e:
            logging.warning(f"Gemini API Key #{current_index + 1} failed. Error: {e}")
            if i == len(ACTIVE_GEMINI_KEYS) - 1:
                logging.error("All Gemini API keys have failed.")
                raise
            continue
    raise Exception("No active Gemini API keys were available to attempt the call.")

@firestore.transactional
def activate_new_challenges_transaction(transaction, challenge_type, new_challenges):
    """Atomically deactivates old challenges of a specific type and activates new ones."""
    challenge_collection_ref = db.collection('challenges')
    query = challenge_collection_ref.where(filter=firestore.FieldFilter('type', '==', challenge_type)).where(filter=firestore.FieldFilter('isActive', '==', True))
    deactivated_count = 0
    for old_challenge in query.stream(transaction=transaction):
        transaction.update(old_challenge.reference, {'isActive': False})
        deactivated_count += 1
    logging.info(f"Transaction: Deactivated {deactivated_count} old '{challenge_type}' challenge(s).")
    
    for challenge_data in new_challenges:
        challenge_id = str(uuid.uuid4())
        new_ref = challenge_collection_ref.document(challenge_id)
        challenge_data.update({"challengeId": challenge_id, "isActive": True, "createdAt": firestore.SERVER_TIMESTAMP})
        transaction.set(new_ref, challenge_data)
    logging.info(f"Transaction: Set {len(new_challenges)} new '{challenge_type}' challenge(s).")

def generate_challenge_set(challenge_type, simple_count, progress_count):
    """Main function to generate a mixed set of simple and progress challenges."""
    total_count = simple_count + progress_count
    logging.info(f"Starting process: generate {total_count} '{challenge_type}' challenges ({simple_count} simple, {progress_count} progress)...")
    try:
        wib_tz = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.datetime.now(wib_tz)
        if challenge_type == 'daily':
            expires_at = now_wib.replace(hour=23, minute=59, second=59)
        elif challenge_type == 'weekly':
            expires_at = (now_wib + datetime.timedelta(days=6 - now_wib.weekday())).replace(hour=23, minute=59, second=59)
        elif challenge_type == 'monthly':
            next_month = now_wib.replace(day=28) + datetime.timedelta(days=4)
            expires_at = (next_month - datetime.timedelta(days=next_month.day)).replace(hour=23, minute=59, second=59)
        else: raise ValueError("Invalid challenge type specified.")
        
        query = db.collection('challenges').where(filter=firestore.FieldFilter('type', '==', challenge_type)).where(filter=firestore.FieldFilter('isActive', '==', True))
        previous_descriptions = [c.to_dict().get('description') for c in query.stream() if c.to_dict().get('description')]
        new_challenges = []
        
        # Generate simple challenges
        for i in range(simple_count):
            challenge_data = generate_new_challenge_from_ai(challenge_type, 'simple', previous_descriptions)
            if not all(k in challenge_data for k in ["description", "bonusPoints"]):
                logging.warning(f"AI generated invalid simple data, skipping: {challenge_data}")
                continue
            challenge_data.update({
                'type': challenge_type,
                'expiresAt': expires_at.astimezone(datetime.timezone.utc),
                'isTeamUpEligible': False
            })
            new_challenges.append(challenge_data)
            previous_descriptions.append(challenge_data['description'])

        # Generate progress challenges
        for i in range(progress_count):
            challenge_data = generate_new_challenge_from_ai(challenge_type, 'progress', previous_descriptions)
            if not all(k in challenge_data for k in ["description", "bonusPoints", "progressGoal"]):
                logging.warning(f"AI generated invalid progress data, skipping: {challenge_data}")
                continue
            challenge_data.update({
                'type': challenge_type,
                'expiresAt': expires_at.astimezone(datetime.timezone.utc),
                'isTeamUpEligible': True # Progress challenges are eligible for Team Up
            })
            new_challenges.append(challenge_data)
            previous_descriptions.append(challenge_data['description'])

        if not new_challenges: raise Exception("AI failed to generate any valid challenges.")
        
        transaction = db.transaction()
        activate_new_challenges_transaction(transaction, challenge_type, new_challenges)
        return new_challenges
    except Exception as e:
        logging.error(f"FATAL: Error in main challenge creation process: {e}", exc_info=True)
        return None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate new TrackEco challenges.')
    parser.add_argument('--type', type=str, required=True, choices=['daily', 'weekly', 'monthly'])
    parser.add_argument('--simple-count', type=int, default=0)
    parser.add_argument('--progress-count', type=int, default=0)
    args = parser.parse_args()

    if args.simple_count == 0 and args.progress_count == 0:
        print("Error: Must specify at least one simple or progress challenge count.")
        exit()

    print(f"[{datetime.datetime.now()}] Running challenge generator: type='{args.type}', simple={args.simple_count}, progress={args.progress_count}...")
    lock_key = "lock:challenge_generator"
    # nx=True means set only if the key does not exist. ex=60 means expire after 60 seconds.
    is_lock_acquired = redis_client.set(lock_key, "running", ex=60, nx=True)
    
    if not is_lock_acquired:
        print(f"[{datetime.datetime.now()}] Challenge generation is already in progress. Exiting.")
        logging.warning("Challenge generation is already in progress. Exiting.")
        exit()

    try:
        print(f"[{datetime.datetime.now()}] Acquired lock. Running challenge generator: type='{args.type}', simple={args.simple_count}, progress={args.progress_count}...")
        results = generate_challenge_set(args.type, args.simple_count, args.progress_count)
        if results:
            print(f"[{datetime.datetime.now()}] Success! Created {len(results)} new challenges.")
        else:
            print(f"[{datetime.datetime.now()}] Failure. Check log file for details.")
    finally:
        # Always release the lock when done
        redis_client.delete(lock_key)
        print(f"[{datetime.datetime.now()}] Released lock.")