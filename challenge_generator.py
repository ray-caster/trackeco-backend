import os
import logging
import datetime
import json
import uuid
import argparse
from google.cloud import firestore
from google import genai
from google.oauth2 import service_account
from dotenv import load_dotenv
import pytz

# --- SETUP & CONFIG ---
try:
    from logging_config import setup_logging
    setup_logging()
    load_dotenv()

    SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/home/trackeco/app/firebase-admin-key.json")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"Service account key not found at path: {SERVICE_ACCOUNT_FILE}")
    credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE)
    db = firestore.Client(credentials=credentials)
    logging.info("Successfully initialized Firestore client for challenge generator.")

    GEMINI_API_KEYS = [ os.environ.get(f"GEMINI_API_KEY_{i+1}") for i in range(4) ]
    ACTIVE_GEMINI_KEYS = [key for key in GEMINI_API_KEYS if key]
    if not ACTIVE_GEMINI_KEYS:
        raise ValueError("No GEMINI_API_KEY environment variables found.")
except Exception as e:
    logging.critical(f"FATAL: Script setup failed. Error: {e}", exc_info=True)
    exit()

CHALLENGE_PROMPT = """
<RoleAndGoal>
You are "Eco-Quest," an AI that designs environmental challenges for the TrackEco app. Your goal is to generate a fun, achievable challenge based on a requested type. The output must be a single, raw JSON object.
</RoleAndGoal>
<Rules>
1.  **Challenge Type:** Adhere to the requested challenge type (`simple` or `progress`).
2.  **Variety:** Given a list of previous challenge descriptions, create a NEW, different challenge.
3.  **Description:** Write a clear, user-facing description. For progress challenges, include the goal number (e.g., "Recycle 5 plastic bottles").
4.  **Keyword:** Provide a simple, single, lowercase keyword for the item to be tracked (e.g., "bottle"). This is mandatory for ALL challenges.
5.  **Bonus Points:** Assign total bonus points (5-25 for simple, 20-50 for progress).
6.  **Progress Goal:** For `progress` challenges ONLY, provide a `progressGoal` between 3 and 10. For `simple` challenges, this must be `null`.
7.  **Strict JSON Output:** The output must ONLY be the JSON object.
</Rules>
<Input>
Challenge Type Requested: **{challenge_type_placeholder}**
Previous Challenges (for ensuring variety):
{previous_challenges_placeholder}
</Input>
<OutputFormat>
```json
{
  "description": "<string>",
  "bonusPoints": <integer>,
  "keyword": "<string>",
  "progressGoal": <integer | null>
}
```
</OutputFormat>
<Examples>
- Request: `simple` -> Output: `{"description": "Properly recycle any aluminum can!", "bonusPoints": 10, "keyword": "can", "progressGoal": null}`
- Request: `progress` -> Output: `{"description": "Recycle a total of 5 plastic bottles!", "bonusPoints": 30, "keyword": "bottle", "progressGoal": 5}`
</Examples>
"""

def generate_new_challenge_from_ai(challenge_type, previous_descriptions=[]):
    """Calls Gemini to generate a simple or progress-based challenge, ensuring variety."""
    logging.info(f"Attempting to generate a new '{challenge_type}' challenge from Gemini API...")
    try:
        client_instance = genai.Client(api_key=ACTIVE_GEMINI_KEYS[0])
        previous_list = "- " + "\n- ".join(previous_descriptions) if previous_descriptions else "N/A"
        prompt = CHALLENGE_PROMPT.replace('{challenge_type_placeholder}', challenge_type)
        prompt = prompt.replace('{previous_challenges_placeholder}', previous_list)
        response = client_instance.models.generate_content(model="gemini-2.5-pro", contents=[prompt])
        logging.info("Successfully received response from Gemini API.")
        raw_text = response.text
        logging.debug(f"Raw Gemini response: {raw_text}")
        cleaned_json_string = raw_text.strip().removeprefix("```json").removesuffix("```").strip()
        parsed_json = json.loads(cleaned_json_string)
        logging.info(f"Successfully parsed JSON from Gemini response: {parsed_json}")
        return parsed_json
    except Exception as e:
        logging.error(f"ERROR: Failed during Gemini API call or JSON parsing. Error: {e}", exc_info=True)
        raise

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
        
        logging.info(f"Calculated expiration for new challenges: {expires_at.isoformat()}")
        query = db.collection('challenges').where(filter=firestore.FieldFilter('type', '==', challenge_type)).where(filter=firestore.FieldFilter('isActive', '==', True))
        previous_descriptions = [c.to_dict().get('description') for c in query.stream() if c.to_dict().get('description')]
        new_challenges = []
        
        # Generate simple challenges
        for i in range(simple_count):
            logging.info(f"Generating simple challenge {i+1} of {simple_count}...")
            challenge_data = generate_new_challenge_from_ai('simple', previous_descriptions)
            if not all(k in challenge_data for k in ["description", "bonusPoints", "keyword"]):
                logging.warning(f"AI generated invalid simple data, skipping: {challenge_data}")
                continue
            challenge_data.update({'type': challenge_type, 'expiresAt': expires_at.astimezone(datetime.timezone.utc)})
            new_challenges.append(challenge_data)
            previous_descriptions.append(challenge_data['description'])

        # Generate progress challenges
        for i in range(progress_count):
            logging.info(f"Generating progress challenge {i+1} of {progress_count}...")
            challenge_data = generate_new_challenge_from_ai('progress', previous_descriptions)
            if not all(k in challenge_data for k in ["description", "bonusPoints", "keyword", "progressGoal"]):
                logging.warning(f"AI generated invalid progress data, skipping: {challenge_data}")
                continue
            challenge_data.update({'type': challenge_type, 'expiresAt': expires_at.astimezone(datetime.timezone.utc)})
            new_challenges.append(challenge_data)
            previous_descriptions.append(challenge_data['description'])

        if not new_challenges: raise Exception("AI failed to generate any valid challenges after all attempts.")
        
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
    results = generate_challenge_set(args.type, args.simple_count, args.progress_count)
    if results:
        print(f"[{datetime.datetime.now()}] Success! Created {len(results)} new challenges.")
    else:
        print(f"[{datetime.datetime.now()}] Failure. Check log file for details.")