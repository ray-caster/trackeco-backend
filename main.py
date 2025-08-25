import os
import json
import base64
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_session import Session
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import func
from dotenv import load_dotenv
import random
import secrets

from security import SecurityHeaders, rate_limit, validate_json, sanitize_input, InputValidator, log_security_event

from models import db, User, Disposal, DailyDisposalLog, Challenge, Hotspot, UserChallengeProgress, UserSkill, WasteType, UserDiscovery, DailyMission
from gemini_service import validate_disposal_with_ai_video
from hotspot_generator import generate_hotspots, generate_offline_hotspots
from offline_manager import OfflineManager

load_dotenv()
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

# Create the app
app = Flask(__name__)
CORS(app)

# Enhanced Session configuration for security
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'trackeco:'
app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only
app.config['SESSION_COOKIE_HTTPONLY'] = True  # No JavaScript access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size
Session(app)

# Configure the database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "postgresql://postgres:password@localhost:5432/trackeco")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "pool_timeout": 20,
    "max_overflow": 0,
    "echo": False
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize the app with the extension
db.init_app(app)

# Initialize offline manager
offline_manager = OfflineManager()

# Register auth blueprint
from auth import auth_bp, require_auth
app.register_blueprint(auth_bp)

# Predefined challenge pool
CHALLENGE_POOL = [
    {"type": "COUNT", "category": "Plastic", "goal": 5, "reward": 50, "description": "Dispose of 5 Plastic items today"},
    {"type": "COUNT", "category": "Metal", "goal": 3, "reward": 50, "description": "Dispose of 3 Metal items today"},
    {"type": "COUNT", "category": "Glass", "goal": 2, "reward": 50, "description": "Dispose of 2 Glass items today"},
    {"type": "VARIETY", "category": "Metal", "goal": 3, "reward": 50, "description": "Dispose of 3 different Metal sub-types today"},
    {"type": "VARIETY", "category": "Plastic", "goal": 4, "reward": 50, "description": "Dispose of 4 different Plastic sub-types today"},
    {"type": "HOTSPOT", "goal": 1, "reward": 50, "description": "Complete 1 disposal inside a Litter Hotspot"},
    {"type": "COUNT", "category": "Paper/Cardboard", "goal": 3, "reward": 50, "description": "Dispose of 3 Paper/Cardboard items today"},
]

with app.app_context():
    # Import models to ensure tables are created
    import models
    db.create_all()
    
    # Initialize challenges if they don't exist
    if Challenge.query.count() == 0:
        for i, challenge_data in enumerate(CHALLENGE_POOL):
            challenge = Challenge()
            challenge.challenge_id = f"challenge_{i+1}"
            challenge.challenge_type = challenge_data["type"]
            challenge.category = challenge_data.get("category")
            challenge.goal = challenge_data["goal"]
            challenge.reward = challenge_data["reward"]
            challenge.description = challenge_data["description"]
            db.session.add(challenge)
        db.session.commit()
        logger.info("Initialized challenge pool")
    
    # Initialize waste types if they don't exist
    if WasteType.query.count() == 0:
        waste_types = [
            ("Plastic", "PET Bottles", "common", 10),
            ("Plastic", "HDPE Containers", "common", 10),
            ("Plastic", "PVC Pipes", "uncommon", 15),
            ("Plastic", "LDPE Bags", "common", 10),
            ("Plastic", "PP Food Containers", "common", 10),
            ("Plastic", "PS Foam Cups", "common", 10),
            ("Plastic", "Other Plastics", "common", 10),
            ("Paper/Cardboard", "Newspaper", "common", 10),
            ("Paper/Cardboard", "Cardboard Boxes", "common", 10),
            ("Paper/Cardboard", "Office Paper", "common", 10),
            ("Paper/Cardboard", "Magazine Paper", "uncommon", 15),
            ("Paper/Cardboard", "Pizza Boxes", "uncommon", 15),
            ("Glass", "Clear Glass Bottles", "common", 10),
            ("Glass", "Brown Glass Bottles", "uncommon", 15),
            ("Glass", "Green Glass Bottles", "uncommon", 15),
            ("Glass", "Window Glass", "rare", 20),
            ("Metal", "Aluminum Cans", "common", 10),
            ("Metal", "Steel Cans", "common", 10),
            ("Metal", "Copper Wire", "rare", 25),
            ("Metal", "Scrap Metal", "uncommon", 15)
        ]
        
        for category, sub_type, rarity, xp in waste_types:
            waste_type = WasteType(
                category=category,
                sub_type=sub_type,
                rarity=rarity,
                discovery_xp=xp
            )
            db.session.add(waste_type)
        
        db.session.commit()
        logger.info("Initialized waste types")

@app.route('/')
def index():
    """Serve the main application or redirect to auth"""
    if 'user_id' not in session:
        return render_template('auth.html')
    return render_template('index.html')

@app.route('/auth')
def auth_page():
    """Serve the authentication page"""
    return render_template('auth.html')

@app.route('/api/user/<user_id>')
@require_auth
def get_user(user_id):
    """Get user profile data with offline caching"""
    try:
        user = User.query.filter_by(user_id=user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Get user's daily challenge
        daily_challenge = get_or_assign_daily_challenge(user_id)
        
        # Get user's challenge progress
        progress = UserChallengeProgress.query.filter_by(
            user_id=user_id,
            challenge_id=daily_challenge.challenge_id
        ).first()
        
        current_progress = progress.current_progress if progress else 0
        
        # Calculate community rank (simplified ranking)
        total_users = User.query.count()
        users_with_higher_xp = User.query.filter(User.xp > user.xp).count()
        community_rank = users_with_higher_xp + 1
        
        # Calculate active users (users who have logged in within last 7 days)
        from datetime import datetime, timedelta
        week_ago = datetime.utcnow() - timedelta(days=7)
        active_users = User.query.filter(User.last_login >= week_ago).count() if hasattr(User, 'last_login') else total_users
        
        # Get comprehensive level information
        from auth import calculate_level_info
        level_info = calculate_level_info(user.xp)
        
        user_data = {
            "user_id": user.user_id,
            "xp": user.xp,
            "points": user.points,
            "streak": user.streak,
            "eco_rank": calculate_eco_rank(user.xp),
            "has_completed_first_disposal": user.has_completed_first_disposal,
            "member_since": user.created_at.strftime("%b %Y") if user.created_at else "Aug 2025",
            "community_rank": f"#{community_rank:,} globally",
            "total_users": total_users,
            "active_users": active_users,
            "level_info": level_info,
            "daily_challenge": {
                "description": daily_challenge.description,
                "progress": current_progress,
                "goal": daily_challenge.goal,
                "reward": daily_challenge.reward
            }
        }
        
        # Cache user data for offline access
        offline_manager.cache_user_data(user_id, user_data)
        
        return jsonify(user_data)
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {str(e)}")
        
        # Fallback to cached data
        try:
            cached_data = offline_manager.get_cached_user_data(user_id)
            if cached_data:
                logger.info(f"Returning cached data for user {user_id}")
                return jsonify(cached_data)
        except Exception as cache_error:
            logger.error(f"Error getting cached user data: {str(cache_error)}")
        
        return jsonify({"error": str(e)}), 500

@app.route('/api/hotspots')
@require_auth
def get_hotspots():
    """Get current litter hotspots with offline fallback"""
    try:
        # Try to get from main database first
        hotspots = Hotspot.query.filter(Hotspot.expires_at > datetime.utcnow()).all()
        hotspot_data = [{
            "id": h.id,
            "latitude": float(h.latitude),
            "longitude": float(h.longitude),
            "intensity": h.intensity,
            "created_at": h.created_at.isoformat()
        } for h in hotspots]
        
        # Cache hotspots for offline use
        offline_manager.cache_hotspots(hotspot_data)
        
        return jsonify(hotspot_data)
    except Exception as e:
        logger.error(f"Error getting hotspots from database: {str(e)}")
        
        # Fallback to cached data
        try:
            cached_hotspots = offline_manager.get_cached_hotspots()
            logger.info(f"Returning {len(cached_hotspots)} cached hotspots")
            return jsonify(cached_hotspots)
        except Exception as cache_error:
            logger.error(f"Error getting cached hotspots: {str(cache_error)}")
            return jsonify({"error": str(e)}), 500

# Enhanced global error handlers with security considerations
@app.errorhandler(400)
def bad_request_error(error):
    log_security_event('BAD_REQUEST', {'error': str(error)})
    return jsonify({
        "success": False,
        "error": "Bad request",
        "reason_code": "BAD_REQUEST",
        "message": "Invalid request format or data."
    }), 400

@app.errorhandler(401)
def unauthorized_error(error):
    return jsonify({
        "success": False,
        "error": "Unauthorized",
        "reason_code": "UNAUTHORIZED",
        "message": "Authentication required."
    }), 401

@app.errorhandler(403)
def forbidden_error(error):
    log_security_event('FORBIDDEN_ACCESS', {'error': str(error)})
    return jsonify({
        "success": False,
        "error": "Forbidden",
        "reason_code": "FORBIDDEN",
        "message": "Access denied."
    }), 403

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({
        "success": False,
        "error": "Not found",
        "reason_code": "NOT_FOUND",
        "message": "The requested resource was not found."
    }), 404

@app.errorhandler(413)
def payload_too_large_error(error):
    log_security_event('PAYLOAD_TOO_LARGE', {'size': request.content_length})
    return jsonify({
        "success": False,
        "error": "Request too large",
        "reason_code": "PAYLOAD_TOO_LARGE",
        "message": "Request size exceeds maximum allowed limit."
    }), 413

@app.errorhandler(429)
def rate_limit_error(error):
    log_security_event('RATE_LIMIT_EXCEEDED')
    return jsonify({
        "success": False,
        "error": "Too many requests",
        "reason_code": "RATE_LIMIT_EXCEEDED",
        "message": "Please slow down and try again later."
    }), 429

@app.errorhandler(500)
def internal_error(error):
    log_security_event('INTERNAL_SERVER_ERROR', {'error': str(error)})
    return jsonify({
        "success": False,
        "error": "Internal server error",
        "reason_code": "SERVER_ERROR",
        "message": "Something went wrong. Please try again."
    }), 500

# Apply security headers to all responses
@app.after_request
def apply_security_headers(response):
    return SecurityHeaders.apply_headers(response)

@app.route('/api/verify_disposal', methods=['POST'])
@rate_limit(max_requests=10)  # Limit AI API calls
@validate_json()
@require_auth
def verify_disposal():
    """Verify disposal action with AI and award points (with offline support)"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        video_b64 = data.get('video')  # Base64 encoded video
        
        if not all([user_id, latitude, longitude, video_b64]):
            return jsonify({"error": "Missing required fields"}), 400
            
        # Get user
        user = User.query.filter_by(user_id=user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Anti-cheat: Check GPS cooldown/distance
        recent_disposal = Disposal.query.filter_by(user_id=user_id).filter(
            Disposal.timestamp > datetime.utcnow() - timedelta(minutes=1)
        ).order_by(Disposal.timestamp.desc()).first()
        
        if recent_disposal:
            # Simple distance check (approximately 20 meters)
            lat_diff = abs(float(latitude) - float(recent_disposal.latitude))
            lon_diff = abs(float(longitude) - float(recent_disposal.longitude))
            if lat_diff < 0.0002 and lon_diff < 0.0002:  # Roughly 20 meters
                return jsonify({
                    "success": False,
                    "reason_code": "FAIL_TOO_CLOSE",
                    "message": "Please move to a different location before your next disposal."
                }), 400
        
        # Decode video from base64
        try:
            video_bytes = base64.b64decode(video_b64.split(',')[1] if ',' in video_b64 else video_b64)
        except Exception as e:
            logger.error(f"Error decoding video: {str(e)}")
            return jsonify({"error": "Invalid video data"}), 400
            
        # Check if online for AI validation
        if offline_manager.is_online():
            # Call Gemini AI for validation
            ai_result = validate_disposal_with_ai_video(video_bytes)
            
            if not ai_result["success"]:
                return jsonify({
                    "success": False,
                    "reason_code": ai_result["reason_code"],
                    "message": get_failure_message(ai_result["reason_code"])
                })
        else:
            # Offline mode - cache disposal for later processing
            success = offline_manager.cache_disposal_offline(
                user_id, float(latitude), float(longitude), [video_b64]
            )
            
            if success:
                return jsonify({
                    "success": True,
                    "points_earned": 10,  # Standard offline points
                    "xp_earned": 15,
                    "waste_category": "General Waste",  # Placeholder
                    "waste_sub_type": "Other General Waste",  # Placeholder
                    "bonuses_awarded": [],
                    "challenges_completed": [],
                    "reason_code": "OFFLINE_CACHED",
                    "message": "Disposal cached offline and will be validated when connection is restored.",
                    "offline_mode": True
                })
            else:
                return jsonify({
                    "success": False,
                    "reason_code": "OFFLINE_ERROR",
                    "message": "Unable to cache disposal offline. Please try again."
                }), 500
            
        # AI validation successful - calculate rewards
        waste_category = ai_result["waste_category"]
        waste_sub_type = ai_result["waste_sub_type"]
        
        # Check if this is first disposal
        points_earned = 10  # Standard reward
        xp_earned = 15     # Standard XP
        bonuses_awarded = []
        challenges_completed = []
        
        if not user.has_completed_first_disposal:
            points_earned = 200  # First-ever bonus
            xp_earned = 50
            user.has_completed_first_disposal = True
            bonuses_awarded.append("First Disposal Bonus: +190 points!")
            
        # Discovery bonus for new category and update skills
        if not has_discovered_category(user_id, waste_category):
            xp_earned += 10
            bonuses_awarded.append(f"Discovery Bonus: +10 XP for first {waste_category}!")
            
            # Add to discoveries if it's a new waste type
            waste_type = WasteType.query.filter_by(
                category=waste_category, 
                sub_type=waste_sub_type
            ).first()
            
            if waste_type:
                existing_discovery = UserDiscovery.query.filter_by(
                    user_id=user_id,
                    waste_type_id=waste_type.id
                ).first()
                
                if not existing_discovery:
                    discovery = UserDiscovery(
                        user_id=user_id,
                        waste_type_id=waste_type.id
                    )
                    db.session.add(discovery)
                    xp_earned += waste_type.discovery_xp
                    bonuses_awarded.append(f"New Discovery: {waste_type.sub_type} (+{waste_type.discovery_xp} XP)")
        
        # Update skills XP
        update_user_skills(user_id, waste_category, xp_earned)
            
        # Check daily challenge completion
        daily_challenge = get_or_assign_daily_challenge(user_id)
        challenge_bonus = check_daily_challenge_completion(user_id, daily_challenge, waste_category, waste_sub_type, latitude, longitude)
        
        if challenge_bonus > 0:
            points_earned += challenge_bonus
            challenges_completed.append(f"Daily Challenge Complete! +{challenge_bonus} points")
        
        # Update daily mission progress
        update_daily_mission_progress(user_id, waste_category, waste_sub_type)
            
        # Update user stats
        user.points += points_earned
        user.xp += xp_earned
        user.last_disposal_date = datetime.utcnow().date()
        
        # Update streak
        yesterday = datetime.utcnow().date() - timedelta(days=1)
        if user.last_disposal_date == yesterday:
            user.streak += 1
        elif user.last_disposal_date != datetime.utcnow().date():
            user.streak = 1
            
        # Log the disposal with database error handling
        try:
            disposal = Disposal(
                user_id=user_id,
                latitude=latitude,
                longitude=longitude,
                waste_category=waste_category,
                waste_sub_type=waste_sub_type,
                points_awarded=points_earned
            )
            db.session.add(disposal)
            
            # Log for anti-cheat (daily sub-type tracking)
            daily_log = DailyDisposalLog(
                user_id=user_id,
                date=datetime.utcnow().date(),
                waste_sub_type=waste_sub_type
            )
            db.session.add(daily_log)
            
            db.session.commit()
        
        except Exception as db_error:
            logger.error(f"Database error in verify_disposal: {str(db_error)}")
            # Roll back the transaction
            try:
                db.session.rollback()
            except:
                pass
            # Still return success to user since AI validation passed
        
        return jsonify({
            "success": True,
            "points_earned": points_earned,
            "xp_earned": xp_earned,
            "waste_category": waste_category,
            "waste_sub_type": waste_sub_type,
            "bonuses_awarded": bonuses_awarded,
            "challenges_completed": challenges_completed,
            "reason_code": "SUCCESS",
            "new_total_points": user.points,
            "new_total_xp": user.xp,
            "new_streak": user.streak,
            "eco_rank": calculate_eco_rank(user.xp)
        })
        
    except Exception as e:
        logger.error(f"Error verifying disposal: {str(e)}")
        # Ensure we rollback any uncommitted transactions
        try:
            db.session.rollback()
        except:
            pass
        return jsonify({
            "success": False,
            "reason_code": "SERVER_ERROR", 
            "message": "An error occurred processing your disposal. Please try again."
        }), 500

@app.route('/api/generate_hotspots', methods=['POST'])
def trigger_hotspot_generation():
    """Manually trigger hotspot generation (normally runs daily)"""
    try:
        generate_hotspots()
        return jsonify({"message": "Hotspots generated successfully"})
    except Exception as e:
        logger.error(f"Error generating hotspots: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync_offline', methods=['POST'])
def sync_offline_data():
    """Sync offline cached data with server"""
    try:
        sync_stats = offline_manager.sync_offline_data(app.app_context())
        return jsonify({
            "message": "Offline data synced successfully",
            "synced_disposals": sync_stats['synced_disposals'],
            "failed_disposals": sync_stats['failed_disposals']
        })
    except Exception as e:
        logger.error(f"Error syncing offline data: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/offline_status')
def get_offline_status():
    """Get offline cache status"""
    try:
        pending_disposals = offline_manager.get_pending_disposals()
        return jsonify({
            "is_online": offline_manager.is_online(),
            "pending_disposals": len(pending_disposals),
            "cache_available": True
        })
    except Exception as e:
        logger.error(f"Error getting offline status: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/user/<user_id>/discovered_categories')
@require_auth
def get_discovered_categories(user_id):
    """Get categories discovered by user"""
    try:
        categories = [
            "Plastic", "Paper/Cardboard", "Glass", "Metal", 
            "Organic", "E-Waste", "General Waste"
        ]
        
        discovered = {}
        for category in categories:
            discovered[category] = has_discovered_category(user_id, category)
        
        return jsonify(discovered)
    except Exception as e:
        logger.error(f"Error getting discovered categories for user {user_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/user/<user_id>/progress')
@require_auth
def get_user_progress(user_id):
    """Get comprehensive progress data for Progress tab"""
    try:
        # Get or create user skills
        skills_data = []
        skill_types = ['waste_detective', 'prevention_expert', 'impact_champion']
        
        for skill_type in skill_types:
            skill = UserSkill.query.filter_by(user_id=user_id, skill_type=skill_type).first()
            if not skill:
                skill = UserSkill(user_id=user_id, skill_type=skill_type)
                db.session.add(skill)
                db.session.commit()
            
            # Calculate progress based on skill type
            xp_for_current = (skill.level - 1) * 100
            xp_for_next = skill.level * 100
            progress_xp = skill.current_xp - xp_for_current
            progress_percent = min(100, (progress_xp / 100) * 100)
            
            skill_info = {
                'type': skill_type,
                'level': skill.level,
                'current_xp': skill.current_xp,
                'xp_for_next': xp_for_next,
                'progress_percent': progress_percent,
                'unlocked': True
            }
            
            # Special unlock conditions
            if skill_type == 'impact_champion':
                waste_detective = UserSkill.query.filter_by(user_id=user_id, skill_type='waste_detective').first()
                skill_info['unlocked'] = waste_detective and waste_detective.level >= 5
                if not skill_info['unlocked']:
                    skill_info['unlock_requirement'] = 'Requires Level 5 Detective'
            
            skills_data.append(skill_info)
        
        # Get discoveries
        total_waste_types = WasteType.query.count()
        user_discoveries = UserDiscovery.query.filter_by(user_id=user_id).count()
        
        discoveries = {
            'discovered': user_discoveries,
            'remaining': total_waste_types - user_discoveries,
            'categories': get_discovery_categories(user_id)
        }
        
        # Get today's mission
        mission = get_todays_mission(user_id)
        
        return jsonify({
            'skills': skills_data,
            'discoveries': discoveries,
            'daily_mission': mission
        })
        
    except Exception as e:
        logger.error(f"Error getting progress for user {user_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

def get_or_assign_daily_challenge(user_id):
    """Get or assign daily challenge for user"""
    today = datetime.utcnow().date()
    
    # Check if user has progress for today
    progress = UserChallengeProgress.query.filter_by(user_id=user_id).filter(
        func.date(UserChallengeProgress.assigned_date) == today
    ).first()
    
    if progress:
        return Challenge.query.filter_by(challenge_id=progress.challenge_id).first()
    
    # Assign random challenge
    challenges = Challenge.query.all()
    if not challenges:
        return None
        
    selected_challenge = random.choice(challenges)
    
    # Create progress record
    new_progress = UserChallengeProgress(
        user_id=user_id,
        challenge_id=selected_challenge.challenge_id,
        current_progress=0,
        assigned_date=datetime.utcnow()
    )
    db.session.add(new_progress)
    db.session.commit()
    
    return selected_challenge

def check_daily_challenge_completion(user_id, challenge, waste_category, waste_sub_type, latitude, longitude):
    """Check if disposal completes daily challenge"""
    if not challenge:
        return 0
        
    progress = UserChallengeProgress.query.filter_by(
        user_id=user_id,
        challenge_id=challenge.challenge_id
    ).filter(
        func.date(UserChallengeProgress.assigned_date) == datetime.utcnow().date()
    ).first()
    
    if not progress or progress.is_completed:
        return 0
        
    if challenge.challenge_type == "COUNT":
        if challenge.category == waste_category:
            progress.current_progress += 1
            if progress.current_progress >= challenge.goal:
                progress.is_completed = True
                db.session.commit()
                return challenge.reward
                
    elif challenge.challenge_type == "VARIETY":
        if challenge.category == waste_category:
            # Check if this sub-type was already logged today
            today = datetime.utcnow().date()
            existing = DailyDisposalLog.query.filter_by(
                user_id=user_id,
                date=today,
                waste_sub_type=waste_sub_type
            ).first()
            
            if not existing:  # New sub-type for today
                progress.current_progress += 1
                if progress.current_progress >= challenge.goal:
                    progress.is_completed = True
                    db.session.commit()
                    return challenge.reward
                    
    elif challenge.challenge_type == "HOTSPOT":
        # Check if location is in a hotspot
        hotspots = Hotspot.query.filter(Hotspot.expires_at > datetime.utcnow()).all()
        for hotspot in hotspots:
            # Simple distance check for hotspot
            lat_diff = abs(float(latitude) - float(hotspot.latitude))
            lon_diff = abs(float(longitude) - float(hotspot.longitude))
            if lat_diff < 0.001 and lon_diff < 0.001:  # Within hotspot
                progress.current_progress += 1
                if progress.current_progress >= challenge.goal:
                    progress.is_completed = True
                    db.session.commit()
                    return challenge.reward
                break
    
    db.session.commit()
    return 0

def has_discovered_category(user_id, category):
    """Check if user has discovered this category before"""
    return Disposal.query.filter_by(user_id=user_id, waste_category=category).first() is not None

def calculate_eco_rank(xp):
    """Calculate eco rank based on XP"""
    if xp < 100:
        return "Eco Novice"
    elif xp < 300:
        return "Eco Cadet"
    elif xp < 600:
        return "Eco Guardian"
    elif xp < 1000:
        return "Eco Champion"
    elif xp < 1500:
        return "Eco Master"
    else:
        return "Eco Legend"

def get_failure_message(reason_code):
    """Get user-friendly failure message"""
    messages = {
        "FAIL_LITTERING": "The waste was not disposed of in a proper receptacle. Please use a trash bin or recycling container.",
        "FAIL_WASTE_USABLE": "The item appears to be new or usable. Please only dispose of actual waste items.",
        "FAIL_OBJECT_TOO_SMALL": "The item is too small to be meaningful for our cleanup goals.",
        "FAIL_UNCLEAR": "The disposal action was unclear. Please ensure good lighting and clear visibility of the waste disposal."
    }
    return messages.get(reason_code, "Disposal validation failed. Please try again with a clearer recording.")

def get_todays_mission(user_id):
    """Get or create today's mission for user"""
    today = datetime.utcnow().date()
    
    # Check for existing mission
    mission = DailyMission.query.filter_by(user_id=user_id, mission_date=today).first()
    
    if not mission:
        # Create new mission
        mission_types = [
            {
                'type': 'plastic_variety',
                'description': 'Dispose of 4 different Plastic sub-types today',
                'goal': 4,
                'reward': 50
            },
            {
                'type': 'metal_count',
                'description': 'Dispose of 3 Metal items today',
                'goal': 3,
                'reward': 50
            },
            {
                'type': 'glass_discovery', 
                'description': 'Find and record 2 Glass items today',
                'goal': 2,
                'reward': 50
            }
        ]
        
        selected = random.choice(mission_types)
        mission = DailyMission(
            user_id=user_id,
            mission_date=today,
            mission_type=selected['type'],
            description=selected['description'],
            goal=selected['goal'],
            reward_points=selected['reward']
        )
        db.session.add(mission)
        db.session.commit()
    
    return {
        'description': mission.description,
        'goal': mission.goal,
        'current_progress': mission.current_progress,
        'reward_points': mission.reward_points,
        'is_completed': mission.is_completed,
        'time_remaining': mission.time_remaining
    }

def get_discovery_categories(user_id):
    """Get discovery status by category"""
    categories = ['Plastic', 'Paper/Cardboard', 'Glass']
    result = []
    
    for category in categories:
        total_in_category = WasteType.query.filter_by(category=category).count()
        discovered_in_category = db.session.query(UserDiscovery).join(WasteType).filter(
            UserDiscovery.user_id == user_id,
            WasteType.category == category
        ).count()
        
        result.append({
            'name': category,
            'discovered': discovered_in_category,
            'total': total_in_category,
            'unlocked': discovered_in_category > 0
        })
    
    return result

def update_user_skills(user_id, waste_category, base_xp):
    """Update user skills based on disposal action"""
    # Waste Detective - gets XP for any disposal
    detective_skill = UserSkill.query.filter_by(user_id=user_id, skill_type='waste_detective').first()
    if not detective_skill:
        detective_skill = UserSkill(user_id=user_id, skill_type='waste_detective')
        db.session.add(detective_skill)
    
    detective_skill.current_xp += base_xp
    
    # Level up check
    while detective_skill.current_xp >= detective_skill.level * 100:
        detective_skill.level += 1
    
    # Prevention Expert - gets XP for recyclable categories
    recyclable_categories = ['Plastic', 'Paper/Cardboard', 'Glass', 'Metal']
    if waste_category in recyclable_categories:
        prevention_skill = UserSkill.query.filter_by(user_id=user_id, skill_type='prevention_expert').first()
        if not prevention_skill:
            prevention_skill = UserSkill(user_id=user_id, skill_type='prevention_expert')
            db.session.add(prevention_skill)
        
        prevention_skill.current_xp += base_xp // 2  # Half XP for prevention
        
        while prevention_skill.current_xp >= prevention_skill.level * 100:
            prevention_skill.level += 1
    
    db.session.commit()

def update_daily_mission_progress(user_id, waste_category, waste_sub_type):
    """Update daily mission progress based on disposal"""
    today = datetime.utcnow().date()
    mission = DailyMission.query.filter_by(user_id=user_id, mission_date=today).first()
    
    if not mission or mission.is_completed:
        return
    
    if mission.mission_type == 'plastic_variety' and waste_category == 'Plastic':
        # Check if this sub-type was already counted today
        existing = DailyDisposalLog.query.filter_by(
            user_id=user_id,
            date=today,
            waste_sub_type=waste_sub_type
        ).first()
        
        if not existing:
            mission.current_progress += 1
            
    elif mission.mission_type == 'metal_count' and waste_category == 'Metal':
        mission.current_progress += 1
        
    elif mission.mission_type == 'glass_discovery' and waste_category == 'Glass':
        mission.current_progress += 1
    
    if mission.current_progress >= mission.goal:
        mission.is_completed = True
    
    db.session.commit()

if __name__ == "__main__":
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    app.run(host=host, port=port, debug=debug)