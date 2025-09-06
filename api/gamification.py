import json
from flask import Blueprint, request, jsonify
from google.cloud import firestore
import uuid

from .config import db, redis_client
from .auth import token_required
from .pydantic_models import TeamUpRequest, V2LeaderboardResponse, LeaderboardEntry

gamification_bp = Blueprint('gamification_bp', __name__)
import logging

def get_user_profiles_from_ids(user_ids):
    if not user_ids: return []
    refs = (db.collection('users').document(uid) for uid in user_ids)
    docs = db.get_all(refs)
    profiles = []
    for doc in docs:
        if doc.exists:
            user = doc.to_dict()
            profiles.append({
                "userId": user.get('userId'),
                "displayName": user.get('displayName'),
                "username": user.get('username'),
                "avatarUrl": user.get('avatarUrl')
            })
    return profiles

def get_user_profiles_from_docs(docs):
    """Helper to format user documents into LeaderboardEntry models."""
    profiles = []
    for doc in docs:
        if doc.exists:
            user = doc.to_dict()
            profiles.append(LeaderboardEntry(
                rank="-", # Rank will be assigned later
                displayName=user.get('displayName', "Anonymous"),
                userId=user.get('userId'),
                totalPoints=int(user.get('totalPoints', 0)),
                avatarUrl=user.get('avatarUrl'),
                isCurrentUser=False # Flag will be set later
            ))
    return profiles

@gamification_bp.route('/v2/leaderboard', methods=['GET'])
@token_required
def get_v2_leaderboard(user_id):
    """
    A scalable leaderboard endpoint that can fetch chunks of the leaderboard
    from any starting point, in either direction.
    """
    try:
        # Get pagination parameters from the request query string
        start_after_rank = request.args.get('startAfterRank', type=int)
        
        page_size = 25 # Define how many users to fetch per page

        # --- Query Logic ---
        query = db.collection('users').order_by('totalPoints', direction=firestore.Query.DESCENDING)

        if start_after_rank:
            # If paginating, start the query after the given rank
            query = query.offset(start_after_rank)
        
        query = query.limit(page_size)
        docs = list(query.stream())
        entries = get_user_profiles_from_docs(docs)
        
        # Assign correct ranks to the fetched entries
        current_rank = start_after_rank + 1 if start_after_rank else 1
        for entry in entries:
            entry.rank = current_rank
            entry.isCurrentUser = entry.userId == user_id
            current_rank += 1
        
        # --- My Rank Calculation (if not already in the fetched page) ---
        my_rank_entry = next((e for e in entries if e.isCurrentUser), None)
        
        if my_rank_entry is None:
            # If user is not in the current page, calculate their rank separately
            user_doc = db.collection('users').document(user_id).get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                user_points = int(user_data.get("totalPoints", 0))
                
                query_greater = db.collection('users').where("totalPoints", ">", user_points)
                rank_above = query_greater.count().get()[0][0].value
                my_rank = rank_above + 1

                my_rank_entry = LeaderboardEntry(
                    rank=my_rank,
                    displayName=user_data.get('displayName', 'You'),
                    userId=user_id,
                    totalPoints=int(user_points),
                    avatarUrl=user_data.get('avatarUrl'),
                    isCurrentUser=True
                )

        # For this new simplified model, we return one list and the user's rank
        return jsonify({
            "leaderboardPage": [entry.model_dump() for entry in entries],
            "myRank": my_rank_entry.model_dump() if my_rank_entry else None
        }), 200

    except Exception as e:
        logging.error(f"Error fetching v2 leaderboard: {e}", exc_info=True)
        return jsonify({"error": "Could not load leaderboard data."}), 500
    
@gamification_bp.route('/profile', methods=['GET'])
@token_required
def get_profile(user_id):
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404

    user_data = user_doc.to_dict()

    # Fetch full profiles for friends and requests
    friend_ids = user_data.get('friends', [])
    sent_request_ids = user_data.get('friendRequestsSent', [])
    received_request_ids = user_data.get('friendRequestsReceived', [])

    friends = get_user_profiles_from_ids(friend_ids)
    sent_requests = get_user_profiles_from_ids(sent_request_ids)
    received_requests = get_user_profiles_from_ids(received_request_ids)

    # Fetch full data for team invitations
    invitation_ids = user_data.get('teamChallengeInvitations', [])
    invitations = []
    if invitation_ids:
        team_refs = [db.collection('teamChallenges').document(tid) for tid in invitation_ids]
        team_docs = db.get_all(team_refs)
        host_ids_to_fetch = {doc.to_dict().get('hostId') for doc in team_docs if doc.exists}
        
        # Fetch display names for all hosts in one go
        host_profiles = {p['userId']: p['displayName'] for p in get_user_profiles_from_ids(list(host_ids_to_fetch))}

        for doc in team_docs:
            if doc.exists:
                team_data = doc.to_dict()
                host_id = team_data.get('hostId')
                invitations.append({
                    "teamChallengeId": team_data.get('teamChallengeId'),
                    "description": team_data.get('description'),
                    "hostDisplayName": host_profiles.get(host_id, "Someone")
                })
    
    # Construct the full, rich profile response
    profile_data = {
        "userId": user_data.get("userId"),
        "displayName": user_data.get("displayName"),
        "username": user_data.get("username"),
        "avatarUrl": user_data.get("avatarUrl"),
        "totalPoints": int(user_data.get("totalPoints", 0)),
        "currentStreak": user_data.get("currentStreak", 0),
        "maxStreak": user_data.get("maxStreak", 0),
        "referralCode": user_data.get("referralCode", "N/A"),
        "completedChallengeIds": user_data.get("completedChallengeIds", []),
        "challengeProgress": user_data.get("challengeProgress", {}),
        "onboardingComplete": user_data.get("onboardingComplete", False),
        "onboardingStep": user_data.get("onboardingStep", 0),
        "activeTeamChallenges": user_data.get("activeTeamChallenges", []),
        "teamChallengeInvitations": invitations,
        # For simplicity, we can remove friends from profile and keep the dedicated /friends endpoint
        # "friends": friends, 
        # "friendRequestsSent": sent_requests,
        # "friendRequestsReceived": received_requests
    }
    return jsonify(profile_data), 200

@gamification_bp.route('/leaderboard', methods=['GET'])
@token_required
def get_leaderboard(user_id):
    cache_key = "leaderboard_top_100"
    
    # --- Fetch Top 100 (Cache or Firestore) ---
    leaderboard_page = None
    if redis_client:
        cached_leaderboard = redis_client.get(cache_key)
        if cached_leaderboard:
            leaderboard_page = json.loads(cached_leaderboard)

    if leaderboard_page is None:
        query = db.collection('users').order_by('totalPoints', direction=firestore.Query.DESCENDING).limit(100)
        leaderboard_page = []
        for i, doc in enumerate(query.stream()):
            user = doc.to_dict()
            entry = {
                "rank": i + 1,
                "displayName": user.get("displayName", "Anonymous"),
                "totalPoints": int(user.get("totalPoints", 0)),
                "avatarUrl": user.get("avatarUrl"),
                "userId": user.get("userId"),
                "docId": doc.id
            }
            leaderboard_page.append(entry)
        if redis_client:
            redis_client.set(cache_key, json.dumps(leaderboard_page), ex=600)

    # --- NEW: Real-time "My Rank" Calculation ---
    my_rank_entry = None
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    if user_doc.exists:
        user_data = user_doc.to_dict()
        user_points = user_data.get('totalPoints', 0)
        
        # This is the efficient "delta" update. It performs a count query on the server.
        # This is extremely fast and costs only a single document read, regardless of user count.
        query_greater = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", user_points))
        count_agg_query = query_greater.count()
        query_result = count_agg_query.get()
        # The result of a count query is a list of lists, so we access the value like this:
        rank_above = query_result[0][0].value
        
        # The user's rank is the number of people above them + 1
        real_time_rank = rank_above + 1
        
        my_rank_entry = {
            "rank": real_time_rank,
            "displayName": user_data.get("displayName", "Anonymous"), 
            "totalPoints": int(user_points), 
            "avatarUrl": user_data.get("avatarUrl"),
            "isCurrentUser": True,
            "userId": user_data.get("userId"),
            "docId": user_doc.id
        }
            
    # Add the isCurrentUser flag to the main Top 100 list
    for entry in leaderboard_page:
        entry["isCurrentUser"] = entry.get("userId") == user_id

    response_data = {"myRank": my_rank_entry, "leaderboardPage": leaderboard_page}
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