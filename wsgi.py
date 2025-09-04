from asgiref.wsgi import WsgiToAsgi
from main import app  # Your Flask or Django WSGI app

application = WsgiToAsgi(app)