import hashlib
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .pydantic_models import OnboardingProfile, OnboardingSurvey, OnboardingReferral
from .config import db
from .auth import token_required # Import the decorator from our auth blueprint
from tasks import sync_user_to_algolia_task

onboarding_bp = Blueprint('onboarding_bp', __name__)

# --- Helpers ---
@firestore.transactional
def set_username_transaction(transaction, username_ref, user_ref, username, display_name):
    """Checks for username existence and updates user doc atomically."""
    if username_ref.get(transaction=transaction).exists: raise ValueError("Username already exists.")
    transaction.set(username_ref, {'userId': user_ref.id})
    transaction.update(user_ref, {'username': username, 'displayName': display_name, 'onboardingStep': 1})

# --- Endpoints ---
@onboarding_bp.route('/profile', methods=['POST'])
@token_required
def onboarding_profile(user_id):
    req_data = OnboardingProfile.model_validate(request.get_json())
    username = req_data.username.lower().strip()
    user_ref, username_ref = db.collection('users').document(user_id), db.collection('usernames').document(username)
    try:
        set_username_transaction(db.transaction(), username_ref, user_ref, username, req_data.displayName)
        sync_user_to_algolia_task.delay(user_id)
        return jsonify({"message": "Profile step complete"}), 200
    except ValueError as e: return jsonify({"error_code": "USERNAME_TAKEN", "message": str(e)}), 409
    except Exception as e: return jsonify({"error_code": "SERVER_ERROR", "message": str(e)}), 500

@onboarding_bp.route('/survey', methods=['POST'])
@token_required
def onboarding_survey(user_id):
    req_data = OnboardingSurvey.model_validate(request.get_json())
    user_ref = db.collection('users').document(user_id)
    user_ref.collection('privateSurvey').document('responses').set(req_data.model_dump())
    user_ref.update({'onboardingStep': 2})
    return jsonify({"message": "Survey step complete"}), 200

@onboarding_bp.route('/referral', methods=['POST'])
@token_required
def onboarding_referral(user_id):
    req_data = OnboardingReferral.model_validate(request.get_json())
    user_ref = db.collection('users').document(user_id)
    if req_data.referralCode:
        code_ref = db.collection('referral_codes').document(req_data.referralCode)
        code_doc = code_ref.get(['userId'])
        if code_doc.exists:
            referrer_id = code_doc.to_dict().get('userId')
            if referrer_id != user_id: user_ref.update({'referredBy': referrer_id})
    
    # If the user granted contact permissions, save their hashed contacts
    if req_data.contactHashes:
        batch = db.batch()
        for chash in req_data.contactHashes:
            hash_ref = db.collection('contact_hashes').document(chash)
            # Store the user's ID against their hashed contact info
            batch.set(hash_ref, {'userId': user_id})
        batch.commit()
        
    user_ref.update({'onboardingStep': 3})
    return jsonify({"message": "Referral step complete"}), 200

@onboarding_bp.route('/finish', methods=['POST'])
@token_required
def onboarding_finish(user_id):
    import logging
    try:
        # Add debug logging to track the update operation
        logging.debug(f"Updating user {user_id} onboarding: step=4, complete=True")
        db.collection('users').document(user_id).update({'onboardingStep': 4, 'onboardingComplete': True})
        logging.debug(f"Successfully updated user {user_id} onboarding status")
        return jsonify({"message": "Onboarding complete"}), 200
    except Exception as e:
        logging.error(f"Failed to update onboarding status for user {user_id}: {str(e)}")
        return jsonify({"error": "Failed to complete onboarding"}), 500

def health_check():
    """
    Performs a non-destructive health check for the onboarding module.
    """
    try:
        _ = list(db.collection('usernames').limit(1).stream())
        _ = list(db.collection('referral_codes').limit(1).stream())
        return {"status": "OK", "details": "Firestore collections are accessible."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to query Firestore collections: {str(e)}"}