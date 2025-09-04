import logging
import datetime
from flask import Blueprint, request, render_template_string
from google import genai
from celery.task.control import inspect

from .config import db, storage_client, redis_client, GCS_BUCKET_NAME, ACTIVE_GEMINI_KEYS
from main import celery_app # Import the celery_app instance from your main file

status_bp = Blueprint('status_bp', __name__)

# --- Helper Check Functions ---

def check_firestore():
    """Checks if we can connect to Firestore and read a document."""
    try:
        # Try to read a well-known document, like the first user or a special status doc.
        # Here, we just list collections as a basic check.
        collections = list(db.collections(limit=1))
        return {"status": "OK", "details": f"Successfully connected and listed {len(collections)} collection(s)."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to connect to Firestore: {str(e)}"}

def check_gcs():
    """Checks if we can connect to Google Cloud Storage and list the bucket."""
    try:
        bucket = storage_client.get_bucket(GCS_BUCKET_NAME)
        return {"status": "OK", "details": f"Successfully connected to bucket '{bucket.name}'."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to connect to GCS bucket '{GCS_BUCKET_NAME}': {str(e)}"}

def check_redis():
    """Checks if the Redis server is responsive."""
    if not redis_client:
        return {"status": "ERROR", "details": "Redis client is not configured."}
    try:
        redis_client.ping()
        return {"status": "OK", "details": "Ping successful."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to ping Redis server: {str(e)}"}

def check_gemini_api():
    """Checks if at least one Gemini API key is valid."""
    if not ACTIVE_GEMINI_KEYS:
        return {"status": "ERROR", "details": "No GEMINI_API_KEY environment variables found."}
    try:
        # Use a simple, low-cost model for the health check
        genai.configure(api_key=ACTIVE_GEMINI_KEYS[0])
        model = genai.get_model('gemini-1.5-flash-latest')
        return {"status": "OK", "details": f"Successfully connected to Gemini API with key 1. Model: {model.name}"}
    except Exception as e:
        return {"status": "ERROR", "details": f"Gemini API key 1 may be invalid or quota exceeded: {str(e)}"}

def check_celery():
    """Checks if there are active Celery workers."""
    try:
        inspector = inspect(app=celery_app)
        active_workers = inspector.ping()
        if not active_workers:
            return {"status": "ERROR", "details": "No active Celery workers found. The worker service may be down."}
        
        worker_count = len(active_workers)
        worker_names = ", ".join(active_workers.keys())
        return {"status": "OK", "details": f"Found {worker_count} active worker(s): {worker_names}"}
    except Exception as e:
        return {"status": "ERROR", "details": f"Could not connect to Celery broker (Redis). Ensure Redis is running and accessible. Error: {str(e)}"}

# --- HTML Template ---
STATUS_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TrackEco Backend Status</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background-color: #f4f7f6; }
        .container { max-width: 800px; margin: 2rem auto; padding: 1rem; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h2 { color: #333; border-bottom: 1px solid #eee; padding-bottom: 0.5rem; }
        .status-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        .status-table th, .status-table td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #eee; }
        .status-table th { font-weight: 600; color: #555; }
        .status-badge { padding: 0.25rem 0.6rem; border-radius: 1rem; font-weight: 700; font-size: 0.9em; color: white; }
        .ok { background-color: #28a745; }
        .error { background-color: #dc3545; }
        .details { font-size: 0.9em; color: #666; word-break: break-word; }
        .footer { text-align: center; margin-top: 2rem; font-size: 0.8em; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <h1>TrackEco Backend Status</h1>
        <p>Last checked: {{ timestamp }}</p>
        
        <h2>Core Services</h2>
        <table class="status-table">
            <thead>
                <tr>
                    <th>Service</th>
                    <th>Status</th>
                    <th>Details</th>
                </tr>
            </thead>
            <tbody>
                {% for name, result in checks.items() %}
                <tr>
                    <td><strong>{{ name }}</strong></td>
                    <td><span class="status-badge {{ 'ok' if result.status == 'OK' else 'error' }}">{{ result.status }}</span></td>
                    <td class="details">{{ result.details }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    <div class="footer">
        TrackEco Status Page
    </div>
</body>
</html>
"""

# --- Main Endpoint ---
@status_bp.route('/status')
def system_status():
    # It's good practice to use a separate, dedicated secret key for a status page
    STATUS_SECRET_KEY = os.environ.get("STATUS_SECRET_KEY", os.environ.get("CRON_SECRET_KEY"))
    
    # Secure the endpoint with a secret key passed as a query parameter
    secret = request.args.get('secret')
    if not STATUS_SECRET_KEY or secret != STATUS_SECRET_KEY:
        return "Unauthorized", 401

    all_checks = {
        "Firestore Database": check_firestore(),
        "Google Cloud Storage": check_gcs(),
        "Redis Cache": check_redis(),
        "Gemini AI API": check_gemini_api(),
        "Celery Workers": check_celery(),
    }

    timestamp = datetime.datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')
    
    return render_template_string(STATUS_PAGE_TEMPLATE, checks=all_checks, timestamp=timestamp)