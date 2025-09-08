import logging
import json
import uuid
from flask import Blueprint, request, jsonify
from google.cloud import firestore

from .config import db, redis_client
from .auth import token_required
from .pydantic_models import TeamUpRequest, UserSummary, V2LeaderboardResponse
# Import the single, canonical helper for fetching user profiles
from .users import get_user_profiles_from_ids
from extensions import limiter

gamification_bp = Blueprint('gamification_bp', __name__)
def _apply_privacy_filter(profiles: list[UserSummary]):
    """
    Applies privacy settings to a list of user profiles.
    This function fetches the original user documents to check their settings.
    """
    if not profiles:
        return []

    user_ids = [p.userId for p in profiles]
    user_docs = db.collection('users').where('userId', 'in', user_ids).stream()
    privacy_settings = {doc.id: doc.to_dict() for doc in user_docs}

    anonymized_profiles = []
    for profile in profiles:
        user_settings = privacy_settings.get(profile.userId, {})
        
        if user_settings.get('showDisplayNameInLeaderboard', True) is False:
            profile.displayName = "Anonymous"
        
        if user_settings.get('showAvatarInLeaderboard', True) is False:
            profile.avatarUrl = None
            
        anonymized_profiles.append(profile)
    
    return anonymized_profiles

@gamification_bp.route('/v2/leaderboard', methods=['GET'])
@token_required
@limiter.exempt
def get_v2_leaderboard(user_id):
    try:
        start_after_doc_id = request.args.get('startAfterDocId')
        start_before_doc_id = request.args.get('startBeforeDocId')
        page_size = 20
        my_rank_entry = None
        
        # This base query is correct and essential for deterministic ordering
        base_query = db.collection('users').order_by(
            'totalPoints', direction=firestore.Query.DESCENDING
        ).order_by(
            'userId', direction=firestore.Query.ASCENDING
        )

        # Get total user count once at the beginning
        total_users = base_query.count().get()[0][0].value

        # --- PAGINATION LOGIC ---

        # --- Scrolling Down (Fetching the next page) ---
        if start_after_doc_id:
            last_doc_snapshot = db.collection('users').document(start_after_doc_id).get()
            if not last_doc_snapshot.exists:
                return jsonify({"error": "Paging document not found."}), 404
            
            query = base_query.start_after(last_doc_snapshot).limit(page_size)
            docs = list(query.stream())
            
            # Calculate the rank of the cursor document (the last item of the previous page)
            cursor_points = last_doc_snapshot.to_dict().get("totalPoints", 0)
            rank_above_cursor = base_query.where(filter=firestore.FieldFilter("totalPoints", ">", cursor_points)).count().get()[0][0].value
            rank_at_cursor_level = base_query.where(filter=firestore.FieldFilter("totalPoints", "==", cursor_points)).where(filter=firestore.FieldFilter("userId", "<=", last_doc_snapshot.id)).count().get()[0][0].value
            cursor_rank = rank_above_cursor + rank_at_cursor_level
            
            entries = get_user_profiles_from_ids([doc.id for doc in docs], user_id)
            for i, entry in enumerate(entries):
                # BUG FIX: Rank of the *next* item is the cursor's rank + 1 + index
                entry.rank = cursor_rank + i + 1

        # --- Scrolling Up (Fetching the previous page) ---
        elif start_before_doc_id:
            first_doc_snapshot = db.collection('users').document(start_before_doc_id).get()
            if not first_doc_snapshot.exists:
                return jsonify({"error": "Paging document not found."}), 404
            
            query = base_query.end_before(first_doc_snapshot).limit_to_last(page_size)
            docs = list(reversed(list(query.stream()))) # Use .stream() and reverse

            entries = []
            if docs:
                first_new_doc = docs[0]
                first_new_doc_points = first_new_doc.to_dict().get("totalPoints", 0)

                # Calculate the rank of the very first item in our new page
                rank_above = base_query.where(filter=firestore.FieldFilter("totalPoints", ">", first_new_doc_points)).count().get()[0][0].value
                rank_at_level = base_query.where(filter=firestore.FieldFilter("totalPoints", "==", first_new_doc_points)).where(filter=firestore.FieldFilter("userId", "<=", first_new_doc.id)).count().get()[0][0].value
                first_item_rank = rank_above + rank_at_level

                entries = get_user_profiles_from_ids([doc.id for doc in docs], user_id)
                for i, entry in enumerate(entries):
                    # BUG FIX: Simply start from the calculated rank of the first item
                    entry.rank = first_item_rank + i
        
        # --- INITIAL LOAD (Centered on the current user) ---
        else:
            user_doc = db.collection('users').document(user_id).get()
            if not user_doc.exists:
                return jsonify({"error": "Current user not found."}), 404
            
            user_data = user_doc.to_dict()
            user_points = int(user_data.get("totalPoints", 0))
            
            # Calculate the current user's rank
            rank_above = base_query.where(filter=firestore.FieldFilter("totalPoints", ">", user_points)).count().get()[0][0].value
            rank_at_my_level = base_query.where(filter=firestore.FieldFilter("totalPoints", "==", user_points)).where(filter=firestore.FieldFilter("userId", "<=", user_id)).count().get()[0][0].value
            my_rank = rank_above + rank_at_my_level

            # Fetch users before and after the current user to center them
            query_before = base_query.end_before(user_doc).limit_to_last(10)
            docs_before = list(reversed(list(query_before.stream())))
            
            query_after = base_query.start_at(user_doc).limit(11) # 10 after + the user themself
            docs_after = list(query_after.stream())

            all_docs = docs_before + docs_after
            all_doc_ids = [doc.id for doc in all_docs]

            entries = get_user_profiles_from_ids(all_doc_ids, user_id)
            entries.sort(key=lambda e: (-e.totalPoints, e.userId))

            # BUG FIX: The rank of the first person in our list is my_rank minus the number of people before me
            start_rank = my_rank - len(docs_before)
            
            for i, entry in enumerate(entries):
                entry.rank = start_rank + i
            
            my_rank_entry = next((e for e in entries if e.isCurrentUser), None)

        # BUG FIX: Call the now-defined privacy filter function
        final_entries = _apply_privacy_filter(entries)
        final_my_rank_entry = _apply_privacy_filter([my_rank_entry])[0] if my_rank_entry else None

        # Add docId to myRank object for consistency on the client
        if final_my_rank_entry:
            final_my_rank_entry.docId = final_my_rank_entry.userId

        final_page = [entry.model_dump() for entry in final_entries]
        
        return jsonify({
            "leaderboardPage": final_page,
            "myRank": final_my_rank_entry.model_dump() if final_my_rank_entry else None,
            "totalUsers": total_users
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