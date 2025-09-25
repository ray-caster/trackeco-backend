"""
DEPRECATED: This module is now replaced by dependencies.py for proper dependency injection.
Please import from dependencies.py instead.
"""
import warnings
warnings.warn("api/config.py is deprecated. Use dependencies.py instead.", DeprecationWarning)

# Re-export from dependencies for backward compatibility
from dependencies import (
    db, storage_client, tasks_client, redis_client, algolia_client,
    GCP_PROJECT_ID, GCP_QUEUE_ID, GCP_QUEUE_LOCATION, WORKER_TARGET_URL,
    GCS_BUCKET_NAME, JWT_SECRET_KEYS, ANDROID_CLIENT_ID, ALGOLIA_APP_ID,
    ALGOLIA_ADMIN_API_KEY, ALGOLIA_INDEX_NAME, ALGOLIA_SEARCH_API_KEY,
    GEMINI_API_KEYS, ACTIVE_GEMINI_KEYS
)
# --- Initialize Google Cloud Clients ---
# These clients are initialized once here and then imported by other modules.
# They will automatically use the GOOGLE_APPLICATION_CREDENTIALS environment variable.
db = firestore.Client()
storage_client = storage.Client()
tasks_client = tasks_v2.CloudTasksClient()

# --- Load Environment variables ---
# These variables are loaded from the .env file (via load_dotenv() in main.py)
# and are made available here for easy importing into other modules.
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCP_QUEUE_ID = os.environ.get("GCP_QUEUE_ID")
GCP_QUEUE_LOCATION = os.environ.get("GCP_QUEUE_LOCATION")
WORKER_TARGET_URL = os.environ.get("WORKER_TARGET_URL")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
# JWT Secret Key Management with Rotation Support
# JWT Secret Key Management with Encryption Support
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
ANDROID_CLIENT_ID = os.environ.get("ANDROID_CLIENT_ID") # This is your WEB Client ID
ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_ADMIN_API_KEY = get_algolia_api_key()
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX_NAME")
ALGOLIA_SEARCH_API_KEY = os.environ.get("ALGOLIA_SEARCH_API_KEY")

# --- Redis Cache Setup ---
try:
    # `decode_responses=True` ensures that Redis returns strings, not bytes.
    redis_client = redis.from_url(
        os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), 
        decode_responses=True
    )
    # Check the connection on startup.
    redis_client.ping()
except redis.exceptions.ConnectionError:
    # If Redis is not available, set the client to None.
    # Other parts of the app can check for this and skip caching.
    redis_client = None

# --- Algolia ---
if ALGOLIA_APP_ID and ALGOLIA_ADMIN_API_KEY:
    algolia_client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY)
else:
    algolia_client = None

# --- Gemini API Keys ---
# Load up to 4 keys for redundancy with encryption support.
GEMINI_API_KEYS = [ get_gemini_api_key(i+1) for i in range(4) ]
ACTIVE_GEMINI_KEYS = [key for key in GEMINI_API_KEYS if key]