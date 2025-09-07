# FILE: trackeco-backend/api/search_utils.py

import logging
from .config import db, algolia_index

def sync_user_to_algolia(user_id):
    """
    Fetches the latest user data from Firestore and syncs it to Algolia.
    This is the single source of truth for indexing logic.
    """
    if not algolia_index:
        logging.warning("Algolia client not configured. Skipping sync.")
        return

    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()

        if not user_doc.exists:
            # If the user doesn't exist, try to delete them from the index
            algolia_index.delete_object(user_id)
            logging.info(f"Deleted user {user_id} from Algolia index as they no longer exist in Firestore.")
            return

        user_data = user_doc.to_dict()

        # Construct the record for Algolia.
        # The 'objectID' is crucial and must be the same as your Firestore document ID.
        record = {
            'objectID': user_id,
            'userId': user_data.get('userId'),
            'displayName': user_data.get('displayName'),
            'username': user_data.get('username'),
            'avatarUrl': user_data.get('avatarUrl'),
            'totalPoints': user_data.get('totalPoints', 0)
        }
        
        # Save the object to the index. This will create or update the record.
        algolia_index.save_object(record).wait()
        logging.info(f"Successfully synced user {user_id} to Algolia.")

    except Exception as e:
        # Log the error but don't crash the main application flow
        logging.error(f"Failed to sync user {user_id} to Algolia: {e}", exc_info=True)