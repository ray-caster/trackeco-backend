import os
import logging
from google.cloud import firestore
from dotenv import load_dotenv

# --- SETUP & CONFIG ---
# This allows the script to find other modules like logging_config
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from logging_config import setup_logging

setup_logging()
load_dotenv()

try:
    # When running on the VPS, it needs the explicit credentials path
    SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/home/trackeco/app/firebase-admin-key.json")
    db = firestore.Client.from_service_account_json(SERVICE_ACCOUNT_FILE)
    logging.info("Successfully initialized Firestore client for rank updater script.")
except Exception as e:
    logging.critical(f"FATAL: Failed to initialize Firestore client. Error: {e}")
    exit()

def update_all_user_ranks():
    """
    Iterates through all users, calculates their rank based on totalPoints,
    and updates the 'rank' field in their Firestore document.
    """
    logging.info("Starting the user rank update process...")
    
    try:
        users_ref = db.collection('users')
        query = users_ref.order_by('totalPoints', direction=firestore.Query.DESCENDING)
        
        batch = db.batch()
        updated_users_count = 0
        
        for rank, user_doc in enumerate(query.stream(), 1):
            user_ref = users_ref.document(user_doc.id)
            batch.update(user_ref, {'rank': rank})
            updated_users_count += 1
            
            if updated_users_count % 500 == 0:
                logging.info(f"Committing a batch of 500 rank updates...")
                batch.commit()
                batch = db.batch()

        if updated_users_count % 500 != 0:
            logging.info(f"Committing the final batch of {updated_users_count % 500} rank updates...")
            batch.commit()

        logging.info(f"Successfully updated the rank for {updated_users_count} users.")
        print(f"Success: Updated rank for {updated_users_count} users.")

    except Exception as e:
        logging.error(f"An error occurred during the rank update process: {e}", exc_info=True)
        print(f"Failure: An error occurred. Check the log file for details.")

# This makes the script runnable from the command line
if __name__ == '__main__':
    update_all_user_ranks()