# FILE: trackeco-backend/extensions.py

from flask_compress import Compress
from api.rate_limiter import auth_rate_limit, data_modification_rate_limit, data_retrieval_rate_limit, limit

# Backward compatibility with existing Flask-Limiter decorators
class CustomLimiter:
    def __init__(self):
        self.storage_uri = None
        
    def limit(self, rate_string):
        return limit(rate_string)
        
    def exempt(self, f):
        return f  # No-op exempt decorator
        
    def init_app(self, app):
        # No-op initialization for compatibility
        pass

limiter = CustomLimiter()

compress = Compress()