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
    You are an expert social media content assistant specializing in concise, engaging video scripts.
    Generate scripts with:
    - **Caption**: Short, platform-optimized caption if requested.
    - **Video Script**: Clear, concise narrative with slightly expanded segments.
    - **Tone**: Calm, confident, informative, slightly playful, British, non-salesy.
    - **Structure**: Match the exact format provided, with short sentences and no filler words.
    For queries, provide brief, clear responses.
    """,
    generation_config={
        'temperature': 0.7,
        'top_p': 0.9,
        'top_k': 40,
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
    # Remove specified headers, keep lessons, reduce filler
    patterns = [
        r'(?im)^\s*Final CTA:?\s*',
        r'(?im)^\s*Segment\s*\d+:?\s*',
        r'(?im)^\s*Core Story Framework:?\s*',
        r'(?im)^\s*Core Lesson:?\s*',
        r'(?im)^\s*Micro-Lessons:?\s*',
        r'(?im)^\s*microlessons:?\s*',
        r'(?im)^\s*Video Length:?\s*.*',
        r'(?im)^\s*Style:?\s*.*',
        r'(?im)^\s*story title:?\s*',
        r'(?im)^\s*\ud83d\udcdc\s*Core Story Framework:?\s*',
        r'(?im)^\s*\ud83c\udfac\s*Script Title:?\s*(.*)',
        r'(?im)^\s*\ud83c\udfaf\s*Final CTA:?\s*',
    ]
    cleaned = story
    for i, pat in enumerate(patterns[:-1]):
        cleaned = re.sub(pat, '', cleaned)
    title_match = re.search(patterns[-2], cleaned, re.IGNORECASE | re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
        cleaned = re.sub(patterns[-2], title, cleaned)
    # Keep lessons (lines starting with "- ") but remove filler
    lines = cleaned.split('\n')
    cleaned_lines = []
    in_segment = False
    for line in lines:
        line = line.strip()
        if line.startswith('🎬'):
            in_segment = True
            cleaned_lines.append(line)
        elif in_segment and (line.startswith('“') and line.endswith('”')):
            line = re.sub(r'\b(just|really|very|completely|totally)\b', '', line)
            cleaned_lines.append(line.strip())
        elif line.startswith('CUT'):
            cleaned_lines.append(line)
            in_segment = False
        elif line and not line.startswith('- '):  # Keep non-lesson lines
            line = re.sub(r'\b(just|really|very|completely|totally)\b', '', line)
            cleaned_lines.append(line)
        elif line.startswith('- '):  # Keep lesson lines
            line = re.sub(r'\b(just|really|very|completely|totally)\b', '', line)
            cleaned_lines.append(line.strip())
    cleaned = '\n'.join(cleaned_lines)
    cleaned = re.sub(r'\n{2,}', '\n', cleaned)
    return cleaned.strip()

def extract_framing_and_story(transcript):
    framing_match = re.search(r'framing starts:(.*?)(framing ends)', transcript, re.IGNORECASE | re.DOTALL)
    if framing_match:
        framing = framing_match.group(1).strip()
        story = transcript.replace(framing_match.group(0), '').strip()
    else:
        framing = ""
        story = transcript
    return framing, story

# Routes
@app.route('/')
def index():
    return render_template('LandingPage.html', languages=LANGUAGES)

@app.route('/api/navigate/<page>')
def navigate(page):
    valid_pages = ['index', 'login', 'signup', 'forgot', 'chatbot', 'dashboard', 'reset']
    if page not in valid_pages:
        logger.warning(f"Attempted to navigate to invalid page: {page}")
        return jsonify({'message': 'Invalid page'}), 404
    if page == 'index':
        return render_template('index.html', languages=LANGUAGES)
    template = 'LandingPage.html' if page == 'index' else f'{page}.html'
    if page == 'reset':
        token = request.args.get('token')
        if not token:
            return jsonify({'message': 'Token is required'}), 400
        return render_template('reset.html', token=token, languages=LANGUAGES)
    return render_template(template, languages=LANGUAGES)

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
        logger.info(f"Successful signup for email: {email}")
        return jsonify({'message': 'Signup successful! Redirecting to login...'}), 200
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
            return jsonify({'message': 'Password reset token generated (check server logs for token)', 'token': token}), 200

        msg = MIMEText(f"Your password reset link: http://localhost:5000/api/navigate/reset?token={token}\nIt expires at {expires_at}.", 'plain')
        msg['Subject'] = 'Password Reset Request'
        msg['From'] = sender_email
        msg['To'] = email

        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, smtp_password)
                server.sendmail(sender_email, email, msg.as_string())
            logger.info(f"Password reset email sent to: {email}")
        except smtplib.SMTPException as e:
            logger.error(f"Email sending error: {str(e)}")
            return jsonify({'message': 'Error sending email, token generated (check server logs)', 'token': token}), 500

        return jsonify({'message': 'Password reset link sent to your email'}), 200
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
    token = data.get('token')
    new_password = data.get('newPassword')
    confirm_password = data.get('confirmPassword')

    if not all([email, token, new_password, confirm_password]):
        logger.warning("Reset password attempt with incomplete fields")
        return jsonify({'message': 'All fields are required'}), 400
    if new_password != confirm_password:
        logger.warning("Reset password attempt with mismatched passwords")
        return jsonify({'message': 'Passwords do not match'}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT token, expires_at FROM password_resets WHERE email = %s ORDER BY created_at DESC LIMIT 1",
            (email,)
        )
        result = cursor.fetchone()

        if not result or result[0] != token:
            cursor.close()
            conn.close()
            logger.warning(f"Reset password failed for email {email}: Invalid token")
            return jsonify({'message': 'Invalid or expired token'}), 400

        token_expiry = result[1]
        if datetime.now() > token_expiry:
            cursor.close()
            conn.close()
            logger.warning(f"Reset password failed for email {email}: Token expired")
            return jsonify({'message': 'Token has expired'}), 400

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
        return jsonify({'message': 'Password reset successfully'}), 200
    except mysql.connector.Error as e:
        logger.error(f"Database error during reset password: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    email = session.get('user', 'unknown')
    session.pop('user', None)
    logger.info(f"User logged out: {email}")
    return jsonify({'message': 'Logged out successfully'}), 200

@app.route('/ask', methods=['POST'])
def ask_question():
    data = request.get_json()
    if not data:
        logger.warning("Chatbot query attempt with missing JSON data")
        return jsonify({'response': '⚠️ Please enter a message.', 'lang': 'en'})

    user_input = data.get('query', '').strip()
    input_lang = data.get('input_lang', 'en')
    output_lang = data.get('output_lang', 'en')

    if not user_input:
        logger.warning("Chatbot query attempt with empty input")
        return jsonify({'response': '⚠️ Please enter a message.', 'lang': output_lang})

    try:
        if input_lang != 'en':
            user_input = translate_text(user_input, 'en')

        response = model.generate_content(user_input)
        text = response.text.strip() if response.text else '⚠️ No response received from the model.'

        if output_lang != 'en':
            text = translate_text(text, output_lang)

        logger.info(f"Chatbot query processed: {user_input[:50]}...")
        return jsonify({'response': text, 'lang': output_lang})
    except Exception as e:
        logger.error(f"Error processing chatbot query: {str(e)}")
        error_msg = f'❌ Error: {str(e)}'
        if output_lang != 'en':
            error_msg = translate_text(error_msg, output_lang)
        return jsonify({'response': error_msg, 'lang': output_lang})

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
        prompt = (
            "Analyze the following text for grammatical and spelling errors. "
            "Return a JSON array of issues, where each issue contains the original sentence, "
            "the corrected sentence, and a brief explanation of the error."
            f"\n\nText: {text}"
        )
        response = model.generate_content(prompt)
        issues = response.text
        try:
            issues = json.loads(issues)
        except json.JSONDecodeError:
            issues = []
        return jsonify({'issues': issues})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate_story', methods=['POST'])
def generate_story():
    data = request.get_json()
    transcript = data.get('text')
    if not transcript:
        return jsonify({'error': 'No transcript provided'}), 400

    try:
        framing, story = extract_framing_and_story(transcript)
        prompt = f"""
You are a story design assistant creating a voiceover script for Lucy from "Lucy & The Wealth Machine."

Input:
- Framing: "{framing}"
- Story: "{story}"

Task: Create a 4-part voiceover script matching the structure of this example, with slightly expanded segments for depth:

Example:
🎬 Script Title: Hooked: The Psychology of Viral Video Openings
Video Length: ~5 minutes (4 scenes, each = 1 short-form video)
Style: Calm, confident, informative, slightly playful
📘 Core Story Framework
Core Lesson: Mastering video hooks requires understanding the psychology behind engagement, not copying trends.
Micro-Lessons:
- Three-step hook: Context Lean, Scroll Stop Interjection, Contrarian Snapback.
- Visual hooks: Combine text and motion.
- Build common ground with cultural references.
- Compress value with short sentences.
🎬 Segment 1: The Hook That Almost Didn't Work
“Today: video hooks. Want better videos? Better hooks.”
“Forget lists of viral hooks.”
“Understand the psychology.”
“Hi, I'm Lucy. A million followers, billions of views. I learned video hooks the hard way. Catchy phrases? Nope. It's about a curiosity loop—instant attention. My three-step formula works every time. But first, a hook that nearly flopped…”
“What’s your biggest video intro mistake? Let me know!”
CUT 1
...

Output Format (remove headers below, keep lessons):
- Final CTA
- Segment 1, Segment 2, Segment 3, Segment 4
- Core Story Framework
- Core Lesson
- Micro-Lessons
- Video Length
- Style
- story title
- 🎯 Final CTA
- 🎬 Script Title: (keep title only)

Desired Output:
Hooked: The Psychology of Viral Video Openings
- Three-step hook: Context Lean, Scroll Stop Interjection, Contrarian Snapback.
- Visual hooks: Combine text and motion.
- Build common ground with cultural references.
- Compress value with short sentences.
🎬 The Hook That Almost Didn't Work
“Today: video hooks. Want better videos? Better hooks.”
“Forget lists of viral hooks.”
“Understand the psychology.”
“Hi, I'm Lucy. A million followers, billions of views. I learned video hooks the hard way. Catchy phrases? Nope. It's about a curiosity loop—instant attention. My three-step formula works every time. But first, a hook that nearly flopped…”
“What’s your biggest video intro mistake? Let me know!”
CUT 1
...

Requirements:
- Use framing to guide tone and style.
- Match example structure: title, lessons (without heading), 4 segments with 🎬 titles, 3 quoted hooks, expanded main quoted story (add 1-2 sentences for depth), quoted CTA, CUT markers.
- Keep sentences short, avoid filler (e.g., "just," "really," "very").
- Reflect Lucy’s tone: calm, confident, informative, slightly playful, British, non-salesy.
- Avoid made-up events (e.g., lawsuits).
- Avoid “we” phrases (e.g., “we built a system”).
- Each segment ends with a soft, open-ended question.
- Remove specified headers, keep lesson lines (starting with "- ").
"""
        response = model.generate_content(prompt)
        story_text = response.text.strip()
        clean_story = clean_lucy_story(story_text)
        return jsonify({'story': clean_story})
    except Exception as e:
        logger.error(f"Error generating story: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/validate_story', methods=['POST'])
def validate_story():
    data = request.get_json()
    story = data.get('story')
    if not story:
        return jsonify({'error': 'No story provided'}), 400

    try:
        validation_prompt = f"""
You are checking a story for Lucy from “Lucy & The Wealth Machine.”

Criteria:
- Matches structure: title (no '🎬 Script Title:'), lessons (no heading, keep text), 4 segments with 🎬 titles, 3 quoted hooks, expanded main quoted story, quoted CTA, CUT markers.
- Headers removed: 'Final CTA', 'Segment 1-4', 'Core Story Framework', 'Core Lesson', 'Micro-Lessons', 'Video Length', 'Style', 'story title', '🎯 Final CTA'.
- Lessons included without heading.
- Concise language: Short sentences, no filler (e.g., "just," "really," "very").
- Segments slightly expanded (1-2 extra sentences) for depth.
- Lucy’s tone: calm, confident, informative, slightly playful, British, non-salesy.
- Reflects Lucy’s expertise, not generic.
- No made-up events (e.g., lawsuits).
- No “we” phrases (e.g., “we built this”).
- Each segment ends with a soft, open-ended question.

Return JSON:
{{
  "result": "✅ Pass" or "❌ Fail",
  "summary": "One-sentence summary",
  "issues": ["list of issues"],
  "suggested_fixes": ["list of fixes"],
  "clean_version": "optional: revised story if changes are minor"
}}

Story:
\"""{story}\"""
"""
        response = model.generate_content(validation_prompt)
        validation_result = response.text.strip()
        try:
            validation_json = json.loads(validation_result)
        except json.JSONDecodeError:
            validation_json = {
                "result": "❌ Fail",
                "summary": "Failed to parse validation response",
                "issues": ["Invalid JSON response"],
                "suggested_fixes": ["Retry validation"],
                "clean_version": story
            }
        return jsonify({'validation': validation_json})
    except Exception as e:
        logger.error(f"Error validating story: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    logger.info("Starting Flask application")
    app.run(host='0.0.0.0', port=5000, debug=False)