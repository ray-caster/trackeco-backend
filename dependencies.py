"""
Dependency injection container for TrackEco backend.
This module provides centralized access to shared resources and services
to avoid circular imports and enable proper dependency injection.
"""

import logging
import os
from google.cloud import firestore, storage, tasks_v2
import redis
from algoliasearch.search.client import SearchClientSync
from firebase_init import initialize_firebase
from api.encryption_utils import get_gemini_api_key, get_jwt_secret_key, get_algolia_api_key, get_brevo_api_key

# Initialize Firebase once
initialize_firebase()

# --- Google Cloud Clients ---
db = firestore.Client()
storage_client = storage.Client()
tasks_client = tasks_v2.CloudTasksClient()

# --- Environment variables ---
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCP_QUEUE_ID = os.environ.get("GCP_QUEUE_ID")
GCP_QUEUE_LOCATION = os.environ.get("GCP_QUEUE_LOCATION")
WORKER_TARGET_URL = os.environ.get("WORKER_TARGET_URL")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
ANDROID_CLIENT_ID = os.environ.get("ANDROID_CLIENT_ID")
ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX_NAME")
ALGOLIA_SEARCH_API_KEY = os.environ.get("ALGOLIA_SEARCH_API_KEY")

# --- JWT Secret Key Management with Encryption Support ---
JWT_SECRET_KEYS = [
    get_jwt_secret_key('CURRENT'),
    get_jwt_secret_key('PREVIOUS'),
    get_jwt_secret_key('NEXT')
]
# Filter out None values
JWT_SECRET_KEYS = [key for key in JWT_SECRET_KEYS if key]
# Default to single key if rotation not configured
if not JWT_SECRET_KEYS and os.environ.get("JWT_SECRET_KEY"):
    JWT_SECRET_KEYS = [os.environ.get("JWT_SECRET_KEY")]

# --- Redis Connection Pool with Retry Logic ---
import threading
from redis.retry import Retry
from redis.backoff import ExponentialBackoff

# Thread-local storage for Redis connections
_redis_local = threading.local()

def get_redis_connection():
    """
    Get a thread-safe Redis connection from the pool with retry logic.
    Returns a new connection for each thread to ensure thread safety.
    """
    if not hasattr(_redis_local, 'connection'):
        try:
            # Create connection pool with retry configuration
            retry = Retry(ExponentialBackoff(), retries=3)
            connection_pool = redis.ConnectionPool.from_url(
                os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
                decode_responses=True,
                retry=retry,
                max_connections=20,
                health_check_interval=30,
                socket_connect_timeout=5,
                socket_timeout=10
            )
            
            # Create client with connection pool
            _redis_local.connection = redis.Redis(connection_pool=connection_pool)
            _redis_local.connection.ping()  # Test connection
            logging.info("Redis connection pool initialized successfully")
            
        except redis.exceptions.ConnectionError as e:
            logging.error(f"Failed to connect to Redis: {e}")
            _redis_local.connection = None
        except Exception as e:
            logging.error(f"Unexpected error initializing Redis: {e}")
            _redis_local.connection = None
    
    return _redis_local.connection

# Global redis_client for backward compatibility (thread-safe via get_redis_connection)
def redis_client():
    """Thread-safe access to Redis client."""
    return get_redis_connection()

# Initialize connection pool on module load
get_redis_connection()

# --- Algolia Client ---
ALGOLIA_ADMIN_API_KEY = get_algolia_api_key()
if ALGOLIA_APP_ID and ALGOLIA_ADMIN_API_KEY:
    algolia_client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY)
else:
    algolia_client = None

# --- Gemini API Keys ---
GEMINI_API_KEYS = [get_gemini_api_key(i+1) for i in range(4)]
ACTIVE_GEMINI_KEYS = [key for key in GEMINI_API_KEYS if key]

# --- Brevo API Key ---
BREVO_API_KEY = get_brevo_api_key()
VERIFIED_SENDER_EMAIL = os.environ.get("VERIFIED_SENDER_EMAIL")