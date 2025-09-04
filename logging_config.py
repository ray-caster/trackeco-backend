# logging_config.py
import logging
import os
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
# Use an environment variable for the log file, with a default
LOG_FILE = os.environ.get("LOG_FILE_PATH", "/tmp/trackeco_app.log")
load_dotenv()
def setup_logging():
    """Configures a rotating file logger for the entire application."""
    # Get the root logger
    logger = logging.getLogger()
    
    # Avoid adding handlers multiple times
    if logger.hasHandlers() and any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        return

    logger.setLevel(logging.INFO)
    
    # Create a rotating file handler: 10MB per file, keep last 5 files
    handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
    
    # Create a formatter and set it for the handler
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    # Add the handler to the root logger
    logger.addHandler(handler)