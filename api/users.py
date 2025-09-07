# FILE: trackeco-backend/api/users.py

import logging
import json # <-- IMPORT json
from flask import Blueprint, request, jsonify
from google.cloud import firestore
import datetime

from .config import db, storage_client, GCS_BUCKET_NAME, redis_client # <-- IMPORT redis_client
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
from .cache_utils import get_user_summary_cache_key, invalidate_user_summary_cache # <-- IMPORT cache helpers

def get_user_profiles_from_ids(user_ids, current_user_id=None):
    """
    The single, canonical helper function to fetch a list of user profiles.
    IMPLEMENTATION: Includes Redis caching and now populates the docId field.
    """
    if not user_ids:
        return []

    profiles_from_cache = {}
    ids_to_fetch_from_db = []

    if redis_client:
        keys = [get_user_summary_cache_key(uid) for uid in user_ids]
        cached_results = redis_client.mget(keys)
        for user_id, cached_json in zip(user_ids, cached_results):
            if cached_json:
                model_data = json.loads(cached_json)
                model_data.setdefault('rank', 0)
                # --- THE FIX ---
                # Add docId from the user_id
                model_data['docId'] = user_id
                profiles_from_cache[user_id] = UserSummary.model_validate(model_data)
            else:
                ids_to_fetch_from_db.append(user_id)
    else:
        ids_to_fetch_from_db = user_ids

    profiles_from_db = []
    if ids_to_fetch_from_db:
        refs = (db.collection('users').document(str(uid)) for uid in ids_to_fetch_from_db)
        docs = db.get_all(refs)
        
        pipe = redis_client.pipeline() if redis_client else None
        
        for doc in docs:
            if doc.exists:
                user = doc.to_dict()
                entry = UserSummary(
                    rank=0,
                    userId=user.get('userId'),
                    # --- THE FIX ---
                    # The docId is the same as the userId.
                    docId=user.get('userId'),
                    displayName=user.get('displayName'),
                    username=user.get('username'),
                    avatarUrl=user.get('avatarUrl'),
                    totalPoints=int(user.get('totalPoints', 0)),
                )
                profiles_from_db.append(entry)
                if pipe:
                    key = get_user_summary_cache_key(user.get('userId'))
                    cache_data = entry.model_dump(exclude={'rank', 'isCurrentUser', 'docId'})
                    pipe.set(key, json.dumps(cache_data), ex=300)
        
        if pipe:
            pipe.execute()

    all_profiles_map = {p.userId: p for p in list(profiles_from_cache.values()) + profiles_from_db}
    
    if current_user_id and current_user_id in all_profiles_map:
        all_profiles_map[current_user_id].isCurrentUser = True

    return [all_profiles_map[uid] for uid in user_ids if uid in all_profiles_map]

users_bp = Blueprint('users_bp', __name__)

@users_bp.route('/search', methods=['GET'])
@token_required
def search_users(user_id):
    query_str = request.args.get('q', '').lower()
    if not query_str or len(query_str) < 3:
        return jsonify([]), 200

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

@users_bp.route('/avatar-upload-complete', methods=['POST'])
@token_required
def avatar_upload_complete(user_id):
    """
    This endpoint is now just a trigger. The actual resizing is offloaded to a task.
    """
    from tasks import process_avatar_image # Local import
    req_data = request.get_json()
    gcs_path = req_data.get('gcsPath')
    if not gcs_path:
        return jsonify({"error": "gcsPath is required"}), 400
    
    # Invalidate the cache immediately for a responsive UI
    invalidate_user_summary_cache(user_id)
    
    # Offload the heavy image processing to a Celery task
    process_avatar_image.delay(gcs_path, user_id)
    
    return jsonify({"message": "Avatar processing queued"}), 202


@users_bp.route('/me', methods=['GET'])
@token_required
def get_my_profile(user_id):
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
        completedChallengeIds=user_data.get('completedChallengeIds', []),
        challengeProgress=user_data.get('challengeProgress', {}),
        activeTeamChallenges=user_data.get('activeTeamChallenges', []),
        teamChallengeInvitations=invitations,
    )
    
    return profile.model_dump(), 200


@users_bp.route('/<profile_user_id>/profile', methods=['GET'])
@token_required
def get_public_profile(user_id, profile_user_id):
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