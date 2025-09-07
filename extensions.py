# FILE: trackeco-backend/extensions.py

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    # The default key is the IP address of the user making the request.
    key_func=get_remote_address,
    # This option is passed to the Redis client to ensure it decodes responses to strings.
    storage_options={"decode_responses": True},
    # The default storage will be set in main.py from the environment variable.
    default_limits=["1000 per day", "300 per hour"] # A sensible default limit for most endpoints.
)