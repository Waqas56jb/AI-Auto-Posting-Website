import logging
import os
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
from flask_session import Session
from googletrans import Translator
from dotenv import load_dotenv
import google.generativeai as genai
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import secrets
import smtplib
from email.mime.text import MIMEText
import whisper
import torch
import json
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info(f"DB_NAME from env: {os.getenv('DB_NAME')}")

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    logger.error("SECRET_KEY is not set in environment variables")
    raise ValueError("SECRET_KEY must be set in .env file")

# Configure folders
app.config['UPLOAD_FOLDER'] = 'static/audio'
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
Session(app)

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

# Allowed file extensions for audio uploads
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'mp4', 'mov'}

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

# Initialize Whisper model
try:
    whisper_model = whisper.load_model("base")
except Exception as e:
    logger.error(f"Failed to load Whisper model: {str(e)}")
    raise ValueError(f"Whisper model initialization failed: {str(e)}")

# Initialize translator
translator = Translator()

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
        translation = translator.translate(text, dest=dest_lang)
        return translation.text
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
        return text

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def clean_lucy_story(story):
    cleaned = story
    cleaned = re.sub(r'^```.*?\n', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'```$', '', cleaned.strip())
    cleaned = re.sub(r'^-(\s+)?', '', cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace('"', '“').replace('"', '”')
    cleaned = re.sub(r'\b(just|really|very|completely|totally)\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n{2,}', '\n', cleaned)
    cleaned = cleaned.replace('\r\n', '\n')
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

# Routes
@app.route('/')
def index():
    logger.info("Navigated to index page")
    return render_template('LandingPage.html', languages=LANGUAGES)

@app.route('/api/navigate/<page>')
def navigate(page):
    """
    Handles navigation to various pages, rendering corresponding templates.
    Logs navigation events for tracking user flow.
    """
    valid_pages = ['index', 'login', 'signup', 'forgot', 'chatbot', 'dashboard', 'reset']
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

        # Project-specific context, no mention of Lucy, no fixed intro
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
        # Project-specific context, no name, no fixed intro
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
def download_file():
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
            result = whisper_model.transcribe(filepath, language='en')
            transcript = result['text']
            os.remove(filepath)
            return jsonify({'transcript': transcript})
        except Exception as e:
            os.remove(filepath)
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Invalid file format'}), 400

@app.route('/api/check_grammar', methods=['POST'])
def check_grammar():
    data = request.get_json()
    text = data.get('text')
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    try:
        # OPTIMIZATION: Truncate long text for grammar check
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
        # Use the custom story pattern as the prompt
        prompt = story_pattern.replace('{transcript}', transcript)
    else:
        framing = f"Tone: {tone if tone else 'Calm, reflective, personal, passionate, British'}. " \
                  f"Title: {title if title else 'Generate a suitable title'}. " \
                  f"Core Lesson: {core_lesson if core_lesson else 'Generate a relevant theme'}. " \
                  f"Micro-Lessons: {', '.join(micro_lessons) if micro_lessons else 'Generate three relevant micro-lessons'}. " \
                  "Lucy is a British woman sharing her story as if speaking to a close friend. Use UK English spelling (e.g., realised, neighbours, organised) and vocabulary (e.g., flat, lift, lorry). Avoid Americanisms (e.g., apartment, elevator, truck), hype, or salesy language. Ensure the narrative flows naturally with consistent energy, passion, and emotion, as if one person is speaking throughout."

        prompt = f"""
You are a British storyteller and content creator. Generate a 4-part voiceover script that feels authentic, with consistent rhythm, passion, and emotion throughout. Use exclusively UK English spelling (e.g., realised, neighbours, organised) and vocabulary (e.g., flat, lift, lorry). Avoid Americanisms (e.g., apartment, elevator, truck), hype, or salesy language. The narrative must flow naturally, as if one person is speaking, with no disjointed tone or style shifts.

EXAMPLE FORMAT (for structure and tone ONLY—do NOT copy the content below! The topic and context must come from the user's input, not the example):

🎬 Title:
From Property Nightmare to Hands-Off Wealth: My Journey

🎬 The Mistake That Shattered My Dreams
“I thought I’d found the perfect tenant—until the police knocked.”
“My first flat was ready, and I rushed to let it.”
“I missed the trouble brewing right under my nose.”

“My name’s Lucy. Fifteen years ago, I dove into property investment, chasing a dream of financial freedom. I was so eager that I skipped proper tenant checks and ignored odd behaviour from my first tenant. Months later, my flat was a crime scene—drugs, police, furious neighbours. It cost me thousands, and I nearly gave up. But that heartbreak taught me to slow down and think. I realised mistakes are lessons if you choose to learn.”
“Ever leapt into something and regretted it? What happened?”
CUT 1

🎬 The Turning Point That Changed Everything
“That chaos woke me up.”
“I stopped, reflected, and rebuilt.”
“One decision set me on a new path.”

“That disaster wasn’t the end—it was my beginning. I didn’t want midnight calls or endless stress. I sat down and mapped out every mistake I’d made. That’s when I started building what I call ‘The Machine.’ I invested in proper training, found reliable trades, and set up systems to handle tenants and repairs. It wasn’t cheap, but it gave me peace. My flats stayed let, and I could finally breathe.”
“What’s a moment that changed how you think? Share it.”
CUT 2

🎬 Letting Go to Gain Control
“I thought doing everything myself proved I was serious.”
“Handing over tasks was my breakthrough.”
“Systems gave me freedom, not chaos.”

“I used to think being hands-on showed my commitment. I was wrong. Delegation changed everything. Now, experts handle acquisitions, refurbishments, and tenant issues. I review reports monthly and make the big calls. If something goes wrong, my systems catch it before it spirals. I’ve got time for my family, my life, and my dreams. True wealth is living on your terms.”
“What could you let go of to find more freedom?”
CUT 3

🎬 The Machine That Runs Itself
“People ask how my system works.”
“It’s not flashy; it’s steady.”
“It could work for you—or it might not.”

“I don’t chase quick wins or showy projects. My system delivers steady income without the hassle. Investors join me, skipping the stress of repairs or tenant dramas. I check the numbers; the machine hums along. It’s not for everyone, but if you crave calm over chaos, it might be for you. Freedom comes from building something that doesn’t own you.”
“Could a system like this change your life? Let’s talk.”
CUT 4

Building wealth isn’t about grinding harder—it’s about crafting smarter systems.

FRAMING (guide tone, title, lessons):
{framing}

STORY (source material; do not invent beyond this):
{transcript}

IMPORTANT: The above example is for structure, energy, and UK English style ONLY. The actual story content, topic, and speaker must come from the user's transcript and context. If the transcript is not about Lucy or property, adapt the story to the new context and speaker, but always use the same structure, energy, and UK English style. The story should feel like a real person speaking, with natural rhythm and emotion, and must be fully dynamic to the user's input.
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
  - One quoted narration paragraph (4-5 sentences, single pair of smart quotes, clear and personal).
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

if __name__ == '__main__':
    logger.info("Starting Flask application")
    app.run(host='0.0.0.0', port=5000, debug=False)