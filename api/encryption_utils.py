"""
Encryption utilities for sensitive environment variables and API keys.
Uses Fernet symmetric encryption for securing sensitive data.
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging

# Environment variable name for the encryption key
ENCRYPTION_KEY_ENV = 'TRACKECO_ENCRYPTION_KEY'

def get_encryption_key() -> bytes:
    """
    Get or generate the encryption key from environment variables.
    If not set, generates a new key and logs a warning.
    """
    key_str = os.environ.get(ENCRYPTION_KEY_ENV)
    
    if not key_str:
        # Generate a new key if not set (for development/testing)
        logging.warning("TRACKECO_ENCRYPTION_KEY not set in environment. Generating temporary key.")
        key = Fernet.generate_key()
        return key
    
    # Convert base64 string to bytes
    try:
        return base64.urlsafe_b64decode(key_str)
    except (ValueError, TypeError):
        logging.error("Invalid encryption key format. Must be base64 encoded.")
        raise ValueError("Invalid encryption key format")

def encrypt_value(value: str) -> str:
    """
    Encrypt a string value using Fernet encryption.
    
    Args:
        value: The string value to encrypt
        
    Returns:
        Base64 encoded encrypted string
    """
    if not value:
        return ""
    
    key = get_encryption_key()
    fernet = Fernet(key)
    encrypted = fernet.encrypt(value.encode())
    return base64.urlsafe_b64encode(encrypted).decode()

def decrypt_value(encrypted_value: str) -> str:
    """
    Decrypt a base64 encoded encrypted string.
    
    Args:
        encrypted_value: Base64 encoded encrypted string
        
    Returns:
        Decrypted string value
    """
    if not encrypted_value:
        return ""
    
    try:
        key = get_encryption_key()
        fernet = Fernet(key)
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_value)
        decrypted = fernet.decrypt(encrypted_bytes)
        return decrypted.decode()
    except Exception as e:
        logging.error(f"Failed to decrypt value: {e}")
        raise ValueError("Failed to decrypt value")

def get_encrypted_env_var(env_var_name: str, default: str = None) -> str:
    """
    Get and decrypt an environment variable that contains encrypted data.
    
    Args:
        env_var_name: Name of the environment variable
        default: Default value if environment variable is not set
        
    Returns:
        Decrypted value or default if not set
    """
    encrypted_value = os.environ.get(env_var_name)
    if encrypted_value is None:
        return default
    
    try:
        return decrypt_value(encrypted_value)
    except ValueError:
        logging.warning(f"Failed to decrypt {env_var_name}, returning raw value")
        return encrypted_value  # Fallback to raw value

# Utility functions for specific API key types
def get_gemini_api_key(index: int) -> str:
    """
    Get a decrypted Gemini API key by index.
    
    Args:
        index: Index of the API key (1-4)
        
    Returns:
        Decrypted API key or None if not set
    """
    env_var_name = f"GEMINI_API_KEY_{index}"
    encrypted_key = os.environ.get(env_var_name)
    
    if encrypted_key:
        try:
            return decrypt_value(encrypted_key)
        except ValueError:
            logging.warning(f"Failed to decrypt {env_var_name}, using raw value")
            return encrypted_key
    
    return None

def get_jwt_secret_key(key_type: str) -> str:
    """
    Get a decrypted JWT secret key by type.
    
    Args:
        key_type: Type of key ('CURRENT', 'PREVIOUS', 'NEXT')
        
    Returns:
        Decrypted JWT secret key or None if not set
    """
    env_var_name = f"JWT_SECRET_KEY_{key_type}"
    encrypted_key = os.environ.get(env_var_name)
    
    if encrypted_key:
        try:
            return decrypt_value(encrypted_key)
        except ValueError:
            logging.warning(f"Failed to decrypt {env_var_name}, using raw value")
            return encrypted_key
    
    # Fallback to legacy single key
    if key_type == 'CURRENT' and os.environ.get('JWT_SECRET_KEY'):
        return os.environ.get('JWT_SECRET_KEY')
    
    return None

def get_algolia_api_key() -> str:
    """
    Get decrypted Algolia API key.
    
    Returns:
        Decrypted Algolia API key or None if not set
    """
    return get_encrypted_env_var('ALGOLIA_ADMIN_API_KEY')

def get_brevo_api_key() -> str:
    """
    Get decrypted Brevo (SendinBlue) API key.
    
    Returns:
        Decrypted Brevo API key or None if not set
    """
    return get_encrypted_env_var('BREVO_API_KEY')

# Example usage for configuration
if __name__ == "__main__":
    # Example: How to encrypt a value for environment setup
    original_value = "your-secret-api-key-here"
    encrypted = encrypt_value(original_value)
    print(f"Original: {original_value}")
    print(f"Encrypted: {encrypted}")
    print(f"Decrypted: {decrypt_value(encrypted)}")