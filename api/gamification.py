import logging
import json
import uuid
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .config import db, redis_client
from .auth import token_required
from .pydantic_models import TeamUpRequest, UserSummary
# Import the single, canonical helper for fetching user profiles
from users import get_user_profiles_from_ids

gamification_bp = Blueprint('gamification_bp', __name__)

@gamification_bp.route('/v2/leaderboard', methods=['GET'])
@token_required
def get_v2_leaderboard(user_id):
    """
    A scalable, paginated leaderboard endpoint.
    It can fetch pages from any starting rank.
    """
    try:
        start_after_rank = request.args.get('startAfterRank', type=int)
        page_size = 25
        
        query = db.collection('users').order_by('totalPoints', direction=firestore.Query.DESCENDING)
        
        if start_after_rank:
            query = query.offset(start_after_rank)
        
        query = query.limit(page_size)
        docs = list(query.stream())
        
        # Use the central helper to get Pydantic models
        doc_ids = [doc.id for doc in docs]
        entries = get_user_profiles_from_ids(doc_ids, current_user_id=user_id)
        
        current_rank = start_after_rank + 1 if start_after_rank else 1
        for entry in entries:
            entry.rank = current_rank
            current_rank += 1
        
        # --- My Rank Calculation (if not already in the fetched page) ---
        my_rank_entry = next((e for e in entries if e.isCurrentUser), None)
        
        if my_rank_entry is None:
            user_doc = db.collection('users').document(user_id).get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                user_points = int(user_data.get("totalPoints", 0))
                
                query_greater = db.collection('users').where("totalPoints", ">", user_points)
                rank_above = query_greater.count().get()[0][0].value
                my_rank = rank_above + 1

                my_rank_entry = UserSummary(
                    rank=my_rank,
                    displayName=user_data.get('displayName', 'You'),
                    userId=user_id,
                    totalPoints=user_points,
                    avatarUrl=user_data.get('avatarUrl'),
                    isCurrentUser=True
                )

        return jsonify({
            "leaderboardPage": [entry.model_dump() for entry in entries],
            "myRank": my_rank_entry.model_dump() if my_rank_entry else None
        }), 200

    except Exception as e:
        logging.error(f"Error fetching v2 leaderboard: {e}", exc_info=True)
        return jsonify({"error": "Could not load leaderboard data."}), 500
    
@gamification_bp.route('/challenges', methods=['GET'])
def get_challenges():
    """Fetches the list of currently active challenges, with caching."""
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
        redis_client.set(cache_key, json.dumps(active_challenges, default=str), ex=3600) # Cache for 1 hour
        
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
    
    # NEW: Create a members map to track invitation status
    members_map = { user_id: "accepted" } # Host auto-accepts
    for invitee_id in req_data.inviteeIds:
        members_map[invitee_id] = "pending"
    
    team_challenge_data = {
        "teamChallengeId": team_challenge_id, "originalChallengeId": req_data.challengeId,
        "description": challenge_data.get('description'), "progressGoal": challenge_data.get('progressGoal'),
        "bonusPoints": challenge_data.get('bonusPoints'), "hostId": user_id,
        "members": members_map, "status": "pending", # Status is now 'pending' by default
        "currentProgress": 0, "expiresAt": challenge_data.get('expiresAt')
    }
    team_challenge_ref.set(team_challenge_data)

    # Add the invitation to each invitee's user document
    batch = db.batch()
    # Add the active challenge to the host's document immediately
    host_ref = db.collection('users').document(user_id)
    batch.update(host_ref, {'activeTeamChallenges': firestore.ArrayUnion([team_challenge_id])})

    for invitee_id in req_data.inviteeIds:
        user_ref = db.collection('users').document(invitee_id)
        batch.update(user_ref, {'teamChallengeInvitations': firestore.ArrayUnion([team_challenge_id])})
    batch.commit()

    return jsonify(team_challenge_data), 201

@gamification_bp.route('/team-challenges/<team_challenge_id>/accept', methods=['POST'])
@token_required
def accept_invitation(user_id, team_challenge_id):
    team_ref = db.collection('teamChallenges').document(team_challenge_id)
    user_ref = db.collection('users').document(user_id)
    
    @firestore.transactional
    def accept_in_transaction(transaction):
        team_doc = team_ref.get(transaction=transaction)
        if not team_doc.exists: return {"error": "Invitation not found."}, 404
        
        team_data = team_doc.to_dict()
        members = team_data.get('members', {})
        
        if user_id not in members or members.get(user_id) != "pending":
            return {"error": "Invalid invitation or you have already responded."}, 400

        # Update user's status in the team document
        members[user_id] = "accepted"
        transaction.update(team_ref, {"members": members})
        
        # Move from invitations to active challenges for the user
        transaction.update(user_ref, {
            "teamChallengeInvitations": firestore.ArrayRemove([team_challenge_id]),
            "activeTeamChallenges": firestore.ArrayUnion([team_challenge_id])
        })
        return {"message": "Invitation accepted."}, 200

    message, status_code = accept_in_transaction(db.transaction())
    return jsonify(message), status_code

@gamification_bp.route('/team-challenges/<team_challenge_id>/decline', methods=['POST'])
@token_required
def decline_invitation(user_id, team_challenge_id):
    team_ref = db.collection('teamChallenges').document(team_challenge_id)
    user_ref = db.collection('users').document(user_id)

    # Update team document by removing the member
    team_doc = team_ref.get()
    if team_doc.exists:
        team_data = team_doc.to_dict()
        members = team_data.get('members', {})
        if user_id in members:
            members.pop(user_id)
            team_ref.update({"members": members})

    # Always remove the invitation from the user's list
    user_ref.update({"teamChallengeInvitations": firestore.ArrayRemove([team_challenge_id])})
    
    return jsonify({"message": "Invitation declined."}), 200

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