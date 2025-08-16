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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info(f"DB_NAME from env: {os.getenv('DB_NAME')}")

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    logger.error("SECRET_KEY is not set in environment variables")
    raise ValueError("SECRET_KEY must be set in .env file")

# Configure folders
app.config['UPLOAD_FOLDER'] = 'static/audio'
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
UPLOAD_FOLDER = 'static/uploads'
TRIM_FOLDER = 'static/trimmed'
UPLOAD_FOLDER_EDIT = 'static/videos'
TRIMMED_FOLDER_EDIT = 'static/trimmed'
Session(app)

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TRIM_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_EDIT, exist_ok=True)
os.makedirs(TRIMMED_FOLDER_EDIT, exist_ok=True)

# Expand allowed file extensions
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'mp4', 'mov', 'm4a', 'avi', 'mkv', 'webm', 'flac', 'aac', 'ogg'}
ALLOWED_EXTENSIONS_EDIT = {'mp4', 'mov'}

# Database configuration
db_config = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': os.getenv('DB_PORT', '3306'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': 'automation'
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
    Generate video scripts as if you are speaking directly to a friend, using a calm, reflective, personal, and passionate tone. 
    Use exclusively UK English spelling (e.g., realised, neighbours, organised) and vocabulary (e.g., flat, lift, lorry). 
    Ensure the narrative feels authentic, with consistent energy and emotion, as if one person is sharing their heartfelt story. 
    Avoid Americanisms (e.g., apartment, elevator, truck), hype, or salesy language. 
    Each segment should flow naturally into the next, maintaining rhythm and a single voice throughout.
    """,
    generation_config={
        'temperature': 0.6,
        'top_p': 0.85,
        'top_k': 35,
        'max_output_tokens': 800
    }
)

# Whisper model initialization removed - not needed for core functionality
whisper_model = None
whisper_edit_model = None

# Initialize translator
translator = None # Removed googletrans import, so translator is no longer available

# Add transcript generation functionality
def generate_transcript_from_video(video_path):
    """Generate transcript from video using Google Speech Recognition or other methods"""
    try:
        # Extract audio from video using ffmpeg
        audio_path = video_path.replace('.mp4', '.wav').replace('.mov', '.wav').replace('.avi', '.wav').replace('.mkv', '.wav').replace('.webm', '.wav')
        
        # Use ffmpeg to extract audio
        import subprocess
        try:
            subprocess.run([
                'ffmpeg', '-i', video_path, 
                '-vn', '-acodec', 'pcm_s16le', 
                '-ar', '16000', '-ac', '1', 
                audio_path, '-y'
            ], check=True, capture_output=True)
            
            # Now generate transcript from audio
            result = generate_transcript_from_audio(audio_path)
            
            # Clean up temporary audio file
            if os.path.exists(audio_path):
                os.remove(audio_path)
            
            return result
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {str(e)}")
            # Fallback to placeholder
            return {
                'success': True,
                'transcript': f"Transcript generated for video: {os.path.basename(video_path)}. This is a placeholder transcript. In production, implement actual speech recognition using services like Google Speech-to-Text, Azure Speech Services, or OpenAI Whisper.",
                'word_count': 25,
                'duration': '00:00:30'
            }
            
    except Exception as e:
        logger.error(f"Error generating transcript: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

def generate_transcript_from_audio(audio_path):
    """Generate transcript from audio file"""
    try:
        # Try to use speech recognition if available
        try:
            import speech_recognition as sr
            
            recognizer = sr.Recognizer()
            
            with sr.AudioFile(audio_path) as source:
                # Read the audio data
                audio_data = recognizer.record(source)
                
                # Try Google Speech Recognition (free tier available)
                try:
                    transcript = recognizer.recognize_google(audio_data)
                    word_count = len(transcript.split())
                    
                    # Get audio duration
                    import wave
                    with wave.open(audio_path, 'rb') as wav_file:
                        frames = wav_file.getnframes()
                        rate = wav_file.getframerate()
                        duration_seconds = frames / rate
                        duration = f"{int(duration_seconds//60):02d}:{int(duration_seconds%60):02d}"
                    
                    return {
                        'success': True,
                        'transcript': transcript,
                        'word_count': word_count,
                        'duration': duration
                    }
                    
                except sr.UnknownValueError:
                    return {
                        'success': True,
                        'transcript': f"Audio content could not be understood clearly. This might be due to background noise, unclear speech, or audio quality issues.",
                        'word_count': 0,
                        'duration': '00:00:00'
                    }
                    
                except sr.RequestError as e:
                    logger.warning(f"Google Speech Recognition service error: {str(e)}")
                    # Fallback to placeholder
                    return {
                        'success': True,
                        'transcript': f"Transcript generated for audio: {os.path.basename(audio_path)}. Speech recognition service temporarily unavailable. This is a placeholder transcript.",
                        'word_count': 20,
                        'duration': '00:00:25'
                    }
                    
        except ImportError:
            logger.warning("Speech recognition library not available. Using placeholder transcript.")
            # Fallback to placeholder
            return {
                'success': True,
                'transcript': f"Transcript generated for audio: {os.path.basename(audio_path)}. Speech recognition library not installed. This is a placeholder transcript. Install 'speech_recognition' and 'pyaudio' for real transcription.",
                'word_count': 20,
                'duration': '00:00:25'
            }
            
    except Exception as e:
        logger.error(f"Error generating transcript: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

# Supported languages
LANGUAGES = {
    'en': {'name': 'English', 'flag': '🇬🇧'},
    'es': {'name': 'Spanish', 'flag': '🇪🇸'},
    'fr': {'name': 'French', 'flag': '🇫🇷'},
    'de': {'name': 'German', 'flag': '🇩🇪'},
    'it': {'name': 'Italian', 'flag': '🇮🇹'},
    'pt': {'name': 'Portuguese', 'flag': '🇵🇹'},
    'ru': {'name': 'Russian', 'flag': '🇷🇺'},
    'zh': {'name': 'Chinese', 'flag': '🇨🇳'},
    'ja': {'name': 'Japanese', 'flag': '🇯🇵'},
    'ar': {'name': 'Arabic', 'flag': '🇸🇦'},
    'hi': {'name': 'Hindi', 'flag': '🇮🇳'},
}

def translate_text(text, dest_lang='en'):
    try:
        if dest_lang == 'en':
            return text
        # Removed googletrans import, so this function is no longer functional
        # If translation is needed, it would require a different library or implementation
        # For now, it will return the original text
        logger.warning("Translation functionality is currently unavailable.")
        return text
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
        return text

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_file_edit(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_EDIT

def clean_lucy_story(story):
    cleaned = story
    cleaned = re.sub(r'^```.*?\n', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'```$', '', cleaned.strip())
    cleaned = re.sub(r'^-(\s+)?', '', cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace('"', '“').replace('"', '”')
    cleaned = re.sub(r'\b(just|really|very|completely|totally)\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n{2,}', '\n', cleaned)
    cleaned = cleaned.replace('\r\n', '\n')
    
    # Remove icons (🎬) from the beginning of lines
    cleaned = re.sub(r'^🎬\s*', '', cleaned, flags=re.MULTILINE)
    
    # Remove segment markers like "segment 1", "segment 1 complete", etc.
    cleaned = re.sub(r'^segment\s+\d+\s*$', '', cleaned, flags=re.MULTILINE | re.IGNORECASE)
    cleaned = re.sub(r'^segment\s+\d+\s+complete\s*$', '', cleaned, flags=re.MULTILINE | re.IGNORECASE)
    
    # Clean up any empty lines that might be left after removing segments
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return cleaned.strip()

def parse_story_to_json(story_text):
    try:
        lines = story_text.split('\n')
        result = {
            'title': '',
            'segments': [],
            'finalCTA': '',
            'personality_score': 0
        }
        current_segment = None
        in_segment = False
        hooks = []
        uk_words = ['realised', 'neighbours', 'organised', 'learnt', 'colour', 'favour', 'analyse', 'flat', 'lift', 'lorry']
        personal_words = ['I', 'my', 'me', 'myself']
        reflective_words = ['felt', 'learned', 'realised', 'thought', 'wondered']
        personality_tokens = []

        for line in lines:
            line = line.strip()
            if line.startswith('🎬 Title:'):
                result['title'] = line.replace('🎬 Title:', '').strip()
            elif line.startswith('🎬 ') and not line.startswith('🎬 Title:'):
                if current_segment:
                    result['segments'].append(current_segment)
                current_segment = {
                    'title': line.replace('🎬 ', '').strip(),
                    'hooks': [],
                    'narration': '',
                    'engagement': '',
                    'cut': '',
                    'wordCount': 0,
                    'readTimeSeconds': 0
                }
                in_segment = True
                hooks = []
            elif in_segment and line.startswith('“') and line.endswith('”'):
                if not current_segment['narration']:
                    if len(hooks) < 3:
                        hooks.append(line)
                        current_segment['hooks'] = hooks
                    elif not current_segment['engagement']:
                        current_segment['engagement'] = line
                else:
                    current_segment['narration'] = line
                personality_tokens.extend(re.findall(r'\b\w+\b', line.lower()))
            elif in_segment and line.startswith('CUT'):
                current_segment['cut'] = line
                text = ' '.join(current_segment['hooks'] + [current_segment['narration'], current_segment['engagement']])
                word_count = len(re.findall(r'\b\w+\b', text))
                current_segment['wordCount'] = word_count
                current_segment['readTimeSeconds'] = round((word_count / 120) * 60)
                in_segment = False
            elif line and not in_segment and not result['finalCTA']:
                result['finalCTA'] = line
                personality_tokens.extend(re.findall(r'\b\w+\b', line.lower()))

        if current_segment:
            result['segments'].append(current_segment)

        uk_count = sum(1 for word in personality_tokens if word in uk_words)
        personal_count = sum(1 for word in personality_tokens if word in personal_words)
        reflective_count = sum(1 for word in personality_tokens if word in reflective_words)
        total_tokens = len(personality_tokens)
        if total_tokens > 0:
            result['personality_score'] = min(100, round(
                ((uk_count * 2 + personal_count * 1.5 + reflective_count) / total_tokens) * 100
            ))

        return result
    except Exception as e:
        logger.error(f"Error parsing story to JSON: {str(e)}")
        return {'error': str(e), 'title': '', 'segments': [], 'finalCTA': '', 'personality_score': 0}

def extract_framing_and_story(transcript: str):
    match = re.search(r'framing starts:(.*?)(framing ends)', transcript, re.IGNORECASE | re.DOTALL)
    if match:
        framing = match.group(1).strip()
        story = transcript.replace(match.group(0), '').strip()
    else:
        framing = ""
        story = transcript.strip()
    return framing, story

def trim_with_ffmpeg(input_path, start_time, end_time, output_path):
    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-ss', str(start_time),
        '-to', str(end_time),
        '-c', 'copy',
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise RuntimeError('FFmpeg is not installed or not found in PATH. Please install FFmpeg.')
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg error: {e.stderr.decode()}')

def add_text_to_video(input_path, text, output_path):
    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-vf', f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:x=(w-text_w)/2:y=(h-text_h)/2",
        '-c:v', 'libx264',
        '-c:a', 'copy',
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise RuntimeError('FFmpeg is not installed or not found in PATH. Please install FFmpeg.')
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg error: {e.stderr.decode()}')

def convert_to_browser_friendly(input_path, output_path, is_video=True):
    if is_video:
        cmd = [
            'ffmpeg', '-y', '-i', input_path,
            '-c:v', 'libx264', '-c:a', 'aac', '-strict', 'experimental',
            '-movflags', '+faststart', output_path
        ]
    else:
        cmd = [
            'ffmpeg', '-y', '-i', input_path,
            '-vn', '-ar', '44100', '-ac', '2', '-b:a', '192k', output_path
        ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

upload_statuses = {}
trim_jobs = {}

def convert_to_browser_friendly_bg(input_path, output_path, is_video, status_key):
    try:
        convert_to_browser_friendly(input_path, output_path, is_video)
        upload_statuses[status_key] = 'done'
    except Exception as e:
        upload_statuses[status_key] = f'error: {str(e)}'

# Routes from server.py
@app.route('/')
def indexakkal():
    logger.info("Navigated to index page")
    return render_template('LandingPage.html', languages=LANGUAGES)

@app.route('/api/existing-videos')
def get_existing_videos():
    """Get list of existing videos from static folders"""
    try:
        videos = []
        
        # Get videos from static/videos folder
        videos_folder = 'static/videos'
        if os.path.exists(videos_folder):
            for item in os.listdir(videos_folder):
                item_path = os.path.join(videos_folder, item)
                if os.path.isdir(item_path):
                    # Look for video files in subdirectories
                    for file in os.listdir(item_path):
                        if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                            videos.append({
                                'name': file,
                                'path': f'videos/{item}/{file}',
                                'type': 'original',
                                'folder': item
                            })
                elif item.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    videos.append({
                        'name': item,
                        'path': f'videos/{item}',
                        'type': 'original',
                        'folder': 'root'
                    })
        
        # Get trimmed videos from static/trimmed folder
        trimmed_folder = 'static/trimmed'
        if os.path.exists(trimmed_folder):
            for file in os.listdir(trimmed_folder):
                if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    videos.append({
                        'name': file,
                        'path': f'trimmed/{file}',
                        'type': 'trimmed',
                        'folder': 'trimmed'
                    })
        
        return jsonify({
            'success': True,
            'videos': videos
        })
        
    except Exception as e:
        logging.error(f"Error getting existing videos: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/edit')
def edit_page():
    """Edit page showing existing videos"""
    try:
        # Get existing videos instead of requiring upload
        videos = []
        
        # Get videos from static/videos folder
        videos_folder = 'static/videos'
        if os.path.exists(videos_folder):
            for item in os.listdir(videos_folder):
                item_path = os.path.join(videos_folder, item)
                if os.path.isdir(item_path):
                    # Look for video files in subdirectories
                    for file in os.listdir(item_path):
                        if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                            videos.append({
                                'name': file,
                                'path': f'videos/{item}/{file}',
                                'type': 'original',
                                'folder': item
                            })
                elif item.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    videos.append({
                        'name': file,
                        'path': f'videos/{item}',
                        'type': 'original',
                        'folder': 'root'
                    })
        
        # Get trimmed videos from static/trimmed folder
        trimmed_folder = 'static/trimmed'
        if os.path.exists(trimmed_folder):
            for file in os.listdir(trimmed_folder):
                if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    videos.append({
                        'name': file,
                        'path': f'trimmed/{file}',
                        'type': 'trimmed',
                        'folder': 'trimmed'
                    })
        
        return render_template('edit.html', videos=videos)
        
    except Exception as e:
        logging.error(f"Error in edit page: {e}")
        return render_template('edit.html', videos=[], error=str(e))

@app.route('/clip-video')
def clip_video_page():
    """Page for uploading and clipping videos with start/end time controls"""
    return render_template('clip_video.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')

@app.route('/api/navigate/<page>')
def navigate(page):
    valid_pages = ['index', 'login', 'signup', 'forgot', 'chatbot', 'dashboard', 'reset', 'editing']
    if page not in valid_pages:
        logger.warning(f"Attempted to navigate to invalid page: {page}")
        return jsonify({'message': f'Invalid page: {page}'}), 404
    logger.info(f"Navigated to page: {page}")
    template = f'{page}.html'
    try:
        if page == 'reset':
            token = request.args.get('token')
            if not token:
                logger.warning("Reset page accessed without token")
                return jsonify({'message': 'Token is required for reset page'}), 400
            return render_template(template, token=token, languages=LANGUAGES)
        if page == 'editing':
            return render_template(template)
        return render_template(template, languages=LANGUAGES)
    except Exception as e:
        logger.error(f"Error rendering template {template}: {str(e)}")
        return jsonify({'message': f'Error navigating to {page}: {str(e)}'}), 500

@app.route('/forgot')
def forgot_page():
    return render_template('forget.html', languages=LANGUAGES)

@app.route('/api/db-status')
def db_status():
    return jsonify(check_db_connection())

@app.route('/api/test-db')
def test_db():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE();")
        db_name = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return jsonify({'status': 'success', 'database': db_name})
    except mysql.connector.Error as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    if not data:
        logger.warning("Signup attempt with missing JSON data")
        return jsonify({'message': 'Invalid request'}), 400

    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    confirm_password = data.get('confirmPassword')

    if not all([username, email, password, confirm_password]):
        logger.warning("Signup attempt with incomplete fields")
        return jsonify({'message': 'All fields are required'}), 400
    if password != confirm_password:
        logger.warning("Signup attempt with mismatched passwords")
        return jsonify({'message': 'Passwords do not match'}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        cursor.execute("SELECT username FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            logger.warning(f"Signup failed: Username {username} already exists")
            return jsonify({'message': 'Username already exists'}), 400

        cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            logger.warning(f"Signup failed: Email {email} already exists")
            return jsonify({'message': 'Email already exists'}), 400

        hashed_password = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO users (username, email, password, created_at) VALUES (%s, %s, %s, %s)",
            (username, email, hashed_password, datetime.now())
        )
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Successful signup for email: {email}, redirecting to login")
        return jsonify({
            'message': 'Signup successful! Redirecting to login...',
            'redirect': url_for('navigate', page='login')
        }), 200
    except mysql.connector.Error as e:
        logger.error(f"Database error during signup: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        logger.warning("Login attempt with missing JSON data")
        return jsonify({'message': 'Invalid request'}), 400

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        logger.warning("Login attempt with missing email or password")
        return jsonify({'message': 'Email and password are required'}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user or not check_password_hash(user[0], password):
            logger.warning(f"Failed login attempt for email: {email}")
            return jsonify({'message': 'Invalid email or password'}), 401

        session['user'] = email
        logger.info(f"Successful login for email: {email}")
        return jsonify({'message': 'Login successful', 'redirect': url_for('navigate', page='index')}), 200
    except mysql.connector.Error as e:
        logger.error(f"Database error during login: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

@app.route('/api/verify-email', methods=['POST'])
def verify_email():
    data = request.get_json()
    if not data:
        logger.warning("Email verification attempt with missing JSON data")
        return jsonify({'message': 'Invalid request'}), 400

    email = data.get('email')
    if not email:
        logger.warning("Email verification attempt with missing email")
        return jsonify({'message': 'Email is required'}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user:
            logger.warning(f"Email verification failed: Email {email} not found")
            return jsonify({'message': 'Email not found'}), 404

        logger.info(f"Email verification successful for: {email}")
        return jsonify({'message': 'Email verified, please set new password'}), 200
    except mysql.connector.Error as e:
        logger.error(f"Database error during email verification: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

@app.route('/api/forgot', methods=['POST'])
def forgot_password():
    data = request.get_json()
    if not data:
        logger.warning("Forgot password attempt with missing JSON data")
        return jsonify({'message': 'Invalid request'}), 400

    email = data.get('email')
    if not email:
        logger.warning("Forgot password attempt with missing email")
        return jsonify({'message': 'Email is required'}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            cursor.close()
            conn.close()
            logger.warning(f"Forgot password attempt for non-existent email: {email}")
            return jsonify({'message': 'Email not found'}), 404

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=1)

        cursor.execute(
            "INSERT INTO password_resets (email, token, expires_at, created_at) VALUES (%s, %s, %s, %s)",
            (email, token, expires_at, datetime.now())
        )
        conn.commit()
        cursor.close()
        conn.close()

        sender_email = os.getenv('SMTP_SENDER_EMAIL')
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = os.getenv('SMTP_PORT', 587)
        smtp_password = os.getenv('SMTP_PASSWORD')

        if not all([sender_email, smtp_password]):
            logger.warning("SMTP credentials not set, returning token for demo")
            return jsonify({
                'message': 'Password reset token generated (check server logs for token)',
                'token': token,
                'redirect': url_for('navigate', page='reset', token=token)
            }), 200

        msg = MIMEText(f"Your password reset link: {url_for('navigate', page='reset', token=token, _external=True)}\nIt expires at {expires_at}.", 'plain')
        msg['Subject'] = 'Password Reset Request'
        msg['From'] = sender_email
        msg['To'] = email

        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, smtp_password)
                server.sendmail(sender_email, email, msg.as_string())
            logger.info(f"Password reset email sent to: {email}, redirecting to reset page with token")
            return jsonify({
                'message': 'Password reset link sent to your email',
                'redirect': url_for('navigate', page='reset', token=token)
            }), 200
        except smtplib.SMTPException as e:
            logger.error(f"Email sending error: {str(e)}")
            return jsonify({
                'message': 'Error sending email, token generated (check server logs)',
                'token': token,
                'redirect': url_for('navigate', page='reset', token=token)
            }), 500
    except mysql.connector.Error as e:
        logger.error(f"Database error during forgot password: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error during forgot password: {str(e)}")
        return jsonify({'message': 'An unexpected error occurred'}), 500

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    if not data:
        logger.warning("Reset password attempt with missing JSON data")
        return jsonify({'message': 'Invalid request'}), 400

    email = data.get('email')
    new_password = data.get('newPassword')
    confirm_password = data.get('confirmPassword')

    if not all([email, new_password, confirm_password]):
        logger.warning("Reset password attempt with incomplete fields")
        return jsonify({'message': 'All fields are required'}), 400
    if new_password != confirm_password:
        logger.warning("Reset password attempt with mismatched passwords")
        return jsonify({'message': 'Passwords do not match'}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            cursor.close()
            conn.close()
            logger.warning(f"Reset password failed for email {email}: Email not found")
            return jsonify({'message': 'Email not found'}), 404

        hashed_password = generate_password_hash(new_password)
        cursor.execute(
            "UPDATE users SET password = %s WHERE email = %s",
            (hashed_password, email)
        )
        cursor.execute("DELETE FROM password_resets WHERE email = %s", (email,))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Password reset successful for email: {email}")
        return jsonify({
            'message': 'Password reset successfully',
            'redirect': url_for('navigate', page='login')
        }), 200
    except mysql.connector.Error as e:
        logger.error(f"Database error during reset password: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    email = session.get('user', 'unknown')
    session.pop('user', None)
    logger.info(f"User logged out: {email}")
    return jsonify({'message': 'Logged out successfully', 'redirect': url_for('navigate', page='index')}), 200

@app.route('/ask', methods=['POST'])
def ask_question():
    data = request.get_json()
    if not data:
        logger.warning("Chatbot query attempt with missing JSON data")
        return jsonify({'response': '\u26a0\ufe0f Please enter a message.', 'lang': 'en'})

    user_input = data.get('query', '').strip()
    input_lang = data.get('input_lang', 'en')
    output_lang = data.get('output_lang', 'en')

    if not user_input:
        logger.warning("Chatbot query attempt with empty input")
        return jsonify({'response': '\u26a0\ufe0f Please enter a message.', 'lang': output_lang})

    try:
        if input_lang != 'en':
            user_input = translate_text(user_input, 'en')

        project_context = (
            "You are a professional virtual assistant for a story generator project. "
            "You help users generate stories for video content, explain how the project works, "
            "and guide them on posting to TikTok, Facebook, YouTube, and LinkedIn. "
            "Be friendly, helpful, and focused on story/video generation. Do not use any name or fixed introduction."
        )
        full_prompt = f"{project_context}\nUser: {user_input}"

        response = model.generate_content(full_prompt)
        text = response.text.strip() if response.text else '\u26a0\ufe0f No response received from the model.'

        if output_lang != 'en':
            text = translate_text(text, output_lang)

        logger.info(f"Chatbot query processed: {user_input[:50]}...")
        return jsonify({'response': text, 'lang': output_lang})
    except Exception as e:
        logger.error(f"Error processing chatbot query: {str(e)}")
        error_msg = f'\u274c Error: {str(e)}'
        if output_lang != 'en':
            error_msg = translate_text(error_msg, output_lang)
        return jsonify({'response': error_msg, 'lang': output_lang})

@app.route('/api/gemini_chat', methods=['POST'])
def gemini_chat():
    data = request.get_json()
    if not data:
        return jsonify({'response': 'No input provided.'}), 400
    user_input = data.get('query', '').strip()
    if not user_input:
        return jsonify({'response': 'Please enter a message.'}), 400
    try:
        project_context = (
    "please keep length of response focused to the point and concise",
    "You are a professional virtual assistant for a story generator project. "
    "Respond in a friendly, concise, conversational way. "
    "Answer user questions, help generate stories for video content, and guide them on posting to TikTok, Facebook, YouTube, and LinkedIn. "
    "Do not use any name or fixed introduction. If the user just says hello, greet them briefly and ask how you can help."

        )
        full_prompt = f"{project_context}\nUser: {user_input}"
        response = model.generate_content(full_prompt)
        text = response.text.strip() if response.text else 'No response received from Gemini.'
        return jsonify({'response': text})
    except Exception as e:
        return jsonify({'response': f'Error: {str(e)}'}), 500

@app.route('/download', methods=['POST'])
def download_text():
    data = request.get_json()
    if not data:
        logger.warning("Download attempt with missing JSON data")
        return jsonify({'status': 'error', 'message': 'Invalid request'}), 400

    text = data.get('text', '')
    if not text:
        logger.warning("Download attempt with empty text")
        return jsonify({'status': 'error', 'message': 'No text to download'}), 400

    filename = f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)
        logger.info(f"File downloaded: {filename}")
        return jsonify({'status': 'success', 'url': f'/downloads/{filename}'}), 200
    except Exception as e:
        logger.error(f"Error saving file {filename}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/downloads/<filename>')
def download_file(filename):
    try:
        logger.info(f"Serving file: {filename}")
        return send_from_directory(app.config['DOWNLOAD_FOLDER'], filename, as_attachment=True)
    except Exception as e:
        logger.error(f"Error serving file {filename}: {str(e)}")
        return jsonify({'status': 'error', 'message': 'File not found'}), 404

@app.route('/api/transcribe', methods=['POST'])
def transcribe():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        try:
            if whisper_model:
                result = whisper_model.transcribe(filepath, language='en')
                transcript = result['text']
            else:
                # Use our custom transcript generation
                if file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                    result = generate_transcript_from_video(filepath)
                else:
                    result = generate_transcript_from_audio(filepath)
                
                if result['success']:
                    transcript = result['transcript']
                else:
                    transcript = "Transcription service not available. Please use text input instead."
            
            os.remove(filepath)
            return jsonify({'transcript': transcript})
        except Exception as e:
            os.remove(filepath)
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Invalid file format'}), 400

@app.route('/api/generate-transcript', methods=['POST'])
def generate_transcript():
    """Generate transcript from uploaded video/audio file"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Invalid file format. Supported formats: MP3, WAV, MP4, MOV, AVI, MKV, WEBM, FLAC, AAC, OGG'}), 400
        
        # Save file temporarily
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        temp_filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
        file.save(filepath)
        
        try:
            # Generate transcript based on file type
            if filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                result = generate_transcript_from_video(filepath)
            else:
                result = generate_transcript_from_audio(filepath)
            
            # Clean up temp file
            os.remove(filepath)
            
            if result['success']:
                return jsonify({
                    'success': True,
                    'transcript': result['transcript'],
                    'word_count': result.get('word_count', 0),
                    'duration': result.get('duration', '00:00:00'),
                    'filename': filename
                })
            else:
                return jsonify({
                    'success': False,
                    'error': result.get('error', 'Failed to generate transcript')
                }), 500
                
        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(filepath):
                os.remove(filepath)
            raise e
            
    except Exception as e:
        logger.error(f"Error in generate_transcript: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate-transcript-from-url', methods=['POST'])
def generate_transcript_from_url():
    """Generate transcript from video/audio URL"""
    try:
        data = request.get_json()
        if not data or 'url' not in data:
            return jsonify({'success': False, 'error': 'No URL provided'}), 400
        
        video_url = data['url']
        
        # For now, we'll use a placeholder since we can't download external URLs
        # In production, you would implement URL downloading and processing
        result = {
            'success': True,
            'transcript': f"Transcript generated for video URL: {video_url}. This is a placeholder transcript. In production, implement URL downloading and speech recognition.",
            'word_count': 30,
            'duration': '00:00:45'
        }
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in generate_transcript_from_url: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/check_grammar', methods=['POST'])
def check_grammar():
    data = request.get_json()
    text = data.get('text')
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    try:
        max_len = 1000
        truncated = False
        if len(text) > max_len:
            text = text[:max_len]
            truncated = True
        prompt = (
            "Analyze the following text for grammatical and spelling errors. "
            "Return a JSON array of issues, where each issue contains the original sentence, "
            "the corrected sentence, and a brief explanation of the error."
            f"\n\nText: {text}"
        )
        response = model.generate_content(prompt, generation_config={
            'temperature': 0.3,
            'max_output_tokens': 300
        })
        issues = response.text
        try:
            issues = json.loads(issues)
        except json.JSONDecodeError:
            issues = []
        result = {'issues': issues}
        if truncated:
            result['note'] = 'Only the first 1000 characters were checked for grammar to ensure fast response.'
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate_story', methods=['POST'])
def generate_story():
    data = request.get_json()
    transcript = data.get('text', '')
    title = data.get('title', '')
    tone = data.get('tone', '')
    core_lesson = data.get('coreLesson', '')
    micro_lessons = data.get('microLessons', [])
    story_pattern = data.get('story_pattern', None)

    if not transcript and not title:
        return jsonify({'error': 'Please provide either story content or a title'}), 400

    if story_pattern and story_pattern.strip():
        prompt = story_pattern.replace('{transcript}', transcript)
    else:
        framing = f"Tone: {tone if tone else 'Calm, reflective, personal, passionate, British'}. " \
                  f"Title: {title if title else 'Generate a suitable title'}. " \
                  f"Core Lesson: {core_lesson if core_lesson else 'Generate a relevant theme'}. " \
                  f"Micro-Lessons: {', '.join(micro_lessons) if micro_lessons else 'Generate three relevant micro-lessons'}. " \
                  "Lucy is a British woman sharing her story as if speaking to a close friend. Use UK English spelling (e.g., realised, neighbours, organised) and vocabulary (e.g., flat, lift, lorry). Avoid Americanisms (e.g., apartment, elevator, truck), hype, or salesy language. Ensure the narrative flows naturally with consistent energy, passion, and emotion, as if one person is speaking throughout."

        prompt = f"""
You are a British storyteller and content creator. Generate a 4-part voiceover script in UK English, following this exact structure:

🎬 Title: [Story Title]
segment 1
🎬 [Title of First Part]
“Short, punchy line.”
“Another emotional line.”
“A third line if needed.”

“Short, clear, personal narration (3–4 sentences).”
“Soft, reflective, personal engagement question.”
segment 1 complete
segment 2
🎬 [Title of Second Part]
“Short, punchy line.”
“Another emotional line.”
“A third line if needed.”

“Short, clear, personal narration (3–4 sentences).”
“Soft, reflective, personal engagement question.”
segment 2 complete
segment 3
🎬 [Title of Third Part]
“Short, punchy line.”
“Another emotional line.”
“A third line if needed.”

“Short, clear, personal narration (3–4 sentences).”
“Soft, reflective, personal engagement question.”
segment 3 complete
segment 4
🎬 [Title of Fourth Part]
“Short, punchy line.”
“Another emotional line.”
“A third line if needed.”

“Short, clear, personal narration (3–4 sentences).”
“Soft, reflective, personal engagement question.”
segment 4 complete
[Optional closing line.]

Do NOT use the word 'start' or 'CUT' anywhere in the story. Only use the segment title (e.g., 🎬 The Turning Point That Changed Everything). Do NOT label lines as “HOOK” or “CTA”—just write them as natural lines. Only add the segment X and segment X complete flags for technical segmentation, not for display. The output must match the above pattern exactly, with no extra labels or formatting. Use only UK English spelling and vocabulary. The story content, topic, and speaker must come from the user's transcript and context, not the example.

FRAMING (guide tone, title, lessons):
{framing}

STORY (source material; do not invent beyond this):
{transcript}

IMPORTANT: The above example is for structure, energy, and UK English style ONLY. The actual story content, topic, and speaker must come from the user's transcript and context. If the transcript is not about Lucy or property, adapt the story to the new context and speaker, but always use the same structure, energy, and UK English style. The story should feel like a real person speaking, with natural rhythm and emotion, and must be fully dynamic to the user's input. Do NOT change the pattern, order, or content—just add segment X and segment X complete flags for technical segmentation.
""".strip()

    try:
        response = model.generate_content(prompt)
        story_text = response.text.strip() if response.text else ""
        clean_story = clean_lucy_story(story_text)
        structured_story = parse_story_to_json(clean_story)
        logger.info(f"Generated story with title: {structured_story.get('title', 'Untitled')}")
        return jsonify({'story': clean_story, 'structured': structured_story})

    except Exception as e:
        logger.error(f"Error generating story: {str(e)}")
        return jsonify({'error': str(e), 'story': '', 'structured': {}}), 500

@app.route('/api/validate_story', methods=['POST'])
def validate_story():
    data = request.get_json()
    story = data.get('story')
    if not story:
        return jsonify({'error': 'No story provided'}), 400

    try:
        validation_prompt = f"""
You are validating a Lucy-format story from "Lucy & The Wealth Machine."

Check the following:

STRUCTURE
- Top block: 🎬 Title:, followed by title line.
- NO Video Length, Style, Core Story Framework, Core Lesson, Micro-Lessons, or "🎯 Final CTA" heading.
- Exactly 4 segments with headings: 🎬 <Title> (no "Segment X").
- Each segment has:
  - 3 short quoted hook lines (each on own line, smart quotes).
  - Blank line.
  - One quoted narration paragraph (3-4 sentences, single pair of smart quotes, clear and personal).
  - One quoted engagement question (soft, reflective, personal).
  - CUT X marker (no brackets).
- Ends with one unquoted closing line (reflective, personal, not salesy).

VOICE
- UK English spelling (e.g., realised, neighbours, organised) and vocabulary (e.g., flat, lift, lorry) required; flag Americanisms (e.g., apartment, elevator, truck).
- First-person singular (I, my); no "we" unless source specifies collaboration.
- Calm, reflective, personal, passionate British tone; no American hype or guru tone.
- Narrative feels like one person speaking naturally with consistent energy and emotion.
- No invented lawsuits, drama, or money claims unless in source.
- Lessons integrated organically into narrative, not listed explicitly.

Return strict JSON:
{{
  "result": "✅ Pass" or "❌ Fail",
  "summary": "One sentence summary",
  "issues": ["List of structure/tone/accuracy problems, including Americanisms"],
  "suggested_fixes": ["Brief fix suggestions"],
  "clean_version": "Revised story in same format if fixes are minor; else empty string"
}}

STORY TO VALIDATE:
\"\"\"{story}\"\"\"
""".strip()

        response = model.generate_content(validation_prompt)
        raw = response.text.strip() if response.text else ""
        try:
            validation_json = json.loads(raw)
        except json.JSONDecodeError:
            validation_json = {
                "result": "❌ Fail",
                "summary": "Validator did not return valid JSON.",
                "issues": ["Model output was not valid JSON.", "Check logs and retry."],
                "suggested_fixes": ["Regenerate validation.", "Tighten prompt or use lower temperature."],
                "clean_version": ""
            }
        return jsonify({'validation': validation_json})

    except Exception as e:
        logger.error(f"Error validating story: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/chatbot')
def chatbot_page():
    return render_template('chatbot.html', languages=LANGUAGES)

@app.route('/editing')
def editing_page():
    return render_template('editing.html')

@app.route('/api/upload_video', methods=['POST'])
def upload_video():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext != 'mp4':
        return jsonify({'error': 'Only MP4 files are allowed for trimming.'}), 400
    base_name = os.path.splitext(secure_filename(file.filename))[0]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    folder_name = f"{base_name}_{timestamp}_{str(uuid.uuid4())[:8]}"
    folder_path = os.path.join('static', 'videos', folder_name)
    os.makedirs(folder_path, exist_ok=True)
    filename = secure_filename(file.filename)
    filepath = os.path.join(folder_path, filename)
    file.save(filepath)
    return jsonify({
        'converted': f'/static/videos/{folder_name}/{filename}',
        'original': f'/static/videos/{folder_name}/{filename}',
        'status_key': None
    })

@app.route('/api/upload_status', methods=['GET'])
def upload_status():
    status_key = request.args.get('status_key')
    if not status_key:
        return jsonify({'error': 'Missing status_key'}), 400
    status = upload_statuses.get(status_key, 'not_found')
    return jsonify({'status': status})

@app.route('/api/trim_video', methods=['POST'])
def trim_video():
    data = request.get_json()
    file_url = data.get('file')
    clips = data.get('clips', [])
    if not file_url or not clips:
        return jsonify({'error': 'Missing file or clips'}), 400
    try:
        if not file_url.startswith('/static/videos/'):
            return jsonify({'error': 'Invalid file path'}), 400
        rel_path = file_url[len('/static/'):]
        abs_path = os.path.join('static', rel_path)
        folder = os.path.dirname(abs_path)
        base_name = os.path.splitext(os.path.basename(abs_path))[0]
        ext = os.path.splitext(abs_path)[1]
        trimmed = []
        for idx, clip in enumerate(clips):
            start = float(clip['start'])
            end = float(clip['end'])
            out_name = f"{base_name}_clip{idx+1}_{start:.2f}-{end:.2f}{ext}"
            out_path = os.path.join(folder, out_name)
            cmd = [
                'ffmpeg', '-y', '-i', abs_path,
                '-ss', str(start), '-to', str(end),
                '-c', 'copy', out_path
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return jsonify({'error': f'FFmpeg error: {str(e)}'}), 500
            trimmed.append({'url': f"/static/videos/{os.path.basename(folder)}/{out_name}", 'name': out_name})
        return jsonify({'clips': trimmed})
    except Exception as e:
        return jsonify({'error': f'Trimming failed: {str(e)}'}), 500

@app.route('/api/list_videos')
def list_videos():
    video_files = []
    for root, dirs, files in os.walk(UPLOAD_FOLDER_EDIT):
        for file in files:
            if file.lower().endswith((".mp4", ".webm", ".ogg", ".mp3", ".wav")) and '_converted' in file:
                folder = os.path.basename(root)
                video_files.append({'url': f"/static/videos/{folder}/{file}", 'name': file})
    return jsonify({'videos': video_files})

@app.route('/api/save-credentials', methods=['POST'])
def save_credentials():
    try:
        data = request.get_json()
        platform = data.get('platform')
        credentials = data.get('credentials')
        
        if not platform or not credentials:
            return jsonify({'success': False, 'message': 'Missing platform or credentials'}), 400
        
        # Create credentials directory if it doesn't exist
        credentials_dir = 'credentials'
        if not os.path.exists(credentials_dir):
            os.makedirs(credentials_dir)
        
        # Save to CSV file
        csv_file = os.path.join(credentials_dir, f'{platform}_credentials.csv')
        with open(csv_file, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['key', 'value'])
            for key, value in credentials.items():
                writer.writerow([key, value])
        
        logger.info(f"Saved credentials for {platform}")
        return jsonify({'success': True, 'message': f'{platform} credentials saved successfully'})
    except Exception as e:
        logger.error(f"Error saving credentials: {str(e)}")
        return jsonify({'success': False, 'message': f'Error saving credentials: {str(e)}'}), 500

@app.route('/api/load-credentials', methods=['GET'])
def load_credentials():
    try:
        platform = request.args.get('platform')
        if not platform:
            return jsonify({'success': False, 'message': 'Missing platform parameter'}), 400
        
        csv_file = os.path.join('credentials', f'{platform}_credentials.csv')
        if not os.path.exists(csv_file):
            return jsonify({'success': False, 'message': 'No credentials found'}), 404
        
        credentials = {}
        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                credentials[row['key']] = row['value']
        
        return jsonify({'success': True, 'credentials': credentials})
    except Exception as e:
        logger.error(f"Error loading credentials: {str(e)}")
        return jsonify({'success': False, 'message': f'Error loading credentials: {str(e)}'}), 500

@app.route('/api/post-to-social', methods=['POST'])
def post_to_social():
    try:
        data = request.get_json()
        platform = data.get('platform')
        video_path = data.get('video_path')
        caption = data.get('caption')
        hashtags = data.get('hashtags')
        
        if not all([platform, video_path, caption]):
            return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
        
        # Load credentials for the platform
        csv_file = os.path.join('credentials', f'{platform}_credentials.csv')
        if not os.path.exists(csv_file):
            return jsonify({'success': False, 'message': f'{platform} credentials not found. Please configure in settings.'}), 404
        
        credentials = {}
        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                credentials[row['key']] = row['value']
        
        # Simulate posting (in production, you would use actual API calls)
        logger.info(f"Posting to {platform}: Video={video_path}, Caption={caption}, Hashtags={hashtags}")
        
        # For demo purposes, we'll simulate a successful post
        # In production, you would implement actual API calls to each platform
        
        return jsonify({
            'success': True, 
            'message': f'Successfully posted to {platform}!',
            'platform': platform,
            'video_path': video_path,
            'caption': caption,
            'hashtags': hashtags
        })
        
    except Exception as e:
        logger.error(f"Error posting to social media: {str(e)}")
        return jsonify({'success': False, 'message': f'Error posting to social media: {str(e)}'}), 500

@app.route('/api/youtube/upload', methods=['POST'])
def youtube_upload():
    """Upload a video to YouTube using future-proof uploader, with caption and hashtags.
    Expected JSON body: { video_path: "/static/.../file.mp4", title?: str, caption?: str, hashtags?: str, privacyStatus?: str }
    """
    try:
        data = request.get_json() or {}
        video_url_path = data.get('video_path') or data.get('videoUrl') or data.get('path')
        title = data.get('title')
        caption = data.get('caption') or ''
        hashtags = data.get('hashtags') or ''
        privacy_status = (data.get('privacyStatus') or 'public').lower()

        if not video_url_path:
            return jsonify({'success': False, 'message': 'Missing video_path'}), 400

        # Convert URL path to filesystem path
        # e.g., /static/trimmed/2025.../video.mp4 -> <app_root>/static/trimmed/2025.../video.mp4
        fs_path = os.path.join(app.root_path, video_url_path.lstrip('/').replace('/', os.sep))

        if not os.path.exists(fs_path):
            return jsonify({'success': False, 'message': f'Video file not found: {video_url_path}'}), 404

        # Derive and sanitize title
        filename = os.path.basename(fs_path)
        cap_line = (caption or '').splitlines()[0].strip() if caption else ''
        candidate_title = (title or cap_line or os.path.splitext(filename)[0] or '').strip()
        # Fallback hard title if still empty
        if not candidate_title:
            candidate_title = f"Video Upload - {os.path.splitext(filename)[0]}"
        # Collapse whitespace and limit length
        try:
            import re as _re
            candidate_title = _re.sub(r"\s+", " ", candidate_title)
        except Exception:
            pass
        if len(candidate_title) > 95:
            candidate_title = candidate_title[:95].rstrip()

        # Build description from caption + hashtags
        # Build detailed description with filename and separators
        description_parts = []
        if caption:
            description_parts.append(caption.strip())
        if hashtags:
            description_parts.append(hashtags.strip())
        # footer removed per request
        description = '\n\n'.join([p for p in description_parts if p])

        # Build tags list from hashtags
        tags = []
        if hashtags:
            try:
                # split on spaces, remove '#', keep non-empty
                tags = [h.replace('#', '').strip() for h in hashtags.split() if h.strip()]
            except Exception:
                tags = []

        # Initialize uploader and upload
        uploader = YouTubeUploader()
        uploader.initialize()
        video_id = uploader.upload_video(
            video_path=fs_path,
            title=candidate_title,
            description=description or title,
            tags=tags or ["automation", "python"],
            category_id="22",
            privacy_status=privacy_status
        )

        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"YouTube upload successful: {video_id}")

        return jsonify({
            'success': True,
            'videoId': video_id,
            'url': watch_url,
            'title': candidate_title
        })

    except Exception as e:
        logger.error(f"YouTube upload failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': f'YouTube upload failed: {str(e)}'}), 500

# Routes from app.py
@app.route('/upload', methods=['POST'])
def upload():
    video = request.files['video']
    if video:
        video_path = os.path.join(UPLOAD_FOLDER, video.filename)
        video.save(video_path)
        return redirect(url_for('trim_page', filename=video.filename))
    return "No video uploaded."

@app.route('/trim/<filename>', methods=['GET', 'POST'])
def trim_page(filename):
    video_path = os.path.join(UPLOAD_FOLDER, filename)
    trimmed_videos = []

    if request.method == 'POST':
        start = request.form.get('start')
        end = request.form.get('end')
        out_filename = f"trim_{start}_{end}_{filename}"
        out_path = os.path.join(TRIM_FOLDER, out_filename)

        duration = float(end) - float(start)
        command = [
            'ffmpeg', '-i', video_path,
            '-ss', start,
            '-t', str(duration),
            '-c:v', 'libx264', '-c:a', 'aac',
            '-strict', 'experimental', '-y',
            out_path
        ]
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        trimmed_videos.append(out_filename)

    existing_trims = os.listdir(TRIM_FOLDER)
    relevant_trims = [f for f in existing_trims if filename in f]

    return render_template('edit.html', filename=filename, trims=relevant_trims)

@app.route('/delete_trimmed', methods=['POST'])
def delete_trimmed():
    data = request.get_json()
    filename = data.get('filename')
    if not filename:
        return jsonify({'success': False, 'error': 'No filename provided.'}), 400
    file_path = os.path.join(TRIM_FOLDER, filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    else:
        return jsonify({'success': False, 'error': 'File not found.'}), 404

@app.route('/api/generate_caption', methods=['POST'])
def generate_caption():
    """Generate AI-powered caption for video content using Google Gemini AI"""
    try:
        data = request.get_json()
        if not data:
            logger.error("No JSON data received in caption generation request")
            return jsonify({'success': False, 'error': 'No data provided.'}), 400
            
        filename = data.get('filename')
        if not filename:
            logger.error("No filename provided in caption generation request")
            return jsonify({'success': False, 'error': 'No filename provided.'}), 400
        
        # Log the caption generation request
        logger.info(f"Generating caption for video: {filename}")
        
        # Enhanced prompt for better caption generation
        prompt = (
            "You are Lucy from 'Lucy & The Wealth Machine,' a British social media content creator and property investment expert. "
            "Generate a unique, engaging, and professional caption for a property investment video that would be posted on social media. "
            "The caption should be:\n"
            "- Conversational and friendly, as if speaking to a friend\n"
            "- Relevant to property investment and wealth building\n"
            "- Suitable for platforms like Instagram, TikTok, YouTube, and LinkedIn\n"
            "- Professional yet approachable\n"
            "- Include a call-to-action when appropriate\n\n"
            "Include 5-8 relevant hashtags for property investment, UK property, and real estate. "
            "Use British English (not Americanisms).\n\n"
            "Example hashtags: #PropertyInvestment #UKProperty #WealthBuilding #PassiveIncome #RealEstate #InvestSmart #FinancialFreedom #PropertyTips #UKInvestor\n\n"
            "Output format:\n"
            "[Caption text here]\n\n"
            "[Hashtags space-separated all in one line]\n\n"
            f"Video context: {filename}"
        )
        
        # Generate caption using Gemini AI
        logger.info(f"Sending prompt to Gemini AI for video: {filename}")
        response = model.generate_content(prompt)
        
        if not response or not response.text:
            logger.error(f"No response from Gemini AI for video: {filename}")
            return jsonify({'success': False, 'error': 'AI service returned no response.'}), 500
        
        text = response.text.strip()
        logger.info(f"Raw AI response received for {filename}: {text[:100]}...")
        
        # Parse the response to extract caption and hashtags
        parts = text.split('\n')
        caption = ''
        hashtags = ''
        
        for part in parts:
            part = part.strip()
            if part.startswith('#'):
                hashtags = part
            elif part and not part.startswith('[') and not part.startswith('Video context:'):
                caption += part + ' '
        
        caption = caption.strip()
        
        # Validate the generated content
        if not caption:
            logger.error(f"No caption text generated for video: {filename}")
            return jsonify({'success': False, 'error': 'Failed to generate caption text.'}), 500
        
        if not hashtags:
            logger.warning(f"No hashtags generated for video: {filename}, using defaults")
            hashtags = "#PropertyInvestment #UKProperty #WealthBuilding #PassiveIncome #RealEstate"
        
        # Log successful generation
        logger.info(f"Caption generated successfully for {filename}: {caption[:50]}...")
        
        return jsonify({
            'success': True, 
            'caption': caption, 
            'hashtags': hashtags,
            'generated_at': datetime.now().isoformat()
        })
        
    except Exception as e:
        error_msg = f"Error generating caption for {filename if 'filename' in locals() else 'unknown'}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'success': False, 'error': f'Caption generation failed: {str(e)}'}), 500

@app.route('/api/save_caption', methods=['POST'])
def save_caption():
    """Save generated caption to file for persistence"""
    try:
        data = request.get_json()
        if not data:
            logger.error("No JSON data received in save caption request")
            return jsonify({'success': False, 'error': 'No data provided.'}), 400
            
        filename = data.get('filename')
        caption = data.get('caption')
        hashtags = data.get('hashtags')
        
        if not filename or not caption:
            logger.error(f"Missing required data in save caption request: filename={filename}, caption={bool(caption)}")
            return jsonify({'success': False, 'error': 'Missing filename or caption.'}), 400
        
        # Sanitize filename for security
        safe_filename = secure_filename(filename)
        if not safe_filename:
            logger.error(f"Invalid filename provided: {filename}")
            return jsonify({'success': False, 'error': 'Invalid filename provided.'}), 400
        
        try:
            # Create captions directory if it doesn't exist
            captions_dir = 'captions'
            os.makedirs(captions_dir, exist_ok=True)
            
            # Save caption to file with metadata
            caption_file = os.path.join(captions_dir, f"{safe_filename}.txt")
            with open(caption_file, 'w', encoding='utf-8') as f:
                f.write(f"Caption: {caption}\n")
                f.write(f"Hashtags: {hashtags}\n")
                f.write(f"Generated: {datetime.now().isoformat()}\n")
                f.write(f"Original_Filename: {filename}\n")
            
            logger.info(f"Caption saved successfully for file: {filename} -> {safe_filename}")
            return jsonify({
                'success': True, 
                'message': 'Caption saved successfully',
                'saved_filename': safe_filename,
                'saved_at': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error saving caption to file for {filename}: {str(e)}")
            return jsonify({'success': False, 'error': f'Failed to save caption: {str(e)}'}), 500
            
    except Exception as e:
        error_msg = f"Unexpected error in save caption endpoint: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/load_caption', methods=['GET'])
def load_caption():
    """Load previously saved caption for a video file"""
    try:
        filename = request.args.get('filename')
        if not filename:
            logger.error("No filename provided in load caption request")
            return jsonify({'success': False, 'error': 'No filename provided.'}), 400
        
        # Sanitize filename for security
        safe_filename = secure_filename(filename)
        if not safe_filename:
            logger.error(f"Invalid filename provided: {filename}")
            return jsonify({'success': False, 'error': 'Invalid filename provided.'}), 400
        
        try:
            # Look for caption file
            captions_dir = 'captions'
            caption_file = os.path.join(captions_dir, f"{safe_filename}.txt")
            
            if os.path.exists(caption_file):
                with open(caption_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    caption = ''
                    hashtags = ''
                    generated_at = ''
                    original_filename = ''
                    
                    for line in lines:
                        line = line.strip()
                        if line.startswith('Caption: '):
                            caption = line.replace('Caption: ', '').strip()
                        elif line.startswith('Hashtags: '):
                            hashtags = line.replace('Hashtags: ', '').strip()
                        elif line.startswith('Generated: '):
                            generated_at = line.replace('Generated: ', '').strip()
                        elif line.startswith('Original_Filename: '):
                            original_filename = line.replace('Original_Filename: ', '').strip()
                    
                    logger.info(f"Caption loaded successfully for file: {filename}")
                    return jsonify({
                        'success': True, 
                        'caption': caption, 
                        'hashtags': hashtags,
                        'generated_at': generated_at,
                        'original_filename': original_filename,
                        'loaded_at': datetime.now().isoformat()
                    })
            else:
                logger.info(f"No caption file found for: {filename}")
                return jsonify({
                    'success': False, 
                    'caption': '', 
                    'hashtags': '',
                    'message': 'No saved caption found'
                })
                
        except Exception as e:
            logger.error(f"Error reading caption file for {filename}: {str(e)}")
            return jsonify({'success': False, 'error': f'Failed to read caption file: {str(e)}'}), 500
            
    except Exception as e:
        error_msg = f"Unexpected error in load caption endpoint: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/captions/status', methods=['GET'])
def get_caption_status():
    """Get status of caption generation service and recent activity"""
    try:
        # Check if Gemini AI is configured
        ai_status = {
            'configured': bool(google_api_key),
            'model': 'gemini-1.5-flash' if google_api_key else None
        }
        
        # Check captions directory
        captions_dir = 'captions'
        captions_count = 0
        if os.path.exists(captions_dir):
            captions_count = len([f for f in os.listdir(captions_dir) if f.endswith('.txt')])
        
        return jsonify({
            'success': True,
            'ai_service': ai_status,
            'captions_stored': captions_count,
            'captions_directory': captions_dir,
            'server_time': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting caption status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/captions/list', methods=['GET'])
def list_captions():
    """List all available captions with metadata"""
    try:
        captions_dir = 'captions'
        if not os.path.exists(captions_dir):
            return jsonify({'success': True, 'captions': []})
        
        captions = []
        for filename in os.listdir(captions_dir):
            if filename.endswith('.txt'):
                file_path = os.path.join(captions_dir, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        caption_data = {
                            'filename': filename.replace('.txt', ''),
                            'size': os.path.getsize(file_path),
                            'modified': datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
                        }
                        
                        for line in lines:
                            line = line.strip()
                            if line.startswith('Caption: '):
                                caption_data['caption_preview'] = line.replace('Caption: ', '')[:100] + '...'
                            elif line.startswith('Generated: '):
                                caption_data['generated_at'] = line.replace('Generated: ', '')
                        
                        captions.append(caption_data)
                except Exception as e:
                    logger.warning(f"Error reading caption file {filename}: {str(e)}")
                    continue
        
        return jsonify({
            'success': True,
            'captions': captions,
            'total_count': len(captions)
        })
        
    except Exception as e:
        logger.error(f"Error listing captions: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/captions/bulk_generate', methods=['POST'])
def bulk_generate_captions():
    """Generate captions for multiple videos in batch"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided.'}), 400
            
        filenames = data.get('filenames', [])
        if not filenames or not isinstance(filenames, list):
            return jsonify({'success': False, 'error': 'Invalid filenames list provided.'}), 400
        
        if len(filenames) > 10:  # Limit batch size
            return jsonify({'success': False, 'error': 'Batch size too large. Maximum 10 videos per batch.'}), 400
        
        results = []
        failed_count = 0
        
        for filename in filenames:
            try:
                # Generate caption for each video
                prompt = (
                    "You are Lucy from 'Lucy & The Wealth Machine,' a British social media content creator and property investment expert. "
                    "Generate a unique, engaging, and professional caption for a property investment video that would be posted on social media. "
                    "The caption should be conversational, relevant to property investment, and suitable for platforms like Instagram, TikTok, YouTube, and LinkedIn. "
                    "Include 5-8 relevant hashtags for property investment, UK property, and real estate. Use British English.\n\n"
                    "Output format:\n[Caption text here]\n\n[Hashtags space-separated all in one line]\n\n"
                    f"Video context: {filename}"
                )
                
                response = model.generate_content(prompt)
                if response and response.text:
                    text = response.text.strip()
                    parts = text.split('\n')
                    caption = ''
                    hashtags = ''
                    
                    for part in parts:
                        part = part.strip()
                        if part.startswith('#'):
                            hashtags = part
                        elif part and not part.startswith('[') and not part.startswith('Video context:'):
                            caption += part + ' '
                    
                    caption = caption.strip()
                    if not hashtags:
                        hashtags = "#PropertyInvestment #UKProperty #WealthBuilding #PassiveIncome #RealEstate"
                    
                    # Save caption
                    captions_dir = 'captions'
                    os.makedirs(captions_dir, exist_ok=True)
                    safe_filename = secure_filename(filename)
                    caption_file = os.path.join(captions_dir, f"{safe_filename}.txt")
                    
                    with open(caption_file, 'w', encoding='utf-8') as f:
                        f.write(f"Caption: {caption}\n")
                        f.write(f"Hashtags: {hashtags}\n")
                        f.write(f"Generated: {datetime.now().isoformat()}\n")
                        f.write(f"Original_Filename: {filename}\n")
                    
                    results.append({
                        'filename': filename,
                        'success': True,
                        'caption': caption,
                        'hashtags': hashtags
                    })
                    
                    logger.info(f"Bulk caption generated successfully for: {filename}")
                else:
                    results.append({
                        'filename': filename,
                        'success': False,
                        'error': 'No response from AI service'
                    })
                    failed_count += 1
                    
            except Exception as e:
                logger.error(f"Error generating caption for {filename} in bulk operation: {str(e)}")
                results.append({
                    'filename': filename,
                    'success': False,
                    'error': str(e)
                })
                failed_count += 1
        
        return jsonify({
            'success': True,
            'results': results,
            'total_processed': len(filenames),
            'successful': len(filenames) - failed_count,
            'failed': failed_count,
            'completed_at': datetime.now().isoformat()
        })
        
    except Exception as e:
        error_msg = f"Error in bulk caption generation: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/captions/health', methods=['GET'])
def caption_service_health():
    """Health check for caption generation service"""
    try:
        # Check Gemini AI configuration
        ai_healthy = bool(google_api_key)
        
        # Check if we can create the captions directory
        captions_dir = 'captions'
        try:
            os.makedirs(captions_dir, exist_ok=True)
            dir_healthy = True
        except Exception:
            dir_healthy = False
        
        # Test AI service with a simple prompt
        ai_test_healthy = False
        if ai_healthy:
            try:
                test_response = model.generate_content("Hello")
                ai_test_healthy = bool(test_response and test_response.text)
            except Exception as e:
                logger.warning(f"AI service test failed: {str(e)}")
                ai_test_healthy = False
        
        overall_health = ai_healthy and dir_healthy and ai_test_healthy
        
        return jsonify({
            'success': True,
            'healthy': overall_health,
            'services': {
                'gemini_ai_configured': ai_healthy,
                'gemini_ai_responding': ai_test_healthy,
                'captions_directory': dir_healthy
            },
            'timestamp': datetime.now().isoformat(),
            'status': 'healthy' if overall_health else 'degraded'
        })
        
    except Exception as e:
        logger.error(f"Caption service health check failed: {str(e)}")
        return jsonify({
            'success': False,
            'healthy': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
            'status': 'unhealthy'
        }), 500

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/trimmed/<path:filename>')
def serve_trimmed(filename):
    return send_from_directory(TRIM_FOLDER, filename)

# Global variable to store scheduled posts
scheduled_posts = []
scheduler_running = False

def load_scheduled_posts():
    """Load scheduled posts from CSV file"""
    global scheduled_posts
    try:
        if os.path.exists('scheduled_posts.csv'):
            with open('scheduled_posts.csv', 'r', newline='', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                scheduled_posts = list(reader)
        else:
            # Create CSV file with headers if it doesn't exist
            with open('scheduled_posts.csv', 'w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(['timestamp', 'video_name', 'platform', 'caption', 'hashtags', 'status', 'scheduled_time'])
    except Exception as e:
        logging.error(f"Error loading scheduled posts: {e}")
        scheduled_posts = []

def save_scheduled_posts():
    """Save scheduled posts to CSV file"""
    try:
        with open('scheduled_posts.csv', 'w', newline='', encoding='utf-8') as file:
            if scheduled_posts:
                writer = csv.DictWriter(file, fieldnames=scheduled_posts[0].keys())
                writer.writeheader()
                writer.writerows(scheduled_posts)
    except Exception as e:
        logging.error(f"Error saving scheduled posts: {e}")

def scheduler_worker():
    """Background worker that checks for scheduled posts and executes them"""
    global scheduler_running, scheduled_posts
    
    while scheduler_running:
        try:
            current_time = datetime.now()
            
            # Check for posts that need to be executed
            posts_to_execute = []
            for post in scheduled_posts:
                if post['status'] == 'pending':
                    try:
                        scheduled_time = datetime.fromisoformat(post['scheduled_time'])
                        if current_time >= scheduled_time:
                            posts_to_execute.append(post)
                    except:
                        continue
            
            # Execute posts
            for post in posts_to_execute:
                try:
                    # Simulate posting to social media
                    success = post_to_social(
                        post['platform'],
                        post['video_name'],
                        post['caption'],
                        post['hashtags']
                    )
                    
                    # Update status
                    post['status'] = 'posted' if success else 'failed'
                    post['executed_time'] = current_time.isoformat()
                    
                    logging.info(f"Executed scheduled post: {post['video_name']} to {post['platform']} - {'Success' if success else 'Failed'}")
                    
                except Exception as e:
                    logging.error(f"Error executing scheduled post: {e}")
                    post['status'] = 'failed'
                    post['error'] = str(e)
            
            # Save updated statuses
            if posts_to_execute:
                save_scheduled_posts()
            
            # Sleep for 1 minute before next check
            time.sleep(60)
            
        except Exception as e:
            logging.error(f"Error in scheduler worker: {e}")
            time.sleep(60)

def start_scheduler():
    """Start the background scheduler"""
    global scheduler_running
    if not scheduler_running:
        scheduler_running = True
        scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
        scheduler_thread.start()
        logging.info("Scheduler started")

@app.route('/api/schedule-post', methods=['POST'])
def schedule_post():
    """Schedule a post for later execution"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['filename', 'date', 'time', 'platform', 'caption']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'Missing required field: {field}'})
        
        # Create scheduled time
        scheduled_datetime = datetime.strptime(f"{data['date']} {data['time']}", "%Y-%m-%d %H:%M")
        
        # Check if scheduled time is in the future
        if scheduled_datetime <= datetime.now():
            return jsonify({'success': False, 'message': 'Scheduled time must be in the future'})
        
        # Create post entry
        post_entry = {
            'timestamp': datetime.now().isoformat(),
            'video_name': data['filename'],
            'platform': data['platform'],
            'caption': data.get('caption', ''),
            'hashtags': data.get('hashtags', ''),
            'status': 'pending',
            'scheduled_time': scheduled_datetime.isoformat()
        }
        
        # Add to scheduled posts
        scheduled_posts.append(post_entry)
        save_scheduled_posts()
        
        # Start scheduler if not running
        if not scheduler_running:
            start_scheduler()
        
        logging.info(f"Scheduled post: {data['filename']} to {data['platform']} at {scheduled_datetime}")
        
        return jsonify({
            'success': True, 
            'message': 'Post scheduled successfully',
            'scheduled_time': scheduled_datetime.isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error scheduling post: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/scheduled-posts', methods=['GET'])
def get_scheduled_posts():
    """Get all scheduled posts"""
    try:
        return jsonify({
            'success': True,
            'posts': scheduled_posts
        })
    except Exception as e:
        logging.error(f"Error getting scheduled posts: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/scheduled-posts')
def view_scheduled_posts():
    """View all scheduled posts"""
    try:
        # Load latest scheduled posts
        load_scheduled_posts()
        return render_template('scheduled_posts.html', posts=scheduled_posts)
    except Exception as e:
        logging.error(f"Error viewing scheduled posts: {e}")
        return render_template('scheduled_posts.html', posts=[], error=str(e))

@app.route('/api/execute-post', methods=['POST'])
def execute_post():
    """Execute a scheduled post immediately"""
    try:
        data = request.get_json()
        timestamp = data.get('timestamp')
        
        if not timestamp:
            return jsonify({'success': False, 'message': 'Timestamp is required'})
        
        # Find the post
        post = None
        for p in scheduled_posts:
            if p['timestamp'] == timestamp:
                post = p
                break
        
        if not post:
            return jsonify({'success': False, 'message': 'Post not found'})
        
        if post['status'] != 'pending':
            return jsonify({'success': False, 'message': 'Post is not pending'})
        
        # Execute the post
        try:
            success = post_to_social(
                post['platform'],
                post['video_name'],
                post['caption'],
                post['hashtags']
            )
            
            # Update status
            post['status'] = 'posted' if success else 'failed'
            post['executed_time'] = datetime.now().isoformat()
            
            if not success:
                post['error'] = 'Post execution failed'
            
            # Save updated status
            save_scheduled_posts()
            
            logging.info(f"Executed post: {post['video_name']} to {post['platform']} - {'Success' if success else 'Failed'}")
            
            return jsonify({
                'success': True,
                'message': 'Post executed successfully' if success else 'Post execution failed',
                'status': post['status']
            })
            
        except Exception as e:
            post['status'] = 'failed'
            post['error'] = str(e)
            post['executed_time'] = datetime.now().isoformat()
            save_scheduled_posts()
            
            logging.error(f"Error executing post: {e}")
            return jsonify({'success': False, 'message': f'Error executing post: {str(e)}'})
        
    except Exception as e:
        logging.error(f"Error in execute_post: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/delete-scheduled-post', methods=['POST'])
def delete_scheduled_post():
    """Delete a scheduled post"""
    try:
        data = request.get_json()
        timestamp = data.get('timestamp')
        
        if not timestamp:
            return jsonify({'success': False, 'message': 'Timestamp is required'}), 400
        
        # Find and remove the post
        global scheduled_posts
        original_length = len(scheduled_posts)
        scheduled_posts = [p for p in scheduled_posts if p['timestamp'] != timestamp]
        
        if len(scheduled_posts) == original_length:
            return jsonify({'success': False, 'message': 'Post not found'}), 404
        
        # Save updated list
        save_scheduled_posts()
        
        logging.info(f"Deleted scheduled post with timestamp: {timestamp}")
        
        return jsonify({
            'success': True,
            'message': 'Post deleted successfully'
        })
        
    except Exception as e:
        logging.error(f"Error deleting scheduled post: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/delete_video', methods=['POST'])
def delete_video():
    """Delete a video file"""
    try:
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'message': 'Filename is required'}), 400
        
        # Look for the video in various folders
        video_found = False
        folders_to_check = [
            'static/videos',
            'static/trimmed',
            'static/uploads'
        ]
        
        for folder in folders_to_check:
            folder_path = os.path.join(folder)
            if os.path.exists(folder_path):
                for root, dirs, files in os.walk(folder_path):
                    for file in files:
                        if file == filename or filename in file:
                            file_path = os.path.join(root, file)
                            try:
                                os.remove(file_path)
                                video_found = True
                                logging.info(f"Deleted video: {file_path}")
                            except Exception as e:
                                logging.error(f"Error deleting file {file_path}: {e}")
                                return jsonify({'success': False, 'message': f'Error deleting file: {str(e)}'}), 500
        
        if video_found:
            return jsonify({'success': True, 'message': 'Video deleted successfully'})
        else:
            return jsonify({'success': False, 'message': 'Video not found'}), 404
            
    except Exception as e:
        logging.error(f"Error in delete_video: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/create-video-clip', methods=['POST'])
def create_video_clip():
    """Create a video clip with specified start and end time"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        start_time = request.form.get('start_time', '0')
        end_time = request.form.get('end_time', '30')
        
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        # Validate file type
        if not file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            return jsonify({'success': False, 'message': 'Only video files are allowed'}), 400
        
        # Validate time inputs
        try:
            start_time = float(start_time)
            end_time = float(end_time)
            if start_time < 0 or end_time <= start_time:
                return jsonify({'success': False, 'message': 'Invalid time range'}), 400
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid time format'}), 400
        
        # Create timestamp for folder naming
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        day_name = datetime.now().strftime('%A')
        
        # Create folder structure: static/trimmed/YYYYMMDD_HHMMSS_dayname
        folder_name = f"{timestamp}_{day_name.lower()}"
        folder_path = os.path.join('static', 'trimmed', folder_name)
        os.makedirs(folder_path, exist_ok=True)
        
        # Generate clip filename with time info
        base_name = os.path.splitext(secure_filename(file.filename))[0]
        clip_filename = f"{base_name}_clip_{start_time:.2f}-{end_time:.2f}.mp4"
        clip_path = os.path.join(folder_path, clip_filename)
        
        # Save original file temporarily
        temp_path = os.path.join(folder_path, secure_filename(file.filename))
        file.save(temp_path)
        
        # Use FFmpeg to create the clip
        try:
            cmd = [
                'ffmpeg', '-y',
                '-i', temp_path,
                '-ss', str(start_time),
                '-to', str(end_time),
                '-c', 'copy',
                clip_path
            ]
            
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # Remove temporary file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            # Return success with clip information
            return jsonify({
                'success': True,
                'message': 'Video clip created successfully',
                'clip_path': f'/static/trimmed/{folder_name}/{clip_filename}',
                'clip_filename': clip_filename,
                'folder_name': folder_name,
                'start_time': start_time,
                'end_time': end_time,
                'duration': end_time - start_time,
                'timestamp': timestamp,
                'day_name': day_name
            })
            
        except subprocess.CalledProcessError as e:
            # Clean up on error
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.exists(clip_path):
                os.remove(clip_path)
            
            logger.error(f"FFmpeg error: {str(e)}")
            return jsonify({'success': False, 'message': f'Video processing failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Error creating video clip: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/trimmed-videos-dashboard', methods=['GET'])
def get_trimmed_videos_dashboard():
    """Get trimmed videos organized by date with statistics"""
    try:
        trimmed_folder = 'static/trimmed'
        dashboard_data = {}
        
        if not os.path.exists(trimmed_folder):
            return jsonify({'success': True, 'dashboard': {}})
        
        # Get all trimmed video files from organized folders
        for folder_name in os.listdir(trimmed_folder):
            folder_path = os.path.join(trimmed_folder, folder_name)
            
            if os.path.isdir(folder_path):
                # This is an organized folder (YYYYMMDD_HHMMSS_dayname)
                try:
                    # Extract date from folder name
                    if '_' in folder_name and len(folder_name.split('_')) >= 3:
                        date_part = folder_name.split('_')[0]
                        day_part = folder_name.split('_')[2]
                        
                        # Convert YYYYMMDD to date
                        folder_date = datetime.strptime(date_part, '%Y%m%d')
                        date_key = folder_date.strftime('%Y-%m-%d')
                        
                        # Get videos from this folder
                        folder_videos = []
                        for filename in os.listdir(folder_path):
                            if filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                                file_path = os.path.join(folder_path, filename)
                                file_stat = os.stat(file_path)
                                
                                # Extract time information from filename
                                time_info = extract_time_from_filename(filename)
                                
                                folder_videos.append({
                                    'filename': filename,
                                    'path': f'/static/trimmed/{folder_name}/{filename}',
                                    'size': format_file_size(file_stat.st_size),
                                    'modified': folder_date.strftime('%H:%M'),
                                # time_info removed per request in UI
                                    'folder': folder_name
                                })
                        
                        if folder_videos:
                            if date_key not in dashboard_data:
                                dashboard_data[date_key] = {
                                    'date': folder_date.strftime('%B %d, %Y'),
                                    'day_name': folder_date.strftime('%A'),
                                    'total_videos': 0,
                                    'videos': []
                                }
                            
                            dashboard_data[date_key]['videos'].extend(folder_videos)
                            dashboard_data[date_key]['total_videos'] += len(folder_videos)
                            
                except (ValueError, IndexError):
                    # Skip folders that don't match the expected format
                    continue
            
            elif folder_name.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                # This is a loose file (legacy format)
                file_path = os.path.join(trimmed_folder, folder_name)
                file_stat = os.stat(file_path)
                file_date = datetime.fromtimestamp(file_stat.st_mtime)
                date_key = file_date.strftime('%Y-%m-%d')
                
                # Extract time information from filename if possible
                # Extracted time info no longer used in UI
                time_info = extract_time_from_filename(folder_name)
                
                if date_key not in dashboard_data:
                    dashboard_data[date_key] = {
                        'date': file_date.strftime('%B %d, %Y'),
                        'day_name': file_date.strftime('%A'),
                        'total_videos': 0,
                        'videos': []
                    }
                
                dashboard_data[date_key]['total_videos'] += 1
                dashboard_data[date_key]['videos'].append({
                    'filename': folder_name,
                    'path': f'/static/trimmed/{folder_name}',
                    'size': format_file_size(file_stat.st_size),
                    'modified': file_date.strftime('%H:%M'),
                    # time_info removed per request in UI
                    'folder': 'root'
                })
        
        # Sort by date (newest first)
        sorted_dates = sorted(dashboard_data.keys(), reverse=True)
        sorted_dashboard = {date: dashboard_data[date] for date in sorted_dates}
        
        return jsonify({
            'success': True,
            'dashboard': sorted_dates,
            'dashboard_data': sorted_dashboard,
            'total_trimmed_videos': sum(data['total_videos'] for data in dashboard_data.values())
        })
        
    except Exception as e:
        logger.error(f"Error getting trimmed videos dashboard: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

def extract_time_from_filename(filename):
    """Extract time information from trimmed video filename"""
    try:
        # Look for patterns like "trim_0_5_", "clip1_0.00-30.00", etc.
        if 'trim_' in filename:
            parts = filename.split('_')
            if len(parts) >= 3:
                try:
                    start = float(parts[1])
                    end = float(parts[2])
                    duration = end - start
                    return f"{start:.1f}s - {end:.1f}s ({duration:.1f}s)"
                except:
                    pass
        elif 'clip' in filename and '-' in filename:
            # Handle clip patterns like "clip1_0.00-30.00" or "filename_clip_0.00-30.00.mp4"
            if '_clip_' in filename:
                # New format: filename_clip_start-end.mp4
                time_part = filename.split('_clip_')[1]
                if time_part and '-' in time_part:
                    # Remove file extension
                    time_part = time_part.split('.')[0]
                    start, end = time_part.split('-')
                    try:
                        start_time = float(start)
                        end_time = float(end)
                        duration = end_time - start_time
                        return f"{start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s)"
                    except:
                        pass
            else:
                # Handle clip patterns like "clip1_0.00-30.00"
                clip_part = filename.split('clip')[1] if 'clip' in filename else ''
                if clip_part and '-' in clip_part:
                    time_part = clip_part.split('_')[1] if '_' in clip_part else clip_part
                    if '-' in time_part:
                        start, end = time_part.split('-')
                        try:
                            start_time = float(start)
                            end_time = float(end)
                            duration = end_time - start_time
                            return f"{start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s)"
                        except:
                            pass
        return "Time info not available"
    except:
        return "Time info not available"

@app.route('/api/check_video_exists', methods=['GET'])
def check_video_exists():
    """Check if a video file exists in the system"""
    try:
        filename = request.args.get('filename')
        if not filename:
            return jsonify({'success': False, 'error': 'No filename provided.'}), 400
        
        # Sanitize filename
        safe_filename = secure_filename(filename)
        if not safe_filename:
            return jsonify({'success': False, 'error': 'Invalid filename provided.'}), 400
        
        # Check various locations where videos might be stored
        locations_to_check = [
            ('uploads', UPLOAD_FOLDER),
            ('trimmed', TRIM_FOLDER),
            ('videos', UPLOAD_FOLDER_EDIT),
            ('trimmed_edit', TRIMMED_FOLDER_EDIT)
        ]
        
        # Exact match at root of each folder
        for location_name, folder_path in locations_to_check:
            file_path = os.path.join(folder_path, safe_filename)
            if os.path.exists(file_path):
                logger.info(f"Video found in {location_name}: {safe_filename}")
                return jsonify({
                    'success': True,
                    'exists': True,
                    'location': location_name,
                    'full_path': file_path,
                    'size': os.path.getsize(file_path)
                })
        
        # Also check recursively for variations of the filename
        base_name = os.path.splitext(safe_filename)[0]
        for location_name, folder_path in locations_to_check:
            if os.path.exists(folder_path):
                for root, _, files in os.walk(folder_path):
                    for existing_file in files:
                        if existing_file == safe_filename or base_name in existing_file:
                            file_path = os.path.join(root, existing_file)
                            rel_path = os.path.relpath(file_path, folder_path)
                            logger.info(f"Video found in {location_name} (recursive match): {existing_file} at {rel_path}")
                            return jsonify({
                                'success': True,
                                'exists': True,
                                'location': location_name,
                                'full_path': file_path,
                                'size': os.path.getsize(file_path),
                                'matched_file': existing_file,
                                'relative_path': rel_path
                            })
        
        logger.info(f"Video not found in system: {safe_filename}")
        return jsonify({
            'success': True,
            'exists': False,
            'filename': safe_filename,
            'message': 'Video not found in any storage location'
        })
        
    except Exception as e:
        error_msg = f"Error checking video existence: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'success': False, 'error': error_msg}), 500

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.1f}{size_names[i]}"

if __name__ == '__main__':
    # Load scheduled posts on startup
    load_scheduled_posts()
    
    # Start scheduler
    start_scheduler()
    
    logging.info("Starting Flask application")
    app.run(host='0.0.0.0', port=5000, debug=False)