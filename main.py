import os
import logging
from flask import Flask, jsonify
from dotenv import load_dotenv
import firebase_admin
from pydantic import ValidationError
from google.cloud import firestore
from logging_config import setup_logging
from celery_worker import celery_app

# --- SETUP & CONFIG ---
# Load environment variables for the Flask app process.
load_dotenv()
setup_logging()

# Initialize Firebase for the Gunicorn/Flask processes.
# The `if not firebase_admin._apps:` check prevents re-initialization.
if not firebase_admin._apps:
    firebase_admin.initialize_app()

app = Flask(__name__)
celery_app.conf.update(app.config)

# --- Import and Register Blueprints ---
# This part is now safe because the blueprints only import clients, they don't initialize them.
from api.auth import auth_bp
from api.onboarding import onboarding_bp
from api.social import social_bp
from api.gamification import gamification_bp
from api.core import core_bp
from api.admin import admin_bp

app.register_blueprint(auth_bp, url_prefix='/')
app.register_blueprint(onboarding_bp, url_prefix='/onboarding')
app.register_blueprint(social_bp, url_prefix='/friends')
app.register_blueprint(gamification_bp, url_prefix='/')
app.register_blueprint(core_bp, url_prefix='/')
app.register_blueprint(admin_bp, url_prefix='/')

# --- Global Error Handlers ---
@app.errorhandler(ValidationError)
def handle_validation_error(e):
    return jsonify({"error_code": "BAD_REQUEST", "details": e.errors()}), 400

@app.errorhandler(404)
def resource_not_found(e):
    """Handles 404 Not Found errors for a clean API response."""
    return jsonify(error_code="NOT_FOUND", message="The requested resource was not found."), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handles unexpected 500 Internal Server Errors for a clean API response."""
    logging.critical(f"An unhandled exception occurred: {e}", exc_info=True)
    return jsonify(error_code="INTERNAL_SERVER_ERROR", message="An unexpected error occurred on the server."), 500

# --- SERVER STARTUP LOGIC ---
# This is also safe now.
with app.app_context():
    logging.info("Server starting up. Checking for active challenges...")
    from api.config import db
    from challenge_generator import generate_challenge_set
    
    query = db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True)).limit(1)
    if not list(query.stream()):
        logging.warning("No active challenges found on startup. Generating defaults.")
        generate_challenge_set('daily', simple_count=2, progress_count=1)
        generate_challenge_set('weekly', simple_count=1, progress_count=1)
        generate_challenge_set('monthly', simple_count=1, progress_count=1)
    else:
        logging.info("Active challenges found. Startup check complete.")