import os
import sys
import logging
import datetime
import pytz
from google.cloud import firestore
from dotenv import load_dotenv

# --- SETUP & CONFIG ---
# This allows the script to find other modules like logging_config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from logging_config import setup_logging
# This allows the script to find the cache utility
from api.cache_utils import invalidate_user_summary_cache
from api.config import redis_client

# Load environment variables and configure logging
setup_logging()
load_dotenv()

try:
    # Initialize the Firestore client
    db = firestore.Client()
    logging.info("Successfully initialized Firestore client for streak updater script.")
except Exception as e:
    logging.critical(f"FATAL: Failed to initialize Firestore client. Error: {e}")
    exit()

def reset_inactive_streaks():
    """
    Finds all users whose last streak update was before today and resets their
    current streak to 0. This script is designed to be run once per day via cron.
    """
    logging.info("Starting the user streak reset process...")

    try:
        # 1. Define the time window based on the target timezone (WIB)
        wib_tz = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.datetime.now(wib_tz)
        start_of_today_wib = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)

        logging.info(f"Timezone: WIB. Current time: {now_wib}. Resetting streaks for activity before: {start_of_today_wib}")

        # 2. Construct the Firestore query
        # This query finds users who have an active streak but haven't updated it today.
        # IMPORTANT: This requires a composite index in Firestore.
        users_ref = db.collection('users')
        query = users_ref.where(
            filter=firestore.FieldFilter('currentStreak', '>', 0)
        ).where(
            filter=firestore.FieldFilter('lastStreakTimestamp', '<', start_of_today_wib)
        )

        # 3. Process the results in batches
        batch = db.batch()
        streaks_reset_count = 0
        
        for user_doc in query.stream():
            user_ref = users_ref.document(user_doc.id)
            logging.info(f"User {user_doc.id} streak will be reset. Last active: {user_doc.to_dict().get('lastStreakTimestamp')}")
            
            # Add the update to the batch
            batch.update(user_ref, {'currentStreak': 0})
            streaks_reset_count += 1
            
            # Invalidate the user's cache in Redis
            if redis_client:
                invalidate_user_summary_cache(user_doc.id)

            # Commit the batch every 500 users and start a new one
            if streaks_reset_count % 500 == 0:
                logging.info(f"Committing a batch of 500 streak resets...")
                batch.commit()
                batch = db.batch()

        # Commit any remaining users in the last batch
        if streaks_reset_count % 500 != 0:
            logging.info(f"Committing the final batch of {streaks_reset_count % 500} streak resets...")
            batch.commit()

        if streaks_reset_count == 0:
            logging.info("Process complete. No user streaks needed to be reset.")
            print("Success: No user streaks needed to be reset.")
        else:
            logging.info(f"Successfully reset the streak for {streaks_reset_count} users.")
            print(f"Success: Reset streak for {streaks_reset_count} users.")

    except Exception as e:
        logging.error(f"An error occurred during the streak reset process: {e}", exc_info=True)
        print(f"Failure: An error occurred. Check the log file for details.")

# This makes the script runnable from the command line
if __name__ == '__main__':
    reset_inactive_streaks()