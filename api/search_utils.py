# FILE: trackeco-backend/api/search_utils.py

import logging
from .config import db, algolia_client, ALGOLIA_INDEX_NAME

def sync_user_to_algolia(user_id):
    """
    Fetches the latest user data from Firestore and syncs it to Algolia
    using the add_or_update_object method.
    """
    logging.info(f"[Algolia Sync] Starting sync for user_id: {user_id}")
    
    if not algolia_client:
        logging.error("[Algolia Sync] FATAL: Algolia client is not configured. Check environment variables.")
        return

    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()

        if not user_doc.exists:
            logging.warning(f"[Algolia Sync] User {user_id} not found in Firestore. Deleting from Algolia.")
            # This method correctly deletes the object if the user is removed from Firestore.
            algolia_client.delete_object(index_name=ALGOLIA_INDEX_NAME, object_id=user_id)
            logging.info(f"[Algolia Sync] Successfully deleted user {user_id} from Algolia.")
            return

        user_data = user_doc.to_dict()
        logging.info(f"[Algolia Sync] Fetched user data from Firestore for {user_id}: {user_data.get('displayName')}")

        # The record to be saved. Note: The objectID is passed separately to the method.
        record_body = {
            'userId': user_data.get('userId'),
            'displayName': user_data.get('displayName'),
            'username': user_data.get('username'),
            'avatarUrl': user_data.get('avatarUrl'),
            'totalPoints': user_data.get('totalPoints', 0)
        }
        
        # --- FIX: Using the correct add_or_update_object() method ---
        logging.info(f"[Algolia Sync] Saving record to Algolia for objectID '{user_id}': {record_body}")
        
        algolia_client.add_or_update_object(
            index_name=ALGOLIA_INDEX_NAME,
            object_id=user_id,
            body=record_body
        )
        # --------------------------------------------------------------------------
        
        logging.info(f"[Algolia Sync] SUCCESS: Successfully synced user {user_id} to Algolia.")

    except Exception as e:
        logging.error(f"[Algolia Sync] FAILED to sync user {user_id} to Algolia. Error: {e}", exc_info=True)
        # Re-raise the exception so Celery knows the task failed and can retry it
        raise