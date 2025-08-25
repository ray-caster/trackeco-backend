"""
Comprehensive Security Module for TrackEco
Implements multiple layers of security protection
"""

import re
import time
import hashlib
import secrets
import logging
from functools import wraps
from collections import defaultdict, deque
from datetime import datetime, timedelta
from flask import request, jsonify, abort, session, g
from werkzeug.exceptions import BadRequest
import bleach

# Security logger
security_logger = logging.getLogger('security')
security_logger.setLevel(logging.WARNING)

# Rate limiting storage
rate_limit_storage = defaultdict(lambda: deque())
failed_login_attempts = defaultdict(lambda: deque())

class SecurityConfig:
    """Security configuration constants"""
    
    # Rate limiting
    MAX_REQUESTS_PER_MINUTE = 60
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION = 15 * 60  # 15 minutes
    
    # Input validation
    MAX_USERNAME_LENGTH = 50
    MAX_EMAIL_LENGTH = 254
    MAX_PASSWORD_LENGTH = 128
    MIN_PASSWORD_LENGTH = 8
    MAX_REQUEST_SIZE = 16 * 1024 * 1024  # 16MB
    
    # Session security
    SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours
    
    # Content security
    ALLOWED_HTML_TAGS = []
    ALLOWED_HTML_ATTRIBUTES = {}

class SecurityHeaders:
    """Security HTTP headers"""
    
    @staticmethod
    def apply_headers(response):
        """Apply comprehensive security headers"""
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(self)'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "connect-src 'self'; "
            "media-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'"
        )
        return response

class InputValidator:
    """Comprehensive input validation and sanitization"""
    
    @staticmethod
    def sanitize_string(value, max_length=None):
        """Sanitize string input"""
        if not isinstance(value, str):
            raise ValueError("Input must be a string")
        
        # Remove null bytes and control characters
        value = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', value)
        
        # Strip whitespace
        value = value.strip()
        
        # Check length
        if max_length and len(value) > max_length:
            raise ValueError(f"Input too long (max {max_length} characters)")
        
        # HTML sanitization - remove ALL HTML tags and dangerous patterns
        value = bleach.clean(value, tags=SecurityConfig.ALLOWED_HTML_TAGS, 
                           attributes=SecurityConfig.ALLOWED_HTML_ATTRIBUTES, strip=True)
        
        # Additional XSS protection
        dangerous_patterns = [
            r'<script[^>]*>.*?</script>',
            r'javascript:',
            r'on\w+\s*=',
            r'<iframe',
            r'<object',
            r'<embed',
            r'<link',
            r'<meta',
            r'vbscript:',
            r'data:text/html'
        ]
        
        for pattern in dangerous_patterns:
            value = re.sub(pattern, '', value, flags=re.IGNORECASE)
        
        # Remove any remaining < or > characters that could be dangerous
        value = value.replace('<', '').replace('>', '')
        
        return value
    
    @staticmethod
    def validate_username(username):
        """Validate username with strict rules"""
        username = InputValidator.sanitize_string(username, SecurityConfig.MAX_USERNAME_LENGTH)
        
        if len(username) < 3:
            raise ValueError("Username must be at least 3 characters")
        
        if not re.match(r'^[a-zA-Z0-9_-]+$', username):
            raise ValueError("Username can only contain letters, numbers, underscores, and hyphens")
        
        # Check for SQL injection patterns
        if re.search(r'(union|select|insert|update|delete|drop|create|alter|exec|execute)', username, re.IGNORECASE):
            raise ValueError("Username contains prohibited characters")
        
        return username
    
    @staticmethod
    def validate_email(email):
        """Validate email with comprehensive checks"""
        email = InputValidator.sanitize_string(email, SecurityConfig.MAX_EMAIL_LENGTH)
        
        # Basic format validation
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            raise ValueError("Invalid email format")
        
        # Check for dangerous patterns
        if re.search(r'[<>"\']', email):
            raise ValueError("Email contains prohibited characters")
        
        return email.lower()
    
    @staticmethod
    def validate_password(password):
        """Validate password with security requirements"""
        if not isinstance(password, str):
            raise ValueError("Password must be a string")
        
        if len(password) < SecurityConfig.MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {SecurityConfig.MIN_PASSWORD_LENGTH} characters")
        
        if len(password) > SecurityConfig.MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password too long (max {SecurityConfig.MAX_PASSWORD_LENGTH} characters)")
        
        # Check for complexity requirements
        has_upper = re.search(r'[A-Z]', password)
        has_lower = re.search(r'[a-z]', password)
        has_digit = re.search(r'\d', password)
        
        if not (has_upper and has_lower and has_digit):
            raise ValueError("Password must contain uppercase, lowercase, and numeric characters")
        
        # Check for common weak patterns
        if re.search(r'(.)\1{2,}', password):  # More than 2 repeated characters
            raise ValueError("Password cannot contain repeated character sequences")
        
        common_passwords = ['password', '123456', 'password123', 'admin', 'qwerty']
        if password.lower() in common_passwords:
            raise ValueError("Password is too common")
        
        return password
    
    @staticmethod
    def validate_coordinates(lat, lng):
        """Validate geographical coordinates"""
        try:
            lat = float(lat)
            lng = float(lng)
        except (ValueError, TypeError):
            raise ValueError("Coordinates must be valid numbers")
        
        if not (-90 <= lat <= 90):
            raise ValueError("Latitude must be between -90 and 90")
        
        if not (-180 <= lng <= 180):
            raise ValueError("Longitude must be between -180 and 180")
        
        return lat, lng

class RateLimiter:
    """Rate limiting for API endpoints"""
    
    @staticmethod
    def get_client_id():
        """Get client identifier for rate limiting"""
        # Use IP address + user agent hash for identification
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        user_agent = request.headers.get('User-Agent', '')
        return hashlib.sha256(f"{client_ip}:{user_agent}".encode()).hexdigest()[:16]
    
    @staticmethod
    def is_rate_limited(client_id, max_requests=SecurityConfig.MAX_REQUESTS_PER_MINUTE):
        """Check if client is rate limited"""
        now = time.time()
        window_start = now - 60  # 1 minute window
        
        # Clean old requests
        requests = rate_limit_storage[client_id]
        while requests and requests[0] < window_start:
            requests.popleft()
        
        # Check if limit exceeded
        if len(requests) >= max_requests:
            security_logger.warning(f"Rate limit exceeded for client {client_id}")
            return True
        
        # Add current request
        requests.append(now)
        return False
    
    @staticmethod
    def is_login_blocked(identifier):
        """Check if login attempts are blocked"""
        now = time.time()
        window_start = now - SecurityConfig.LOCKOUT_DURATION
        
        # Clean old attempts
        attempts = failed_login_attempts[identifier]
        while attempts and attempts[0] < window_start:
            attempts.popleft()
        
        return len(attempts) >= SecurityConfig.MAX_LOGIN_ATTEMPTS
    
    @staticmethod
    def record_failed_login(identifier):
        """Record a failed login attempt"""
        failed_login_attempts[identifier].append(time.time())
        security_logger.warning(f"Failed login attempt for {identifier}")

class CSRFProtection:
    """CSRF token generation and validation"""
    
    @staticmethod
    def generate_token():
        """Generate CSRF token"""
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        return session['csrf_token']
    
    @staticmethod
    def validate_token(token):
        """Validate CSRF token"""
        if not token or 'csrf_token' not in session:
            return False
        return secrets.compare_digest(session['csrf_token'], token)

def rate_limit(max_requests=SecurityConfig.MAX_REQUESTS_PER_MINUTE):
    """Rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            client_id = RateLimiter.get_client_id()
            
            if RateLimiter.is_rate_limited(client_id, max_requests):
                security_logger.warning(f"Rate limit hit for {request.endpoint} by {client_id}")
                return jsonify({
                    "error": "Too many requests", 
                    "message": "Please slow down and try again later"
                }), 429
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def login_rate_limit():
    """Special rate limiting for login attempts"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            client_id = RateLimiter.get_client_id()
            
            # Check general rate limit
            if RateLimiter.is_rate_limited(client_id, 10):  # 10 requests per minute for login
                return jsonify({
                    "error": "Too many requests", 
                    "message": "Please wait before trying again"
                }), 429
            
            # Check login-specific blocking
            request_data = request.get_json(silent=True) or {}
            identifier = request_data.get('username', client_id)
            
            if RateLimiter.is_login_blocked(identifier):
                security_logger.warning(f"Login blocked for {identifier} due to repeated failures")
                return jsonify({
                    "error": "Account temporarily locked", 
                    "message": "Too many failed login attempts. Please try again later."
                }), 423
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def validate_json():
    """Decorator to safely validate JSON input with comprehensive error handling"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                # Check if request has content
                if request.content_length == 0:
                    return jsonify({"error": "Request body is empty"}), 400
                
                # Check content type
                content_type = request.headers.get('Content-Type', '')
                if 'application/json' not in content_type:
                    return jsonify({"error": "Content-Type must be application/json"}), 400
                
                # Check content length
                if request.content_length and request.content_length > SecurityConfig.MAX_REQUEST_SIZE:
                    return jsonify({"error": "Request too large"}), 413
                
                # Try to get raw data first
                try:
                    raw_data = request.get_data(as_text=True)
                    if not raw_data or raw_data.strip() == '':
                        return jsonify({"error": "Empty request body"}), 400
                except Exception:
                    return jsonify({"error": "Unable to read request data"}), 400
                
                # Try to parse JSON
                try:
                    data = request.get_json(force=True, silent=False)
                    if data is None:
                        return jsonify({"error": "Invalid JSON data"}), 400
                except ValueError as e:
                    security_logger.warning(f"JSON parsing error from {request.remote_addr}: {str(e)}")
                    return jsonify({"error": "Malformed JSON syntax"}), 400
                except Exception as e:
                    security_logger.warning(f"Unexpected JSON error from {request.remote_addr}: {str(e)}")
                    return jsonify({"error": "Invalid request format"}), 400
                
                # Validate JSON structure
                if not isinstance(data, dict):
                    return jsonify({"error": "JSON must be an object"}), 400
                
                # Store validated data in g for use in the route
                g.validated_json = data
                
            except BadRequest as e:
                security_logger.warning(f"Bad request from {request.remote_addr}: {str(e)}")
                return jsonify({"error": "Invalid request format"}), 400
            except Exception as e:
                security_logger.warning(f"Request validation error from {request.remote_addr}: {str(e)}")
                return jsonify({"error": "Request processing failed"}), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def secure_session():
    """Decorator to ensure secure session handling"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check session timeout
            if 'last_activity' in session:
                if time.time() - session['last_activity'] > SecurityConfig.SESSION_TIMEOUT:
                    session.clear()
                    return jsonify({"error": "Session expired"}), 401
            
            # Update last activity
            session['last_activity'] = time.time()
            session.permanent = True
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def sanitize_input(fields):
    """Decorator to sanitize specific input fields"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            data = getattr(g, 'validated_json', request.get_json(silent=True) or {})
            
            try:
                for field, validator in fields.items():
                    if field in data and data[field] is not None:
                        data[field] = validator(data[field])
                
                # Store sanitized data
                g.sanitized_data = data
                
            except ValueError as e:
                security_logger.warning(f"Input validation failed: {str(e)}")
                return jsonify({"error": str(e)}), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def log_security_event(event_type, details=None):
    """Log security-related events"""
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
    user_agent = request.headers.get('User-Agent', 'Unknown')
    
    log_data = {
        'timestamp': datetime.utcnow().isoformat(),
        'event_type': event_type,
        'client_ip': client_ip,
        'user_agent': user_agent,
        'endpoint': request.endpoint,
        'method': request.method,
        'details': details
    }
    
    security_logger.warning(f"Security Event: {event_type} - {log_data}")

# Export security decorators and utilities
__all__ = [
    'SecurityHeaders', 'InputValidator', 'RateLimiter', 'CSRFProtection',
    'rate_limit', 'login_rate_limit', 'validate_json', 'secure_session', 
    'sanitize_input', 'log_security_event'
]