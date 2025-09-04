import os
from celery import Celery
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# This is the central Celery application instance.
# Other modules will import this `celery_app` object.
celery_app = Celery('tasks',
                    broker=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
                    # This tells Celery where to find the tasks file.
                    include=['tasks'])

# Optional: Add any further Celery configuration here if needed
celery_app.conf.update(
    task_track_started=True
)