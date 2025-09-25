# FILE: trackeco-backend/extensions.py

from flask_compress import Compress
from api.rate_limiter import auth_rate_limit, data_modification_rate_limit, data_retrieval_rate_limit, limit

# Backward compatibility with existing Flask-Limiter decorators
limiter = type('Limiter', (), {
    'limit': limit,
    'exempt': lambda f: f  # No-op exempt decorator
})()

compress = Compress()