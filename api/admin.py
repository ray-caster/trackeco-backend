import os
import logging
import datetime
import pytz
from flask import Blueprint, request, render_template_string
from google import genai
from google.cloud import firestore
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
def get_all_challenges_data():
    """
    Fetches ALL challenges from Firestore and categorizes them.
    This query requires a composite index on (type ASC, createdAt DESC).
    """
    try:
        query = db.collection('challenges').order_by('type').order_by('createdAt', direction=firestore.Query.DESCENDING)
        
        all_challenges = [doc.to_dict() for doc in query.stream()]
        
        categorized = {
            'daily': [c for c in all_challenges if c.get('type') == 'daily'],
            'weekly': [c for c in all_challenges if c.get('type') == 'weekly'],
            'monthly': [c for c in all_challenges if c.get('type') == 'monthly'],
        }
        return categorized
    except Exception as e:
        logging.error(f"Admin dashboard failed to fetch challenges: {e}")
        return None
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

# --- NEW HTML Template for the Admin Dashboard ---
ADMIN_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TrackEco Admin Dashboard</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background-color: #f4f7f6; color: #333; }
        .container { max-width: 1200px; margin: 2rem auto; padding: 1rem; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
        .full-width { grid-column: 1 / -1; }
        .card { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin-bottom: 2rem; }
        h1, h2 { color: #1a202c; border-bottom: 1px solid #e2e8f0; padding-bottom: 0.5rem; margin-top: 0; }
        .status-table, .data-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        .status-table th, .status-table td, .data-table th, .data-table td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #e2e8f0; }
        .status-table th { font-weight: 600; color: #4a5568; width: 30%; }
        .data-table th { font-weight: 600; color: #4a5568; background-color: #f7fafc; }
        .data-table tr.inactive td { color: #a0aec0; background-color: #fdfdfd; }
        .status-badge { padding: 0.25rem 0.6rem; border-radius: 1rem; font-weight: 700; font-size: 0.9em; color: white; display: inline-block; }
        .ok { background-color: #38a169; }
        .error { background-color: #e53e3e; }
        .details { font-size: 0.9em; color: #718096; word-break: break-word; }
        .footer { text-align: center; margin-top: 2rem; font-size: 0.8em; color: #a0aec0; }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
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
            <div class="card full-width">
                <h2>Live Leaderboard (Top 10)</h2>
                {% if leaderboard %}
                <table class="data-table">
                    <thead><tr><th>Rank</th><th>Display Name</th><th>Total Points</th></tr></thead>
                    <tbody>
                    {% for user in leaderboard %}
                        <tr><td>#{{ user.rank }}</td><td>{{ user.displayName }}</td><td>{{ "%.1f"|format(user.totalPoints) }} pts</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
                {% else %}<p class="details">Leaderboard is empty or could not be loaded.</p>{% endif %}
            </div>
            <div class="card full-width">
                <h2>All Challenges</h2>
                {% if challenges %}
                    {% for type, challenge_list in challenges.items() %}
                        {% if challenge_list %}
                            <h3>{{ type|capitalize }} Challenges</h3>
                            <table class="data-table">
                                <thead><tr><th>Description</th><th>Status</th><th>Type</th><th>Points</th><th>Expires</th></tr></thead>
                                <tbody>
                                {% for challenge in challenge_list %}
                                    <tr class="{{ 'inactive' if not challenge.isActive }}">
                                        <td>{{ challenge.description }}</td>
                                        <td><span class="status-badge {{ 'ok' if challenge.isActive else 'error' }}">{{ 'Active' if challenge.isActive else 'Inactive' }}</span></td>
                                        <td>{{ 'Progress' if challenge.progressGoal else 'Simple' }}</td>
                                        <td>+{{ challenge.bonusPoints }}</td>
                                        <td>{{ challenge.expiresAt.strftime('%Y-%m-%d %H:%M') if challenge.expiresAt else 'N/A' }}</td>
                                    </tr>
                                {% endfor %}
                                </tbody>
                            </table>
                        {% endif %}
                    {% endfor %}
                {% else %}
                <p class="details">No challenges found or could not be loaded.</p>
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

    # ... (internal_module_checks and external_service_checks are the same) ...

    # Fetch live data using the new categorized function
    categorized_challenges = get_all_challenges_data()
    live_leaderboard = get_leaderboard_data()

    timestamp = datetime.datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')
    
    return render_template_string(
        ADMIN_PAGE_TEMPLATE, 
        internal_checks=internal_module_checks,
        external_checks=external_service_checks,
        challenges=categorized_challenges, # Pass the categorized dictionary
        leaderboard=live_leaderboard,
        timestamp=timestamp
    )