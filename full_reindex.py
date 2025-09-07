# FILE: trackeco-backend/full_reindex.py
import os
import logging
from google.cloud import firestore
from algoliasearch.search.client import SearchClient as SearchClientSync # Use new client
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()
logging.basicConfig(level=logging.INFO)

db = firestore.Client()

ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_ADMIN_API_KEY = os.environ.get("ALGOLIA_ADMIN_API_KEY")
ALGOLIA_INDEX_NAME = "users"

if not all([ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY]):
    raise ValueError("Algolia credentials are not set.")

client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY)

def reindex_all_users():
    logging.info(f"Starting re-indexing to Algolia index: '{ALGOLIA_INDEX_NAME}'")
    all_users = db.collection('users').stream()

    records_to_index = []
    for user_doc in all_users:
        user_data = user_doc.to_dict()
        record = {
            'objectID': user_doc.id,
            'userId': user_data.get('userId'),
            'displayName': user_data.get('displayName'),
            'username': user_data.get('username'),
            'avatarUrl': user_data.get('avatarUrl'),
            'totalPoints': user_data.get('totalPoints', 0)
        }
        records_to_index.append(record)
        
        if len(records_to_index) >= 500:
            # Use the new client.save_objects method
            client.save_objects(
                index_name=ALGOLIA_INDEX_NAME, objects=records_to_index
            )
            logging.info(f"Indexed a batch of {len(records_to_index)} users...")
            records_to_index = []

    if records_to_index:
        client.save_objects(
            index_name=ALGOLIA_INDEX_NAME, objects=records_to_index
        )
        logging.info(f"Indexed final batch of {len(records_to_index)} users.")

    logging.info("Full re-indexing complete.")

if __name__ == "__main__":
    reindex_all_users()