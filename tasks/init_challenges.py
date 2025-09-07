import logging
from google.cloud import firestore
from dotenv import load_dotenv

from logging_config import setup_logging
from challenge_generator import generate_challenge_set

# --- SETUP & CONFIG ---
# This script uses the same environment and credentials as your other scripts.
setup_logging()
load_dotenv()
def run_initial_setup():
    """
    Checks if active challenges exist and generates the default set if not.
    This should be run once during deployment or manually when needed.
    """
    logging.info("Running initial setup. Checking for active challenges...")
    
    # We must explicitly initialize the client in a standalone script
    db = firestore.Client() 
    
    query = db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True)).limit(1)
    try:
        # Generate 3 daily challenges (2 simple, 1 progress)
        generate_challenge_set('daily', simple_count=2, progress_count=1)
        # Generate 2 weekly challenges (1 simple, 1 progress)
        generate_challenge_set('weekly', simple_count=1, progress_count=1)
        # Generate 2 monthly challenges (1 simple, 1 progress)
        generate_challenge_set('monthly', simple_count=1, progress_count=1)
        logging.info("Default challenges generated successfully.")
        print("Default challenges generated successfully.")
    except Exception as e:
        logging.error(f"Failed to generate default challenges during setup: {e}", exc_info=True)
        print(f"ERROR: Failed to generate default challenges. Check logs. Error: {e}")

if __name__ == "__main__":
    run_initial_setup()