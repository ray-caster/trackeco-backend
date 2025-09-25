"""
Redis-based rate limiting with sliding window algorithm for TrackEco backend.
Provides distributed rate limiting across multiple server instances.
"""

import time
import logging
import functools
from typing import Optional, Callable, Dict, Any, Tuple
from flask import request, jsonify, current_app
from dependencies import redis_client

# Rate limit configurations
RATE_LIMIT_CONFIGS = {
    'auth': {
        'requests': 10,
        'window_seconds': 60,
        'key_func': lambda: request.remote_addr,  # IP-based for auth
        'description': 'Authentication endpoints'
    },
    'data_modification': {
        'requests': 30,
        'window_seconds': 60,
        'key_func': lambda: f"user:{getattr(request, 'user_id', 'anonymous')}",
        'description': 'Data modification endpoints'
    },
    'data_retrieval': {
        'requests': 60,
        'window_seconds': 60,
        'key_func': lambda: f"user:{getattr(request, 'user_id', 'anonymous')}",
        'description': 'Data retrieval endpoints'
    }
}

# Metrics for monitoring rate limiting
_rate_limit_metrics = {
    'total_requests': 0,
    'rate_limited_requests': 0,
    'auth_limited': 0,
    'modification_limited': 0,
    'retrieval_limited': 0,
    'redis_errors': 0
}

def get_rate_limit_metrics() -> Dict[str, int]:
    """Returns current rate limit metrics for monitoring."""
    return _rate_limit_metrics.copy()

def reset_rate_limit_metrics() -> None:
    """Resets rate limit metrics counters."""
    global _rate_limit_metrics
    _rate_limit_metrics = {
        'total_requests': 0,
        'rate_limited_requests': 0,
        'auth_limited': 0,
        'modification_limited': 0,
        'retrieval_limited': 0,
        'redis_errors': 0
    }

def _increment_metric(metric_name: str) -> None:
    """Increments a rate limit metric counter."""
    if metric_name in _rate_limit_metrics:
        _rate_limit_metrics[metric_name] += 1

def _get_redis_key(limit_type: str, identifier: str) -> str:
    """Generates Redis key for rate limiting."""
    return f"rate_limit:{limit_type}:{identifier}"

def _sliding_window_check(
    redis_conn, 
    key: str, 
    max_requests: int, 
    window_seconds: int
) -> Tuple[bool, int, Optional[int]]:
    """
    Implements sliding window rate limiting using Redis sorted sets.
    Returns (is_allowed, remaining_requests, retry_after_seconds)
    """
    current_time = int(time.time())
    window_start = current_time - window_seconds
    
    try:
        # Remove old entries outside the window
        redis_conn.zremrangebyscore(key, 0, window_start)
        
        # Get current count
        current_count = redis_conn.zcard(key)
        
        if current_count >= max_requests:
            # Find the oldest request to calculate retry-after
            oldest_request = redis_conn.zrange(key, 0, 0, withscores=True)
            if oldest_request:
                oldest_time = int(oldest_request[0][1])
                retry_after = window_start + window_seconds - oldest_time
                return False, 0, max(1, retry_after)
            return False, 0, window_seconds
        
        # Add current request
        redis_conn.zadd(key, {str(current_time): current_time})
        redis_conn.expire(key, window_seconds * 2)  # Extra buffer for cleanup
        
        remaining = max(0, max_requests - current_count - 1)
        return True, remaining, None
        
    except Exception as e:
        logging.error(f"Redis error in sliding window check for key {key}: {e}")
        _increment_metric('redis_errors')
        # Allow requests if Redis is unavailable
        return True, max_requests, None

def rate_limit_decorator(limit_type: str) -> Callable:
    """
    Creates a rate limit decorator for a specific limit type.
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            _increment_metric('total_requests')
            
            config = RATE_LIMIT_CONFIGS[limit_type]
            identifier = config['key_func']()
            max_requests = config['requests']
            window_seconds = config['window_seconds']
            
            redis_conn = redis_client()
            if not redis_conn:
                # Redis unavailable, allow request but log warning
                logging.warning(f"Redis unavailable, skipping rate limit for {limit_type}: {identifier}")
                return f(*args, **kwargs)
            
            redis_key = _get_redis_key(limit_type, identifier)
            allowed, remaining, retry_after = _sliding_window_check(
                redis_conn, redis_key, max_requests, window_seconds
            )
            
            if not allowed:
                _increment_metric('rate_limited_requests')
                _increment_metric(f'{limit_type}_limited')
                
                logging.warning(
                    f"Rate limit exceeded for {limit_type}: {identifier}. "
                    f"Limit: {max_requests}/{window_seconds}s, Retry after: {retry_after}s"
                )
                
                response = jsonify({
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded. Please try again in {retry_after} seconds.",
                    "retry_after": retry_after
                })
                response.headers['Retry-After'] = str(retry_after)
                response.status_code = 429
                return response
            
            # Add rate limit headers to successful responses
            response = f(*args, **kwargs)
            if hasattr(response, 'headers'):
                response.headers['X-RateLimit-Limit'] = str(max_requests)
                response.headers['X-RateLimit-Remaining'] = str(remaining)
                response.headers['X-RateLimit-Reset'] = str(window_seconds)
            
            return response
        
        return decorated_function
    return decorator

# Specific rate limit decorators
auth_rate_limit = rate_limit_decorator('auth')
data_modification_rate_limit = rate_limit_decorator('data_modification')
data_retrieval_rate_limit = rate_limit_decorator('data_retrieval')

def limit(rate_string: str) -> Callable:
    """
    Generic rate limit decorator that mimics Flask-Limiter's syntax.
    Example: @limit("10 per minute")
    """
    def parse_rate_string(rate_str: str) -> Tuple[int, int]:
        """Parse rate string like '10 per minute'"""
        parts = rate_str.split()
        if len(parts) != 3 or parts[1].lower() != 'per':
            raise ValueError(f"Invalid rate string format: {rate_str}")
        
        requests = int(parts[0])
        unit = parts[2].lower()
        
        time_units = {
            'second': 1,
            'minute': 60,
            'hour': 3600,
            'day': 86400
        }
        
        if unit not in time_units:
            raise ValueError(f"Unknown time unit: {unit}")
        
        return requests, time_units[unit]
    
    requests, window_seconds = parse_rate_string(rate_string)
    
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            _increment_metric('total_requests')
            
            identifier = request.remote_addr  # Default to IP address
            redis_conn = redis_client()
            
            if not redis_conn:
                logging.warning(f"Redis unavailable, skipping custom rate limit for {identifier}")
                return f(*args, **kwargs)
            
            redis_key = _get_redis_key('custom', identifier)
            allowed, remaining, retry_after = _sliding_window_check(
                redis_conn, redis_key, requests, window_seconds
            )
            
            if not allowed:
                _increment_metric('rate_limited_requests')
                
                logging.warning(
                    f"Custom rate limit exceeded: {identifier}. "
                    f"Limit: {requests}/{window_seconds}s, Retry after: {retry_after}s"
                )
                
                response = jsonify({
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded. Please try again in {retry_after} seconds.",
                    "retry_after": retry_after
                })
                response.headers['Retry-After'] = str(retry_after)
                response.status_code = 429
                return response
            
            response = f(*args, **kwargs)
            if hasattr(response, 'headers'):
                response.headers['X-RateLimit-Limit'] = str(requests)
                response.headers['X-RateLimit-Remaining'] = str(remaining)
                response.headers['X-RateLimit-Reset'] = str(window_seconds)
            
            return response
        
        return decorated_function
    return decorator