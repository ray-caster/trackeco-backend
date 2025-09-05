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
import redis
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
    redis_client = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    redis_client.ping()
except Exception as e:
    logging.critical(f"FATAL: Script setup failed. Error: {e}", exc_info=True)
    exit()
    
except Exception as e:
    logging.critical(f"FATAL: Challenge generator could not connect to Redis. Error: {e}")
    redis_client = None
    exit()

CHALLENGE_PROMPT = """
<RoleAndGoal>
    You are "Eco-Quest," an AI game designer for the environmental app TrackEco. Your primary goal is to generate a single, engaging, and clearly defined challenge. Your entire output must be a single, raw JSON object that strictly adheres to the schema provided in `<OutputSchema>`.
    </RoleAndGoal>

<CoreDirectives>
1. **Adhere to Request:**  
   The generated challenge MUST perfectly match the requested `Timescale` (`daily`, `weekly`, `monthly`) and `Challenge Type` (`simple`, `progress`).  

2. **Scale Difficulty by Timescale (with examples):**  
   - **daily:** A simple, common action one person can do in a single day.  
     *Examples:* recycle two bottles, compost kitchen scraps, bring a reusable cup, eat one plant-based meal, unplug one unused device, pick up 5 pieces of litter, water a plant with leftover cooking water, turn off lights when leaving a room.  

   - **weekly:** A more involved action or a larger quantity for progress challenges.  
     *Examples:* collect and recycle 10–20 cans, properly dispose of used batteries, commit to one “no single-use plastic” day, carpool/bike to school three times, sort and recycle a bag of e-waste, host a mini clean-up with two friends, replace a household item with a sustainable version, track food scraps in a compost jar for 7 days.  

   - **monthly:** A significant, high-impact goal that may require planning.  
     *Examples:* achieve a progress goal of 50+ items, clean a sack of items from a beach or park, create a recycled art project, plant a tree or start a small garden, organize a group clean-up event, complete a zero-waste week, build a DIY eco-project (compost bin, bird feeder, solar oven), host a community “green swap.”  

3. **Ensure Variety:**  
   Apply divergent thinking principles when generating challenges to encourage creativity:  
   - **Fluency:** Generate plentiful possibilities, not just obvious ones.  
   - **Flexibility:** Draw from different categories (personal habits, community actions, creative projects, lifestyle changes).  
   - **Originality:** Favor novel, surprising, or less common ideas.  
   - **Elaboration:** Add detail so the challenge feels specific and actionable.  
   - **Perspective Shifting & Reframing:** Consider different viewpoints (child, elder, nature, community) and reframe problems to uncover new angles.  
   - **Association & Metaphor:** Combine unrelated ideas or use analogies to inspire fresh directions.  

4. **Prioritize Safety, Feasibility & Systems Awareness:**  
   - **Personal Safety:** Challenges must avoid physical risk, hazardous materials, or unsafe environments.  
   - **Accessibility & Feasibility:** Tasks should be doable by anyone with minimal resources, requiring no purchase or specialized equipment.  

   - **Public & Recordable:** Actions must occur in real, observable environments (home, school, street, park, community space) and be recordable in a short video for accountability and sharing.  

   - **No Digital-Only Tasks:** All challenges must involve tangible, physical-world actions rather than purely online or screen-based activities.  

   - **Global Issues Awareness:** Encourage challenges that connect personal actions to broader issues such as climate change, waste management, biodiversity loss, clean water, air quality, or sustainable consumption. Example: collecting litter links to ocean plastic pollution, reducing meat intake connects to deforestation and emissions.  

   - **Systems Thinking & Feedback Loops:** Design challenges that highlight cause–effect relationships and feedback loops. For instance:  
     - Reducing food waste lowers methane emissions → slows climate change → benefits agriculture → improves food security.  
     - Planting greenery improves air quality → supports pollinators → strengthens ecosystems → enhances human well-being.  
     - Choosing reusables reduces demand for plastics → lowers production → cuts emissions → lessens global warming.

5. **Balance Accessibility with Meaningful Challenge:**  
   - **Approachable for All:** Ensure challenges are easy enough for anyone to start, regardless of age, background, or resources.  
   - **Incremental Difficulty:** Offer tasks that can be simple at first but also include optional stretch goals for those who want a greater challenge.  
   - **Environmental Impact:** Each task, whether easy or hard, must have a clear connection to helping the environment — reducing waste, conserving resources, protecting biodiversity, or improving community well-being.  
   - **Motivation Through Achievement:** Make tasks rewarding by allowing participants to see immediate impact (like cleaner surroundings) while also contributing to long-term systemic change.   
   - **Sustainability of Effort:** Encourage challenges that, while accessible, have the potential to grow into larger habits or community initiatives over time.  
   - **Long-Term Mindset:** Favor challenges that build habits, foster ripple effects in communities, or demonstrate how small, repeated actions scale up to systemic change.  
   - **Brag-Worthy & Shareable:** Favor challenges that participants would feel proud to show to friends or post online — visually clear, socially impressive, and likely to inspire others through positive peer influence.  
</CoreDirectives>

<ChainOfThought>
Before constructing the JSON, reason through these steps:
Before constructing the JSON, reason through these steps:

1. **Review Inputs (Critical Thinking):**
   - Confirm the requested `Timescale` and `Challenge Type`.
   - Scan `Previous Challenges` to avoid duplication.
   - Ask: What is the requested `Timescale` and `Challenge Type`? What challenges have been done before? Does the request align with the difficulty scaling rules?

2. **Generate Possibilities (Divergent Thinking):**
   - Brainstorm multiple ideas using fluency, flexibility, originality, elaboration, and association.
   - Consider reframing: Could the challenge be seen through the eyes of different stakeholders (e.g., child, elder, community, ecosystem)?
   - Look for analogies or metaphors (e.g., “feeding the soil” instead of just “composting”).
   - Ask: Based on the timescale, what is a new, safe, and meaningful environmental action?

3. **Select & Refine (Critical + Divergent Thinking):**
   - Pick the most novel but feasible idea.
   - Ensure it is distinct from previous challenges.
   - Phrase the challenge with clarity, making it concrete, recordable, and brag-worthy.
   - Ask: How can I phrase this clearly and engagingly? For progress challenges, the goal number must be in the description.

4. **Evaluate Short- vs. Long-Term Impact (Temporal Thinking):**
   - Ask: What is the immediate visible effect of this challenge? (short-term)
   - Ask: How could this action compound into long-term systemic change if repeated or scaled? (long-term)

5. **Map Systemic Connections (Systemic Thinking):**
   - Trace cause–effect and feedback loops:
     - Direct impact (e.g., picking litter → cleaner space).
     - Ripple effects (e.g., cleaner park → stronger community pride → less littering).
   - Connect action to global issues (climate, biodiversity, waste, resources).

6. **Check Safety & Feasibility (Critical + Systemic Thinking):**
   - Is the challenge safe for all ages?
   - Does it require no purchases or special tools?
   - Can it be done in a public space and recorded within 5 minutes?
   - Is it culturally appropriate across different global contexts?

7. **Finalize JSON Fields:**
   - **description:** Write a concise, engaging challenge statement with goal numbers for progress tasks.
   - **keyword:** Choose the best single, lowercase keyword for this action (e.g., "bottle", "litter", "compost")?
   - **bonusPoints:** Scale fairly by timescale.
   - **progressGoal:** Null for simple, realistic number for progress. Is it a fair `bonusPoints` value for this difficulty? If it's a progress challenge, what is a realistic `progressGoal`?
   - Confirm output is strictly one JSON object, no markdown or extra text.

8. **FINAL CHECK** Does the generated challenge meet all `CoreDirectives`? If not, please redo from scratch.
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
      "description": "Points based on difficulty (daily: 5-20, weekly: 70-150, monthly: 700-1000)."
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
Generate the JSON response now. Your entire output must start with `{` and end with `}`.Do not include Markdown formatting, explanations, or text before/after.
</FinalInstruction>
"""

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
            prompt = CHALLENGE_PROMPT.replace('{timescale_placeholder}', timescale)
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