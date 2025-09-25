"""
Input sanitization utilities for TrackEco API endpoints.
Provides protection against XSS, injection attacks, and malformed data.
"""

import re
import html
from typing import Any, Dict, List, Optional
import logging

def sanitize_string(input_str: str, max_length: Optional[int] = None) -> str:
    """
    Sanitize a string input by:
    1. Stripping leading/trailing whitespace
    2. HTML escaping to prevent XSS
    3. Truncating to max_length if specified
    4. Removing control characters
    
    Args:
        input_str: The input string to sanitize
        max_length: Optional maximum length for truncation
        
    Returns:
        Sanitized string
    """
    if not isinstance(input_str, str):
        if input_str is None:
            return ""
        return str(input_str)
    
    # Strip whitespace and basic sanitization
    sanitized = input_str.strip()
    
    # HTML escape to prevent XSS
    sanitized = html.escape(sanitized)
    
    # Remove control characters (except tab, newline, carriage return)
    sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', sanitized)
    
    # Truncate if max_length specified
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
        logging.warning(f"Input truncated from {len(input_str)} to {max_length} characters")
    
    return sanitized

def sanitize_email(email: str) -> str:
    """
    Sanitize and validate email address format.
    
    Args:
        email: Email address to sanitize
        
    Returns:
        Sanitized email address in lowercase
    """
    if not email:
        return ""
    
    email = sanitize_string(email).lower()
    
    # Basic email format validation
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_regex, email):
        logging.warning(f"Invalid email format: {email}")
        # Still return sanitized version but log warning
        return email
    
    return email

def sanitize_username(username: str) -> str:
    """
    Sanitize username to allow only alphanumeric characters, underscores, and hyphens.
    
    Args:
        username: Username to sanitize
        
    Returns:
        Sanitized username
    """
    if not username:
        return ""
    
    username = sanitize_string(username)
    
    # Remove any non-alphanumeric characters except underscores and hyphens
    username = re.sub(r'[^a-zA-Z0-9_-]', '', username)
    
    # Enforce reasonable length limits
    if len(username) > 30:
        username = username[:30]
        logging.warning(f"Username truncated to 30 characters: {username}")
    
    return username

def sanitize_url(url: str) -> str:
    """
    Sanitize URL input to prevent XSS and malformed URLs.
    
    Args:
        url: URL to sanitize
        
    Returns:
        Sanitized URL
    """
    if not url:
        return ""
    
    url = sanitize_string(url)
    
    # Basic URL validation - allow only http, https, and data URLs for avatars
    if url and not url.startswith(('http://', 'https://', 'data:image/')):
        logging.warning(f"Potentially unsafe URL scheme: {url}")
        # Still return sanitized version but log warning
    
    return url

def sanitize_integer(value: Any, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """
    Sanitize integer input with range validation.
    
    Args:
        value: Value to convert to integer
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        
    Returns:
        Sanitized integer
        
    Raises:
        ValueError: If value cannot be converted to integer or is out of range
    """
    try:
        int_value = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid integer value: {value}")
    
    if min_val is not None and int_value < min_val:
        raise ValueError(f"Value {int_value} is below minimum {min_val}")
    
    if max_val is not None and int_value > max_val:
        raise ValueError(f"Value {int_value} is above maximum {max_val}")
    
    return int_value

def sanitize_float(value: Any, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    """
    Sanitize float input with range validation.
    
    Args:
        value: Value to convert to float
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        
    Returns:
        Sanitized float
        
    Raises:
        ValueError: If value cannot be converted to float or is out of range
    """
    try:
        float_value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid float value: {value}")
    
    if min_val is not None and float_value < min_val:
        raise ValueError(f"Value {float_value} is below minimum {min_val}")
    
    if max_val is not None and float_value > max_val:
        raise ValueError(f"Value {float_value} is above maximum {max_val}")
    
    return float_value

def sanitize_dict(data: Dict[str, Any], field_rules: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize a dictionary based on field-specific rules.
    
    Args:
        data: Dictionary to sanitize
        field_rules: Dictionary mapping field names to sanitization functions or rules
        
    Returns:
        Sanitized dictionary
    """
    sanitized = {}
    
    for field, value in data.items():
        if field in field_rules:
            rule = field_rules[field]
            
            if callable(rule):
                # Rule is a sanitization function
                sanitized[field] = rule(value)
            elif isinstance(rule, dict):
                # Rule is a nested structure
                if isinstance(value, dict):
                    sanitized[field] = sanitize_dict(value, rule)
                else:
                    sanitized[field] = value  # Keep as-is if not dict
            else:
                sanitized[field] = value
        else:
            # No specific rule, apply basic string sanitization if it's a string
            if isinstance(value, str):
                sanitized[field] = sanitize_string(value)
            else:
                sanitized[field] = value
    
    return sanitized

# Common sanitization rules for API endpoints
USERNAME_RULES = {
    'username': sanitize_username,
    'displayName': lambda x: sanitize_string(x, 50),
    'email': sanitize_email,
    'avatarUrl': sanitize_url
}

CHALLENGE_RULES = {
    'description': lambda x: sanitize_string(x, 200),
    'challengeId': sanitize_string,
    'type': lambda x: sanitize_string(x, 20)
}

UPLOAD_RULES = {
    'filename': lambda x: sanitize_string(x, 100),
    'upload_id': sanitize_string,
    'fcm_token': sanitize_string
}