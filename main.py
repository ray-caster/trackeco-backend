# FILE: trackeco-backend/main.py

import os
import logging
from flask import Flask, jsonify
from dotenv import load_dotenv
import firebase_admin
from pydantic import ValidationError
from google.cloud import firestore
from logging_config import setup_logging
from celery_worker import celery_app
from extensions import limiter  # <-- IMPORT the new limiter instance

# --- SETUP & CONFIG ---
# Load environment variables for the Flask app process.
load_dotenv()
setup_logging()

# Initialize Firebase for the Gunicorn/Flask processes.
# The `if not firebase_admin._apps:` check prevents re-initialization.
if not firebase_admin._apps:
    firebase_admin.initialize_app()

app = Flask(__name__)

# --- Initialize Extensions ---
# Set the Redis URL for the rate limiter from your environment variables.
limiter.storage_uri = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
limiter.init_app(app) # Initialize the limiter with the Flask app.

celery_app.conf.update(app.config)

# --- Import and Register Blueprints ---
# This part is now safe because the blueprints only import clients, they don't initialize them.
from api.auth import auth_bp
from api.onboarding import onboarding_bp
from api.social import social_bp
from api.gamification import gamification_bp
from api.core import core_bp
from api.admin import admin_bp
from api.users import users_bp

app.register_blueprint(auth_bp, url_prefix='/', strict_slashes=False    )
app.register_blueprint(onboarding_bp, url_prefix='/onboarding', strict_slashes=False)
app.register_blueprint(social_bp, url_prefix='/social', strict_slashes=False)
app.register_blueprint(gamification_bp, url_prefix='/', strict_slashes=False)
app.register_blueprint(core_bp, url_prefix='/', strict_slashes=False)
app.register_blueprint(admin_bp, url_prefix='/', strict_slashes=False)
app.register_blueprint(users_bp, url_prefix='/users')

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