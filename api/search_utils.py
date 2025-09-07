# FILE: trackeco-backend/api/search_utils.py

import logging
from .config import db, algolia_client, ALGOLIA_INDEX_NAME # <-- Import client and index name

def sync_user_to_algolia(user_id):
    """
    Fetches the latest user data from Firestore and syncs it to Algolia
    using the modern v3+ SDK.
    """
    if not algolia_client:
        logging.warning("Algolia client not configured. Skipping sync.")
        return

    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()

        if not user_doc.exists:
            # If the user doesn't exist, delete them from the index
            algolia_client.delete_object(
                index_name=ALGOLIA_INDEX_NAME, object_id=user_id
            ).wait()
            logging.info(f"Deleted user {user_id} from Algolia as they no longer exist in Firestore.")
            return

        user_data = user_doc.to_dict()

        record = {
            'objectID': user_id,
            'userId': user_data.get('userId'),
            'displayName': user_data.get('displayName'),
            'username': user_data.get('username'),
            'avatarUrl': user_data.get('avatarUrl'),
            'totalPoints': user_data.get('totalPoints', 0)
        }
        
        # Call save_object directly on the client
        algolia_client.save_object(
            index_name=ALGOLIA_INDEX_NAME, object=record
        ).wait()
        logging.info(f"Successfully synced user {user_id} to Algolia.")

    except Exception as e:
        logging.error(f"Failed to sync user {user_id} to Algolia: {e}", exc_info=True)