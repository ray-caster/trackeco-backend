import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

try:
    from asgiref.wsgi import WsgiToAsgi
    logger.debug("Successfully imported WsgiToAsgi from asgiref")
except ImportError as e:
    logger.error(f"Failed to import WsgiToAsgi: {e}")
    raise

from main import app  # Your Flask or Django WSGI app

def log_scope_info(scope):
    """Helper function to log scope information"""
    logger.debug(f"Scope type: {scope.get('type')}")
    logger.debug(f"Scope method: {scope.get('method')}")
    logger.debug(f"Scope path: {scope.get('path')}")
    logger.debug(f"Scope headers: {scope.get('headers')}")

class DebuggingWsgiToAsgi(WsgiToAsgi):
    async def __call__(self, scope, receive, send):
        logger.debug(f"Received scope: {scope}")
        log_scope_info(scope)
        
        # Check if this is a non-HTTP request
        if scope.get('type') != 'http':
            logger.error(f"Non-HTTP request received: {scope['type']}")
            raise ValueError(f"WSGI wrapper received a non-HTTP-request message: {scope['type']}")
        
        return await super().__call__(scope, receive, send)

application = DebuggingWsgiToAsgi(app)
logger.debug("WSGI to ASGI application wrapper initialized")