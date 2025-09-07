# FILE: trackeco-backend/api/notifications.py

import logging
from firebase_admin import messaging
from .config import db

def send_notification(user_id, title, body, data=None):
    """
    Sends a push notification to a specific user.

    Args:
        user_id (str): The ID of the user to send the notification to.
        title (str): The title of the notification.
        body (str): The body/message of the notification.
        data (dict, optional): A dictionary of custom data to send with the message.
    """
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            logging.warning(f"Attempted to send notification to non-existent user: {user_id}")
            return

        user_data = user_doc.to_dict()
        fcm_token = user_data.get('fcmToken')

        if not fcm_token:
            logging.info(f"User {user_id} does not have an FCM token. Skipping notification.")
            return

        # Ensure all data values are strings, as required by FCM
        if data:
            safe_data = {k: str(v) for k, v in data.items()}
        else:
            safe_data = {}
        
        # Add title and body to the data payload so the app can always read it
        safe_data['title'] = title
        safe_data['body'] = body

        message = messaging.Message(
            data=safe_data,
            token=fcm_token,
        )

        response = messaging.send(message)
        logging.info(f"Successfully sent notification to user {user_id}. Response: {response}")

    except Exception as e:
        logging.error(f"Failed to send notification to user {user_id}. Error: {e}", exc_info=True)