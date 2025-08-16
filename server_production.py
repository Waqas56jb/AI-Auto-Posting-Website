import logging
import os
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for, send_file
from flask_session import Session
import google.generativeai as genai
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import secrets
import smtplib
from email.mime.text import MIMEText
import json
import re
import threading
import uuid
import subprocess
import time
import concurrent.futures
from flask_cors import CORS
import csv
from dotenv import load_dotenv
from run import YouTubeUploader

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log') if os.path.exists('app.log') else logging.NullHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info(f"DB_NAME from env: {os.getenv('DB_NAME')}")

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Production secret key
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
if not app.secret_key:
    logger.error("SECRET_KEY is not set in environment variables")
    raise ValueError("SECRET_KEY must be set in .env file")

# Configure folders for production
app.config['UPLOAD_FOLDER'] = 'static/audio'
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
UPLOAD_FOLDER = 'static/uploads'
TRIM_FOLDER = 'static/trimmed'
UPLOAD_FOLDER_EDIT = 'static/videos'
TRIMMED_FOLDER_EDIT = 'static/trimmed'
Session(app)

# Create directories safely
for folder in [app.config['UPLOAD_FOLDER'], app.config['DOWNLOAD_FOLDER'], 
               UPLOAD_FOLDER, TRIM_FOLDER, UPLOAD_FOLDER_EDIT, TRIMMED_FOLDER_EDIT]:
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        logger.warning(f"Could not create directory {folder}: {e}")

# Expand allowed file extensions
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'mp4', 'mov', 'm4a', 'avi', 'mkv', 'webm', 'flac', 'aac', 'ogg'}
ALLOWED_EXTENSIONS_EDIT = {'mp4', 'mov'}

# Database configuration for production
db_config = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'automation'),
    'autocommit': True,
    'pool_size': 5,
    'pool_reset_session': True
}
logger.info(f"Database config: {db_config}")

# Check database connection
def check_db_connection():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE();")
        db_name = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        logger.info(f"Connected to database: {db_name}")
        return {'status': 'success', 'message': f'Database connected successfully: {db_name}'}
    except mysql.connector.Error as e:
        logger.error(f"Database connection failed: {str(e)}")
        return {'status': 'error', 'message': f'Database connection failed: {str(e)}'}

# Configure Google Gemini AI
google_api_key = os.getenv('GOOGLE_API_KEY')
if not google_api_key:
    logger.error("GOOGLE_API_KEY is not set in environment variables")
    raise ValueError("GOOGLE_API_KEY must be set in .env file")

genai.configure(api_key=google_api_key)
model = genai.GenerativeModel(
    model_name='gemini-1.5-flash',
    system_instruction="""
    You are Lucy from "Lucy & The Wealth Machine," a British social media content creator. 
    Create engaging, authentic content that resonates with your audience.
    """
)

# Production error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}")
    return jsonify({'error': 'Something went wrong'}), 500

# Health check endpoint for Railway
@app.route('/')
def health_check():
    try:
        db_status = check_db_connection()
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'database': db_status['status'],
            'version': '1.0.0'
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

# Import all your existing routes here
# (You'll need to copy the routes from your original server.py)

if __name__ == '__main__':
    # Production settings
    port = int(os.environ.get('PORT', 5000))
    
    # Load scheduled posts on startup
    try:
        # load_scheduled_posts()  # Uncomment when you have this function
        pass
    except Exception as e:
        logger.warning(f"Could not load scheduled posts: {e}")
    
    # Start scheduler
    try:
        # start_scheduler()  # Uncomment when you have this function
        pass
    except Exception as e:
        logger.warning(f"Could not start scheduler: {e}")
    
    logging.info(f"Starting Flask application on port {port}")
    
    # Production server configuration
    app.run(
        host='0.0.0.0', 
        port=port, 
        debug=False,
        threaded=True
    )
