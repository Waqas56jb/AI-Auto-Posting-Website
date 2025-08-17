"""
Configuration file for YouTube Video Uploader
Modified for PostgreSQL and Fly.io deployment
"""

import os
from datetime import timedelta

# Flask Configuration
SECRET_KEY = 'dev-secret-key-change-in-production'
FLASK_ENV = 'development'
FLASK_DEBUG = True

# Database Configuration for PostgreSQL (Local Development)
DB_HOST = '127.0.0.1'
DB_PORT = 5432
DB_USER = 'postgres'
DB_PASSWORD = '1234'
DB_NAME = 'automation'

# Google API Configuration
GOOGLE_API_KEY = 'AIzaSyAVW6w9Fb8td1rtTgKCtTIrUEM9GTVzS6k'

# YouTube API Configuration (optional)
YOUTUBE_CLIENT_ID = 'your-youtube-client-id'
YOUTUBE_CLIENT_SECRET = 'your-youtube-client-secret'

# Email Configuration (optional)
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SMTP_USERNAME = 'your-email@gmail.com'
SMTP_PASSWORD = 'your-app-password'

# File Upload Configuration
MAX_CONTENT_LENGTH = 16777216
UPLOAD_FOLDER = 'static/uploads'

# Security Configuration
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# Logging Configuration
LOG_LEVEL = 'INFO'

# Local Development Configuration
PORT = 5000

# Database config dictionary for easy use
DB_CONFIG = {
    'host': DB_HOST,
    'port': DB_PORT,
    'user': DB_USER,
    'password': DB_PASSWORD,
    'database': DB_NAME
}
