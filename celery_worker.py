import os
from celery import Celery
from dotenv import load_dotenv
import firebase_admin

# --- CELERY WORKER INITIALIZATION ---

# 1. Load environment variables. This MUST happen before anything else.
load_dotenv()

# 2. Initialize Firebase Admin SDK. This will be inherited by the forked worker processes.
# It's safe to do here because it happens in the main Celery process before forking.
if not firebase_admin._apps:
    # This automatically uses GOOGLE_APPLICATION_CREDENTIALS
    firebase_admin.initialize_app()

# 3. Create the Celery app instance.
celery_app = Celery('tasks',
                    broker=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
                    include=['tasks']) # This tells Celery to look for tasks in tasks.py

# Optional: Add any further Celery configuration here if needed
celery_app.conf.update(
    task_track_started=True
)