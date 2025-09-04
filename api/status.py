import os
import logging
import datetime
import pytz
from flask import Blueprint, request, render_template_string
from google import genai
from main import celery_app

# Import the specific health_check function from each of your API modules
from .config import redis_client, ACTIVE_GEMINI_KEYS
from .auth import health_check as auth_health_check
from .onboarding import health_check as onboarding_health_check
from .social import health_check as social_health_check
from .gamification import health_check as gamification_health_check
from .core import health_check as core_health_check

status_bp = Blueprint('status_bp', __name__)

# --- External Service Check Functions ---
def check_redis():
    if not redis_client: return {"status": "ERROR", "details": "Redis client is not configured."}
    try:
        redis_client.ping(); return {"status": "OK", "details": "Ping successful."}
    except Exception as e: return {"status": "ERROR", "details": f"Failed to ping Redis server: {str(e)}"}

def check_gemini_api():
    if not ACTIVE_GEMINI_KEYS: return {"status": "ERROR", "details": "No GEMINI_API_KEY variables found."}
    try:
        genai.configure(api_key=ACTIVE_GEMINI_KEYS[0])
        model = genai.GenerativeModel('gemini-2.5-flash')
        model.count_tokens("test")
        return {"status": "OK", "details": "Successfully authenticated with Gemini API."}
    except Exception as e: return {"status": "ERROR", "details": f"Gemini API key 1 may be invalid or quota exceeded: {str(e)}"}

def check_celery():
    try:
        inspector = celery_app.control.inspect()
        active_workers = inspector.ping()
        if not active_workers: return {"status": "ERROR", "details": "No active Celery workers responded. Service may be down."}
        return {"status": "OK", "details": f"Found {len(active_workers)} active worker(s): {', '.join(active_workers.keys())}"}
    except Exception as e: return {"status": "ERROR", "details": f"Could not connect to Celery broker (Redis). Error: {str(e)}"}

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
        .status-table th { font-weight: 600; color: #555; width: 25%; }
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
        
        <h2>Internal Module Health</h2>
        <table class="status-table">
            <thead><tr><th>Module</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
                {% for name, result in internal_checks.items() %}
                <tr>
                    <td><strong>{{ name }}</strong></td>
                    <td><span class="status-badge {{ 'ok' if result.status == 'OK' else 'error' }}">{{ result.status }}</span></td>
                    <td class="details">{{ result.details }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <h2>External Service Connectivity</h2>
        <table class="status-table">
            <thead><tr><th>Service</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
                {% for name, result in external_checks.items() %}
                <tr>
                    <td><strong>{{ name }}</strong></td>
                    <td><span class="status-badge {{ 'ok' if result.status == 'OK' else 'error' }}">{{ result.status }}</span></td>
                    <td class="details">{{ result.details }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    <div class="footer">TrackEco Status Page</div>
</body>
</html>
"""

# --- Main Endpoint ---
@status_bp.route('/status')
def system_status():
    STATUS_SECRET_KEY = os.environ.get("STATUS_SECRET_KEY")
    secret = request.args.get('secret')
    if not STATUS_SECRET_KEY or secret != STATUS_SECRET_KEY:
        return "Unauthorized", 401

    internal_module_checks = {
        "Authentication Module (auth.py)": auth_health_check(),
        "Onboarding Module (onboarding.py)": onboarding_health_check(),
        "Social Module (social.py)": social_health_check(),
        "Gamification Module (gamification.py)": gamification_health_check(),
        "Core Module (core.py)": core_health_check(),
    }

    external_service_checks = {
        "Redis Cache": check_redis(),
        "Celery Workers": check_celery(),
        "Gemini AI API": check_gemini_api(),
    }

    timestamp = datetime.datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')
    
    return render_template_string(
        STATUS_PAGE_TEMPLATE, 
        internal_checks=internal_module_checks,
        external_checks=external_service_checks,
        timestamp=timestamp
    )