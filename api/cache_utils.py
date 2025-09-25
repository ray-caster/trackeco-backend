import json
import logging
import time
from typing import Optional, Any, Dict, List
from dependencies import redis_client
from redis.lock import Lock

# Cache metrics for monitoring
_cache_metrics = {
    'hits': 0,
    'misses': 0,
    'stampede_preventions': 0,
    'errors': 0
}

def get_user_summary_cache_key(user_id: str) -> str:
    """Generates the standard Redis key for a user summary."""
    return f"user_summary:{user_id}"

def get_user_summary_lock_key(user_id: str) -> str:
    """Generates the lock key for user summary cache stampede protection."""
    return f"lock:user_summary:{user_id}"

def get_cache_metrics() -> Dict[str, int]:
    """Returns current cache metrics for monitoring."""
    return _cache_metrics.copy()

def reset_cache_metrics() -> None:
    """Resets cache metrics counters."""
    global _cache_metrics
    _cache_metrics = {
        'hits': 0,
        'misses': 0,
        'stampede_preventions': 0,
        'errors': 0
    }

def _increment_metric(metric_name: str) -> None:
    """Increments a cache metric counter."""
    if metric_name in _cache_metrics:
        _cache_metrics[metric_name] += 1

def get_cached_user_summary(user_id: str, lock_timeout: int = 10, cache_timeout: int = 300) -> Optional[Dict]:
    """
    Gets a user summary from cache with stampede protection.
    Returns None if not cached or error.
    """
    redis_conn = redis_client()
    if not redis_conn:
        _increment_metric('errors')
        return None

    cache_key = get_user_summary_cache_key(user_id)
    lock_key = get_user_summary_lock_key(user_id)

    try:
        # Try to get cached data
        cached_data = redis_conn.get(cache_key)
        if cached_data:
            _increment_metric('hits')
            return json.loads(cached_data)
        
        _increment_metric('misses')
        
        # Check if another process is already fetching this data
        lock_acquired = redis_conn.set(lock_key, '1', nx=True, ex=lock_timeout)
        if not lock_acquired:
            # Another process is fetching, prevent stampede
            _increment_metric('stampede_preventions')
            return None
            
        return None  # Cache miss, caller should fetch from DB

    except Exception as e:
        logging.error(f"Error getting cached user summary for {user_id}: {e}")
        _increment_metric('errors')
        return None

def set_cached_user_summary(user_id: str, data: Dict, cache_timeout: int = 300) -> bool:
    """
    Sets a user summary in cache with optimized TTL.
    Returns True if successful, False otherwise.
    """
    redis_conn = redis_client()
    if not redis_conn:
        _increment_metric('errors')
        return False

    cache_key = get_user_summary_cache_key(user_id)
    lock_key = get_user_summary_lock_key(user_id)

    try:
        # Set cache data with TTL
        success = redis_conn.set(
            cache_key, 
            json.dumps(data), 
            ex=cache_timeout
        )
        
        # Release the lock
        redis_conn.delete(lock_key)
        
        return bool(success)
        
    except Exception as e:
        logging.error(f"Error setting cached user summary for {user_id}: {e}")
        _increment_metric('errors')
        try:
            # Ensure lock is released even on error
            redis_conn.delete(lock_key)
        except:
            pass
        return False

def invalidate_user_summary_cache(user_id: str) -> bool:
    """
    Deletes a user's summary from the Redis cache.
    Returns True if successful, False otherwise.
    """
    redis_conn = redis_client()
    if not redis_conn or not user_id:
        _increment_metric('errors')
        return False

    try:
        cache_key = get_user_summary_cache_key(user_id)
        lock_key = get_user_summary_lock_key(user_id)
        
        # Delete both cache and lock
        redis_conn.delete(cache_key)
        redis_conn.delete(lock_key)
        return True
        
    except Exception as e:
        logging.error(f"Error invalidating user summary cache for {user_id}: {e}")
        _increment_metric('errors')
        return False

def batch_get_cached_user_summaries(user_ids: List[str], lock_timeout: int = 10, cache_timeout: int = 300) -> Dict[str, Optional[Dict]]:
    """
    Gets multiple user summaries from cache with stampede protection.
    Returns a dict mapping user_id to cached data or None.
    """
    redis_conn = redis_client()
    if not redis_conn:
        _increment_metric('errors')
        return {uid: None for uid in user_ids}

    results = {}
    cache_keys = [get_user_summary_cache_key(uid) for uid in user_ids]
    lock_keys = [get_user_summary_lock_key(uid) for uid in user_ids]

    try:
        # Batch get cached data
        cached_results = redis_conn.mget(cache_keys)
        
        for user_id, cached_json, cache_key, lock_key in zip(user_ids, cached_results, cache_keys, lock_keys):
            if cached_json:
                _increment_metric('hits')
                results[user_id] = json.loads(cached_json)
            else:
                _increment_metric('misses')
                # Check if another process is fetching
                lock_acquired = redis_conn.set(lock_key, '1', nx=True, ex=lock_timeout)
                if not lock_acquired:
                    _increment_metric('stampede_preventions')
                results[user_id] = None

        return results

    except Exception as e:
        logging.error(f"Error in batch_get_cached_user_summaries: {e}")
        _increment_metric('errors')
        return {uid: None for uid in user_ids}

def batch_set_cached_user_summaries(user_data_map: Dict[str, Dict], cache_timeout: int = 300) -> bool:
    """
    Sets multiple user summaries in cache with optimized TTL.
    Returns True if successful, False otherwise.
    """
    redis_conn = redis_client()
    if not redis_conn:
        _increment_metric('errors')
        return False

    try:
        pipe = redis_conn.pipeline()
        
        for user_id, data in user_data_map.items():
            cache_key = get_user_summary_cache_key(user_id)
            lock_key = get_user_summary_lock_key(user_id)
            
            pipe.set(cache_key, json.dumps(data), ex=cache_timeout)
            pipe.delete(lock_key)  # Release locks
        
        pipe.execute()
        return True
        
    except Exception as e:
        logging.error(f"Error in batch_set_cached_user_summaries: {e}")
        _increment_metric('errors')
        return False

# Backward compatibility functions
def get_user_profiles_from_ids_optimized(user_ids: List[str], current_user_id: Optional[str] = None) -> List[Dict]:
    """
    Optimized version of get_user_profiles_from_ids with stampede protection and metrics.
    This is a drop-in replacement for the original function.
    """
    from .users import UserSummary  # Import here to avoid circular imports
    
    if not user_ids:
        return []

    # Get cached data with stampede protection
    cached_results = batch_get_cached_user_summaries(user_ids)
    
    profiles_from_cache = {}
    ids_to_fetch_from_db = []
    
    for user_id, cached_data in cached_results.items():
        if cached_data:
            # Convert cached data to UserSummary model
            model_data = cached_data.copy()
            model_data.setdefault('rank', 0)
            model_data.setdefault('currentStreak', 0)
            model_data['docId'] = user_id
            profiles_from_cache[user_id] = UserSummary.model_validate(model_data)
        else:
            ids_to_fetch_from_db.append(user_id)
    
    # Fetch remaining profiles from database
    profiles_from_db = []
    if ids_to_fetch_from_db:
        from dependencies import db
        refs = (db.collection('users').document(str(uid)) for uid in ids_to_fetch_from_db)
        docs = db.get_all(refs)
        
        user_data_to_cache = {}
        
        for doc in docs:
            if doc.exists:
                user = doc.to_dict()
                entry = UserSummary(
                    rank=0,
                    userId=user.get('userId'),
                    docId=user.get('userId'),
                    displayName=user.get('displayName'),
                    username=user.get('username'),
                    avatarUrl=user.get('avatarUrl'),
                    currentStreak=int(user.get('currentStreak', 0)),
                    totalPoints=int(user.get('totalPoints', 0)),
                )
                profiles_from_db.append(entry)
                user_data_to_cache[user.get('userId')] = entry.model_dump(exclude={'rank', 'isCurrentUser', 'docId'})
        
        # Cache the newly fetched data
        if user_data_to_cache:
            batch_set_cached_user_summaries(user_data_to_cache)
    
    all_profiles_map = {p.userId: p for p in list(profiles_from_cache.values()) + profiles_from_db}
    
    if current_user_id and current_user_id in all_profiles_map:
        all_profiles_map[current_user_id].isCurrentUser = True
    
    return [all_profiles_map[uid] for uid in user_ids if uid in all_profiles_map]