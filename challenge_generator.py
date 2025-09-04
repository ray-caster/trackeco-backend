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
You are "Eco-Quest," an AI game designer for the environmental app TrackEco. Your primary goal is to generate a single, engaging, and clearly defined challenge. Your entire output must be a single, raw JSON object that strictly adheres to the schema provided in `<OutputSchema>`.
</RoleAndGoal>

<CoreDirectives>
1.  **Adhere to Request:** The generated challenge MUST perfectly match the requested `Timescale` (`daily`, `weekly`, `monthly`) and `Challenge Type` (`simple`, `progress`).
2.  **Scale Difficulty by Timescale:**
    *   **daily:** A simple, common action one person can do in a single day (e.g., recycle two bottles, compost kitchen scraps).
    *   **weekly:** A more involved action or a larger quantity for progress challenges (e.g., collect and recycle 10 cans, properly dispose of a used battery).
    *   **monthly:** A significant, high-impact goal that may require planning (e.g., achieve a progress goal of 10+ items, clean a sack of items in a beach, create a recycled art project).
3.  **Ensure Variety:** Create a NEW challenge that is distinct from the provided `Previous Challenges` list. Be creative.
4.  **Prioritize Safety & Feasibility:** The action described must be safe, public, recordable in a short video, and must not require the user to spend money or interact with strangers.
</CoreDirectives>

<ChainOfThought>
Before constructing the JSON, reason through these steps:
1.  **Review Inputs:** What is the requested `Timescale` and `Challenge Type`? What challenges have been done before?
2.  **Brainstorm Action:** Based on the timescale, what is a new, safe, and meaningful environmental action?
3.  **Formulate Description:** How can I phrase this clearly and engagingly? For progress challenges, the goal number must be in the description.
4.  **Select Keyword:** What is the best single, lowercase keyword for this action (e.g., "bottle", "litter", "compost")?
5.  **Assign Points & Goal:** What is a fair `bonusPoints` value for this difficulty? If it's a progress challenge, what is a realistic `progressGoal`?
6.  **Recording Feasibility:** Is this challenge possible to record in one go within 5 minutes? Is this challenge proper to record, does it ask the user to record private matters?
7.  **Final Check:** Does the generated challenge meet all `CoreDirectives`?
</ChainOfThought>

<InputData>
Timescale Requested: **{timescale_placeholder}**
Challenge Type Requested: **{challenge_type_placeholder}**
Previous Challenges (for ensuring variety):
{previous_challenges_placeholder}
</InputData>

<OutputSchema>
Your response must be a single JSON object conforming to this JSON Schema. Do not include markdown like ```json or any other text before or after the JSON.
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "description": {
      "type": "string",
      "description": "The clear, user-facing challenge description. Must include the goal number for progress types."
    },
    "bonusPoints": {
      "type": "integer",
      "description": "Points based on difficulty (daily: 5-20, weekly: 25-50, monthly: 60-100)."
    },
    "keyword": {
      "type": "string",
      "description": "A simple, single, lowercase keyword for the primary item in the description."
    },
    "progressGoal": {
      "type": ["integer", "null"],
      "description": "The target number for progress challenges. MUST be null for simple challenges."
    }
  },
  "required": ["description", "bonusPoints", "keyword", "progressGoal"]
}
```
</OutputSchema>
<FinalInstruction>
Generate the JSON response now. Your entire output must start with `{` and end with `}`.
</FinalInstruction>
"""

def generate_new_challenge_from_ai(timescale, challenge_type, previous_descriptions=[]):
    """Calls Gemini to generate a challenge based on specific criteria."""
    logging.info(f"Attempting to generate a new '{challenge_type}' challenge for timescale '{timescale}' from Gemini API...")
    for api_key in ACTIVE_GEMINI_KEYS:
        client_instance = genai.Client(api_key=api_key)
        
        previous_list = "- " + "\n- ".join(previous_descriptions) if previous_descriptions else "N/A"
        prompt = CHALLENGE_PROMPT.replace('{timescale_placeholder}', timescale)
        prompt = prompt.replace('{challenge_type_placeholder}', challenge_type)
        prompt = prompt.replace('{previous_challenges_placeholder}', previous_list)

        response = client_instance.models.generate_content(model="gemini-2.5-pro", contents=[prompt])
        
        cleaned_json_string = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(cleaned_json_string)

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
            if not all(k in challenge_data for k in ["description", "bonusPoints", "keyword"]):
                logging.warning(f"AI generated invalid simple data, skipping: {challenge_data}")
                continue
            challenge_data.update({
                'type': challenge_type,
                'expiresAt': expires_at.astimezone(datetime.timezone.utc),
                'isTeamUpEligible': False # Simple challenges are not for teams
            })
            new_challenges.append(challenge_data)
            previous_descriptions.append(challenge_data['description'])

        # Generate progress challenges
        for i in range(progress_count):
            challenge_data = generate_new_challenge_from_ai(challenge_type, 'progress', previous_descriptions)
            if not all(k in challenge_data for k in ["description", "bonusPoints", "keyword", "progressGoal"]):
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
    
    results = generate_challenge_set(args.type, args.simple_count, args.progress_count)
    if results:
        print(f"[{datetime.datetime.now()}] Success! Created {len(results)} new challenges.")
    else:
        print(f"[{datetime.datetime.now()}] Failure. Check log file for details.")