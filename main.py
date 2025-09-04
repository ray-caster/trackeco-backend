import os
import logging
from flask import Flask, jsonify
from dotenv import load_dotenv
import firebase_admin
from pydantic import ValidationError
from celery import Celery

from logging_config import setup_logging
from celery_worker import celery_app # Import the shared celery_app instance

# --- SETUP & CONFIG ---
setup_logging()
load_dotenv()

try:
    SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not SERVICE_ACCOUNT_FILE:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"Service account key not found at path: {SERVICE_ACCOUNT_FILE}")

    # Create credential object
    creds = firebase_admin.credentials.Certificate(SERVICE_ACCOUNT_FILE)
    # Initialize Firebase with these specific credentials
    firebase_admin.initialize_app(creds)
    logging.info("Firebase Admin SDK initialized successfully with explicit credentials.")

except Exception as e:
    logging.critical(f"FATAL: Failed to initialize Firebase Admin SDK. Error: {e}", exc_info=True)
    # If this fails, the app cannot run.
    exit()

# --- Import and Register Blueprints ---
from api.auth import auth_bp
from api.onboarding import onboarding_bp
from api.social import social_bp
from api.gamification import gamification_bp
from api.core import core_bp
from api.admin import admin_bp # <-- IMPORT the new admin blueprint

# Unregister the old status_bp if it was there
# app.register_blueprint(status_bp, url_prefix='/') 

app.register_blueprint(auth_bp, url_prefix='/')
app.register_blueprint(onboarding_bp, url_prefix='/onboarding')
app.register_blueprint(social_bp, url_prefix='/friends')
app.register_blueprint(gamification_bp, url_prefix='/')
app.register_blueprint(core_bp, url_prefix='/')
app.register_blueprint(admin_bp, url_prefix='/') # <-- REGISTER the new admin blueprint

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
def run_initial_setup():
    """
    Checks if active challenges exist and generates the default set if not.
    This should be run once during deployment or manually when needed.
    """
    logging.info("Running initial setup. Checking for active challenges...")
    
    # We must explicitly initialize the client in a standalone script
    db = firestore.Client() 
    
    query = db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True)).limit(1)
    
    # Check if the query returns any results
    if list(query.stream()):
        logging.info("Active challenges already exist. No action needed.")
        print("Active challenges already exist. Setup complete.")
    else:
        logging.warning("No active challenges found. Generating full default set...")
        print("No active challenges found. Generating...")
        try:
            # Generate 3 daily challenges (2 simple, 1 progress)
            generate_challenge_set('daily', simple_count=2, progress_count=1)
            # Generate 2 weekly challenges (1 simple, 1 progress)
            generate_challenge_set('weekly', simple_count=1, progress_count=1)
            # Generate 2 monthly challenges (1 simple, 1 progress)
            generate_challenge_set('monthly', simple_count=1, progress_count=1)
            logging.info("Default challenges generated successfully.")
            print("Default challenges generated successfully.")
        except Exception as e:
            logging.error(f"Failed to generate default challenges during setup: {e}", exc_info=True)
            print(f"ERROR: Failed to generate default challenges. Check logs. Error: {e}")