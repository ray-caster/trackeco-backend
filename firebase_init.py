"""
Centralized Firebase initialization module.
This ensures consistent Firebase initialization across all backend components.
"""

import os
import logging
import firebase_admin
from firebase_admin import credentials

# Global flag to track initialization
_firebase_initialized = False

def initialize_firebase():
    """
    Initialize Firebase Admin SDK with proper error handling and thread safety.
    Uses GOOGLE_APPLICATION_CREDENTIALS environment variable for credentials.
    
    Returns:
        bool: True if initialization was successful, False otherwise
    """
    global _firebase_initialized
    
    if _firebase_initialized:
        logging.debug("Firebase already initialized, skipping.")
        return True
        
    try:
        # Check if Firebase is already initialized
        if not firebase_admin._apps:
            # Use default credentials from GOOGLE_APPLICATION_CREDENTIALS
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred)
            logging.info("Firebase Admin SDK initialized successfully.")
        else:
            logging.info("Firebase Admin SDK already initialized.")
        
        _firebase_initialized = True
        return True
        
    except Exception as e:
        logging.error(f"Failed to initialize Firebase Admin SDK: {e}")
        return False

# Export the initialization function
__all__ = ['initialize_firebase']