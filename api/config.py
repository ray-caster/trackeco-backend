import os
from google.cloud import firestore, storage, tasks_v2
import redis

# --- Initialize Google Cloud Clients ---
db = firestore.Client()
storage_client = storage.Client()
tasks_client = tasks_v2.CloudTasksClient()

# --- Load Environment variables ---
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCP_QUEUE_ID = os.environ.get("GCP_QUEUE_ID")
GCP_QUEUE_LOCATION = os.environ.get("GCP_QUEUE_LOCATION")
WORKER_TARGET_URL = os.environ.get("WORKER_TARGET_URL")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
ANDROID_CLIENT_ID = os.environ.get("ANDROID_CLIENT_ID") # This is your WEB Client ID

# --- Redis Cache Setup ---
try:
    redis_client = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    redis_client.ping()
except redis.exceptions.ConnectionError:
    redis_client = None