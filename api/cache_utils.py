from .config import redis_client

def get_user_summary_cache_key(user_id):
    """Generates the standard Redis key for a user summary."""
    return f"user_summary:{user_id}"

def invalidate_user_summary_cache(user_id):
    """Deletes a user's summary from the Redis cache."""
    if redis_client and user_id:
        key = get_user_summary_cache_key(user_id)
        redis_client.delete(key)