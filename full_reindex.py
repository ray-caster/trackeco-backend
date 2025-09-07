# FILE: trackeco-backend/full_reindex.py
import os
import logging
from google.cloud import firestore
from algoliasearch.search.client import SearchClient
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()
logging.basicConfig(level=logging.INFO)

# Initialize Firestore
db = firestore.Client()

# Initialize Algolia
ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_ADMIN_API_KEY")
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX_NAME")

if not all([ALGOLIA_APP_ID, ALGOLIA_API_KEY]):
    raise ValueError("Algolia credentials are not set in the environment.")

client = SearchClient.create(ALGOLIA_APP_ID, ALGOLIA_API_KEY)
index = client.init_index(ALGOLIA_INDEX_NAME)

def reindex_all_users():
    """
    Fetches all users from Firestore and indexes them in Algolia.
    This should be run once to populate Algolia and can be re-run if the index
    ever gets out of sync.
    """
    logging.info(f"Starting re-indexing of all users to Algolia index: '{ALGOLIA_INDEX_NAME}'")
    users_ref = db.collection('users')
    all_users = users_ref.stream()

    records_to_index = []
    for user_doc in all_users:
        user_data = user_doc.to_dict()
        
        # Algolia requires each record to have a unique 'objectID'.
        # Using the Firestore document ID is the perfect choice.
        record = {
            'objectID': user_doc.id,
            'userId': user_data.get('userId'),
            'displayName': user_data.get('displayName'),
            'username': user_data.get('username'),
            'avatarUrl': user_data.get('avatarUrl'),
            'totalPoints': user_data.get('totalPoints', 0)
        }
        records_to_index.append(record)
        
        # Algolia's API is optimized for batching.
        if len(records_to_index) >= 500:
            index.save_objects(records_to_index, {'autoGenerateObjectIDIfNotExist': False})
            logging.info(f"Indexed a batch of {len(records_to_index)} users...")
            records_to_index = []

    # Index any remaining records
    if records_to_index:
        index.save_objects(records_to_index, {'autoGenerateObjectIDIfNotExist': False})
        logging.info(f"Indexed the final batch of {len(records_to_index)} users.")

    logging.info("Full re-indexing complete.")

if __name__ == "__main__":
    reindex_all_users()