import logging
import json
import base64
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from .config import redis_client


class PaginationError(Exception):
    """Custom exception for pagination errors"""
    pass


def generate_cursor(data: Dict[str, Any]) -> str:
    """
    Generate a cursor from pagination data.
    Uses base64 encoding for safe transmission.
    """
    try:
        cursor_data = json.dumps(data, default=str)
        return base64.urlsafe_b64encode(cursor_data.encode()).decode()
    except Exception as e:
        logging.error(f"Error generating cursor: {e}")
        raise PaginationError("Failed to generate cursor")


def parse_cursor(cursor: str) -> Dict[str, Any]:
    """
    Parse a cursor string back into pagination data.
    """
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        return json.loads(decoded)
    except Exception as e:
        logging.error(f"Error parsing cursor: {e}")
        raise PaginationError("Invalid cursor")


def get_pagination_cache_key(endpoint: str, user_id: str, params: Dict[str, Any]) -> str:
    """
    Generate a unique Redis key for pagination state.
    """
    # Sort params for consistent key generation
    sorted_params = json.dumps(params, sort_keys=True)
    return f"pagination:{endpoint}:{user_id}:{sorted_params}"


def store_pagination_state(
    endpoint: str, 
    user_id: str, 
    params: Dict[str, Any], 
    data: List[Any], 
    ttl_seconds: int = 300
) -> str:
    """
    Store pagination state in Redis and return a cursor.
    """
    redis_conn = redis_client()
    if not redis_conn:
        raise PaginationError("Redis connection not available")
    
    cache_key = get_pagination_cache_key(endpoint, user_id, params)
    cursor_data = {
        "endpoint": endpoint,
        "user_id": user_id,
        "params": params,
        "timestamp": datetime.now().isoformat(),
        "data_length": len(data)
    }
    
    try:
        # Store the actual data with TTL
        redis_conn.setex(
            cache_key,
            ttl_seconds,
            json.dumps(data, default=str)
        )
        
        return generate_cursor(cursor_data)
    except Exception as e:
        logging.error(f"Error storing pagination state: {e}")
        raise PaginationError("Failed to store pagination state")


def retrieve_pagination_state(cursor: str) -> List[Any]:
    """
    Retrieve pagination data from Redis using cursor.
    """
    redis_conn = redis_client()
    if not redis_conn:
        raise PaginationError("Redis connection not available")
    
    try:
        cursor_data = parse_cursor(cursor)
        cache_key = get_pagination_cache_key(
            cursor_data["endpoint"],
            cursor_data["user_id"],
            cursor_data["params"]
        )
        
        cached_data = redis_conn.get(cache_key)
        if not cached_data:
            raise PaginationError("Pagination state expired or not found")
        
        return json.loads(cached_data)
    except Exception as e:
        logging.error(f"Error retrieving pagination state: {e}")
        raise PaginationError("Failed to retrieve pagination state")


def paginate_list(
    full_list: List[Any],
    limit: int,
    cursor: Optional[str] = None
) -> Tuple[List[Any], Optional[str], bool]:
    """
    Paginate a list with cursor-based pagination.
    Returns (page_data, next_cursor, has_more)
    """
    if not full_list:
        return [], None, False
    
    start_index = 0
    if cursor:
        try:
            cursor_data = parse_cursor(cursor)
            start_index = cursor_data.get("next_index", 0)
        except PaginationError:
            # Invalid cursor, start from beginning
            start_index = 0
    
    if start_index >= len(full_list):
        return [], None, False
    
    end_index = min(start_index + limit, len(full_list))
    page_data = full_list[start_index:end_index]
    
    has_more = end_index < len(full_list)
    next_cursor = None
    
    if has_more:
        next_cursor_data = {
            "next_index": end_index,
            "total_items": len(full_list)
        }
        next_cursor = generate_cursor(next_cursor_data)
    
    return page_data, next_cursor, has_more


def validate_pagination_params(limit: Optional[int], cursor: Optional[str]) -> Tuple[int, Optional[str]]:
    """
    Validate and sanitize pagination parameters.
    Returns (sanitized_limit, sanitized_cursor)
    """
    # Validate limit
    if limit is None:
        limit = 20  # Default page size
    elif not isinstance(limit, int) or limit <= 0:
        raise PaginationError("Limit must be a positive integer")
    elif limit > 100:  # Maximum page size
        limit = 100
    
    # Validate cursor if provided
    sanitized_cursor = None
    if cursor:
        if not isinstance(cursor, str) or not cursor.strip():
            raise PaginationError("Cursor must be a non-empty string")
        try:
            # Test if cursor can be parsed
            parse_cursor(cursor)
            sanitized_cursor = cursor
        except PaginationError:
            raise PaginationError("Invalid cursor format")
    
    return limit, sanitized_cursor


def create_pagination_response(
    data: List[Any],
    next_cursor: Optional[str],
    has_more: bool,
    total_count: Optional[int] = None
) -> Dict[str, Any]:
    """
    Create a standardized pagination response.
    """
    response = {
        "data": data,
        "pagination": {
            "has_more": has_more,
            "next_cursor": next_cursor
        }
    }
    
    if total_count is not None:
        response["pagination"]["total_count"] = total_count
    
    return response