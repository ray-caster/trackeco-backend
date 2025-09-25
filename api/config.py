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