"""
Configuration file for YouTube Video Uploader
Modify these settings to customize the uploader behavior
"""

import os
from datetime import timedelta

# API Configuration
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

# File paths for credential management
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'
SERVICE_ACCOUNT_FILE = 'service-account.json'
CREDENTIALS_CACHE_FILE = 'credentials_cache.pkl'

# Credential refresh settings
REFRESH_INTERVAL = timedelta(hours=1)  # How often to refresh credentials
CREDENTIAL_EXPIRY_BUFFER = timedelta(minutes=30)  # Refresh before expiry

# Upload settings
DEFAULT_CHUNK_SIZE = -1  # -1 for auto, or specify bytes
DEFAULT_RETRY_ATTEMPTS = 3
RETRY_DELAY_BASE = 2  # Base for exponential backoff
MAX_RETRY_DELAY = 60  # Maximum delay between retries

# Default video settings
DEFAULT_VIDEO_SETTINGS = {
    "title": "Uploaded from Python",
    "description": "This video was uploaded using the YouTube API",
    "tags": ["python", "api", "automation"],
    "category_id": "22",  # People & Blogs
    "privacy_status": "private",  # private, public, unlisted
    "embeddable": True,
    "license": "youtube",  # youtube, creativeCommon
    "public_stats_viewable": False,
    "made_for_kids": False
}

# Logging configuration
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_FILE = None  # Set to filename to log to file

# Environment variables
OAUTHLIB_INSECURE_TRANSPORT = os.getenv("OAUTHLIB_INSECURE_TRANSPORT", "0")

# Video format preferences
SUPPORTED_FORMATS = [
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"
]

# Maximum file size (in bytes) - YouTube limit is ~128GB
MAX_FILE_SIZE = 128 * 1024 * 1024 * 1024  # 128 GB

# Rate limiting (optional)
RATE_LIMIT_ENABLED = False
RATE_LIMIT_CALLS = 100  # API calls per hour
RATE_LIMIT_PERIOD = 3600  # seconds

# Security settings
ALLOW_INSECURE_TRANSPORT = False  # Set to True only for development
CREDENTIAL_ENCRYPTION = False  # Enable to encrypt cached credentials

# Error handling
IGNORE_SSL_ERRORS = False
RETRY_ON_QUOTA_EXCEEDED = True
RETRY_ON_RATE_LIMIT = True

# Development settings
DEBUG_MODE = False
VERBOSE_LOGGING = False
SAVE_UPLOAD_LOGS = False
