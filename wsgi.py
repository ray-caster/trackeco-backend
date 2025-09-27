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
        
        # Handle lifespan events for ASGI servers
        if scope['type'] == 'lifespan':
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    logger.debug("Handling lifespan startup event")
                    await send({'type': 'lifespan.startup.complete'})
                elif message['type'] == 'lifespan.shutdown':
                    logger.debug("Handling lifespan shutdown event")
                    await send({'type': 'lifespan.shutdown.complete'})
                    break
            return
        
        # For HTTP requests, use the parent implementation
        return await super().__call__(scope, receive, send)

application = DebuggingWsgiToAsgi(app)
logger.debug("WSGI to ASGI application wrapper initialized")