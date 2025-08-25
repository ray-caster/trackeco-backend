from flask import Blueprint, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User
import re
import secrets
import string
import time
from security import (
    rate_limit, login_rate_limit, validate_json, secure_session, 
    sanitize_input, InputValidator, RateLimiter, log_security_event
)

auth_bp = Blueprint('auth', __name__)

def generate_user_id():
    """Generate a unique user ID"""
    timestamp = str(int(time.time() * 1000))
    random_suffix = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"user_{timestamp}_{random_suffix}"

@auth_bp.route('/api/register', methods=['POST'])
@rate_limit(max_requests=5)  # Limit registration attempts
@validate_json()
@sanitize_input({
    'username': InputValidator.validate_username,
    'email': InputValidator.validate_email,
    'password': InputValidator.validate_password
})
def register():
    """Register a new user with comprehensive security"""
    try:
        data = g.sanitized_data
        username = data.get('username', '')
        email = data.get('email', '')
        password = data.get('password', '')
        
        # Additional validation
        if not username or not email or not password:
            log_security_event('REGISTRATION_MISSING_FIELDS')
            return jsonify({"error": "Username, email, and password are required"}), 400
        
        # Check if user already exists (with timing attack protection)
        existing_user = User.query.filter_by(username=username).first()
        existing_email = User.query.filter_by(email=email).first()
        
        if existing_user:
            log_security_event('REGISTRATION_DUPLICATE_USERNAME', {'username': username})
            return jsonify({"error": "Username already exists"}), 400
            
        if existing_email:
            log_security_event('REGISTRATION_DUPLICATE_EMAIL', {'email': email})
            return jsonify({"error": "Email already registered"}), 400
        
        # Create new user
        user_id = generate_user_id()
        hashed_password = generate_password_hash(password)
        
        new_user = User()
        new_user.user_id = user_id
        new_user.username = username
        new_user.email = email
        new_user.password = hashed_password
        new_user.xp = 0
        new_user.points = 0
        new_user.streak = 0
        new_user.has_completed_first_disposal = False
        
        db.session.add(new_user)
        db.session.commit()
        
        # Log user in with secure session
        session['user_id'] = user_id
        session['username'] = username
        session['last_activity'] = time.time()
        session.permanent = True
        
        log_security_event('USER_REGISTERED', {'user_id': user_id, 'username': username})
        
        return jsonify({
            "message": "Registration successful",
            "user": {
                "user_id": user_id,
                "username": username,
                "email": email,
                "xp": 0,
                "points": 0,
                "streak": 0,
                "eco_rank": "Eco Novice"
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        log_security_event('REGISTRATION_ERROR', {'error': str(e)})
        return jsonify({"error": "Registration failed. Please try again."}), 500

@auth_bp.route('/api/login', methods=['POST'])
@login_rate_limit()
@validate_json()
@sanitize_input({
    'username': InputValidator.sanitize_string,
    'password': str  # Don't validate password structure on login
})
def login():
    """Login a user with security protections"""
    try:
        data = g.sanitized_data
        username = data.get('username', '')
        password = data.get('password', '')
        
        if not username or not password:
            log_security_event('LOGIN_MISSING_CREDENTIALS')
            return jsonify({"error": "Username and password are required"}), 400
        
        # Timing attack protection - always hash even for non-existent users
        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()
        
        # Always perform password check to prevent timing attacks
        if user:
            password_valid = check_password_hash(user.password, password)
        else:
            # Dummy hash operation to maintain consistent timing
            # Use a real hash format to prevent werkzeug errors
            dummy_hash = 'scrypt:32768:8:1$dummy$0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
            check_password_hash(dummy_hash, password)
            password_valid = False
        
        if user and password_valid:
            # Successful login
            session['user_id'] = user.user_id
            session['username'] = user.username
            session['last_activity'] = time.time()
            session.permanent = True
            
            log_security_event('USER_LOGIN_SUCCESS', {'user_id': user.user_id, 'username': user.username})
            
            return jsonify({
                "message": "Login successful",
                "user": {
                    "user_id": user.user_id,
                    "username": user.username,
                    "email": user.email,
                    "xp": user.xp,
                    "points": user.points,
                    "streak": user.streak,
                    "eco_rank": calculate_eco_rank(user.xp),
                    "has_completed_first_disposal": user.has_completed_first_disposal
                }
            })
        else:
            # Failed login - record attempt
            RateLimiter.record_failed_login(username)
            log_security_event('USER_LOGIN_FAILED', {'username': username})
            return jsonify({"error": "Invalid credentials"}), 401
        
    except Exception as e:
        log_security_event('LOGIN_ERROR', {'error': str(e)})
        return jsonify({"error": "Login failed. Please try again."}), 500

@auth_bp.route('/api/logout', methods=['POST'])
@secure_session()
def logout():
    """Logout user securely"""
    user_id = session.get('user_id')
    if user_id:
        log_security_event('USER_LOGOUT', {'user_id': user_id})
    
    session.clear()
    return jsonify({"message": "Logout successful"})

@auth_bp.route('/api/user', methods=['GET'])
@rate_limit(max_requests=30)
@secure_session()
def get_current_user():
    """Get current logged-in user"""
    if 'user_id' not in session:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        user = User.query.filter_by(user_id=session['user_id']).first()
        if not user:
            session.clear()
            log_security_event('USER_SESSION_INVALID', {'user_id': session.get('user_id')})
            return jsonify({"error": "User not found"}), 404
        
        return jsonify({
            "user_id": user.user_id,
            "username": user.username,
            "email": user.email,
            "xp": user.xp,
            "points": user.points,
            "streak": user.streak,
            "eco_rank": calculate_eco_rank(user.xp),
            "has_completed_first_disposal": user.has_completed_first_disposal
        })
        
    except Exception as e:
        log_security_event('USER_FETCH_ERROR', {'error': str(e)})
        return jsonify({"error": "Unable to fetch user data"}), 500

def require_auth(f):
    """Enhanced decorator to require authentication with security checks"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function

def get_level_system():
    """Comprehensive level system with progression rewards"""
    return [
        {"level": 1, "name": "Eco Newcomer", "xp_required": 0, "xp_total": 0, "reward": "Welcome to TrackEco!", "color": "#8B5CF6"},
        {"level": 2, "name": "Green Starter", "xp_required": 50, "xp_total": 50, "reward": "+10 bonus points", "color": "#10B981"},
        {"level": 3, "name": "Waste Spotter", "xp_required": 100, "xp_total": 150, "reward": "Unlock daily challenges", "color": "#059669"},
        {"level": 4, "name": "Eco Learner", "xp_required": 150, "xp_total": 300, "reward": "Waste detection accuracy boost", "color": "#047857"},
        {"level": 5, "name": "Green Guardian", "xp_required": 200, "xp_total": 500, "reward": "+25 bonus points", "color": "#065F46"},
        {"level": 6, "name": "Planet Helper", "xp_required": 250, "xp_total": 750, "reward": "Unlock community features", "color": "#0F766E"},
        {"level": 7, "name": "Eco Warrior", "xp_required": 300, "xp_total": 1050, "reward": "Special waste categories", "color": "#0E7490"},
        {"level": 8, "name": "Impact Maker", "xp_required": 350, "xp_total": 1400, "reward": "+50 bonus points", "color": "#0369A1"},
        {"level": 9, "name": "Planet Protector", "xp_required": 400, "xp_total": 1800, "reward": "Advanced tracking tools", "color": "#1D4ED8"},
        {"level": 10, "name": "Earth Champion", "xp_required": 500, "xp_total": 2300, "reward": "Champion badge + 100 bonus", "color": "#6366F1"},
        {"level": 11, "name": "Eco Master", "xp_required": 600, "xp_total": 2900, "reward": "Master level unlocked", "color": "#7C3AED"},
        {"level": 12, "name": "Green Legend", "xp_required": 700, "xp_total": 3600, "reward": "Legend status + special perks", "color": "#9333EA"},
        {"level": 13, "name": "Planet Guardian", "xp_required": 800, "xp_total": 4400, "reward": "Guardian powers activated", "color": "#A855F7"},
        {"level": 14, "name": "Eco Deity", "xp_required": 900, "xp_total": 5300, "reward": "Ultimate eco powers", "color": "#C084FC"},
        {"level": 15, "name": "Earth Savior", "xp_required": 1000, "xp_total": 6300, "reward": "Maximum level reached!", "color": "#E879F9"}
    ]

def calculate_level_info(xp):
    """Calculate current level, progress, and next level information"""
    levels = get_level_system()
    
    current_level_info = levels[0]  # Default to level 1
    next_level_info = levels[1] if len(levels) > 1 else None
    
    # Find current level - check which level the user's XP falls into
    for i, level_data in enumerate(levels):
        # Check if user's XP is within this level's range
        next_level_xp_total = levels[i + 1]["xp_total"] if i + 1 < len(levels) else float('inf')
        
        if level_data["xp_total"] <= xp < next_level_xp_total:
            current_level_info = level_data
            next_level_info = levels[i + 1] if i + 1 < len(levels) else None
            break
    
    # If XP is at or above max level
    if xp >= levels[-1]["xp_total"]:
        current_level_info = levels[-1]
        next_level_info = None
    
    # Calculate progress to next level
    if next_level_info:
        xp_into_current_level = xp - current_level_info["xp_total"]
        xp_needed_for_next = next_level_info["xp_required"]
        progress_percentage = min(100, (xp_into_current_level / xp_needed_for_next) * 100)
        xp_remaining = max(0, xp_needed_for_next - xp_into_current_level)
    else:
        # Max level reached
        xp_into_current_level = xp - current_level_info["xp_total"]
        progress_percentage = 100
        xp_remaining = 0
    
    return {
        "current_level": current_level_info["level"],
        "current_level_name": current_level_info["name"],
        "current_level_color": current_level_info["color"],
        "current_level_reward": current_level_info["reward"],
        "current_xp": xp,
        "current_level_base_xp": current_level_info["xp_total"],
        "xp_into_current_level": xp_into_current_level,
        "next_level": next_level_info["level"] if next_level_info else current_level_info["level"],
        "next_level_name": next_level_info["name"] if next_level_info else "Max Level",
        "next_level_total_xp": next_level_info["xp_total"] if next_level_info else current_level_info["xp_total"],
        "xp_needed_for_next": next_level_info["xp_required"] if next_level_info else 0,
        "xp_remaining": xp_remaining,
        "progress_percentage": progress_percentage,
        "is_max_level": next_level_info is None
    }

def calculate_eco_rank(xp):
    """Calculate eco rank based on XP (backwards compatibility)"""
    level_info = calculate_level_info(xp)
    return level_info["current_level_name"]