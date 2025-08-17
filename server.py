"""
Configuration file for YouTube Video Uploader
Modified for PostgreSQL and Fly.io deployment
"""

import logging
import os
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for, send_file
from flask_session import Session
import google.generativeai as genai
import psycopg2
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


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import configuration
from config import *

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.secret_key = SECRET_KEY
if not app.secret_key:
    logger.error("SECRET_KEY is not set in configuration")
    raise ValueError("SECRET_KEY must be set in config.py")

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

# Database configuration for PostgreSQL
db_config = DB_CONFIG
logger.info(f"Database config: {db_config}")

# Check database connection
def check_db_connection():
    try:
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True  # Set autocommit after connection
        cursor = conn.cursor()
        cursor.execute("SELECT current_database();")
        db_name = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        logger.info(f"Connected to database: {db_name}")
        return {'status': 'success', 'message': f'Database connected successfully: {db_name}'}
    except psycopg2.Error as e:
        logger.error(f"Database connection failed: {str(e)}")
        return {'status': 'error', 'message': f'Database connection failed: {str(e)}'}

# Configure Google Gemini AI
google_api_key = GOOGLE_API_KEY
if not google_api_key:
    logger.error("GOOGLE_API_KEY is not set in configuration")
    raise ValueError("GOOGLE_API_KEY must be set in config.py")

genai.configure(api_key=google_api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

# Initialize Whisper AI model with fallback
whisper_model = None
whisper_edit_model = None

try:
    import whisper
    # Try to load a smaller model first to avoid memory issues
    whisper_model = whisper.load_model("tiny")  # Use "tiny" for faster loading
    logger.info("Whisper AI model loaded successfully (tiny model)")
except ImportError:
    logger.warning("Whisper library not installed. Install with: pip install openai-whisper")
except Exception as e:
    logger.error(f"Failed to load Whisper model: {e}")
    # Try alternative approach
    try:
        import whisper
        whisper_model = whisper.load_model("tiny")
        logger.info("Whisper AI model loaded successfully on second attempt")
    except Exception as e2:
        logger.error(f"Second attempt to load Whisper failed: {e2}")
        whisper_model = None

whisper_edit_model = whisper_model  # Use same model for editing

# Initialize translator
translator = None # Removed googletrans import, so translator is no longer available

# Add transcript generation functionality using Whisper AI
def generate_transcript_from_video(video_path):
    """Generate transcript from video using Whisper AI"""
    try:
        if not whisper_model:
            return {
                'success': False,
                'error': 'Whisper AI model not available. Please install openai-whisper library.'
            }
        
        # Extract audio from video using ffmpeg
        audio_path = video_path.replace('.mp4', '.wav').replace('.mov', '.wav').replace('.avi', '.wav').replace('.mkv', '.wav').replace('.webm', '.wav')
        
        # Use ffmpeg to extract audio
        import subprocess
        try:
            logger.info(f"Extracting audio from video: {video_path}")
            subprocess.run([
                'ffmpeg', '-i', video_path, 
                '-vn', '-acodec', 'pcm_s16le', 
                '-ar', '16000', '-ac', '1', 
                audio_path, '-y'
            ], check=True, capture_output=True)
            
            # Now generate transcript from audio using Whisper
            result = generate_transcript_from_audio(audio_path)
            
            # Clean up temporary audio file
            if os.path.exists(audio_path):
                os.remove(audio_path)
            
            return result
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {str(e)}")
            return {
                'success': False,
                'error': f'Failed to extract audio from video: {str(e)}'
            }
            
    except Exception as e:
        logger.error(f"Error generating transcript from video: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

def generate_transcript_from_audio(audio_path):
    """Generate transcript from audio file using Whisper AI or fallback methods"""
    try:
        # Try Whisper AI first
        if whisper_model:
            logger.info(f"Using Whisper AI for transcription: {audio_path}")
            try:
                result = whisper_model.transcribe(audio_path)
                
                if result and 'text' in result:
                    transcript = result['text'].strip()
                    word_count = len(transcript.split())
                    
                    # Get audio duration from Whisper result
                    if 'segments' in result and result['segments']:
                        duration_seconds = result['segments'][-1]['end']
                        duration = f"{int(duration_seconds//60):02d}:{int(duration_seconds%60):02d}"
                    else:
                        duration = "00:00:00"
                    
                    logger.info(f"Whisper AI successfully generated transcript with {word_count} words")
                    return {
                        'success': True,
                        'transcript': transcript,
                        'word_count': word_count,
                        'duration': duration,
                        'language': result.get('language', 'unknown'),
                        'method': 'whisper_ai'
                    }
            except Exception as whisper_error:
                logger.warning(f"Whisper AI failed, trying fallback: {whisper_error}")
        
        # Fallback to Google Speech Recognition
        logger.info(f"Using Google Speech Recognition fallback: {audio_path}")
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
                    
                    logger.info(f"Google Speech Recognition successfully generated transcript with {word_count} words")
                    return {
                        'success': True,
                        'transcript': transcript,
                        'word_count': word_count,
                        'duration': duration,
                        'language': 'en',  # Google Speech Recognition default
                        'method': 'google_speech'
                    }
                    
                except sr.UnknownValueError:
                    return {
                        'success': False,
                        'error': 'Audio content could not be understood clearly. This might be due to background noise, unclear speech, or audio quality issues.',
                        'method': 'google_speech'
                    }
                    
                except sr.RequestError as e:
                    logger.warning(f"Google Speech Recognition service error: {str(e)}")
                    return {
                        'success': False,
                        'error': 'Speech recognition service temporarily unavailable. Please try again later.',
                        'method': 'google_speech'
                    }
                    
        except ImportError:
            logger.warning("Speech recognition library not available, using simple fallback")
            # Final fallback: simple transcription service
            try:
                from simple_transcription import SimpleTranscriptionService
                service = SimpleTranscriptionService()
                return service.analyze_audio_file(audio_path)
            except ImportError:
                return {
                    'success': False,
                    'error': 'No transcription services available. Please install openai-whisper or SpeechRecognition library.',
                    'method': 'none'
                }
            
    except Exception as e:
        logger.error(f"Error generating transcript from audio: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'method': 'error'
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
    'ja': {'name': 'Japanese', 'flag': '🇯🇵'},
    'ko': {'name': 'Korean', 'flag': '🇰🇷'},
    'zh': {'name': 'Chinese', 'flag': '🇨🇳'},
    'ar': {'name': 'Arabic', 'flag': '🇸🇦'},
    'hi': {'name': 'Hindi', 'flag': '🇮🇳'},
    'tr': {'name': 'Turkish', 'flag': '🇹🇷'},
    'nl': {'name': 'Dutch', 'flag': '🇳🇱'},
    'pl': {'name': 'Polish', 'flag': '🇵🇱'},
    'sv': {'name': 'Swedish', 'flag': '🇸🇪'},
    'da': {'name': 'Danish', 'flag': '🇩🇰'},
    'no': {'name': 'Norwegian', 'flag': '🇳🇴'},
    'fi': {'name': 'Finnish', 'flag': '🇫🇮'},
    'cs': {'name': 'Czech', 'flag': '🇨🇿'},
    'hu': {'name': 'Hungarian', 'flag': '🇭🇺'},
    'ro': {'name': 'Romanian', 'flag': '🇷🇴'},
    'bg': {'name': 'Bulgarian', 'flag': '🇧🇬'},
    'hr': {'name': 'Croatian', 'flag': '🇭🇷'},
    'sk': {'name': 'Slovak', 'flag': '🇸🇰'},
    'sl': {'name': 'Slovenian', 'flag': '🇸🇮'},
    'et': {'name': 'Estonian', 'flag': '🇪🇪'},
    'lv': {'name': 'Latvian', 'flag': '🇱🇻'},
    'lt': {'name': 'Lithuanian', 'flag': '🇱🇹'},
    'mt': {'name': 'Maltese', 'flag': '🇲🇹'},
    'el': {'name': 'Greek', 'flag': '🇬🇷'},
    'he': {'name': 'Hebrew', 'flag': '🇮🇱'},
    'th': {'name': 'Thai', 'flag': '🇹🇭'},
    'vi': {'name': 'Vietnamese', 'flag': '🇻🇳'},
    'id': {'name': 'Indonesian', 'flag': '🇮🇩'},
    'ms': {'name': 'Malay', 'flag': '🇲🇾'},
    'tl': {'name': 'Filipino', 'flag': '🇵🇭'},
    'uk': {'name': 'Ukrainian', 'flag': '🇺🇦'},
    'be': {'name': 'Belarusian', 'flag': '🇧🇾'},
    'mk': {'name': 'Macedonian', 'flag': '🇲🇰'},
    'sq': {'name': 'Albanian', 'flag': '🇦🇱'},
    'ka': {'name': 'Georgian', 'flag': '🇬🇪'},
    'hy': {'name': 'Armenian', 'flag': '🇦🇲'},
    'az': {'name': 'Azerbaijani', 'flag': '🇦🇿'},
    'kk': {'name': 'Kazakh', 'flag': '🇰🇿'},
    'ky': {'name': 'Kyrgyz', 'flag': '🇰🇬'},
    'uz': {'name': 'Uzbek', 'flag': '🇺🇿'},
    'tg': {'name': 'Tajik', 'flag': '🇹🇯'},
    'mn': {'name': 'Mongolian', 'flag': '🇲🇳'},
    'ne': {'name': 'Nepali', 'flag': '🇳🇵'},
    'si': {'name': 'Sinhala', 'flag': '🇱🇰'},
    'my': {'name': 'Burmese', 'flag': '🇲🇲'},
    'km': {'name': 'Khmer', 'flag': '🇰🇭'},
    'lo': {'name': 'Lao', 'flag': '🇱🇦'},
    'gl': {'name': 'Galician', 'flag': '🇪🇸'},
    'eu': {'name': 'Basque', 'flag': '🇪🇸'},
    'ca': {'name': 'Catalan', 'flag': '🇪🇸'},
    'cy': {'name': 'Welsh', 'flag': '🇬🇧'},
    'ga': {'name': 'Irish', 'flag': '🇮🇪'},
    'gd': {'name': 'Scottish Gaelic', 'flag': '🇬🇧'},
    'is': {'name': 'Icelandic', 'flag': '🇮🇸'},
    'fo': {'name': 'Faroese', 'flag': '🇫🇴'},
    'fy': {'name': 'Frisian', 'flag': '🇳🇱'},
    'lb': {'name': 'Luxembourgish', 'flag': '🇱🇺'},
    'rm': {'name': 'Romansh', 'flag': '🇨🇭'},
    'wa': {'name': 'Walloon', 'flag': '🇧🇪'},
    'fur': {'name': 'Friulian', 'flag': '🇮🇹'},
    'sc': {'name': 'Sardinian', 'flag': '🇮🇹'},
    'vec': {'name': 'Venetian', 'flag': '🇮🇹'},
    'lmo': {'name': 'Lombard', 'flag': '🇮🇹'},
    'pms': {'name': 'Piedmontese', 'flag': '🇮🇹'},
    'nap': {'name': 'Neapolitan', 'flag': '🇮🇹'},
    'scn': {'name': 'Sicilian', 'flag': '🇮🇹'},
    'co': {'name': 'Corsican', 'flag': '🇫🇷'},
    'oc': {'name': 'Occitan', 'flag': '🇫🇷'},
    'gsw': {'name': 'Swiss German', 'flag': '🇨🇭'},
    'bar': {'name': 'Bavarian', 'flag': '🇩🇪'},
    'ksh': {'name': 'Colognian', 'flag': '🇩🇪'},
    'swg': {'name': 'Swabian', 'flag': '🇩🇪'},
    'pfl': {'name': 'Palatinate German', 'flag': '🇩🇪'},
    'sxu': {'name': 'Upper Saxon', 'flag': '🇩🇪'},
    'wae': {'name': 'Walser', 'flag': '🇨🇭'},
    'grc': {'name': 'Ancient Greek', 'flag': '🏛️'},
    'la': {'name': 'Latin', 'flag': '🏛️'},
    'ang': {'name': 'Old English', 'flag': '🏛️'},
    'fro': {'name': 'Old French', 'flag': '🏛️'},
    'goh': {'name': 'Old High German', 'flag': '🏛️'},
    'non': {'name': 'Old Norse', 'flag': '🏛️'},
    'peo': {'name': 'Old Persian', 'flag': '🏛️'},
    'sga': {'name': 'Old Irish', 'flag': '🏛️'},
    'sla': {'name': 'Proto-Slavic', 'flag': '🏛️'},
    'ine': {'name': 'Proto-Indo-European', 'flag': '🏛️'},
    'afa': {'name': 'Afro-Asiatic', 'flag': '🌍'},
    'nic': {'name': 'Niger-Congo', 'flag': '🌍'},
    'cau': {'name': 'Caucasian', 'flag': '🌍'},
    'dra': {'name': 'Dravidian', 'flag': '🌍'},
    'tut': {'name': 'Altaic', 'flag': '🌍'},
    'qwe': {'name': 'Quechuan', 'flag': '🌍'},
    'nai': {'name': 'North American Indian', 'flag': '🌍'},
    'cai': {'name': 'Central American Indian', 'flag': '🌍'},
    'sai': {'name': 'South American Indian', 'flag': '🌍'},
    'map': {'name': 'Austronesian', 'flag': '🌍'},
    'aus': {'name': 'Australian Aboriginal', 'flag': '🌍'},
    'paa': {'name': 'Papuan', 'flag': '🌍'},
    'art': {'name': 'Artificial', 'flag': '🤖'},
    'mis': {'name': 'Uncoded', 'flag': '❓'},
    'mul': {'name': 'Multiple', 'flag': '🌐'},
    'und': {'name': 'Undetermined', 'flag': '❓'},
    'zxx': {'name': 'No linguistic content', 'flag': '📄'},
    'xxx': {'name': 'Unassigned', 'flag': '❓'}
}

def translate_text(text, dest_lang='en'):
    """Translate text to specified language"""
    try:
        # Placeholder translation function
        # In production, implement actual translation using services like Google Translate API
        return {
            'success': True,
            'translated_text': f"[Translated to {dest_lang}]: {text}",
            'source_language': 'auto',
            'target_language': dest_lang
        }
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_file_edit(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_EDIT

def extract_framing_and_story(transcript):
    """Extract framing and story from transcript"""
    try:
        # Simple extraction - you can enhance this based on your needs
        # For now, we'll use the first sentence as framing and the rest as story
        sentences = transcript.split('.')
        if len(sentences) > 1:
            framing = sentences[0].strip() + '.'
            story = '.'.join(sentences[1:]).strip()
        else:
            framing = "Personal growth and reflection"
            story = transcript
        
        return framing, story
    except Exception as e:
        logger.error(f"Error extracting framing and story: {e}")
        return "Personal growth and reflection", transcript

def clean_lucy_story(story):
    """Clean and format Lucy's story content by removing markdown symbols for clean output"""
    try:
        # Preserve line breaks but remove markdown formatting symbols
        cleaned = story.strip()
        
        # Remove markdown symbols while preserving content
        cleaned = re.sub(r'#+\s*\*\*(.*?)\*\*', r'\1', cleaned)  # Remove # and ** from titles
        cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', cleaned)  # Remove ** from bold text
        cleaned = re.sub(r'>\s*"(.*?)"', r'"\1"', cleaned)  # Remove > and keep quotes
        cleaned = re.sub(r'>\s*(.*?)(?=\n|$)', r'\1', cleaned)  # Remove > from other lines
        
        # Remove "🎯 Final CTA:" label but keep the content
        cleaned = re.sub(r'🎯 Final CTA:\s*', '', cleaned)
        
        # Ensure proper line breaks between sections
        # Add line breaks after titles
        cleaned = re.sub(r'([^\\n]+)\n\n', r'\1\n\n\n', cleaned)
        
        # Add line breaks after Core Lessons section
        cleaned = re.sub(r'(Core Lessons:.*?)(\n🎬)', r'\1\n\n\n\2', cleaned, flags=re.DOTALL)
        
        # Add line breaks before and after segment titles
        cleaned = re.sub(r'(\n🎬.*?)\n', r'\n\n\n\1\n\n\n', cleaned)
        
        # Remove CUT markers completely but preserve content
        cleaned = re.sub(r'CUT \d+\s*\n', '', cleaned)
        
        # Normalize line breaks but preserve intentional spacing
        cleaned = re.sub(r'\n{5,}', '\n\n\n\n', cleaned)  # Max 4 consecutive line breaks
        
        # Ensure proper sentence endings
        cleaned = re.sub(r'([.!?])\s*([A-Z])', r'\1 \2', cleaned)
        
        # Clean up any remaining markdown artifacts
        cleaned = re.sub(r'^\s*[-*]\s*', '', cleaned, flags=re.MULTILINE)  # Remove bullet points
        cleaned = re.sub(r'^\s*>\s*', '', cleaned, flags=re.MULTILINE)  # Remove any remaining >
        cleaned = re.sub(r'^\s*#+\s*', '', cleaned, flags=re.MULTILINE)  # Remove any remaining #
        
        return cleaned
    except Exception as e:
        logger.error(f"Error cleaning story: {e}")
        return story

def parse_story_to_json(story_text):
    """Parse story text into structured JSON format with markdown formatting"""
    try:
        # Preserve markdown formatting for better display
        lines = story_text.split('\n')
        
        # Extract title (first line with # or bold formatting)
        title = "Generated Story"
        for line in lines:
            line = line.strip()
            if line.startswith('#') or line.startswith('**') or 'Lucy & The Wealth Machine:' in line:
                title = line.replace('#', '').replace('**', '').strip()
                break
        
        # Extract core lessons
        key_points = []
        in_lessons_section = False
        
        for line in lines:
            line = line.strip()
            if 'Core Lessons:' in line or '**Core Lessons:**' in line:
                in_lessons_section = True
                continue
            elif in_lessons_section and line.startswith('##'):
                break
            elif in_lessons_section and line.startswith('-') and line:
                key_points.append(line)
        
        # Extract segments with markdown formatting
        segments = []
        current_segment = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('##') and '🎬' in line:
                if current_segment:
                    segments.append(current_segment)
                current_segment = {
                    'title': line.replace('##', '').replace('**', '').strip(),
                    'content': [],
                    'hooks': [],
                    'main_content': '',
                    'question': ''
                }
            elif current_segment and line.startswith('>'):
                # Extract quoted content
                quoted_text = line.replace('>', '').strip().replace('"', '')
                if quoted_text:
                    current_segment['hooks'].append(quoted_text)
            elif current_segment and line.startswith('**CUT'):
                # End of segment
                if current_segment:
                    segments.append(current_segment)
                    current_segment = None
            elif current_segment and line and not line.startswith('>'):
                # Main content or question
                if '?' in line and not current_segment['question']:
                    current_segment['question'] = line
                elif not current_segment['main_content'] and line:
                    current_segment['main_content'] = line
        
        if current_segment:
            segments.append(current_segment)
        
        # Extract final CTA
        final_cta = ""
        for line in lines:
            if '🎯 Final CTA:' in line or '**🎯 Final CTA:**' in line:
                # Get the next line as CTA
                cta_index = lines.index(line)
                if cta_index + 1 < len(lines):
                    final_cta = lines[cta_index + 1].strip()
                break
        
        # Clean content for word count (remove markdown)
        clean_content = re.sub(r'\*\*(.*?)\*\*', r'\1', story_text)
        clean_content = re.sub(r'#+\s*', '', clean_content)
        clean_content = re.sub(r'>\s*', '', clean_content)
        
        return {
            'title': title,
            'content': story_text,  # Keep original markdown formatting
            'key_points': key_points,
            'segments': segments,
            'final_cta': final_cta,
            'word_count': len(clean_content.split()),
            'estimated_read_time': max(1, len(clean_content.split()) // 200),
            'structure': 'markdown_voiceover_script',
            'formatting': 'universal_markdown'
        }
    except Exception as e:
        logger.error(f"Error parsing story to JSON: {e}")
        return {
            'title': "Generated Story",
            'content': story_text,
            'key_points': [],
            'segments': [],
            'final_cta': "",
            'word_count': len(story_text.split()),
            'estimated_read_time': 1,
            'structure': 'markdown_voiceover_script',
            'formatting': 'universal_markdown'
        }

# Routes
@app.route('/')
def indexakkal():
    """Main landing page"""
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

@app.route('/api/create-video-clip', methods=['POST'])
def create_video_clip():
    """Create a video clip from uploaded video file"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        start_time = float(request.form.get('start_time', 0))
        end_time = float(request.form.get('end_time', 0))
        
        if start_time < 0 or end_time <= start_time:
            return jsonify({'success': False, 'message': 'Invalid time range'}), 400
        
        # Create trimmed folder if it doesn't exist
        trimmed_folder = 'static/trimmed'
        os.makedirs(trimmed_folder, exist_ok=True)
        
        # Generate unique filename for the clip
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = os.path.splitext(file.filename)[0]
        clip_filename = f"{base_name}_clip_{timestamp}.mp4"
        clip_path = os.path.join(trimmed_folder, clip_filename)
        
        # Save uploaded file temporarily
        temp_path = os.path.join(trimmed_folder, f"temp_{file.filename}")
        file.save(temp_path)
        
        try:
            # Use ffmpeg to create the clip
            import subprocess
            
            cmd = [
                'ffmpeg', '-i', temp_path,
                '-ss', str(start_time),
                '-t', str(end_time - start_time),
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-y',  # Overwrite output file
                clip_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                logging.error(f"FFmpeg error: {result.stderr}")
                return jsonify({'success': False, 'message': 'Failed to create video clip'}), 500
            
            # Get clip duration
            duration = end_time - start_time
            
            # Get day name
            day_name = datetime.now().strftime('%A')
            
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            # Store clip information in database or return success
            return jsonify({
                'success': True,
                'message': 'Video clip created successfully!',
                'clip_filename': clip_filename,
                'clip_path': f'trimmed/{clip_filename}',
                'duration': duration,
                'start_time': start_time,
                'end_time': end_time,
                'day_name': day_name,
                'created_at': datetime.now().isoformat()
            })
            
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Video processing timed out'}), 500
        except Exception as e:
            logging.error(f"Error creating video clip: {e}")
            return jsonify({'success': False, 'message': 'Failed to create video clip'}), 500
        finally:
            # Clean up temp file if it still exists
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except Exception as e:
        logging.error(f"Error in create_video_clip: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/trimmed-videos-dashboard')
def get_trimmed_videos_dashboard():
    """Get trimmed videos for dashboard display with date information"""
    try:
        trimmed_folder = 'static/trimmed'
        videos = []
        
        if os.path.exists(trimmed_folder):
            for file in os.listdir(trimmed_folder):
                if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    file_path = os.path.join(trimmed_folder, file)
                    file_stat = os.stat(file_path)
                    
                    # Extract creation date from filename or file stats
                    created_date = datetime.fromtimestamp(file_stat.st_mtime)
                    
                    # Get file size
                    file_size = file_stat.st_size
                    size_mb = round(file_size / (1024 * 1024), 2)
                    
                    videos.append({
                        'filename': file,
                        'path': f'trimmed/{file}',
                        'created_date': created_date.strftime('%Y-%m-%d'),
                        'created_time': created_date.strftime('%H:%M:%S'),
                        'day_name': created_date.strftime('%A'),
                        'size_mb': size_mb,
                        'full_path': file_path
                    })
            
            # Sort by creation date (newest first)
            videos.sort(key=lambda x: x['created_date'], reverse=True)
        
        return jsonify({
            'success': True,
            'videos': videos,
            'total_count': len(videos)
        })
        
    except Exception as e:
        logging.error(f"Error getting trimmed videos dashboard: {e}")
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
    """Video clipping page"""
    return render_template('clip_video.html')

@app.route('/settings')
def settings():
    """Settings page"""
    return render_template('settings.html')

@app.route('/test-whisper')
def test_whisper():
    """Whisper AI test page"""
    return render_template('test_whisper.html')

@app.route('/test-story')
def test_story():
    """Story generation test page"""
    return render_template('test_story.html')

@app.route('/api/test-gemini')
def test_gemini():
    """Test if Google Gemini AI is working"""
    try:
        if not model:
            return jsonify({
                'success': False,
                'error': 'Google Gemini AI model not available',
                'details': 'Check GOOGLE_API_KEY configuration'
            }), 500
        
        # Try a simple test prompt
        test_prompt = "Say hello in one sentence"
        logger.info("Testing Gemini AI with simple prompt...")
        
        response = model.generate_content(test_prompt)
        
        if response and hasattr(response, 'text') and response.text:
            return jsonify({
                'success': True,
                'message': 'Google Gemini AI is working!',
                'test_response': response.text,
                'model_type': str(type(model))
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Gemini response is empty or invalid',
                'response_type': str(type(response))
            }), 500
            
    except Exception as e:
        logger.error(f"Error testing Gemini AI: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Gemini AI test failed: {str(e)}'
        }), 500

@app.route('/api/test-story-simple', methods=['POST'])
def test_story_simple():
    """Simple story generation test without AI model"""
    try:
        # Debug: Log the raw request
        logger.info(f"Simple story test request received")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request headers: {dict(request.headers)}")
        logger.info(f"Request content type: {request.content_type}")
        
        # Try to get JSON data
        try:
            data = request.get_json()
            logger.info(f"Parsed JSON data: {data}")
        except Exception as json_error:
            logger.error(f"Failed to parse JSON: {json_error}")
            # Try to get form data as fallback
            data = request.form.to_dict()
            logger.info(f"Using form data as fallback: {data}")
        
        if not data:
            logger.warning("Simple story test with no data")
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        # Try different ways to get the prompt
        prompt = None
        if isinstance(data, dict):
            prompt = data.get('prompt') or data.get('text') or data.get('message')
        elif isinstance(data, str):
            prompt = data
        
        if not prompt:
            logger.warning(f"Simple story test with missing prompt. Data received: {data}")
            return jsonify({'success': False, 'error': 'No prompt provided. Please send a prompt in the request.'}), 400
        
        logger.info(f"Simple story test with prompt: {prompt[:100]}...")
        
        # Create a simple story response for testing
        test_story = f"This is a test story based on your prompt: '{prompt}'. This is just a placeholder to test if the endpoint is working correctly."
        
        parsed_story = parse_story_to_json(test_story)
        
        return jsonify({
            'success': True,
            'story': parsed_story,
            'raw_response': test_story,
            'note': 'This is a test response without AI generation'
        })
            
    except Exception as e:
        logger.error(f"Error in simple story test: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/navigate/<page>')
def navigate(page):
    """Navigation endpoint"""
    try:
        if page == 'login':
            return render_template('login.html')
        elif page == 'signup':
            return render_template('signup.html')
        elif page == 'index':
            return render_template('index.html')
        elif page == 'forgot':
            return render_template('forget.html')
        elif page == 'reset':
            token = request.args.get('token')
            return render_template('reset.html', token=token)
        else:
            return render_template('LandingPage.html', languages=LANGUAGES)
    except Exception as e:
        logging.error(f"Navigation error: {e}")
        return render_template('LandingPage.html', languages=LANGUAGES)

@app.route('/forgot')
def forgot_page():
    """Forgot password page"""
    return render_template('forget.html')

@app.route('/api/db-status')
def db_status():
    """Database status endpoint"""
    return check_db_connection()

@app.route('/api/test-db')
def test_db():
    """Test database connection"""
    try:
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("SELECT current_database();")
        db_name = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return jsonify({'status': 'success', 'message': f'Database test successful: {db_name}'})
    except psycopg2.Error as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/check-availability', methods=['POST'])
def check_availability():
    """Check if username or email is available"""
    data = request.get_json()
    if not data:
        return jsonify({'message': 'Invalid request'}), 400

    username = data.get('username', '')
    email = data.get('email', '')
    
    if not username and not email:
        return jsonify({'message': 'Please provide username or email to check'}), 400

    try:
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True
        cursor = conn.cursor()
        
        results = {}
        
        if username:
            cursor.execute("SELECT username FROM users WHERE username = %s", (username,))
            results['username_available'] = not cursor.fetchone()
        
        if email:
            cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
            results['email_available'] = not cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'availability': results
        }), 200
        
    except psycopg2.Error as e:
        logger.error(f"Database error during availability check: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

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
    
    # Additional validation
    if len(username) < 3:
        logger.warning("Signup attempt with username too short")
        return jsonify({'message': 'Username must be at least 3 characters long'}), 400
    if len(username) > 50:
        logger.warning("Signup attempt with username too long")
        return jsonify({'message': 'Username must be less than 50 characters'}), 400
    if len(password) < 6:
        logger.warning("Signup attempt with password too short")
        return jsonify({'message': 'Password must be at least 6 characters long'}), 400
    
    # Basic email validation
    if '@' not in email or '.' not in email:
        logger.warning("Signup attempt with invalid email format")
        return jsonify({'message': 'Please enter a valid email address'}), 400

    try:
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True
        cursor = conn.cursor()

        # Check both username and email in a single query to avoid race conditions
        cursor.execute("SELECT username, email FROM users WHERE username = %s OR email = %s", (username, email))
        existing_user = cursor.fetchone()
        
        if existing_user:
            cursor.close()
            conn.close()
            if existing_user[0] == username:
                logger.warning(f"Signup failed: Username {username} already exists")
                return jsonify({'message': 'Username already exists'}), 400
            else:
                logger.warning(f"Signup failed: Email {email} already exists")
                return jsonify({'message': 'Email already exists'}), 400

        # Generate a unique username if the requested one is taken
        base_username = username
        counter = 1
        while True:
            cursor.execute("SELECT username FROM users WHERE username = %s", (username,))
            if not cursor.fetchone():
                break
            username = f"{base_username}{counter}"
            counter += 1
            if counter > 100:  # Prevent infinite loop
                cursor.close()
                conn.close()
                logger.error("Could not generate unique username after 100 attempts")
                return jsonify({'message': 'Unable to create account. Please try again.'}), 500

        hashed_password = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO users (username, email, password, created_at) VALUES (%s, %s, %s, %s)",
            (username, email, hashed_password, datetime.now())
        )
        cursor.close()
        conn.close()
        
        if username != base_username:
            logger.info(f"Successful signup for email: {email} with generated username: {username}")
            return jsonify({
                'message': f'Signup successful! Username "{base_username}" was taken, so we created "{username}" for you. Redirecting to login...',
                'redirect': url_for('navigate', page='login')
            }), 200
        else:
            logger.info(f"Successful signup for email: {email} with username: {username}")
            return jsonify({
                'message': 'Signup successful! Redirecting to login...',
                'redirect': url_for('navigate', page='login')
            }), 200
            
    except psycopg2.Error as e:
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
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True
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
    except psycopg2.Error as e:
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
        conn = psycopg2.connect(**db_config)
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
    except psycopg2.Error as e:
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
        conn = psycopg2.connect(**db_config)
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

        sender_email = SMTP_USERNAME
        smtp_server = SMTP_SERVER
        smtp_port = SMTP_PORT
        smtp_password = SMTP_PASSWORD

        if all([sender_email, smtp_password]):
            try:
                msg = MIMEText(
                    f"Your password reset link: {url_for('navigate', page='reset', token=token, _external=True)}\n"
                    f"It expires at {expires_at.strftime('%Y-%m-%d %H:%M:%S')}.",
                    'plain'
                )
                msg['Subject'] = 'Password Reset Request - AI Auto-Posting'
                msg['From'] = sender_email
                msg['To'] = email

                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    server.starttls()
                    server.login(sender_email, smtp_password)
                    server.sendmail(sender_email, email, msg.as_string())
                
                logger.info(f"Password reset email sent to: {email}")
                return jsonify({
                    'message': 'Password reset email sent successfully. Please check your inbox.',
                    'redirect': url_for('navigate', page='login')
                }), 200
                
            except Exception as e:
                logger.error(f"Failed to send password reset email: {e}")
                return jsonify({
                    'message': 'Password reset token generated but email delivery failed. Please contact support.',
                    'token': token
                }), 200
        else:
            # For development/demo purposes
            logger.warning("SMTP credentials not configured, returning token for demo")
            return jsonify({
                'message': 'Password reset token generated successfully. Please check server logs for token.',
                'token': token,
                'redirect': url_for('navigate', page='reset', token=token)
            }), 200
            
    except Exception as e:
        logger.error(f"Error during forgot password: {e}")
        return jsonify({'message': 'An error occurred during password reset. Please try again.'}), 500

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
        conn = psycopg2.connect(**db_config)
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
    except psycopg2.Error as e:
        logger.error(f"Database error during reset password: {str(e)}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    email = session.get('user', 'unknown')
    session.pop('user', None)
    logger.info(f"User logged out: {email}")
    return jsonify({'message': 'Logged out successfully', 'redirect': url_for('navigate', page='index')}), 200

@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    """Transcribe uploaded audio/video file using Whisper AI"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'File type not supported'}), 400
        
        # Save uploaded file temporarily
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        try:
            # Generate transcript
            if file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                result = generate_transcript_from_video(file_path)
            else:
                result = generate_transcript_from_audio(file_path)
            
            # Clean up uploaded file
            if os.path.exists(file_path):
                os.remove(file_path)
            
            return jsonify(result)
            
        except Exception as e:
            # Clean up uploaded file on error
            if os.path.exists(file_path):
                os.remove(file_path)
            raise e
            
    except Exception as e:
        logger.error(f"Error in transcribe endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate_story', methods=['POST'])
def generate_story():
    """Generate story content using Google Gemini AI with Lucy's voiceover script format"""
    try:
        # Debug: Log the raw request
        logger.info(f"Story generation request received")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request headers: {dict(request.headers)}")
        logger.info(f"Request content type: {request.content_type}")
        
        # Try to get JSON data
        try:
            data = request.get_json()
            logger.info(f"Parsed JSON data: {data}")
        except Exception as json_error:
            logger.error(f"Failed to parse JSON: {json_error}")
            # Try to get form data as fallback
            data = request.form.to_dict()
            logger.info(f"Using form data as fallback: {data}")
        
        if not data:
            logger.warning("Story generation attempt with no data")
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        # Extract transcript/content
        transcript = data.get('text') if isinstance(data, dict) else None
        
        # Fallback to old prompt format if text is not provided
        if not transcript:
        prompt = None
        if isinstance(data, dict):
                prompt = data.get('prompt') or data.get('message')
        elif isinstance(data, str):
            prompt = data
        
        if not prompt:
                logger.warning(f"Story generation attempt with missing content. Data received: {data}")
                return jsonify({'success': False, 'error': 'No content provided. Please send text or prompt in the request.'}), 400
        
            transcript = prompt
            logger.info(f"Story generation request received with prompt: {transcript[:100]}...")
        else:
            logger.info(f"Story generation request received with transcript: {transcript[:100]}...")
        
        # Check if model is available
        if not model:
            logger.error("Google Gemini AI model not available")
            return jsonify({'success': False, 'error': 'AI model not available. Please check GOOGLE_API_KEY configuration.'}), 500
        
        logger.info("Google Gemini AI model is available, proceeding with generation...")
        
        try:
            # Extract framing and story from transcript
            framing, story = extract_framing_and_story(transcript)
            
            # Get format and custom prompt from request
            story_format = data.get('format', 'lucy')
            use_custom_prompt = data.get('useCustomPrompt', False)
            custom_prompt = data.get('customPrompt', '')

            # Create prompt based on format and custom input
            if use_custom_prompt and custom_prompt.strip():
                # Use custom prompt provided by user
                prompt = f"""
{custom_prompt}

Input Content:
{framing}
{story}

Please generate a story based on the above custom prompt and input content.
"""
            else:
                # Use predefined format prompts
                if story_format == 'lucy':
                    prompt = f"""
You are a professional story design assistant creating a voiceover script for Lucy from "Lucy & The Wealth Machine."

Input:
- Framing: "{framing}"
- Story: "{story}"

Task: Create a professional 4-part voiceover script with CLEAN FORMATTING and PROPER LINE BREAKS.

CRITICAL: You MUST include line breaks between every section. The output must be properly formatted with spacing.

EXACT FORMAT EXAMPLE - Copy this structure precisely:

Lucy & The Wealth Machine: The Psychology of Viral Video Openings

Core Lessons:
- Three-step hook: Context Lean, Scroll Stop Interjection, Contrarian Snapback
- Visual hooks: Combine text and motion
- Build common ground with cultural references
- Compress value with short sentences

🎬 The Hook That Almost Didn't Work

"Today: video hooks. Want better videos? Better hooks."
"Forget lists of viral hooks."
"Understand the psychology."

"Hi, I'm Lucy. A million followers, billions of views. I learned video hooks the hard way. Catchy phrases? Nope. It's about a curiosity loop—instant attention. My three-step formula works every time. But first, a hook that nearly flopped…"

"What's your biggest video intro mistake? Let me know!"


🎬 The Three-Step Formula

"Context lean. Scroll stop. Contrarian snapback."
"It's not about being clever."
"It's about being strategic."

"The psychology is simple: grab attention, create curiosity, deliver value. I tested this formula across thousands of videos. The results? Consistent engagement, predictable growth, sustainable success."

"Which step do you struggle with most?"


🎬 Visual Hooks That Convert

"Text plus motion equals magic."
"Your hook needs to move."
"Literally."

"I discovered that combining bold text with subtle motion increases retention by 40%. It's not about fancy effects—it's about guiding the viewer's eye to your key message."

"What's your favorite visual hook technique?"


🎬 Building Your Hook System

"Systems beat strategies every time."
"Create your hook framework."
"Then scale it."

"I built a hook system that works across all platforms. Same psychology, different execution. Now my team can create engaging content consistently, without guesswork."

"Ready to build your own hook system?"


Ready to master video hooks? Let's build your system together. Visit [website] to start.

---

**MANDATORY FORMATTING REQUIREMENTS:**

1. **ALWAYS start with a blank line after the title**
2. **ALWAYS include line breaks between Core Lessons and first segment**
3. **ALWAYS include line breaks before and after each segment title**
4. **ALWAYS include line breaks between segments (no CUT markers)**
5. **ALWAYS include line breaks before Final CTA**
6. **DO NOT use markdown symbols like #, **, or >**
7. **ALWAYS use quotes around dialogue**
8. **ALWAYS maintain consistent spacing throughout**

**STRUCTURE REQUIREMENTS:**
- Title (no markdown symbols)
- Core Lessons section with bullet points
- 4 segments with 🎬 titles
- Each segment has 3 quoted hooks, main content, and question
- No CUT markers - flow directly from one segment to the next
- Final call-to-action content at the end (no label)

**TONE REQUIREMENTS:**
- Calm, confident, informative, slightly playful, British, non-salesy
- Use Lucy's authentic voice
- Short sentences, avoid filler words
- Each segment ends with an engaging question

**CRITICAL: Ensure proper line breaks and spacing throughout the entire script. Do not compress the text together.**

**FINAL INSTRUCTION: Generate clean, readable output without any markdown formatting symbols. Use only quotes, emojis, and proper line breaks.**
"""

                elif story_format == 'narrative':
                    prompt = f"""
You are a creative storytelling assistant creating engaging narrative stories.

Input:
- Framing: "{framing}"
- Story: "{story}"

Task: Create a compelling narrative story with proper structure and formatting.

EXACT FORMAT EXAMPLE - Copy this structure precisely:

The Midnight Garden

Chapter 1: The Discovery

Sarah never expected to find magic in her grandmother's old garden. The rusted gate creaked open, revealing a world that seemed to breathe with its own life. Moonlight filtered through the ancient oak trees, casting shadows that danced like living creatures.

She stepped forward, her heart pounding with a mixture of fear and wonder. The air was thick with the scent of roses and something else—something she couldn't quite identify.

Chapter 2: The First Encounter

The garden had secrets, and Sarah was about to uncover them all. As she walked deeper into the maze of hedges and flower beds, she heard a soft whisper carried by the wind.

"Welcome, child of the old blood."

Sarah spun around, but there was no one there. Only the rustling of leaves and the distant hoot of an owl.

Chapter 3: The Revelation

Magic wasn't just real—it was alive in this garden. Sarah discovered that her grandmother had been a guardian of ancient knowledge, protecting a portal between worlds.

The roses weren't just flowers; they were sentinels, watching over the balance between light and shadow.

Chapter 4: The Choice

Now Sarah faced the greatest decision of her life. Would she take up her grandmother's mantle and become the new guardian, or would she close the gate forever and return to the ordinary world?

The garden waited, its magic pulsing with anticipation.

---

**STRUCTURE REQUIREMENTS:**
- Engaging title
- 4 chapters with descriptive titles
- Rich descriptive language
- Character development
- Emotional arcs
- Proper paragraph breaks
- Clean formatting without markdown symbols

**TONE REQUIREMENTS:**
- Engaging and immersive
- Descriptive and atmospheric
- Emotional depth
- Suspenseful elements
- Satisfying conclusion

**CRITICAL: Ensure proper line breaks and spacing throughout the entire story. Do not compress the text together.**
"""

                elif story_format == 'business':
                    prompt = f"""
You are a professional business content creator specializing in case studies and business storytelling.

Input:
- Framing: "{framing}"
- Story: "{story}"

Task: Create a professional business case study with clear structure and actionable insights.

EXACT FORMAT EXAMPLE - Copy this structure precisely:

Case Study: Digital Transformation Success Story

Executive Summary

Company X, a traditional manufacturing firm with 50 years in business, faced declining market share and operational inefficiencies. Through a comprehensive digital transformation initiative, they achieved 40% cost reduction and 60% increase in customer satisfaction within 18 months.

The Challenge

Company X struggled with:
- Outdated manual processes causing 30% production delays
- Customer complaints about delivery times and product quality
- Employee turnover at 25% annually
- Market share declining by 15% year-over-year

The Solution

Implemented a three-phase digital transformation:

Phase 1: Process Automation
- Deployed IoT sensors across production lines
- Implemented real-time monitoring systems
- Automated quality control processes

Phase 2: Customer Experience Enhancement
- Launched customer portal for order tracking
- Integrated CRM system for personalized service
- Developed mobile app for easy communication

Phase 3: Employee Engagement
- Digital training platforms for skill development
- Performance tracking and recognition systems
- Flexible work arrangements

The Results

Quantifiable outcomes achieved:
- 40% reduction in operational costs
- 60% improvement in customer satisfaction scores
- 25% increase in employee retention
- 35% growth in market share
- 50% faster time-to-market for new products

Key Learnings

1. Leadership commitment is crucial for transformation success
2. Employee buy-in requires clear communication and training
3. Technology must align with business objectives
4. Customer feedback should drive solution design
5. Continuous improvement processes ensure long-term success

Conclusion

Company X's digital transformation demonstrates that traditional businesses can successfully adapt to modern market demands through strategic technology implementation and cultural change.

---

**STRUCTURE REQUIREMENTS:**
- Professional title and executive summary
- Clear problem statement
- Detailed solution approach
- Quantifiable results
- Key learnings and insights
- Professional conclusion
- Clean formatting without markdown symbols

**TONE REQUIREMENTS:**
- Professional and authoritative
- Data-driven and analytical
- Clear and concise
- Actionable insights
- Business-focused language

**CRITICAL: Ensure proper line breaks and spacing throughout the entire case study. Do not compress the text together.**
"""

                else:
                    # Default to Lucy format
                    prompt = f"""
You are a professional story design assistant creating a voiceover script for Lucy from "Lucy & The Wealth Machine."

Input:
- Framing: "{framing}"
- Story: "{story}"

Task: Create a professional 4-part voiceover script with CLEAN FORMATTING and PROPER LINE BREAKS.

CRITICAL: You MUST include line breaks between every section. The output must be properly formatted with spacing.

EXACT FORMAT EXAMPLE - Copy this structure precisely:

Lucy & The Wealth Machine: The Psychology of Viral Video Openings

Core Lessons:
- Three-step hook: Context Lean, Scroll Stop Interjection, Contrarian Snapback
- Visual hooks: Combine text and motion
- Build common ground with cultural references
- Compress value with short sentences

🎬 The Hook That Almost Didn't Work

"Today: video hooks. Want better videos? Better hooks."
"Forget lists of viral hooks."
"Understand the psychology."

"Hi, I'm Lucy. A million followers, billions of views. I learned video hooks the hard way. Catchy phrases? Nope. It's about a curiosity loop—instant attention. My three-step formula works every time. But first, a hook that nearly flopped…"

"What's your biggest video intro mistake? Let me know!"


🎬 The Three-Step Formula

"Context lean. Scroll stop. Contrarian snapback."
"It's not about being clever."
"It's about being strategic."

"The psychology is simple: grab attention, create curiosity, deliver value. I tested this formula across thousands of videos. The results? Consistent engagement, predictable growth, sustainable success."

"Which step do you struggle with most?"


🎬 Visual Hooks That Convert

"Text plus motion equals magic."
"Your hook needs to move."
"Literally."

"I discovered that combining bold text with subtle motion increases retention by 40%. It's not about fancy effects—it's about guiding the viewer's eye to your key message."

"What's your favorite visual hook technique?"


🎬 Building Your Hook System

"Systems beat strategies every time."
"Create your hook framework."
"Then scale it."

"I built a hook system that works across all platforms. Same psychology, different execution. Now my team can create engaging content consistently, without guesswork."

"Ready to build your own hook system?"


Ready to master video hooks? Let's build your system together. Visit [website] to start.

---

**MANDATORY FORMATTING REQUIREMENTS:**

1. **ALWAYS start with a blank line after the title**
2. **ALWAYS include line breaks between Core Lessons and first segment**
3. **ALWAYS include line breaks before and after each segment title**
4. **ALWAYS include line breaks between segments (no CUT markers)**
5. **ALWAYS include line breaks before Final CTA**
6. **DO NOT use markdown symbols like #, **, or >**
7. **ALWAYS use quotes around dialogue**
8. **ALWAYS maintain consistent spacing throughout**

**STRUCTURE REQUIREMENTS:**
- Title (no markdown symbols)
- Core Lessons section with bullet points
- 4 segments with 🎬 titles
- Each segment has 3 quoted hooks, main content, and question
- No CUT markers - flow directly from one segment to the next
- Final call-to-action content at the end (no label)

**TONE REQUIREMENTS:**
- Calm, confident, informative, slightly playful, British, non-salesy
- Use Lucy's authentic voice
- Short sentences, avoid filler words
- Each segment ends with an engaging question

**CRITICAL: Ensure proper line breaks and spacing throughout the entire script. Do not compress the text together.**

**FINAL INSTRUCTION: Generate clean, readable output without any markdown formatting symbols. Use only quotes, emojis, and proper line breaks.**
"""
            
            response = model.generate_content(prompt)
            
            logger.info(f"Received response from Gemini: {type(response)}")
            
            if response and hasattr(response, 'text') and response.text:
                logger.info(f"Generated content length: {len(response.text)} characters")
                story_text = response.text.strip()
                clean_story = clean_lucy_story(story_text)
                
                # Parse the story into structured format for frontend compatibility
                parsed_story = parse_story_to_json(clean_story)
                
                logger.info(f"Story parsed successfully. Title: {parsed_story.get('title', 'No title')}, Word count: {parsed_story.get('word_count', 'Unknown')}")
                
                return jsonify({
                    'success': True,
                    'story': parsed_story,
                    'raw_response': clean_story
                })
            else:
                logger.warning("Gemini response is empty or invalid")
                return jsonify({'success': False, 'error': 'No response from AI model. Response was empty or invalid.'}), 500
                
        except Exception as e:
            logger.error(f"Error generating story with Gemini: {str(e)}")
            return jsonify({'success': False, 'error': f'AI generation failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Error in generate_story endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/upload-file', methods=['POST'])
def upload_file():
    """Upload audio/video file for transcription"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'File type not supported'}), 400
        
        # Save uploaded file
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        return jsonify({
            'success': True,
            'message': 'File uploaded successfully',
            'filename': filename,
            'file_path': file_path
        })
        
    except Exception as e:
        logger.error(f"Error in upload_file endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Main execution
if __name__ == '__main__':
    try:
        # Test database connection on startup
        db_status_result = check_db_connection()
        if db_status_result['status'] == 'success':
            logger.info("Database connection successful on startup")
        else:
            logger.warning(f"Database connection warning on startup: {db_status_result['message']}")
        
        # Start the Flask application
        port = PORT
        logger.info(f"Starting AI Auto-Posting application on port {port}")
        app.run(host='0.0.0.0', port=port, debug=FLASK_DEBUG, threaded=True)
        
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise
