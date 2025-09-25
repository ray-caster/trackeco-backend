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
storage_client = storage.Client(timeout=10)  # 10 second timeout for GCS operations
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

# --- Redis Connection Pool with Retry Logic and Connection Pooling ---
import threading
import time
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.lock import Lock

# Global connection pool shared across all threads
_redis_connection_pool = None
_redis_pool_lock = threading.Lock()

def get_redis_connection():
    """
    Get a Redis connection from the shared connection pool.
    Uses a global connection pool with proper locking for thread safety.
    """
    global _redis_connection_pool
    
    if _redis_connection_pool is None:
        with _redis_pool_lock:
            if _redis_connection_pool is None:  # Double-check locking
                try:
                    # Create connection pool with retry configuration
                    retry = Retry(ExponentialBackoff(), retries=3)
                    _redis_connection_pool = redis.ConnectionPool.from_url(
                        os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
                        decode_responses=True,
                        retry=retry,
                        max_connections=50,  # Increased for better concurrency
                        health_check_interval=30,
                        socket_connect_timeout=5,
                        socket_timeout=10,
                        socket_keepalive=True
                    )
                    logging.info("Redis connection pool initialized successfully")
                    
                except Exception as e:
                    logging.error(f"Failed to initialize Redis connection pool: {e}")
                    return None
    
    try:
        # Create a new Redis client using the shared connection pool
        client = redis.Redis(connection_pool=_redis_connection_pool)
        client.ping()  # Test connection
        return client
    except redis.exceptions.ConnectionError as e:
        logging.error(f"Failed to get Redis connection from pool: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error getting Redis connection: {e}")
        return None

# Global redis_client for backward compatibility
def redis_client():
    """Thread-safe access to Redis client using connection pooling."""
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