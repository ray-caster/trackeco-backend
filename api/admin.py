import os
import logging
import datetime
import pytz
from flask import Blueprint, request, render_template_string
from google import genai
from main import celery_app

# Import all necessary clients and health checks from other modules
from .config import db, storage_client, redis_client, GCS_BUCKET_NAME, ACTIVE_GEMINI_KEYS
from .auth import health_check as auth_health_check
from .onboarding import health_check as onboarding_health_check
from .social import health_check as social_health_check
from .gamification import health_check as gamification_health_check
from .core import health_check as core_health_check

admin_bp = Blueprint('admin_bp', __name__)

# --- Health Check Functions (External Services) ---
def check_redis():
    if not redis_client: return {"status": "ERROR", "details": "Redis client is not configured."}
    try:
        redis_client.ping(); return {"status": "OK", "details": "Ping successful."}
    except Exception as e: return {"status": "ERROR", "details": f"Failed to ping Redis server: {str(e)}"}

def check_gemini_api():
    if not ACTIVE_GEMINI_KEYS: return {"status": "ERROR", "details": "No GEMINI_API_KEY variables found."}
    try:
        genai.configure(api_key=ACTIVE_GEMINI_KEYS[0])
        model = genai.GenerativeModel('gemini-1.5-flash')
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

# --- Data Fetching Functions for Dashboard ---
def get_current_challenges_data():
    """Fetches all active challenges from Firestore."""
    try:
        query = db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True)).order_by('expiresAt')
        return [doc.to_dict() for doc in query.stream()]
    except Exception as e:
        logging.error(f"Admin dashboard failed to fetch challenges: {e}")
        return None # Return None to indicate an error

def get_leaderboard_data():
    """Fetches top 10 users from Firestore for the leaderboard."""
    try:
        query = db.collection('users').order_by('totalPoints', direction=firestore.Query.DESCENDING).limit(10)
        leaderboard = []
        for i, user_doc in enumerate(query.stream()):
            user = user_doc.to_dict()
            leaderboard.append({"rank": i + 1, "displayName": user.get("displayName", "N/A"), "totalPoints": user.get("totalPoints", 0)})
        return leaderboard
    except Exception as e:
        logging.error(f"Admin dashboard failed to fetch leaderboard: {e}")
        return None # Return None to indicate an error

# --- HTML Template for the Admin Dashboard ---
ADMIN_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TrackEco Admin Dashboard</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background-color: #f4f7f6; color: #333; }
        .container { max-width: 960px; margin: 2rem auto; padding: 1rem; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
        .card { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
        h1, h2 { color: #1a202c; border-bottom: 1px solid #e2e8f0; padding-bottom: 0.5rem; margin-top: 0; }
        .status-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        .status-table th, .status-table td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #e2e8f0; }
        .status-table th { font-weight: 600; color: #4a5568; width: 30%; }
        .status-badge { padding: 0.25rem 0.6rem; border-radius: 1rem; font-weight: 700; font-size: 0.9em; color: white; display: inline-block; }
        .ok { background-color: #38a169; }
        .error { background-color: #e53e3e; }
        .details { font-size: 0.9em; color: #718096; word-break: break-word; }
        .footer { text-align: center; margin-top: 2rem; font-size: 0.8em; color: #a0aec0; }
        .list { list-style: none; padding: 0; }
        .list-item { margin-bottom: 0.5rem; padding-bottom: 0.5rem; border-bottom: 1px solid #eee; }
        .list-item:last-child { border-bottom: none; }
        @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="container">
        <h1>TrackEco Admin Dashboard</h1>
        <p>Last checked: {{ timestamp }}</p>

        <div class="grid">
            <div class="card">
                <h2>Internal Module Health</h2>
                <table class="status-table">
                    <tbody>
                        {% for name, result in internal_checks.items() %}
                        <tr>
                            <td><strong>{{ name }}</strong></td>
                            <td><span class="status-badge {{ 'ok' if result.status == 'OK' else 'error' }}">{{ result.status }}</span></td>
                        </tr>
                        {% if result.details %}<tr><td colspan="2" class="details">{{ result.details }}</td></tr>{% endif %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div class="card">
                <h2>External Service Connectivity</h2>
                <table class="status-table">
                     <tbody>
                        {% for name, result in external_checks.items() %}
                        <tr>
                            <td><strong>{{ name }}</strong></td>
                            <td><span class="status-badge {{ 'ok' if result.status == 'OK' else 'error' }}">{{ result.status }}</span></td>
                        </tr>
                        {% if result.details %}<tr><td colspan="2" class="details">{{ result.details }}</td></tr>{% endif %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div class="card">
                <h2>Live Leaderboard (Top 10)</h2>
                {% if leaderboard %}
                <ul class="list">
                {% for user in leaderboard %}
                    <li class="list-item"><strong>#{{ user.rank }} {{ user.displayName }}</strong> - {{ "%.1f"|format(user.totalPoints) }} pts</li>
                {% endfor %}
                </ul>
                {% else %}
                <p class="details">Leaderboard is empty or could not be loaded.</p>
                {% endif %}
            </div>
            <div class="card">
                <h2>Active Challenges</h2>
                {% if challenges %}
                <ul class="list">
                {% for challenge in challenges %}
                    <li class="list-item"><strong>{{ challenge.type|capitalize }}:</strong> {{ challenge.description }} (+{{ challenge.bonusPoints }} pts)</li>
                {% endfor %}
                </ul>
                 {% else %}
                <p class="details">No active challenges or could not be loaded.</p>
                {% endif %}
            </div>
        </div>
    </div>
    <div class="footer">TrackEco Status Page</div>
</body>
</html>
"""

# --- Main Admin Endpoint ---
@admin_bp.route('/admin')
def system_admin_dashboard():
    ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY")
    secret = request.args.get('secret')
    if not ADMIN_SECRET_KEY or secret != ADMIN_SECRET_KEY:
        return "Unauthorized", 401

    internal_module_checks = {
        "Authentication": auth_health_check(),
        "Onboarding": onboarding_health_check(),
        "Social": social_health_check(),
        "Gamification": gamification_health_check(),
        "Core Upload": core_health_check(),
    }

    external_service_checks = {
        "Redis Cache": check_redis(),
        "Celery Workers": check_celery(),
        "Gemini AI API": check_gemini_api(),
    }

    # Fetch live data
    live_challenges = get_current_challenges_data()
    live_leaderboard = get_leaderboard_data()

    timestamp = datetime.datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')
    
    return render_template_string(
        ADMIN_PAGE_TEMPLATE, 
        internal_checks=internal_module_checks,
        external_checks=external_service_checks,
        challenges=live_challenges,
        leaderboard=live_leaderboard,
        timestamp=timestamp
    )