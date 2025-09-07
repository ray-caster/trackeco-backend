# FILE: trackeco-backend/api/auth.py

import logging
import uuid
import datetime
import random
from functools import wraps
import hashlib
from flask import Blueprint, request, jsonify
from google.cloud import firestore
from google.oauth2 import id_token
from google.auth.transport import requests as google_auth_requests
import jwt
from werkzeug.security import generate_password_hash, check_password_hash

from .email_utils import send_verification_email
from .pydantic_models import AuthRequest, VerifyRequest, GoogleAuthRequest, ResendCodeRequest
from .config import db, JWT_SECRET_KEY, ANDROID_CLIENT_ID
from extensions import limiter # <-- IMPORT the limiter instance
from tasks import sync_user_to_algolia_task
auth_bp = Blueprint('auth_bp', __name__)

# --- Helpers ---
def generate_unique_referral_code():
    """Generates a referral code and guarantees it's unique in the database."""
    while True:
        code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=6))
        code_ref = db.collection('referral_codes').document(code)
        if not code_ref.get().exists:
            return code
        
@firestore.transactional
def create_user_and_mapping_transaction(transaction, user_id, email, user_data, attempt_ref):
    user_ref = db.collection('users').document(user_id)
    email_mapping_ref = db.collection('email_mappings').document(email)
    transaction.set(user_ref, user_data)
    transaction.set(email_mapping_ref, {'userId': user_id})
    transaction.delete(attempt_ref)

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '): return jsonify({"error_code": "TOKEN_MISSING"}), 401
        token = auth_header.split(' ')[1]
        try:
            data = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"]); kwargs['user_id'] = data['user_id']
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError): return jsonify({"error_code": "TOKEN_INVALID"}), 401
        return f(*args, **kwargs)
    return decorated

# --- Endpoints ---
@auth_bp.route('/signup', methods=['POST'])
@limiter.limit("5 per hour") # <-- ADDED RATE LIMIT
def signup():
    req_data = AuthRequest.model_validate(request.get_json())
    if db.collection('email_mappings').document(req_data.email).get().exists: return jsonify({"error_code": "USER_EXISTS"}), 409
    
    hashed_password = generate_password_hash(req_data.password)
    verification_code = str(random.randint(100000, 999999))
    
    db.collection('verification_attempts').document(req_data.email).set({
        'passwordHash': hashed_password, 'verificationCode': verification_code,
        'expiresAt': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)
    })
    
    if send_verification_email(req_data.email, verification_code): return jsonify({"message": "Verification code sent."}), 200
    return jsonify({"error_code": "EMAIL_FAILED"}), 500

@auth_bp.route('/verify', methods=['POST'])
def verify_email():
    req_data = VerifyRequest.model_validate(request.get_json())
    
    attempt_ref = db.collection('verification_attempts').document(req_data.email)
    attempt_doc = attempt_ref.get()
    if not attempt_doc.exists: return jsonify({"error_code": "NOT_FOUND"}), 404
    
    attempt_data = attempt_doc.to_dict()
    if datetime.datetime.now(datetime.timezone.utc) > attempt_data['expiresAt']:
        attempt_ref.delete(); return jsonify({"error_code": "EXPIRED_CODE"}), 400
    
    if attempt_data['verificationCode'] != req_data.code: return jsonify({"error_code": "INVALID_CODE"}), 400
    
    user_id = str(uuid.uuid4())
    referral_code = generate_unique_referral_code()
    
    user_data = {
        'userId': user_id, 
        'email': req_data.email, 
        'passwordHash': attempt_data['passwordHash'],
        'isVerified': True, 
        'createdAt': firestore.SERVER_TIMESTAMP,
        'onboardingStep': 0,
        'onboardingComplete': False, 
        'referralCode': referral_code,
        'totalPoints': 0,
        'currentStreak': 0,
        'maxStreak': 0,
        'avatarUrl': None,
        'displayName': None,
        'username': None,
        'completedChallengeIds': [],
        'challengeProgress': {},
        'activeTeamChallenges': [],
        'teamChallengeInvitations': [],
        'friends': [],
        'friendRequestsSent': [],
        'friendRequestsReceived': []
    }
    
    create_user_and_mapping_transaction(db.transaction(), user_id, req_data.email, user_data, attempt_ref)
    sync_user_to_algolia_task.delay(user_id)
    email_hash = hashlib.sha256(req_data.email.encode('utf-8')).hexdigest()
    db.collection('email_hashes').document(email_hash).set({'userId': user_id})
    db.collection('referral_codes').document(referral_code).set({'userId': user_id})
    
    token = jwt.encode({'user_id': user_id, 'email': req_data.email, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)}, JWT_SECRET_KEY)
    return jsonify({"token": token}), 200

@auth_bp.route('/resend-code', methods=['POST'])
@limiter.limit("1 per 2 minutes") # <-- ADDED RATE LIMIT
def resend_code():
    req_data = ResendCodeRequest.model_validate(request.get_json())
    attempt_ref = db.collection('verification_attempts').document(req_data.email)
    if not attempt_ref.get().exists: return jsonify({"error_code": "NOT_FOUND"}), 404
    
    new_code = str(random.randint(100000, 999999))
    attempt_ref.update({
        'verificationCode': new_code,
        'expiresAt': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)
    })
    
    if send_verification_email(req_data.email, new_code): return jsonify({"message": "A new verification code has been sent."}), 200
    return jsonify({"error_code": "EMAIL_FAILED"}), 500

@auth_bp.route('/login', methods=['POST'])
@limiter.limit("10 per minute") # <-- ADDED RATE LIMIT
def login():
    req_data = AuthRequest.model_validate(request.get_json())
    email_ref = db.collection('email_mappings').document(req_data.email)
    mapping = email_ref.get()
    if not mapping.exists: return jsonify({"error_code": "UNAUTHORIZED"}), 401
    
    user_id = mapping.to_dict().get('userId')
    user_doc = db.collection('users').document(user_id).get()
    if not user_doc.exists: return jsonify({"error_code": "UNAUTHORIZED"}), 401
    
    user_data = user_doc.to_dict()
    if not check_password_hash(user_data.get('passwordHash', ''), req_data.password):
        return jsonify({"error_code": "UNAUTHORIZED"}), 401
    if not user_data.get('isVerified'): return jsonify({"error_code": "NOT_VERIFIED"}), 403
    
    token = jwt.encode({
        'user_id': user_data['userId'], 'email': user_data['email'],
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    }, JWT_SECRET_KEY, algorithm="HS256")
    return jsonify({"token": token}), 200

@auth_bp.route('/auth/google', methods=['POST'])
@limiter.limit("10 per minute") # <-- ADDED RATE LIMIT
def auth_google():
    req_data = GoogleAuthRequest.model_validate(request.get_json())
    idinfo = id_token.verify_oauth2_token(req_data.id_token, google_auth_requests.Request(), ANDROID_CLIENT_ID)
    user_email, google_id, display_name = idinfo['email'].lower(), idinfo['sub'], idinfo.get('name', '')
    
    user_ref = db.collection('users').document(google_id)
    if not user_ref.get().exists:
        referral_code = generate_unique_referral_code()
        
        new_user_data = {
            'userId': google_id, 
            'email': user_email, 
            'isVerified': True,
            'createdAt': firestore.SERVER_TIMESTAMP, 
            'displayName': display_name,
            'onboardingStep': 0, 
            'onboardingComplete': False, 
            'referralCode': referral_code,
            'totalPoints': 0,
            'currentStreak': 0,
            'maxStreak': 0,
            'avatarUrl': None,
            'username': None,
            'completedChallengeIds': [],
            'challengeProgress': {},
            'activeTeamChallenges': [],
            'teamChallengeInvitations': [],
            'friends': [],
            'friendRequestsSent': [],
            'friendRequestsReceived': []
        }
        
        user_ref.set(new_user_data)
        sync_user_to_algolia_task.delay(google_id)
        db.collection('email_mappings').document(user_email).set({'userId': google_id})
        db.collection('referral_codes').document(referral_code).set({'userId': google_id})
        email_hash = hashlib.sha256(user_email.encode('utf-8')).hexdigest()
        db.collection('email_hashes').document(email_hash).set({'userId': google_id})
        
    app_token = jwt.encode({'user_id': google_id, 'email': user_email, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)}, JWT_SECRET_KEY)
    return jsonify({"token": app_token}), 200

def health_check():
    """Performs a non-destructive health check for the auth module."""
    try:
        _ = list(db.collection('users').limit(1).stream())
        _ = list(db.collection('email_mappings').limit(1).stream())
        _ = list(db.collection('verification_attempts').limit(1).stream())
        return {"status": "OK", "details": "Firestore collections are accessible."}
    except Exception as e:
        return {"status": "ERROR", "details": f"Failed to query Firestore collections: {str(e)}"}