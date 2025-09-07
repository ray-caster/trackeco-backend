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
import random # <-- ADD THIS IMPORT
from faker import Faker # <-- ADD THIS IMPORT
gamification_bp = Blueprint('gamification_bp', __name__)
@gamification_bp.route('/v2/leaderboard', methods=['GET'])
@token_required
@limiter.exempt
def get_v2_leaderboard_mock(user_id):
    """
    A mock version of the leaderboard endpoint for frontend testing.
    - Generates 100 users with fake names and random points (1-50).
    - Inserts the current user with exactly 25 points.
    - Sorts the list and assigns correct ranks.
    - Returns the data in the exact same format as the real API.
    - This does NOT use Firestore.
    """
    try:
        fake = Faker()
        all_users_data = []

        # 1. Create the current user with specific data
        current_user_data = {
            "userId": user_id,
            "displayName": "My Test User", # Give your user a special name for easy identification
            "totalPoints": 25,
            "isCurrentUser": True
        }
        all_users_data.append(current_user_data)

        # 2. Generate 99 other random users
        for _ in range(99):
            other_user_data = {
                "userId": str(uuid.uuid4()),
                "displayName": fake.name(),
                "totalPoints": random.randint(1, 50), # Random points between 1 and 50
                "isCurrentUser": False
            }
            all_users_data.append(other_user_data)
        
        # 3. Sort the list just like Firestore would: by points descending, then userId ascending
        all_users_data.sort(key=lambda u: (-u['totalPoints'], u['userId']))

        # 4. Create the final Pydantic models, assign ranks, and find the current user's object
        leaderboard_page = []
        my_rank_object = None

        for i, user_data in enumerate(all_users_data):
            rank = i + 1
            user_summary = UserSummary(
                rank=rank,
                userId=user_data['userId'],
                displayName=user_data['displayName'],
                totalPoints=user_data['totalPoints'],
                avatarUrl=f"https://picsum.photos/seed/{user_data['userId']}/200", # Placeholder avatar
                isCurrentUser=user_data['isCurrentUser'],
                docId=user_data['userId']
            )
            leaderboard_page.append(user_summary)

            if user_summary.isCurrentUser:
                my_rank_object = user_summary

        # 5. Assemble the final response object
        final_page_dump = [entry.model_dump() for entry in leaderboard_page]
        my_rank_dump = my_rank_object.model_dump() if my_rank_object else None

        return jsonify({
            "leaderboardPage": final_page_dump,
            "myRank": my_rank_dump,
            "totalUsers": 100
        }), 200

    except Exception as e:
        logging.error(f"Error generating mock leaderboard: {e}", exc_info=True)
        return jsonify({"error": "Could not generate mock leaderboard data."}), 500
@gamification_bp.route('/v2/leaderboard_real', methods=['GET'])
@token_required
@limiter.exempt
def get_v2_leaderboard(user_id):
    """
    A fully bi-directional, stable, and cursor-based leaderboard endpoint.
    - Includes totalUsers count for frontend percentile calculations.
    - Fixes all deprecated queries.
    - Fixes the off-by-one ranking error.
    """
    try:
        start_after_doc_id = request.args.get('startAfterDocId')
        start_before_doc_id = request.args.get('startBeforeDocId')
        page_size = 20
        my_rank_entry = None

        # --- Stable, secondary sort order ---
        base_query = db.collection('users').order_by(
            'totalPoints', direction=firestore.Query.DESCENDING
        ).order_by(
            'userId', direction=firestore.Query.ASCENDING
        )
        
        # --- NEW FEATURE: Get the total count of users on the leaderboard ---
        # We count users with more than 0 points to get an accurate total.
        total_users_query = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", 0))
        total_users = total_users_query.count().get()[0][0].value

        # --- PAGINATION LOGIC ---
        # --- Scrolling Down ---
        if start_after_doc_id:
            last_doc_snapshot = db.collection('users').document(start_after_doc_id).get()
            if not last_doc_snapshot.exists:
                return jsonify({"error": "Paging document not found."}), 404
            
            query = base_query.start_after(last_doc_snapshot).limit(page_size)
            docs = list(query.stream())
            
            cursor_points = last_doc_snapshot.to_dict().get("totalPoints", 0)

            # --- DEPRECATION FIX ---
            # Create new queries from the base collection reference
            rank_above_cursor_q = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", cursor_points))
            rank_at_cursor_level_q = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", "==", cursor_points)).where(filter=firestore.FieldFilter("userId", "<=", last_doc_snapshot.id))
            rank_above_cursor = rank_above_cursor_q.count().get()[0][0].value
            rank_at_cursor_level = rank_at_cursor_level_q.count().get()[0][0].value
            
            start_rank = rank_above_cursor + rank_at_cursor_level
            
            entries = get_user_profiles_from_ids([doc.id for doc in docs], user_id)
            for i, entry in enumerate(entries):
                # --- RANKING FIX ---
                entry.rank = start_rank + i

        # --- Scrolling Up ---
        elif start_before_doc_id:
            first_doc_snapshot = db.collection('users').document(start_before_doc_id).get()
            if not first_doc_snapshot.exists:
                return jsonify({"error": "Paging document not found."}), 404
            
            query = base_query.end_before(first_doc_snapshot).limit_to_last(page_size)
            docs_reversed = list(query.get())
            docs = list(reversed(docs_reversed))

            if docs:
                first_new_doc = docs[0]
                first_new_doc_dict = first_new_doc.to_dict()
                first_new_doc_points = first_new_doc_dict.get("totalPoints", 0)

                # --- DEPRECATION FIX ---
                rank_above_q = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", first_new_doc_points))
                rank_at_level_q = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", "==", first_new_doc_points)).where(filter=firestore.FieldFilter("userId", "<=", first_new_doc.id))
                rank_above = rank_above_q.count().get()[0][0].value
                rank_at_level = rank_at_level_q.count().get()[0][0].value

                first_item_rank = rank_above + rank_at_level
                start_rank = first_item_rank - 1
            else:
                start_rank = 0

            entries = get_user_profiles_from_ids([doc.id for doc in docs], user_id)
            for i, entry in enumerate(entries):
                # --- RANKING FIX ---
                entry.rank = start_rank + i
        
        # --- INITIAL LOAD ---
        else:
            user_doc = db.collection('users').document(user_id).get()
            if not user_doc.exists: return jsonify({"error": "Current user not found."}), 404
            
            user_data = user_doc.to_dict()
            user_points = int(user_data.get("totalPoints", 0))

            # --- DEPRECATION FIX ---
            rank_above_q = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", ">", user_points))
            rank_at_my_level_q = db.collection('users').where(filter=firestore.FieldFilter("totalPoints", "==", user_points)).where(filter=firestore.FieldFilter("userId", "<=", user_id))
            rank_above = rank_above_q.count().get()[0][0].value
            rank_at_my_level = rank_at_my_level_q.count().get()[0][0].value
            my_rank = rank_above + rank_at_my_level

            query_before = base_query.end_before(user_doc).limit_to_last(10)
            docs_before_reversed = list(query.get())
            docs_before = list(reversed(docs_before_reversed))
            
            query_after = base_query.start_at(user_doc).limit(11)
            docs_after = list(query.after.stream())

            all_docs = docs_before + docs_after
            
            start_rank = my_rank - len(docs_before) - 1
            
            entries = get_user_profiles_from_ids([doc.id for doc in all_docs], user_id)
            entries.sort(key=lambda e: (-e.totalPoints, e.userId))
            
            for i, entry in enumerate(entries):
                # --- RANKING FIX ---
                entry.rank = start_rank + i
            
            my_rank_entry = next((e for e in entries if e.isCurrentUser), None)

        if my_rank_entry:
            my_rank_entry.docId = my_rank_entry.userId
        
        # Use the Pydantic response model for validation and serialization
        response_model = V2LeaderboardResponse(
            leaderboardPage=entries,
            myRank=my_rank_entry,
            totalUsers=total_users
        )

        return response_model.model_dump(), 200

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