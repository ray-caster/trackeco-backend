import logging
from flask import Blueprint, request, jsonify
from google.cloud import firestore
import datetime

from .config import db, storage_client, GCS_BUCKET_NAME
from .auth import token_required
from .pydantic_models import (
    PublicProfileResponse, 
    ProfileResponse, 
    AvatarUploadRequest, 
    UsernameCheckRequest, 
    UserSearchResponse,
    TeamChallengeInvitation,
    UserSummary
)
# We need to import the central helper to get friend data
def get_user_profiles_from_ids(user_ids, current_user_id=None):
    """
    The single, canonical helper function to fetch a list of user profiles.
    Returns a list of Pydantic LeaderboardEntry models.
    """
    if not user_ids:
        return []
    
    refs = (db.collection('users').document(str(uid)) for uid in user_ids)
    docs = db.get_all(refs)
    
    profiles = []
    for doc in docs:
        if doc.exists:
            user = doc.to_dict()
            entry = UserSummary(
                rank="-", # Rank is not relevant in all contexts, default to "-"
                userId=user.get('userId'),
                displayName=user.get('displayName'),
                username=user.get('username'),
                avatarUrl=user.get('avatarUrl'),
                totalPoints=int(user.get('totalPoints', 0)),
                isCurrentUser=user.get('userId') == current_user_id
            )
            profiles.append(entry)
    return profiles

users_bp = Blueprint('users_bp', __name__)

@users_bp.route('/search', methods=['GET'])
@token_required
def search_users(user_id):
    query_str = request.args.get('q', '').lower()
    if not query_str or len(query_str) < 3:
        return jsonify([]), 200

    # Firestore does not support case-insensitive or partial-text search natively.
    # This is a prefix search, which is the best we can do without a dedicated search service.
    users_ref = db.collection('users')
    
    username_query = users_ref.order_by('username').start_at(query_str).end_at(query_str + '\uf8ff').limit(5)
    display_name_query = users_ref.order_by('displayName_lowercase').start_at(query_str).end_at(query_str + '\uf8ff').limit(5)
    
    results = {}
    for doc in username_query.stream():
        user_data = doc.to_dict()
        if doc.id not in results and doc.id != user_id:
            results[doc.id] = UserSearchResponse(**user_data)
            
    for doc in display_name_query.stream():
        user_data = doc.to_dict()
        if doc.id not in results and doc.id != user_id:
            results[doc.id] = UserSearchResponse(**user_data)

    return jsonify([user.model_dump() for user in results.values()]), 200

@users_bp.route('/check-username', methods=['POST'])
@token_required
def check_username(user_id):
    req_data = UsernameCheckRequest.model_validate(request.get_json())
    users_ref = db.collection('users')
    query = users_ref.where(filter=firestore.FieldFilter('username', '==', req_data.username)).limit(1)
    docs = list(query.stream())
    
    # If no documents are found, the username is available
    is_available = not docs
    return jsonify({"available": is_available}), 200


@users_bp.route('/initiate-avatar-upload', methods=['POST'])
@token_required
def initiate_avatar_upload(user_id):
    req_data = AvatarUploadRequest.model_validate(request.get_json())
    gcs_filename = f"{user_id}.{req_data.fileExtension}"
    blob_path = f"avatars_original/{gcs_filename}"
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(blob_path)

    signed_url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=15),
        method="PUT",
        content_type=req_data.contentType
    )
    
    return jsonify({"upload_url": signed_url, "gcs_path": blob_path}), 200


@users_bp.route('/me', methods=['GET'])
@token_required
def get_my_profile(user_id):
    """
    A lightweight endpoint that now ONLY returns the current user's direct data.
    It does NOT include friend lists for maximum speed.
    """
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404

    user_data = user_doc.to_dict()

    invitation_ids = user_data.get('teamChallengeInvitations', [])
    invitations = []
    if invitation_ids:
        team_refs = [db.collection('teamChallenges').document(tid) for tid in invitation_ids]
        team_docs = db.get_all(team_refs)
        
        host_ids_to_fetch = {doc.to_dict().get('hostId') for doc in team_docs if doc.exists and doc.to_dict().get('hostId')}
        
        if host_ids_to_fetch:
            host_profiles_list = get_user_profiles_from_ids(list(host_ids_to_fetch))
            host_profiles_map = {p.userId: p.displayName for p in host_profiles_list}

            for doc in team_docs:
                if doc.exists:
                    team_data = doc.to_dict()
                    host_id = team_data.get('hostId')
                    invitations.append(TeamChallengeInvitation(
                        teamChallengeId=team_data.get('teamChallengeId'),
                        description=team_data.get('description'),
                        hostDisplayName=host_profiles_map.get(host_id, "Someone")
                    ))
    

    # Use the Pydantic model, but the friend lists will be empty by default
    profile = ProfileResponse(
        userId=user_data.get("userId"),
        displayName=user_data.get("displayName"),
        username=user_data.get("username"),
        avatarUrl=user_data.get("avatarUrl"),
        totalPoints=int(user_data.get("totalPoints", 0)),
        currentStreak=user_data.get("currentStreak", 0),
        maxStreak=user_data.get("maxStreak", 0),
        referralCode=user_data.get("referralCode"),
        onboardingComplete=user_data.get("onboardingComplete", False),
        onboardingStep=user_data.get("onboardingStep", 0),
        teamChallengeInvitations=invitations,
    )
    
    return profile.model_dump(), 200


@users_bp.route('/<profile_user_id>/profile', methods=['GET'])
@token_required
def get_public_profile(user_id, profile_user_id):
    """
    Fetches a limited, public version of another user's profile.
    """
    user_ref = db.collection('users').document(profile_user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404
    
    user_data = user_doc.to_dict()
    
    public_profile = PublicProfileResponse(
        userId=user_data.get('userId'),
        displayName=user_data.get('displayName'),
        username=user_data.get('username'),
        avatarUrl=user_data.get('avatarUrl'),
        totalPoints=int(user_data.get('totalPoints', 0)),
        currentStreak=user_data.get('currentStreak', 0)
    )
    
    return public_profile.model_dump(), 200