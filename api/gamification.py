#gamification.py
import json
from flask import Blueprint, request, jsonify
from google.cloud import firestore
import uuid
from .config import db, redis_client
from .auth import token_required
from .pydantic_models import TeamUpRequest
gamification_bp = Blueprint('gamification_bp', __name__)

@gamification_bp.route('/profile', methods=['GET'])
@token_required
def get_profile(user_id):
    user_ref = db.collection('users').document(user_id)
    user = user_ref.get()
    if not user.exists:
        return jsonify({
            "totalPoints": 0, "currentStreak": 0, "maxStreak": 0,
            "completedChallengeIds": [], "challengeProgress": {},
            "referralCode": "N/A", "onboardingComplete": False,
            "activeTeamChallenges": [] # Also return for non-existent user
        }), 200
    
    user_data = user.to_dict()
    profile_data = {
        "totalPoints": user_data.get("totalPoints", 0),
        "currentStreak": user_data.get("currentStreak", 0),
        "maxStreak": user_data.get("maxStreak", 0),
        "completedChallengeIds": user_data.get("completedChallengeIds", []),
        "challengeProgress": user_data.get("challengeProgress", {}),
        "referralCode": user_data.get("referralCode", "N/A"),
        "onboardingComplete": user_data.get("onboardingComplete", False),
        "activeTeamChallenges": user_data.get("activeTeamChallenges", []) # Add this field
    }
    return jsonify(profile_data), 200

@gamification_bp.route('/leaderboard', methods=['GET'])
@token_required
def get_leaderboard(user_id):
    cache_key = "leaderboard_cache"
    if redis_client:
        cached_leaderboard = redis_client.get(cache_key)
        if cached_leaderboard:
            # We still need to fetch the user's rank as it's dynamic
            user_ref = db.collection('users').document(user_id)
            user_doc = user_ref.get()
            my_rank_entry = None
            if user_doc.exists:
                user_data = user_doc.to_dict()
                user_points = user_data.get('totalPoints', 0)
                rank = "-"
                if user_points > 0:
                    query_greater = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", user_points))
                    rank = len(list(query_greater.stream())) + 1
                my_rank_entry = {"rank": rank, "displayName": user.get("displayName", "Anonymous"),, "totalPoints": user_points, "isCurrentUser": True}
            
            response_data = json.loads(cached_leaderboard)
            response_data['myRank'] = my_rank_entry
            return jsonify(response_data), 200
    
    # --- Cache Miss ---
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    my_rank_entry = None
    if user_doc.exists:
        user_data = user_doc.to_dict()
        user_points = user_data.get('totalPoints', 0)
        rank = "-"
        if user_points > 0:
            query_greater = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", user_points))
            rank = len(list(query_greater.stream())) + 1
        my_rank_entry = {"rank": rank, "email": user_data.get("email"), "totalPoints": user_points, "isCurrentUser": True}

    # Fetch top 100 for the general leaderboard
    query = db.collection('users').order_by('totalPoints', direction=firestore.Query.DESCENDING).limit(100)
    leaderboard_page = []
    for i, doc in enumerate(query.stream()):
        user = doc.to_dict()
        entry = {
            "rank": i + 1,
            "email": user.get("email", "Anonymous"),
            "totalPoints": user.get("totalPoints", 0),
            "isCurrentUser": user.get("userId") == user_id,
        }
        leaderboard_page.append(entry)
    
    response_data = {"myRank": my_rank_entry, "leaderboardPage": leaderboard_page}
    
    if redis_client:
        # Cache only the general page, myRank is always dynamic
        redis_client.set(cache_key, json.dumps({"leaderboardPage": leaderboard_page}), ex=600)

    return jsonify(response_data), 200


@gamification_bp.route('/challenges', methods=['GET'])
def get_challenges():
    cache_key = "challenges_cache"
    if redis_client:
        cached_challenges = redis_client.get(cache_key)
        if cached_challenges:
            return jsonify(json.loads(cached_challenges)), 200
            
    query = db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True))
    active_challenges = [doc.to_dict() for doc in query.stream()]
    
    if not active_challenges:
        return jsonify({"error": "No active challenges found"}), 404
        
    if redis_client:
        redis_client.set(cache_key, json.dumps(active_challenges, default=str), ex=3600)
        
    return jsonify(active_challenges), 200


@gamification_bp.route('/challenges/team-up', methods=['POST'])
@token_required
def team_up_on_challenge(user_id):
    req_data = TeamUpRequest.model_validate(request.get_json())
    
    original_challenge_ref = db.collection('challenges').document(req_data.challengeId)
    original_challenge = original_challenge_ref.get()
    if not original_challenge.exists:
        return jsonify({"error": "Original challenge not found"}), 404
    
    challenge_data = original_challenge.to_dict()
    if not challenge_data.get('isTeamUpEligible'):
        return jsonify({"error": "This challenge is not eligible for teams."}), 400

    team_challenge_id = str(uuid.uuid4())
    team_challenge_ref = db.collection('teamChallenges').document(team_challenge_id)
    
    members = list(set([user_id] + req_data.inviteeIds))
    
    team_challenge_data = {
        "teamChallengeId": team_challenge_id,
        "originalChallengeId": req_data.challengeId,
        "challengeType": challenge_data.get('type'),
        "keyword": challenge_data.get('keyword'),
        "progressGoal": challenge_data.get('progressGoal'),
        "bonusPoints": challenge_data.get('bonusPoints'),
        "description": challenge_data.get('description'),
        "members": members,
        "hostId": user_id,
        "currentProgress": 0,
        "status": "active",
        "expiresAt": challenge_data.get('expiresAt')
    }
    team_challenge_ref.set(team_challenge_data)

    batch = db.batch()
    for member_id in members:
        user_ref = db.collection('users').document(member_id)
        batch.update(user_ref, {'activeTeamChallenges': firestore.ArrayUnion([team_challenge_id])})
    batch.commit()

    return jsonify(team_challenge_data), 201

def health_check():
    """
    Performs a non-destructive health check for the gamification module.
    """
    try:
        # Checks if the leaderboard query index is working.
        _ = list(db.collection('users').order_by('totalPoints', direction=firestore.Query.DESCENDING).limit(1).stream())
        _ = list(db.collection('challenges').where(filter=firestore.FieldFilter('isActive', '==', True)).limit(1).stream())
        return {"status": "OK", "details": "Firestore collections and leaderboard index are accessible."}
    except Exception as e:
        # This will catch errors if the required indexes are missing.
        return {"status": "ERROR", "details": f"Failed to query Firestore collections. Check indexes. Error: {str(e)}"}