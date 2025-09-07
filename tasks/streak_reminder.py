# FILE: trackeco-backend/tasks/streak_reminder.py

import os
import sys
import logging
import datetime
import pytz
from google.cloud import firestore
from dotenv import load_dotenv

# --- SETUP & CONFIG ---
# This allows the script to find other modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from logging_config import setup_logging
from api.notifications import send_notification
from api.config import db

setup_logging()
load_dotenv()

def send_streak_reminders():
    """
    Finds users with an active streak who have not recorded an action today
    and sends them a reminder notification.
    """
    logging.info("Starting the streak reminder process...")

    try:
        # 1. Define the time window (e.g., based on WIB timezone)
        wib_tz = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.datetime.now(wib_tz)
        start_of_today_wib = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)

        logging.info(f"Timezone: WIB. Current time: {now_wib}. Checking for activity after: {start_of_today_wib}")

        # 2. Construct the Firestore query
        # This finds users who:
        # - Have an active streak
        # - Have NOT updated their streak today
        # - Have streak reminders enabled (or the field is missing, defaulting to enabled)
        # IMPORTANT: This requires a composite index in Firestore.
        users_ref = db.collection('users')
        query = users_ref.where(
            filter=firestore.FieldFilter('currentStreak', '>', 0)
        ).where(
            filter=firestore.FieldFilter('lastStreakTimestamp', '<', start_of_today_wib)
        )
        
        reminders_sent_count = 0
        
        for user_doc in query.stream():
            user_data = user_doc.to_dict()
            user_id = user_doc.id

            # Check if the user has explicitly disabled reminders
            if user_data.get('streakRemindersEnabled') is False:
                logging.info(f"Skipping reminder for user {user_id} as they have disabled it.")
                continue

            logging.info(f"User {user_id} is eligible for a streak reminder. Last active: {user_data.get('lastStreakTimestamp')}")

            # Send the notification
            send_notification(
                user_id=user_id,
                title="Don't lose your streak! 🔥",
                body=f"You're on a {user_data.get('currentStreak')}-day streak. Record an eco-action to keep it going!",
                data={"type": "streak_reminder"},
                setting_name="streakRemindersEnabled"
            )
            reminders_sent_count += 1
            
        logging.info(f"Process complete. Sent streak reminders to {reminders_sent_count} users.")
        print(f"Success: Sent reminders to {reminders_sent_count} users.")

    except Exception as e:
        logging.error(f"An error occurred during the streak reminder process: {e}", exc_info=True)
        print(f"Failure: An error occurred. Check the log file for details.")

# This makes the script runnable from the command line
if __name__ == '__main__':
    send_streak_reminders()