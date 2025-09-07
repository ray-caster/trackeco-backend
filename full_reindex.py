# FILE: trackeco-backend/full_reindex.py
import os
import logging
from google.cloud import firestore
# --- THIS IS THE FIX ---
from algoliasearch.search.client import SearchClientSync 
# --------------------
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()
logging.basicConfig(level=logging.INFO)

db = firestore.Client()

ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_ADMIN_API_KEY = os.environ.get("ALGOLIA_ADMIN_API_KEY")
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX_NAME")

if not all([ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY]):
    raise ValueError("Algolia credentials are not set.")

# --- AND THIS IS THE FIX ---
client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY)
# ------------------------

def reindex_all_users():
    logging.info(f"Starting re-indexing to Algolia index: '{ALGOLIA_INDEX_NAME}'")
    all_users = db.collection('users').stream()

    records_to_index = []
    total_indexed = 0
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
        
        # Index in batches of 500 for efficiency
        if len(records_to_index) >= 500:
            client.save_objects(
                index_name=ALGOLIA_INDEX_NAME, objects=records_to_index
            )
            logging.info(f"Indexed a batch of {len(records_to_index)} users...")
            total_indexed += len(records_to_index)
            records_to_index = []

    # Index any remaining records in the final batch
    if records_to_index:
        client.save_objects(
            index_name=ALGOLIA_INDEX_NAME, objects=records_to_index
        )
        logging.info(f"Indexed final batch of {len(records_to_index)} users.")
        total_indexed += len(records_to_index)

    logging.info(f"Full re-indexing complete. Total users indexed: {total_indexed}")

if __name__ == "__main__":
    reindex_all_users()