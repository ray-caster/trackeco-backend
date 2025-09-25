"""
Standardized error handling utilities for TrackEco API endpoints.
Provides consistent error formats, HTTP status codes, and error codes across all endpoints.
"""

import logging
from flask import jsonify
from typing import Dict, Any, Optional

# Standard error codes for consistent API responses
ERROR_CODES = {
    # Authentication errors (400-499)
    "TOKEN_MISSING": "Authentication token is missing",
    "TOKEN_INVALID": "Authentication token is invalid or expired",
    "UNAUTHORIZED": "Invalid credentials or unauthorized access",
    "USER_EXISTS": "User already exists with this email",
    "NOT_FOUND": "Resource not found",
    "EXPIRED_CODE": "Verification code has expired",
    "INVALID_CODE": "Invalid verification code",
    "NOT_VERIFIED": "User account is not verified",
    "ONBOARDING_REQUIRED": "Onboarding must be completed before this action",
    
    # Validation errors (400-499)
    "INVALID_REQUEST": "Invalid request body or parameters",
    "VALIDATION_ERROR": "Request validation failed",
    
    # Resource errors (400-499)
    "NOT_FOUND_OR_UNAUTHORIZED": "Resource not found or access denied",
    "USER_NOT_FOUND": "User not found",
    
    # System errors (500-599)
    "SERVER_ERROR": "Internal server error",
    "STORAGE_ERROR": "Error accessing storage service",
    "TASK_QUEUE_ERROR": "Error queuing background task",
    "EMAIL_FAILED": "Failed to send email",
    "DATABASE_ERROR": "Database operation failed",
    "CACHE_ERROR": "Cache operation failed",
    "EXTERNAL_SERVICE_ERROR": "External service unavailable",
    
    # Business logic errors (400-499)
    "SELF_FRIEND_REQUEST": "Cannot send friend request to yourself",
    "TEAM_UP_INELIGIBLE": "Challenge is not eligible for team up",
    "INVITATION_INVALID": "Invalid or expired invitation",
}

def create_error_response(
    error_code: str, 
    message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    status_code: int = 500
) -> tuple:
    """
    Create a standardized error response with consistent format.
    
    Args:
        error_code: One of the standard ERROR_CODES keys
        message: Optional custom message (defaults to standard message)
        details: Optional additional error details
        status_code: HTTP status code
    
    Returns:
        Tuple of (JSON response, HTTP status code)
    """
    if error_code not in ERROR_CODES:
        logging.warning(f"Unknown error code used: {error_code}")
        error_code = "SERVER_ERROR"
    
    error_message = message or ERROR_CODES[error_code]
    
    response_data = {
        "error_code": error_code,
        "message": error_message
    }
    
    if details:
        response_data["details"] = details
    
    logging.error(f"API Error [{error_code}]: {error_message} - Status: {status_code}")
    
    return jsonify(response_data), status_code

def handle_exception(e: Exception, context: str = "API endpoint") -> tuple:
    """
    Handle unexpected exceptions with standardized error response.
    
    Args:
        e: The exception that occurred
        context: Context information for logging
    
    Returns:
        Tuple of (JSON response, HTTP status code)
    """
    error_type = type(e).__name__
    error_message = str(e)
    
    logging.error(f"Unexpected error in {context}: {error_type} - {error_message}", exc_info=True)
    
    return create_error_response(
        error_code="SERVER_ERROR",
        message="An unexpected error occurred",
        details={"error_type": error_type, "error_message": error_message},
        status_code=500
    )

# Common error response shortcuts
def unauthorized_error(message: Optional[str] = None) -> tuple:
    return create_error_response("UNAUTHORIZED", message, status_code=401)

def not_found_error(message: Optional[str] = None) -> tuple:
    return create_error_response("NOT_FOUND", message, status_code=404)

def validation_error(message: Optional[str] = None, details: Optional[Dict] = None) -> tuple:
    return create_error_response("VALIDATION_ERROR", message, details, status_code=400)

def server_error(message: Optional[str] = None) -> tuple:
    return create_error_response("SERVER_ERROR", message, status_code=500)

def bad_request_error(message: Optional[str] = None) -> tuple:
    return create_error_response("INVALID_REQUEST", message, status_code=400)

# Health check utility for error handling
def health_check() -> Dict[str, Any]:
    """Standard health check response format"""
    return {"status": "OK", "details": "Error handling system is functional"}