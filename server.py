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
from threading import Thread
import time as _time

# YouTube API imports
import google_auth_httplib2
import google_auth_oauthlib
import googleapiclient.discovery
import googleapiclient.errors
import googleapiclient.http
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- YouTube credentials hydration for container environments ---
def _maybe_write_file(path: str, content: str) -> None:
    try:
        directory = os.path.dirname(path) or '.'
        os.makedirs(directory, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"Failed to write file {path}: {e}")

def _from_env_json(var_plain: str, var_b64: str) -> str | None:
    value = os.environ.get(var_plain)
    if value:
        return value
    b64 = os.environ.get(var_b64)
    if b64:
        try:
            import base64
            return base64.b64decode(b64).decode('utf-8')
        except Exception:
            logger.warning(f"Invalid base64 provided in {var_b64}")
            return None
    return None

def hydrate_youtube_credentials_from_env() -> None:
    """Hydrate YouTube credentials from env vars if present.

    Supported:
    - CLIENT_SECRETS_JSON or CLIENT_SECRETS_JSON_B64 -> writes ./client_secrets.json
    - YOUTUBE_TOKEN_JSON or YOUTUBE_TOKEN_JSON_B64 -> writes ./static/youtube_token.json
    - YOUTUBE_TOKEN_FILE can override destination path for the token
    """
    try:
        # client_secrets.json
        client_json = _from_env_json('CLIENT_SECRETS_JSON', 'CLIENT_SECRETS_JSON_B64')
        if client_json and not os.path.exists('client_secrets.json'):
            _maybe_write_file('client_secrets.json', client_json)
            logger.info('Wrote client_secrets.json from environment')

        # youtube_token.json
        token_json = _from_env_json('YOUTUBE_TOKEN_JSON', 'YOUTUBE_TOKEN_JSON_B64')
        if token_json:
            token_dest = os.environ.get('YOUTUBE_TOKEN_FILE') or os.path.join('static', 'youtube_token.json')
            if not os.path.exists(token_dest):
                _maybe_write_file(token_dest, token_json)
                logger.info(f'Wrote YouTube token to {token_dest} from environment')
    except Exception as e:
        logger.warning(f"Failed hydrating YouTube credentials from env: {e}")

# Run hydration early on import
hydrate_youtube_credentials_from_env()

# Sync baked token into mounted static volume if present and missing
try:
    baked_token_path = 'youtube_token.json'
    static_token_path = os.path.join('static', 'youtube_token.json')
    if os.path.exists(baked_token_path) and not os.path.exists(static_token_path):
        os.makedirs('static', exist_ok=True)
        with open(baked_token_path, 'r', encoding='utf-8') as _src, open(static_token_path, 'w', encoding='utf-8') as _dst:
            _dst.write(_src.read())
        logger.info('Seeded static/youtube_token.json from baked youtube_token.json')
except Exception as _e:
    logger.warning(f'Failed to seed static youtube token: {_e}')

# --- YouTube OAuth Web Flow (for production authorization) ---
def _get_youtube_scopes():
    return ["https://www.googleapis.com/auth/youtube.upload"]

def _get_redirect_uri(path: str = '/api/youtube/auth/callback') -> str:
    try:
        # Build absolute redirect URI based on current request host
        base = request.host_url.rstrip('/') if request else os.environ.get('PUBLIC_BASE_URL', '')
        # Force https scheme to match OAuth console configuration
        if base.startswith('http://'):
            base = 'https://' + base[len('http://'):]
        if not base:
            # Fallback to Fly app URL if provided
            app_name = os.environ.get('FLY_APP_NAME') or 'ai-auto-posting'
            base = f"https://{app_name}.fly.dev"
        return f"{base}{path}"
    except Exception:
        app_name = os.environ.get('FLY_APP_NAME') or 'ai-auto-posting'
        return f"https://{app_name}.fly.dev{path}"

def _build_oauth_flow(redirect_uri: str, state: str | None = None):
    scopes = _get_youtube_scopes()
    import google_auth_oauthlib.flow as _flow
    if state:
        return _flow.Flow.from_client_secrets_file(
            'client_secrets.json', scopes=scopes, state=state, redirect_uri=redirect_uri
        )
    return _flow.Flow.from_client_secrets_file(
        'client_secrets.json', scopes=scopes, redirect_uri=redirect_uri
    )

def youtube_auth_start():
    try:
        if not os.path.exists('client_secrets.json'):
            return jsonify({
                'success': False,
                'error': 'client_secrets.json missing on server'
            }), 500
        redirect_uri = _get_redirect_uri()
        flow = _build_oauth_flow(redirect_uri)
        auth_url, state = flow.authorization_url(
            access_type='offline', include_granted_scopes='true', prompt='consent'
        )
        session['yt_oauth_state'] = state
        return jsonify({'success': True, 'auth_url': auth_url})
    except Exception as e:
        logger.error(f"YouTube OAuth start error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def youtube_auth_callback():
    try:
        state = session.get('yt_oauth_state')
        redirect_uri = _get_redirect_uri()
        flow = _build_oauth_flow(redirect_uri, state=state)
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        save_path = _get_youtube_token_file(for_save=True)
        with open(save_path, 'w') as f:
            f.write(creds.to_json())
        # Clear state
        session.pop('yt_oauth_state', None)
        return jsonify({'success': True, 'message': 'YouTube authorized', 'token_file': save_path})
    except Exception as e:
        logger.error(f"YouTube OAuth callback error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

# YouTube Upload Functions (Simplified)
def _get_youtube_token_file(for_save: bool = False) -> str:
    """Return the path to the shared YouTube token file, preferring the mounted static volume.

    If for_save is True, returns the preferred save location even if it does not exist yet.
    """
    candidates = []
    env_path = os.environ.get('YOUTUBE_TOKEN_FILE')
    if env_path:
        candidates.append(env_path)
    # Prefer mounted volume path for persistence
    candidates.append(os.path.join('static', 'youtube_token.json'))
    # Fallback to project root (not persisted across deploys)
    candidates.append('youtube_token.json')

    if for_save:
        # Choose first candidate directory that is writable or can be created
        for path in candidates:
            try:
                directory = os.path.dirname(path) or '.'
                os.makedirs(directory, exist_ok=True)
                return path
            except Exception:
                continue
        return candidates[-1]
    else:
        for path in candidates:
            if path and os.path.exists(path):
                # If we find a baked token at root but not in static volume, copy it for persistence
                try:
                    if path == 'youtube_token.json':
                        static_path = os.path.join('static', 'youtube_token.json')
                        if not os.path.exists(static_path):
                            os.makedirs('static', exist_ok=True)
                            with open(path, 'r', encoding='utf-8') as _src, open(static_path, 'w', encoding='utf-8') as _dst:
                                _dst.write(_src.read())
                            logger.info('Copied root youtube_token.json into static volume for persistence')
                except Exception:
                    pass
                return path
        # Default save location if none exist
        return os.path.join('static', 'youtube_token.json')


def authenticate_youtube():
    """Authenticate with YouTube API using a shared app credential cached in youtube_token.json."""
    try:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        token_file = _get_youtube_token_file(for_save=False)
        client_secrets_file = 'client_secrets.json'

        credentials = None
        if token_file and os.path.exists(token_file):
            try:
                credentials = Credentials.from_authorized_user_file(token_file, scopes)
            except Exception:
                credentials = None
        # Fallback: if not found in static, check app root (Dockerfile copies it there)
        if (not credentials) and os.path.exists('youtube_token.json') and token_file != 'youtube_token.json':
            try:
                credentials = Credentials.from_authorized_user_file('youtube_token.json', scopes)
            except Exception:
                credentials = None

        # Refresh or obtain new token
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                save_path = _get_youtube_token_file(for_save=True)
                with open(save_path, 'w') as token:
                    token.write(credentials.to_json())
            except Exception as e:
                logger.warning(f"Token refresh failed, will re-auth: {e}")
                credentials = None

        if not credentials or not credentials.valid:
            if not os.path.exists(client_secrets_file):
                logger.error(f"Client secrets file not found: {client_secrets_file}")
                return None
            # In production, we cannot run a local server flow. Require pre-provisioned token.
            if os.environ.get('FLY_APP_NAME') or os.environ.get('FLY_MACHINE_ID') or os.environ.get('FLASK_ENV') == 'production':
                logger.error('YouTube token not found in production. Please pre-provision youtube_token.json in the static volume.')
                return None
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(client_secrets_file, scopes)
            credentials = flow.run_local_server(port=8080)
            save_path = _get_youtube_token_file(for_save=True)
            with open(save_path, 'w') as token:
                token.write(credentials.to_json())

        youtube = googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
        logger.info("YouTube API authenticated (shared app token)")
        return youtube
    except Exception as e:
        logger.error(f"YouTube authentication error: {e}")
        return None

def upload_video_simple(video_path, title, description, tags, privacy="private"):
    """Upload video to YouTube with simplified approach"""
    try:
        logger.info(f"Starting YouTube upload for: {title}")
        logger.info(f"Video path: {video_path}")
        
        youtube = authenticate_youtube()
        if not youtube:
            return {
                "success": False,
                "error": "Failed to authenticate with YouTube API",
                "hint": "Ensure client_secrets.json exists and youtube_token.json is provisioned (static/youtube_token.json)."
            }
        
        request_body = {
            "snippet": {
                "categoryId": "22",
                "title": title[:100],  # YouTube title limit
                "description": description[:5000],  # YouTube description limit
                "tags": tags or []
            },
            "status": {
                "privacyStatus": privacy
            }
        }
        
        logger.info(f"Upload metadata - Title: {title}, Description length: {len(description)}")
        
        media_file = googleapiclient.http.MediaFileUpload(
            video_path, chunksize=1024*1024, resumable=True)
        
        request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media_file
        )
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Upload progress: {progress}%")
        
        video_id = response['id']
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        logger.info(f"Video uploaded successfully: {video_url}")
        
        return {
            "success": True,
            "video_id": video_id,
            "video_url": video_url,
            "youtube_url": video_url,  # For frontend compatibility
            "title": title,
            "upload_time": datetime.now().isoformat()
        }
        
    except googleapiclient.errors.HttpError as http_err:
        try:
            err_content = http_err.content.decode('utf-8') if hasattr(http_err, 'content') else str(http_err)
        except Exception:
            err_content = str(http_err)
        logger.error(f"YouTube HTTP error: {http_err}\nContent: {err_content}")
        return {"success": False, "error": f"YouTube HTTP error: {http_err}", "details": err_content}
    except Exception as e:
        logger.error(f"Video upload error: {e}")
        return {"success": False, "error": str(e)}

# Global YouTube service instance (using simplified functions)

# Import configuration
from config import *
from google.auth.transport.requests import Request as _GoogleRequest
try:
    from google.oauth2.credentials import Credentials as _OAuthCreds
except Exception:
    _OAuthCreds = None
import threading
import time

# Auto-provision credentials from environment (base64)
try:
    import base64
    def _write_if_env_base64(env_key: str, dest_path: str) -> bool:
        try:
            b64 = os.environ.get(env_key)
            if not b64:
                return False
            # Ensure destination directory exists
            dest_dir = os.path.dirname(dest_path) or '.'
            os.makedirs(dest_dir, exist_ok=True)
            # Decode and write
            with open(dest_path, 'wb') as f:
                f.write(base64.b64decode(b64))
            logger.info(f"Wrote credential from {env_key} to {dest_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed writing {dest_path} from {env_key}: {e}")
            return False

    # Write client secrets if provided
    _write_if_env_base64('CLIENT_SECRETS_JSON_BASE64', os.path.join(os.getcwd(), 'client_secrets.json'))
    # Write token into static so it persists on volume
    _write_if_env_base64('YOUTUBE_TOKEN_JSON_BASE64', os.path.join('static', 'youtube_token.json'))
except Exception:
    pass

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.secret_key = SECRET_KEY
if not app.secret_key:
    logger.error("SECRET_KEY is not set in configuration")
    raise ValueError("SECRET_KEY must be set in config.py")

# Configure session and base folders
app.config['UPLOAD_FOLDER'] = 'static/audio'  # legacy; per-user paths used in handlers
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
Session(app)

# Per-user storage helpers
def get_session_user() -> dict:
    """Return current session user info or None."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    return {
        'id': user_id,
        'username': session.get('username'),
        'email': session.get('user') or session.get('email')
    }

def get_user_base_dir(user_id: int) -> str:
    return os.path.join('static', 'users', str(user_id))

def get_user_subdir(user_id: int, subdir: str) -> str:
    path = os.path.join(get_user_base_dir(user_id), subdir)
    os.makedirs(path, exist_ok=True)
    return path

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

# Register deferred OAuth routes now that app exists
try:
    app.add_url_rule('/api/youtube/auth/start', view_func=youtube_auth_start, methods=['GET'])
    app.add_url_rule('/api/youtube/auth/callback', view_func=youtube_auth_callback, methods=['GET'])
except Exception:
    pass

# Expand allowed file extensions
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'mp4', 'mov', 'm4a', 'avi', 'mkv', 'webm', 'flac', 'aac', 'ogg', 'txt', 'doc', 'docx', 'pdf'}
ALLOWED_EXTENSIONS_EDIT = {'mp4', 'mov'}

# Analytics helpers: YouTube data fetchers
def _yt_service():
    """Return an authenticated YouTube client for analytics.
    Uses analytics.json (root) with long-lived refresh token; auto-refresh & persist.
    Never falls back to upload token as it has insufficient permissions for analytics.
    """
    # analytics.json path - this is the only valid path for analytics
    analytics_path = os.path.join(os.getcwd(), 'analytics.json')
    if not os.path.exists(analytics_path):
        logger.warning("analytics.json not found. Use /api/analytics/device/start to begin authentication.")
        return None
    
    try:
        with open(analytics_path, 'r') as f:
            data = json.load(f)
        
        # Check if this is still the initial installed client config
        if 'installed' in data:
            logger.info("analytics.json contains installed client. Use /api/analytics/device/start to complete authentication.")
            return None
        
        # Check for proper analytics token structure
        token = data.get('token') or data.get('access_token')
        refresh_token = data.get('refresh_token')
        client_id = data.get('client_id')
        client_secret = data.get('client_secret')
        
        if not all([token, refresh_token, client_id, client_secret]):
            logger.warning("analytics.json missing required fields. Use /api/analytics/device/start to complete authentication.")
            return None
        
        token_uri = data.get('token_uri') or 'https://oauth2.googleapis.com/token'
        scopes = data.get('scopes') or ['https://www.googleapis.com/auth/youtube.readonly']
        
        creds = _OAuthCreds(
            token=token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes
        )
        
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(_GoogleRequest())
                # persist refresh result back to file for lifetime use
                persist = {
                    'token': creds.token,
                    'refresh_token': creds.refresh_token,
                    'token_uri': token_uri,
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'scopes': scopes,
                    'expiry': getattr(creds, 'expiry', None).isoformat() if getattr(creds, 'expiry', None) else ''
                }
                with open(analytics_path, 'w') as f:
                    json.dump(persist, f)
                logger.info("Analytics token refreshed successfully")
            except Exception as e:
                logger.warning(f"Analytics token refresh failed: {e}")
                return None
        
        if creds and (creds.valid or creds.refresh_token):
            import googleapiclient.discovery
            youtube = googleapiclient.discovery.build('youtube', 'v3', credentials=creds)
            logger.info("YouTube analytics API authenticated successfully")
            return youtube
        else:
            logger.warning("Analytics credentials are invalid and cannot be refreshed")
            return None
            
    except Exception as e:
        logger.error(f"Failed analytics.json auth: {e}")
        return None

# -------- Analytics Device Flow (for installed client in analytics.json) --------
_DEVICE_STATE_PATH = os.path.join(os.getcwd(), 'analytics_device.json')

def _read_json_safe(path: str):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return None

def _write_json_safe(path: str, payload: dict):
    try:
        with open(path, 'w') as f:
            json.dump(payload, f)
        return True
    except Exception as e:
        logger.warning(f"Failed to write {path}: {e}")
        return False

def _analytics_client_from_installed():
    cfg = _read_json_safe(os.path.join(os.getcwd(), 'analytics.json'))
    if not cfg or 'installed' not in cfg:
        return None
    return cfg['installed']

@app.route('/api/analytics/device/start', methods=['POST'])
def api_analytics_device_start():
    try:
        installed = _analytics_client_from_installed()
        if not installed:
            return jsonify({'success': False, 'message': 'analytics.json (installed client) not found'}), 400
        client_id = installed.get('client_id')
        client_secret = installed.get('client_secret')
        if not client_id or not client_secret:
            return jsonify({'success': False, 'message': 'client_id/client_secret missing in analytics.json'}), 400
        scope = 'https://www.googleapis.com/auth/youtube.readonly'
        import requests
        r = requests.post('https://oauth2.googleapis.com/device/code', data={
            'client_id': client_id,
            'scope': scope
        })
        if r.status_code != 200:
            return jsonify({'success': False, 'message': 'Device code request failed'}), 500
        data = r.json()
        state = {
            'client_id': client_id,
            'client_secret': client_secret,
            'device_code': data['device_code'],
            'user_code': data['user_code'],
            'verification_url': data.get('verification_url') or data.get('verification_uri'),
            'interval': data.get('interval', 5),
            'expires_at': time.time() + int(data.get('expires_in', 1800))
        }
        _write_json_safe(_DEVICE_STATE_PATH, state)
        return jsonify({'success': True, 'verification_url': state['verification_url'], 'user_code': state['user_code']})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def _device_poll_exchange():
    state = _read_json_safe(_DEVICE_STATE_PATH)
    if not state:
        return {'success': False, 'ready': False, 'message': 'No device flow in progress'}
    if time.time() > state.get('expires_at', 0):
        return {'success': False, 'ready': False, 'expired': True, 'message': 'Device code expired'}
    import requests
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': state['client_id'],
        'client_secret': state['client_secret'],
        'device_code': state['device_code'],
        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'
    })
    if r.status_code == 200:
        tok = r.json()
        # Persist to normalized analytics.json format
        persist = {
            'token': tok.get('access_token', ''),
            'refresh_token': tok.get('refresh_token', ''),
            'token_uri': 'https://oauth2.googleapis.com/token',
            'client_id': state['client_id'],
            'client_secret': state['client_secret'],
            'scopes': ['https://www.googleapis.com/auth/youtube.readonly'],
            'expiry': ''
        }
        _write_json_safe(os.path.join(os.getcwd(), 'analytics.json'), persist)
        try:
            os.remove(_DEVICE_STATE_PATH)
        except Exception:
            pass
        return {'success': True, 'ready': True}
    else:
        try:
            err = r.json().get('error')
        except Exception:
            err = None
        # authorization_pending or slow_down are expected while user authorizes
        return {'success': True, 'ready': False, 'error': err}

@app.route('/api/analytics/device/status', methods=['GET'])
def api_analytics_device_status():
    try:
        res = _device_poll_exchange()
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'ready': False, 'message': str(e)}), 500

def _list_channel_uploads(youtube, max_items: int | None = None):
    # Get uploads playlist and list recent videos with statistics
    try:
        ch_resp = youtube.channels().list(part="contentDetails,snippet,statistics", mine=True).execute()
        if not ch_resp.get('items'):
            return [], {}
        channel = ch_resp['items'][0]
        uploads_playlist_id = channel['contentDetails']['relatedPlaylists']['uploads']
        channel_meta = {
            'title': channel['snippet']['title'],
            'description': channel['snippet'].get('description',''),
            'country': channel['snippet'].get('country',''),
            'publishedAt': channel['snippet'].get('publishedAt',''),
            'viewCount': int(channel['statistics'].get('viewCount','0')),
            'subscriberCount': int(channel['statistics'].get('subscriberCount','0')),
            'videoCount': int(channel['statistics'].get('videoCount','0')),
        }
        videos = []
        next_page = None
        # Fetch until no next page or until max_items reached (if provided)
        fetched = 0
        hard_cap = max_items if (isinstance(max_items, int) and max_items > 0) else 1000
        while True and fetched < hard_cap:
            pl_items = youtube.playlistItems().list(part="contentDetails,snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page).execute()
            vid_ids = [it['contentDetails']['videoId'] for it in pl_items.get('items', [])]
            if vid_ids:
                stats_resp = youtube.videos().list(part="snippet,statistics,contentDetails", id=','.join(vid_ids)).execute()
                for v in stats_resp.get('items', []):
                    sn = v.get('snippet', {})
                    st = v.get('statistics', {})
                    cd = v.get('contentDetails', {})
                    videos.append({
                        'id': v['id'],
                        'title': sn.get('title',''),
                        'description': sn.get('description',''),
                        'publishedAt': sn.get('publishedAt',''),
                        'channelTitle': sn.get('channelTitle',''),
                        'tags': sn.get('tags', []),
                        'categoryId': sn.get('categoryId',''),
                        'duration': cd.get('duration',''),
                        'dimension': cd.get('dimension',''),
                        'definition': cd.get('definition',''),
                        'viewCount': int(st.get('viewCount','0') or 0),
                        'likeCount': int(st.get('likeCount','0') or 0),
                        'commentCount': int(st.get('commentCount','0') or 0),
                        'favoriteCount': int(st.get('favoriteCount','0') or 0),
                        'thumbnail': (sn.get('thumbnails',{}).get('medium') or sn.get('thumbnails',{}).get('default') or {}).get('url',''),
                        'url': f"https://www.youtube.com/watch?v={v['id']}",
                    })
            fetched += len(vid_ids)
            next_page = pl_items.get('nextPageToken')
            if not next_page:
                break
        return videos, channel_meta
    except Exception as e:
        logger.error(f"YouTube analytics fetch error: {e}")
        return [], {}

@app.route('/api/analytics/videos')
def api_analytics_videos():
    try:
        yt = _yt_service()
        if not yt:
            return jsonify({
                'success': False, 
                'message': 'YouTube analytics not authenticated',
                'action_required': 'Use /api/analytics/device/start to begin authentication',
                'status': 'needs_auth'
            }), 401
        try:
            max_items = request.args.get('max', type=int)
        except Exception:
            max_items = None
        videos, channel = _list_channel_uploads(yt, max_items=max_items)
        return jsonify({'success': True, 'channel': channel, 'videos': videos})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/analytics/comments')
def api_analytics_comments():
    try:
        video_id = request.args.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'message': 'video_id required'}), 400
        yt = _yt_service()
        if not yt:
            return jsonify({
                'success': False, 
                'message': 'YouTube analytics not authenticated',
                'action_required': 'Use /api/analytics/device/start to begin authentication',
                'status': 'needs_auth'
            }), 401
        comments = []
        page = None
        total = 0
        while True and total < 1000:  # cap for responsiveness
            resp = yt.commentThreads().list(part="snippet", videoId=video_id, maxResults=100, pageToken=page, order='relevance').execute()
            for it in resp.get('items', []):
                sn = it['snippet']
                top = sn['topLevelComment']['snippet']
                comments.append({
                    'author': top.get('authorDisplayName',''),
                    'text': top.get('textDisplay',''),
                    'likeCount': int(top.get('likeCount','0')),
                    'publishedAt': top.get('publishedAt',''),
                    'updatedAt': top.get('updatedAt','')
                })
            total += len(resp.get('items', []))
            page = resp.get('nextPageToken')
            if not page:
                break
        return jsonify({'success': True, 'comments': comments})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/analytics')
def analytics_page():
    try:
        user = get_session_user()
        # Allow viewing even if not logged in, but encourage login for user-scoped features
        return render_template('analytics.html')
    except Exception:
        return render_template('analytics.html')

# Database configuration for PostgreSQL (prefer DATABASE_URL if provided)
db_config = DB_CONFIG
database_url = os.environ.get('DATABASE_URL')
if database_url:
    try:
        # Parse simple postgres://user:pass@host:port/dbname
        import urllib.parse as _urlparse
        parsed = _urlparse.urlparse(database_url)
        db_config = {
            'host': parsed.hostname or DB_HOST,
            'port': parsed.port or DB_PORT,
            'user': parsed.username or DB_USER,
            'password': parsed.password or DB_PASSWORD,
            'database': (parsed.path or '').lstrip('/') or DB_NAME,
        }
        logger.info("Using database configuration from DATABASE_URL")
    except Exception as e:
        logger.warning(f"Failed to parse DATABASE_URL, falling back to config: {e}")
logger.info(f"Database config: {db_config}")
# In-memory scheduler storage (simple file-backed persistence)
SCHEDULE_FILE = 'scheduled_posts.json'

def _load_schedules():
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.warning(f"Failed to load schedules: {e}")
    return []

def _save_schedules(schedules):
    try:
        with open(SCHEDULE_FILE, 'w') as f:
            json.dump(schedules, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save schedules: {e}")

def _current_local_ts():
    # Use local time consistently with naive fromisoformat parsing
    return datetime.now().timestamp()

def _parse_iso(dt_str: str) -> float:
    try:
        return datetime.fromisoformat(dt_str).timestamp()
    except Exception:
        return 0.0

def _scheduler_loop(app):
    with app.app_context():
        while True:
            try:
                schedules = _load_schedules()
                now_ts = _current_local_ts()
                pending = []
                for job in schedules:
                    if job.get('status') == 'pending' and _parse_iso(job.get('run_at_iso', '')) <= now_ts:
                        # Execute upload for the user
                        try:
                            user_id = job['user_id']
                            video_path = job['video_path']
                            title = job.get('title') or os.path.basename(video_path)
                            description = job.get('description', '')
                            tags = job.get('tags', [])
                            privacy = job.get('privacy', 'public')

                            # Resolve per-user path
                            user_trimmed = get_user_subdir(user_id, 'trimmed')
                            candidate = os.path.join(user_trimmed, os.path.basename(video_path))
                            resolved = candidate if os.path.exists(candidate) else video_path

                            result = upload_video_simple(resolved, title, description, tags, privacy)
                            if result.get('success'):
                                job['status'] = 'uploaded'
                                save_upload_record(user_id, video_path, result)
                                # Also mark uploaded in uploads file for dashboard
                                try:
                                    uploads = []
                                    if os.path.exists('youtube_uploads.json'):
                                        with open('youtube_uploads.json', 'r') as f:
                                            uploads = json.load(f)
                                    uploads.append({
                                        'user_id': user_id,
                                        'video_path': video_path,
                                        'filename': os.path.basename(video_path),
                                        'youtube_id': result.get('video_id'),
                                        'youtube_url': result.get('video_url'),
                                        'title': result.get('title'),
                                        'upload_time': result.get('upload_time'),
                                        'status': 'uploaded'
                                    })
                                    with open('youtube_uploads.json', 'w') as f:
                                        json.dump(uploads, f, indent=2)
                                except Exception:
                                    pass
                            else:
                                job['status'] = 'failed'
                                job['error'] = result.get('error')
                        except Exception as e:
                            job['status'] = 'failed'
                            job['error'] = str(e)
                        pending.append(job)
                    else:
                        pending.append(job)
                _save_schedules(pending)
            except Exception as e:
                logging.error(f"Scheduler loop error: {e}")
            _time.sleep(15)

def start_scheduler():
    t = Thread(target=_scheduler_loop, args=(app,), daemon=True)
    t.start()

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

# Ensure required tables exist
def initialize_database_schema():
    try:
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                token VARCHAR(255) UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_password_resets_email ON password_resets (email);")

        cursor.close()
        conn.close()
        logger.info("Database schema ensured (users, password_resets)")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")
        return False

# Configure Google Gemini AI
google_api_key = GOOGLE_API_KEY
if not google_api_key:
    logger.error("GOOGLE_API_KEY is not set in configuration")
    raise ValueError("GOOGLE_API_KEY must be set in config.py")

genai.configure(api_key=google_api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

# Initialize Whisper model lazily to reduce memory at boot (especially on Fly)
whisper_model = None
whisper_edit_model = None

def get_whisper_model():
    """Load Whisper model on first use unless disabled via env var."""
    global whisper_model
    if whisper_model is not None:
        return whisper_model
    if os.environ.get('WHISPER_DISABLED', 'false').lower() == 'true':
        logger.warning("Whisper is disabled via WHISPER_DISABLED env var")
        return None
    try:
        import whisper
        whisper_model_local = whisper.load_model("tiny")
        whisper_model = whisper_model_local
        logger.info("Whisper AI model loaded successfully (tiny model)")
    except ImportError:
        logger.warning("Whisper library not installed. Install with: pip install openai-whisper")
        whisper_model = None
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        whisper_model = None
    return whisper_model

# Initialize translator
translator = None # Removed googletrans import, so translator is no longer available

# Add transcript generation functionality using Whisper AI
def generate_transcript_from_video(video_path):
    """Generate transcript from video using Whisper AI"""
    try:
        model = get_whisper_model()
        if not model:
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
        model = get_whisper_model()
        if model:
            logger.info(f"Using Whisper AI for transcription: {audio_path}")
            try:
                result = model.transcribe(audio_path)
                
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

def process_text_file(file_path):
    """Process text files and extract content"""
    try:
        file_extension = os.path.splitext(file_path)[1].lower()
        
        if file_extension == '.txt':
            # Simple text file
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        elif file_extension == '.pdf':
            # PDF file - would need PyPDF2 or similar
            try:
                import PyPDF2
                with open(file_path, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    content = ""
                    for page in pdf_reader.pages:
                        content += page.extract_text() + "\n"
            except ImportError:
                return {
                    'success': False,
                    'error': 'PDF processing requires PyPDF2. Please install it or convert to text file.'
                }
        elif file_extension in ['.doc', '.docx']:
            # Word documents - would need python-docx
            try:
                from docx import Document
                doc = Document(file_path)
                content = ""
                for paragraph in doc.paragraphs:
                    content += paragraph.text + "\n"
            except ImportError:
                return {
                    'success': False,
                    'error': 'Word document processing requires python-docx. Please install it or convert to text file.'
                }
        else:
            return {
                'success': False,
                'error': f'Unsupported file type: {file_extension}'
            }
        
        # Clean the content
        content = content.strip()
        if not content:
            return {
                'success': False,
                'error': 'File appears to be empty or contains no readable text.'
            }
        
        return {
            'success': True,
            'transcript': content,
            'language': 'en',
            'word_count': len(content.split()),
            'filename': os.path.basename(file_path),
            'method': 'text_file'
        }
        
    except Exception as e:
        logger.error(f"Error processing text file: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'method': 'text_file_error'
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
    """Clean and format Lucy's story content by removing markdown symbols and HeyGen-speaking symbols for clean output"""
    try:
        cleaned = story.strip()
        
        # Remove markdown symbols while preserving content
        cleaned = re.sub(r'#+\s*\*\*(.*?)\*\*', r'\1', cleaned)
        cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', cleaned)
        cleaned = re.sub(r'>\s*"(.*?)"', r'"\1"', cleaned)
        cleaned = re.sub(r'>\s*(.*?)(?=\n|$)', r'\1', cleaned)
        
        # Remove "🎯 Final CTA:" label but keep the content
        cleaned = re.sub(r'🎯 Final CTA:\s*', '', cleaned)
        
        # Strip emojis and pictographs entirely
        emoji_pattern = re.compile(
            r"[\U0001F600-\U0001F64F]|[\U0001F300-\U0001F5FF]|[\U0001F680-\U0001F6FF]|[\U0001F700-\U0001F77F]|[\U0001F780-\U0001F7FF]|[\U0001F800-\U0001F8FF]|[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|[\u2600-\u26FF]|[\u2700-\u27BF]",
            flags=re.UNICODE
        )
        cleaned = emoji_pattern.sub('', cleaned)

        # Remove bracketed placeholders like [website]
        cleaned = re.sub(r"\[[^\]]*\]", '', cleaned)

        # Symbols normalization
        cleaned = re.sub(r'#\s*', '', cleaned)
        cleaned = re.sub(r'@\s*', '', cleaned)
        cleaned = re.sub(r'&\s*', ' and ', cleaned)
        cleaned = re.sub(r'%\s*', ' percent ', cleaned)
        cleaned = re.sub(r'\$\s*', ' dollars ', cleaned)
        cleaned = re.sub(r'\+', ' plus ', cleaned)
        cleaned = re.sub(r'=', ' equals ', cleaned)
        cleaned = re.sub(r'/', ' slash ', cleaned)
        cleaned = re.sub(r'\\', ' backslash ', cleaned)
        cleaned = re.sub(r'\*', '', cleaned)
        cleaned = re.sub(r'_', ' ', cleaned)
        cleaned = re.sub(r'\|', ' or ', cleaned)
        cleaned = re.sub(r'~', ' approximately ', cleaned)
        cleaned = re.sub(r'\^', ' to the power of ', cleaned)

        # Common misreads
        cleaned = re.sub(r'cut\s*board', 'clipboard', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'hash\s*tag', 'hashtag', cleaned, flags=re.IGNORECASE)

        # Remove CUT markers completely
        cleaned = re.sub(r'\bCUT\s*\d+\s*\n', '', cleaned)

        # Normalize excessive line breaks (keep meaningful newlines)
        cleaned = re.sub(r'\n{4,}', '\n\n\n', cleaned)
        # Reduce multiple spaces but do NOT touch newlines
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)

        # Sentence spacing
        cleaned = re.sub(r'([.!?])\s*([A-Z])', r'\1 \2', cleaned)
        
        # Clean up leftover markdown on line starts
        cleaned = re.sub(r'^\s*[-*]\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*>\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*#+\s*', '', cleaned, flags=re.MULTILINE)

        # Normalize repeated punctuation
        cleaned = re.sub(r'\.{2,}', '.', cleaned)
        cleaned = re.sub(r',{2,}', ',', cleaned)
        cleaned = re.sub(r'!{2,}', '!', cleaned)
        cleaned = re.sub(r'\?{2,}', '?', cleaned)

        return cleaned.strip()
    except Exception as e:
        logger.error(f"Error cleaning story: {e}")
        return story

def format_story_universal(text: str) -> str:
    """Enforce universal line-breaking format: blank line after title, between sections, and paragraphs.
    Assumes input is already cleaned of emojis/symbols. Also adds dashed underlines to clear section headings."""
    try:
        # Normalize line endings
        t = text.replace('\r\n', '\n').replace('\r', '\n').strip()
        lines = [l.strip() for l in t.split('\n')]
        # Remove consecutive empty lines
        compact = []
        for l in lines:
            if l == '' and (len(compact) == 0 or compact[-1] == ''):
                continue
            compact.append(l)
        # Ensure a blank line after the first non-empty line (title)
        out = []
        title_done = False
        for l in compact:
            out.append(l)
            if not title_done and l != '':
                out.append('')
                title_done = True
        # Insert blank lines before likely section headers and underline them
        result = []
        prev_blank = True
        def is_heading(s: str) -> bool:
            if not s:
                return False
            if s.startswith(('-', '*', '"', "'", '[')):
                return False
            if s.endswith(('.', '!', '?', '"', "'", ':')) and not s.endswith('Summary:'):
                # Lines ending with terminal punctuation are likely sentences
                return False
            # Treat short, capitalized or title-like lines as headings
            return (3 <= len(s) <= 90) and any(ch.isalpha() for ch in s) and (s[0].isupper())
        for l in out:
            if is_heading(l):
                if not prev_blank and len(result) > 0:
                    result.append('')
                # Heading line
                result.append(l)
                # Dashed underline matching length (min 8, max 100)
                underline = '-' * max(8, min(len(l), 100))
                result.append(underline)
                result.append('')
                prev_blank = True
                continue
            # Normal line handling
            result.append(l)
            prev_blank = (l == '')
        # Collapse excessive blank lines to max 2
        final_lines = []
        blank_count = 0
        for l in result:
            if l == '':
                blank_count += 1
                if blank_count <= 2:
                    final_lines.append('')
            else:
                blank_count = 0
                final_lines.append(l)
        formatted = '\n'.join(final_lines).strip() + '\n'
        return formatted
    except Exception as e:
        logger.warning(f"format_story_universal failed: {e}")
        return text

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

@app.route('/delete_trimmed', methods=['POST'])
def delete_trimmed():
    """Delete a trimmed clip strictly from the logged-in user's folder."""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401

        data = request.get_json(silent=True) or {}
        filename = data.get('filename')
        if not filename:
            return jsonify({'success': False, 'message': 'Filename required'}), 400

        user_dir = get_user_subdir(user['id'], 'trimmed')
        user_path = os.path.join(user_dir, filename)
        if os.path.exists(user_path):
            os.remove(user_path)
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'File not found'}), 404
    except Exception as e:
        logging.error(f"Error deleting trimmed file {filename}: {e}")
        return jsonify({'success': False, 'message': 'Delete failed'}), 500

@app.route('/')
def main_landing():
    """Main landing page - StoryVerse AI"""
    return render_template('LandingPage.html', languages=LANGUAGES)

@app.route('/landing')
def landing_page():
    """Alternative landing page route"""
    return render_template('LandingPage.html', languages=LANGUAGES)

@app.route('/api/existing-videos')
def get_existing_videos():
    """Get list of existing videos from static folders"""
    try:
        videos = []
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401

        videos_folder = get_user_subdir(user['id'], 'videos')
        if os.path.exists(videos_folder):
            for item in os.listdir(videos_folder):
                item_path = os.path.join(videos_folder, item)
                if os.path.isdir(item_path):
                    # Look for video files in subdirectories
                    for file in os.listdir(item_path):
                        if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                            videos.append({
                                'name': file,
                                'path': f'/videos/{item}/{file}',
                                'type': 'original',
                                'folder': item
                            })
                elif item.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    videos.append({
                        'name': item,
                        'path': f'/videos/{item}',
                        'type': 'original',
                        'folder': 'root'
                    })

        # Get trimmed videos from user folder
        trimmed_folder = get_user_subdir(user['id'], 'trimmed')
        if os.path.exists(trimmed_folder):
            for file in os.listdir(trimmed_folder):
                if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    videos.append({
                        'name': file,
                        'path': f'/trimmed/{file}',
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
        user = get_session_user()
        if not user:
            return jsonify({
                'success': False, 
                'message': 'Please log in to create video clips. You will be redirected to the login page.',
                'redirect': '/login'
            }), 401
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        # Validate file type
        if not file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
            return jsonify({'success': False, 'message': 'Invalid file type. Please upload MP4, MOV, AVI, MKV, or WEBM files.'}), 400
        
        start_time = float(request.form.get('start_time', 0))
        end_time = float(request.form.get('end_time', 0))
        
        if start_time < 0 or end_time <= start_time:
            return jsonify({'success': False, 'message': 'Invalid time range'}), 400
        
        # Create per-user trimmed folder
        trimmed_folder = get_user_subdir(user['id'], 'trimmed')
        
        # Generate unique filename for the clip
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = os.path.splitext(file.filename)[0]
        clip_filename = f"{base_name}_clip_{timestamp}.mp4"
        clip_path = os.path.join(trimmed_folder, clip_filename)
        
        # Save uploaded file temporarily (in user trimmed folder)
        temp_path = os.path.join(trimmed_folder, f"temp_{file.filename}")
        file.save(temp_path)
        
        try:
            # Check if ffmpeg is available
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                return jsonify({'success': False, 'message': 'FFmpeg is not available. Please install FFmpeg to use this feature.'}), 500
            
            # Use ffmpeg to create the clip
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
                return jsonify({'success': False, 'message': 'Failed to create video clip. Please check if the video file is valid.'}), 500
            
            # Verify the output file was created
            if not os.path.exists(clip_path):
                return jsonify({'success': False, 'message': 'Failed to create video clip file'}), 500
            
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
            return jsonify({'success': False, 'message': 'Video processing timed out. Please try with a shorter clip duration.'}), 500
        except Exception as e:
            logging.error(f"Error creating video clip: {e}")
            return jsonify({'success': False, 'message': 'Failed to create video clip. Please try again.'}), 500
        finally:
            # Clean up temp file if it still exists
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except Exception as e:
        logging.error(f"Error in create_video_clip: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/trim_video', methods=['POST'])
def trim_video():
    """Trim video from existing videos folder"""
    try:
        data = request.get_json()
        if not data or 'file' not in data or 'clips' not in data:
            return jsonify({'success': False, 'error': 'Invalid request data'}), 400
        
        file_path = data['file']
        clips = data['clips']
        
        if not clips or len(clips) == 0:
            return jsonify({'success': False, 'error': 'No clips specified'}), 400
        
        # Validate clips data
        for i, clip in enumerate(clips):
            if 'start' not in clip or 'end' not in clip:
                return jsonify({'success': False, 'error': f'Invalid clip data at index {i}'}), 400
            
            start_time = float(clip.get('start', 0))
            end_time = float(clip.get('end', 30))
            
            if start_time < 0 or end_time <= start_time:
                return jsonify({'success': False, 'error': f'Invalid time range for clip {i}: start={start_time}, end={end_time}'}), 400
        
        user = get_session_user()
        if not user:
            return jsonify({
                'success': False, 
                'error': 'Please log in to trim videos. You will be redirected to the login page.',
                'redirect': '/login'
            }), 401

        # Determine the source folder based on file path (user-scoped)
        if file_path.startswith('videos/'):
            source_folder = get_user_subdir(user['id'], 'videos')
            relative_path = file_path[7:]  # Remove 'videos/' prefix
        elif file_path.startswith('trimmed/'):
            source_folder = get_user_subdir(user['id'], 'trimmed')
            relative_path = file_path[9:]  # Remove 'trimmed/' prefix
        else:
            return jsonify({'success': False, 'error': 'Invalid file path'}), 400
        
        source_file_path = os.path.join(source_folder, relative_path)
        
        if not os.path.exists(source_file_path):
            return jsonify({'success': False, 'error': 'Source video not found'}), 404
        
        # Check if ffmpeg is available
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return jsonify({'success': False, 'error': 'FFmpeg is not available. Please install FFmpeg to use this feature.'}), 500
        
        # Create user trimmed folder
        trimmed_folder = get_user_subdir(user['id'], 'trimmed')
        
        created_clips = []
        
        for i, clip in enumerate(clips):
            start_time = float(clip.get('start', 0))
            end_time = float(clip.get('end', 30))
            
            if start_time < 0 or end_time <= start_time:
                continue
            
            # Generate unique filename for the clip
            base_name = os.path.splitext(os.path.basename(source_file_path))[0]
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            clip_filename = f"{base_name}_trim_{start_time:.1f}-{end_time:.1f}_{timestamp}.mp4"
            clip_path = os.path.join(trimmed_folder, clip_filename)
            
            try:
                # Super-fast: if only one clip, attempt stream copy (no re-encode)
                result = None
                if len(clips) == 1:
                    copy_cmd = [
                        'ffmpeg',
                        '-ss', str(start_time),
                        '-to', str(end_time),
                        '-i', source_file_path,
                        '-c', 'copy',
                        '-movflags', '+faststart',
                        '-avoid_negative_ts', '1',
                        '-y',
                        clip_path
                    ]
                    result = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=120)

                # Fallback to precise re-encode if fast path not used or failed
                if result is None or result.returncode != 0 or not os.path.exists(clip_path):
                    cmd = [
                        'ffmpeg', '-i', source_file_path,
                        '-ss', str(start_time),
                        '-t', str(end_time - start_time),
                        '-c:v', 'libx264',
                        '-c:a', 'aac',
                        '-y',  # Overwrite output file
                        clip_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    # Verify the output file was created
                    if os.path.exists(clip_path):
                        created_clips.append({
                            'name': clip_filename,
                            'url': f'/trimmed/{clip_filename}',
                            'start_time': start_time,
                            'end_time': end_time,
                            'duration': end_time - start_time
                        })
                    else:
                        logging.error(f"Clip file was not created for clip {i}")
                else:
                    logging.error(f"FFmpeg error for clip {i}: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                logging.error(f"FFmpeg timeout for clip {i}")
            except Exception as e:
                logging.error(f"Error creating clip {i}: {e}")
        
        if created_clips:
            return jsonify({
                'success': True,
                'clips': created_clips,
                'message': f'Successfully created {len(created_clips)} clip(s)'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to create any clips'}), 500
            
    except Exception as e:
        logging.error(f"Error in trim_video: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/trimmed/<filename>')
def serve_trimmed_video(filename):
    """Serve trimmed video files"""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'error': 'Unauthorized'}), 401
        user_dir = get_user_subdir(user['id'], 'trimmed')
        user_path = os.path.join(user_dir, filename)
        if os.path.exists(user_path):
            return send_from_directory(user_dir, filename)
        return jsonify({'error': 'Video not found'}), 404
    except Exception as e:
        logging.error(f"Error serving trimmed video {filename}: {e}")
        return jsonify({'error': 'Video not found'}), 404

@app.route('/videos/<path:filename>')
def serve_video(filename):
    """Serve video files from videos folder"""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'error': 'Unauthorized'}), 401
        user_dir = get_user_subdir(user['id'], 'videos')
        user_path = os.path.join(user_dir, filename)
        if os.path.exists(user_path):
            return send_from_directory(user_dir, filename)
        return jsonify({'error': 'Video not found'}), 404
    except Exception as e:
        logging.error(f"Error serving video {filename}: {e}")
        return jsonify({'error': 'Video not found'}), 404

@app.route('/uploads/<path:filename>')
def serve_uploaded_video(filename):
    """Serve uploaded video files for preview"""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'error': 'Unauthorized'}), 401
        return send_from_directory(get_user_subdir(user['id'], 'uploads'), filename)
    except Exception as e:
        logging.error(f"Error serving uploaded video {filename}: {e}")
        return jsonify({'error': 'Video not found'}), 404

@app.route('/api/trimmed-videos-dashboard')
def get_trimmed_videos_dashboard():
    """Get trimmed videos for dashboard display with date information"""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        trimmed_folder = get_user_subdir(user['id'], 'trimmed')
        videos = []
        seen_filenames = set()
        
        # Load uploaded records for this user to mark cards
        uploaded_filenames = set()
        try:
            if os.path.exists('youtube_uploads.json'):
                with open('youtube_uploads.json', 'r') as f:
                    uploads = json.load(f)
                    for u in uploads:
                        if u.get('user_id') == user['id'] and u.get('status') == 'uploaded':
                            uploaded_filenames.add(u.get('filename'))
        except Exception:
            pass

        if os.path.exists(trimmed_folder):
            for file in os.listdir(trimmed_folder):
                if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    file_path = os.path.join(trimmed_folder, file)
                    file_stat = os.stat(file_path)
                    created_date = datetime.fromtimestamp(file_stat.st_mtime)
                    file_size = file_stat.st_size
                    size_mb = round(file_size / (1024 * 1024), 2)
                    videos.append({
                        'filename': file,
                        'path': f'/trimmed/{file}',
                        'created_date': created_date.strftime('%Y-%m-%d'),
                        'created_time': created_date.strftime('%H:%M:%S'),
                        'day_name': created_date.strftime('%A'),
                        'size_mb': size_mb,
                        'full_path': file_path,
                        'type': 'trimmed',
                        'folder': 'trimmed',
                        'uploaded': file in uploaded_filenames
                    })
                    seen_filenames.add(file)

        # Per-user only: no legacy/global listing for privacy
            
            # Sort by creation date (newest first)
            videos.sort(key=lambda x: x['created_date'], reverse=True)
        
        # Group videos by date for dashboard display
        dashboard_data = {}
        for video in videos:
            date_key = video['created_date']
            if date_key not in dashboard_data:
                dashboard_data[date_key] = {
                    'date': date_key,
                    'day_name': video['day_name'],
                    'videos': []
                }
            dashboard_data[date_key]['videos'].append(video)
        
        return jsonify({
            'success': True,
            'videos': videos,
            'dashboard_data': dashboard_data,
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

        # Prefer per-user directories when logged in
        user = get_session_user()
        if user:
            user_videos_folder = get_user_subdir(user['id'], 'videos')
            if os.path.exists(user_videos_folder):
                for item in os.listdir(user_videos_folder):
                    item_path = os.path.join(user_videos_folder, item)
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

            user_trimmed_folder = get_user_subdir(user['id'], 'trimmed')
            if os.path.exists(user_trimmed_folder):
                for file in os.listdir(user_trimmed_folder):
                    if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                        videos.append({
                            'name': file,
                            'path': f'trimmed/{file}',
                            'type': 'trimmed',
                            'folder': 'trimmed'
                        })
        else:
            # Legacy global static directories fallback
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

# Settings page removed; app uses shared YouTube credentials

@app.route('/test-whisper')
def test_whisper():
    """Whisper AI test page"""
    return render_template('test_whisper.html')

@app.route('/test-story')
def test_story():
    """Story generation test page"""
    return render_template('test_story.html')

@app.route('/chatbot')
def chatbot():
    """AI Chatbot page"""
    return render_template('chatbot.html')

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

# Clean, logical routes for better user experience
@app.route('/login')
def login_page():
    """Login page"""
    return render_template('login.html')

@app.route('/signup')
def signup_page():
    """Signup page"""
    return render_template('signup.html')

@app.route('/story-generator')
def story_generator():
    """AI Story Generator - main workflow entry point"""
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    """Redirect to editor (legacy behavior)"""
    return redirect('/edit')

@app.route('/forgot-password')
def forgot_password_page():
    """Forgot password page"""
    return render_template('forget.html')

@app.route('/reset-password')
def reset_password_page():
    """Reset password page"""
    token = request.args.get('token')
    return render_template('reset.html', token=token)

# Keep legacy routes for backward compatibility but redirect to clean URLs
@app.route('/api/navigate/<page>')
def legacy_navigate(page):
    """Legacy navigation endpoint - redirects to clean URLs"""
    redirects = {
        'login': '/login',
        'signup': '/signup',
        'index': '/story-generator',
        'forgot': '/forgot-password',
        'reset': '/reset-password'
    }
    return redirect(redirects.get(page, '/'))

@app.route('/navigate/<page>')
def navigate(page):
    """Main navigation endpoint - redirects to clean URLs"""
    redirects = {
        'login': '/login',
        'signup': '/signup',
        'index': '/story-generator',
        'forgot': '/forgot-password',
        'reset': '/reset-password'
    }
    return redirect(redirects.get(page, '/'))

@app.route('/forgot')
def forgot_page():
    """Forgot password page"""
    return render_template('forget.html')

@app.route('/api/session')
def session_info():
    user = get_session_user()
    if not user:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'username': user.get('username'), 'email': user.get('email'), 'id': user.get('id')})

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
            "INSERT INTO users (username, email, password, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
            (username, email, hashed_password, datetime.now())
        )
        new_user_id = cursor.fetchone()[0]
        # Prepare per-user directories
        get_user_subdir(new_user_id, 'uploads')
        get_user_subdir(new_user_id, 'videos')
        get_user_subdir(new_user_id, 'trimmed')
        cursor.close()
        conn.close()
        
        if username != base_username:
            logger.info(f"Successful signup for email: {email} with generated username: {username}")
            return jsonify({
                'message': f'Signup successful! Username "{base_username}" was taken, so we created "{username}" for you. Redirecting...',
                'redirect': url_for('navigate', page='login')
            }), 200
        else:
            logger.info(f"Successful signup for email: {email} with username: {username}")
            return jsonify({
                'message': 'Signup successful! Redirecting...',
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
        cursor.execute("SELECT id, username, password FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user or not check_password_hash(user[2], password):
            logger.warning(f"Failed login attempt for email: {email}")
            return jsonify({'message': 'Invalid email or password'}), 401

        session['user'] = email
        session['user_id'] = user[0]
        session['username'] = user[1]
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
def api_reset_password():
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
            elif file.filename.lower().endswith(('.txt', '.doc', '.docx', '.pdf')):
                # Handle text files
                result = process_text_file(file_path)
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

                elif story_format == 'motivational':
                    prompt = f"""
You are a motivational speaker and life coach creating inspiring, action-oriented content.

Input:
- Framing: "{framing}"
- Story: "{story}"

Task: Create a powerful motivational speech with emotional impact and practical steps.

EXACT FORMAT EXAMPLE - Copy this structure precisely:

The Unstoppable Force Within You

Opening Hook

"Have you ever felt like giving up?"
"Like the world is against you?"
"Like success is impossible?"

"Today, I'm going to share a story that will change everything you believe about your potential."

The Wake-Up Call

"Life has a way of hitting us when we're down."
"Just when we think we can't take another step, it throws another obstacle in our path."
"But here's what I discovered: those moments are not setbacks—they're setups."

"Every challenge you face is preparing you for something greater. Every failure is teaching you what not to do. Every rejection is redirecting you to your true path."

The Transformation

"Three years ago, I was at rock bottom."
"I had lost my job, my relationship, and my sense of purpose."
"But instead of staying down, I made a decision that changed everything."

"I decided to become unstoppable."

The Formula

"Here's what I learned about becoming unstoppable:"

"1. **Embrace the Struggle** - Your challenges are your greatest teachers"
"2. **Find Your Why** - Purpose is more powerful than motivation"
"3. **Take Massive Action** - Small steps lead to massive results"
"4. **Build Unshakeable Belief** - Your mind is your most powerful weapon"

The Breakthrough

"Within six months, I had rebuilt my life from the ground up."
"I found a new career that I loved, built stronger relationships, and discovered a purpose that drives me every day."

"The same transformation is available to you."

Your Call to Action

"Right now, you have a choice."
"You can stay where you are, or you can become unstoppable."
"You can accept your current circumstances, or you can create the life you deserve."

"What will you choose?"

"Remember: You are not defined by your past. You are defined by your next decision."
"Make that decision today. Choose to become unstoppable."

---

**STRUCTURE REQUIREMENTS:**
- Powerful opening hook with questions
- Personal story or example
- Clear transformation journey
- Actionable formula or steps
- Emotional breakthrough moment
- Strong call to action
- Clean formatting without markdown symbols

**TONE REQUIREMENTS:**
- Inspiring and motivational
- Emotional and passionate
- Action-oriented
- Empowering and uplifting
- Direct and engaging

**CRITICAL: Ensure proper line breaks and spacing throughout the entire speech. Do not compress the text together.**
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
                universal_story = format_story_universal(clean_story)
                
                # Parse the story into structured format for frontend compatibility
                parsed_story = parse_story_to_json(universal_story)
                
                logger.info(f"Story parsed successfully. Title: {parsed_story.get('title', 'No title')}, Word count: {parsed_story.get('word_count', 'Unknown')}")
                
                return jsonify({
                    'success': True,
                    'story': parsed_story,
                    'raw_response': universal_story
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

@app.route('/api/gemini_chat', methods=['POST'])
def gemini_chat():
    """Chat endpoint using Google Gemini AI"""
    try:
        data = request.get_json()
        if not data or 'query' not in data:
            return jsonify({'success': False, 'error': 'No query provided'}), 400
        
        query = data['query']
        logger.info(f"Chat request received: {query[:100]}...")
        
        # Check if model is available
        if not model:
            logger.error("Google Gemini AI model not available")
            return jsonify({'success': False, 'error': 'AI model not available. Please check GOOGLE_API_KEY configuration.'}), 500
        
        try:
            # Create context-aware prompt for better responses
            context_prompt = f"""
You are an AI assistant for StoryVerse, a content creation platform. You help users with:

1. **Story Generation**: Help users create compelling stories, scripts, and content
2. **Video Creation**: Guide users on video production, editing, and optimization
3. **Content Strategy**: Provide tips on content planning, audience engagement, and platform optimization
4. **Technical Support**: Help with using the platform's features and tools

User Query: {query}

Please provide a helpful, concise response in 2-4 sentences. Be friendly, knowledgeable, and specific to content creation and storytelling. If the user asks about features, mention that they can use the story generation tools, video clipping features, or other platform capabilities.
"""
            
            # Generate response using Gemini
            response = model.generate_content(context_prompt)
            
            if response and hasattr(response, 'text') and response.text:
                bot_response = response.text.strip()
                logger.info(f"Chat response generated successfully: {len(bot_response)} characters")
                
                return jsonify({
                    'success': True,
                    'response': bot_response
                })
            else:
                logger.warning("Gemini chat response is empty or invalid")
                return jsonify({'success': False, 'error': 'No response from AI model'}), 500
                
        except Exception as e:
            logger.error(f"Error generating chat response with Gemini: {str(e)}")
            return jsonify({'success': False, 'error': f'AI generation failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Error in gemini_chat endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/health')
def health_check():
    """Health check endpoint to verify server and ffmpeg availability"""
    try:
        # Check if ffmpeg is available
        ffmpeg_available = False
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, timeout=10)
            ffmpeg_available = True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            ffmpeg_available = False
        
        # Check if required directories exist
        # Basic fs checks
        directories = {
            'static': os.path.exists('static'),
        }
        
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'ffmpeg_available': ffmpeg_available,
            'directories': directories,
            'message': 'Server is running'
        })
        
    except Exception as e:
        logging.error(f"Health check error: {e}")
        return jsonify({
            'status': 'unhealthy',
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }), 500

@app.route('/api/generate_caption', methods=['POST'])
def generate_caption():
    """Generate caption and title for a video using Gemini AI"""
    try:
        data = request.get_json()
        if not data or 'filename' not in data:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        filename = data['filename']
        
        # Extract video information from filename
        video_name = os.path.splitext(filename)[0]
        
        # Analyze filename for context clues
        context_keywords = analyze_filename_for_context(video_name)
        
        # Generate professional title first
        title = generate_professional_title(video_name)
        
        # Generate engaging caption using Gemini AI
        prompt = f"""
        Generate a professional, comprehensive social media caption for a video titled "{title}".
        
        Context clues from filename: {', '.join(context_keywords) if context_keywords else 'No specific context detected'}
        
        CRITICAL REQUIREMENTS:
        1. Create a professional, complete caption that tells a compelling story (500-800 characters)
        2. Make it engaging, informative, and valuable to the audience
        3. REMOVE any video filename references - focus on content value only
        4. Include 15-20 famous, trending hashtags that are:
           - Currently viral and popular on social media
           - Relevant to the video content/theme
           - Mix of broad and specific hashtags
           - Include famous hashtags like #viral, #trending, #fyp, #foryou, #shorts, #reels
        5. Make the caption lengthy and comprehensive (500-800 characters minimum)
        6. Make it suitable for platforms like Instagram, TikTok, YouTube, LinkedIn, and Facebook
        7. Include a clear, professional call-to-action
        8. Use emojis strategically but professionally
        9. Focus on providing value and insights
        10. Avoid generic phrases and be specific to the content
        11. Do NOT mention the video filename or technical details
        12. Make it feel like a professional influencer post
        
        Format the response exactly as:
        Caption: [your professional, comprehensive caption here]
        Hashtags: [famous and relevant hashtags here]
        
        Make the caption professional, complete, and valuable - something that would be used in a real social media post.
        Use the context clues to make the caption more relevant and specific to the actual content.
        """
        
        try:
            response = model.generate_content(prompt)
            content = response.text
            
            # Parse the response to extract caption and hashtags
            lines = content.split('\n')
            caption = ""
            hashtags = ""
            
            for line in lines:
                if line.startswith('Caption:'):
                    caption = line.replace('Caption:', '').strip()
                elif line.startswith('Hashtags:'):
                    hashtags = line.replace('Hashtags:', '').strip()
            
            # If parsing failed, create a fallback
            if not caption:
                caption = "🎬 This moment changed everything... The story you need to see right now! 💫 This is the kind of content that goes viral because it's authentic, powerful, and speaks to everyone. What's your take on this incredible journey? Drop your thoughts below and let's start a conversation! 👇✨"
            
            if not hashtags:
                hashtags = "#viral #trending #fyp #foryou #shorts #reels #viralvideo #trendingnow #mustwatch #amazing #inspiration #life #motivation #storytime #inspiring #viralcontent #trendingvideo #fypシ #viralpost #trendingpost"
            
            return jsonify({
                'success': True,
                'caption': caption,
                'hashtags': hashtags,
                'title': title,
                'filename': filename
            })
            
        except Exception as e:
            logging.error(f"Gemini AI error: {e}")
            # Fallback caption generation
            fallback_caption = "🎬 This moment changed everything... The story you need to see right now! 💫 This is the kind of content that goes viral because it's authentic, powerful, and speaks to everyone. What's your take on this incredible journey? Drop your thoughts below and let's start a conversation! 👇✨"
            fallback_hashtags = "#viral #trending #fyp #foryou #shorts #reels #viralvideo #trendingnow #mustwatch #amazing #inspiration #life #motivation #storytime #inspiring #viralcontent #trendingvideo #fypシ #viralpost #trendingpost"
            
            return jsonify({
                'success': True,
                'caption': fallback_caption,
                'hashtags': fallback_hashtags,
                'title': title,
                'filename': filename
            })
            
    except Exception as e:
        logging.error(f"Error generating caption: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate_title', methods=['POST'])
def generate_title():
    """Generate professional title for a video using Gemini AI"""
    try:
        data = request.get_json()
        if not data or 'filename' not in data:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        filename = data['filename']
        
        # Extract video information from filename
        video_name = os.path.splitext(filename)[0]
        
        # Generate professional title using Gemini AI
        title = generate_professional_title(video_name)
        
        return jsonify({
            'success': True,
            'title': title,
            'filename': filename
        })
        
    except Exception as e:
        logging.error(f"Error generating title: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/regenerate_title', methods=['POST'])
def regenerate_title():
    """Regenerate a different title for a video using Gemini AI"""
    try:
        data = request.get_json()
        if not data or 'filename' not in data:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        filename = data['filename']
        
        # Extract video information from filename
        video_name = os.path.splitext(filename)[0]
        
        # Generate a different professional title using Gemini AI
        title = generate_professional_title(video_name)
        
        return jsonify({
            'success': True,
            'title': title,
            'filename': filename
        })
        
    except Exception as e:
        logging.error(f"Error regenerating title: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def generate_professional_title(video_name):
    """Generate a professional title for YouTube using Gemini AI - 5-6 words, different each time"""
    try:
        # Analyze filename for context clues
        context_keywords = analyze_filename_for_context(video_name)
        
        # Add random seed for variety
        import random
        random_seed = random.randint(1, 1000)
        
        prompt = f"""
        Generate a professional, engaging YouTube video title for a video with filename "{video_name}".
        
        Context clues from filename: {', '.join(context_keywords) if context_keywords else 'No specific context detected'}
        Random seed for variety: {random_seed}
        
        CRITICAL REQUIREMENTS:
        1. Create EXACTLY 5-6 words (no more, no less)
        2. Make it compelling and professional
        3. Use power words that create curiosity and engagement
        4. Make it SEO-friendly and searchable
        5. Avoid clickbait but make it compelling
        6. Make it suitable for YouTube's algorithm
        7. Include relevant keywords naturally
        8. Each generation should be different and unique
        9. Remove any video filename references from the title
        10. Focus on the content value, not the technical details
        
        Format the response exactly as:
        Title: [your 5-6 word professional title here]
        
        Examples of good 5-6 word titles:
        - "The Secret to Viral Success"
        - "How I Built My Empire"
        - "This Changed Everything Forever"
        - "The Ultimate Guide to Growth"
        - "What Nobody Tells You About"
        
        Make the title feel professional and authentic, optimized for YouTube's platform.
        """
        
        response = model.generate_content(prompt)
        content = response.text
        
        # Parse the response to extract title
        lines = content.split('\n')
        title = ""
        
        for line in lines:
            if line.startswith('Title:'):
                title = line.replace('Title:', '').strip()
                break
        
        # If parsing failed, create a fallback
        if not title:
            title = "Amazing Content You Need to See"
        
        # Ensure title is 5-6 words
        words = title.split()
        if len(words) > 6:
            title = ' '.join(words[:6])
        elif len(words) < 5:
            # Add words to make it 5-6 words
            fallback_words = ["Amazing", "Content", "You", "Need", "To", "See"]
            title = ' '.join(words + fallback_words[:5-len(words)])
        
        return title
        
    except Exception as e:
        logging.error(f"Title generation error: {e}")
        # Fallback title
        return "Amazing Content You Need to See"

def analyze_filename_for_context(filename):
    """Analyze filename to extract context clues for better caption generation"""
    context_keywords = []
    
    # Convert to lowercase for analysis
    filename_lower = filename.lower()
    
    # Common video content indicators
    content_types = {
        'tutorial': ['tutorial', 'howto', 'guide', 'learn', 'education', 'teaching'],
        'story': ['story', 'narrative', 'tale', 'journey', 'experience'],
        'funny': ['funny', 'humor', 'comedy', 'laugh', 'joke', 'hilarious'],
        'inspirational': ['inspiration', 'motivation', 'success', 'achievement', 'goal'],
        'behind_scenes': ['behind', 'scenes', 'making', 'process', 'workflow'],
        'review': ['review', 'analysis', 'opinion', 'thoughts', 'feedback'],
        'challenge': ['challenge', 'dare', 'test', 'trial', 'experiment'],
        'transformation': ['transformation', 'change', 'before', 'after', 'progress'],
        'travel': ['travel', 'adventure', 'explore', 'journey', 'trip'],
        'food': ['food', 'cooking', 'recipe', 'meal', 'cuisine'],
        'fitness': ['fitness', 'workout', 'exercise', 'health', 'training'],
        'music': ['music', 'song', 'performance', 'concert', 'band'],
        'art': ['art', 'creative', 'design', 'painting', 'drawing'],
        'tech': ['tech', 'technology', 'gadget', 'app', 'software'],
        'business': ['business', 'entrepreneur', 'startup', 'success', 'money']
    }
    
    # Check for content type matches
    for content_type, keywords in content_types.items():
        if any(keyword in filename_lower for keyword in keywords):
            context_keywords.append(content_type)
    
    # Check for emotional indicators
    emotional_words = ['amazing', 'incredible', 'unbelievable', 'shocking', 'surprising', 'beautiful', 'stunning', 'epic', 'legendary']
    for word in emotional_words:
        if word in filename_lower:
            context_keywords.append(word)
    
    # Check for time indicators
    time_indicators = ['today', 'yesterday', 'morning', 'night', 'weekend', 'holiday', 'birthday', 'anniversary']
    for word in time_indicators:
        if word in filename_lower:
            context_keywords.append(word)
    
    # Check for location indicators
    location_words = ['home', 'office', 'gym', 'park', 'beach', 'city', 'country', 'world']
    for word in location_words:
        if word in filename_lower:
            context_keywords.append(word)
    
    return context_keywords[:5]  # Return top 5 most relevant context clues

@app.route('/api/save_caption', methods=['POST'])
def save_caption():
    """Save caption and title to file"""
    try:
        data = request.get_json()
        if not data or 'filename' not in data or 'caption' not in data:
            return jsonify({'success': False, 'error': 'Filename and caption are required'}), 400
        
        filename = data['filename']
        caption = data.get('caption', '')
        hashtags = data.get('hashtags', '')
        title = data.get('title', '')
        
        # Create captions directory if it doesn't exist
        captions_dir = 'captions'
        os.makedirs(captions_dir, exist_ok=True)
        
        # Create caption file
        caption_filename = f"{os.path.splitext(filename)[0]}.txt"
        caption_path = os.path.join(captions_dir, caption_filename)
        
        # Save caption content with title
        caption_content = f"Title: {title}\n\nCaption: {caption}\n\nHashtags: {hashtags}\n\nGenerated: {datetime.now().isoformat()}"
        
        with open(caption_path, 'w', encoding='utf-8') as f:
            f.write(caption_content)
        
        return jsonify({
            'success': True,
            'message': 'Caption and title saved successfully',
            'filename': caption_filename
        })
        
    except Exception as e:
        logging.error(f"Error saving caption: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/load_caption')
def load_caption():
    """Load caption from file"""
    try:
        filename = request.args.get('filename')
        if not filename:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        # Look for caption file
        captions_dir = 'captions'
        caption_filename = f"{os.path.splitext(filename)[0]}.txt"
        caption_path = os.path.join(captions_dir, caption_filename)
        
        if os.path.exists(caption_path):
            with open(caption_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse caption content
            lines = content.split('\n')
            title = ""
            caption = ""
            hashtags = ""
            
            for line in lines:
                if line.startswith('Title:'):
                    title = line.replace('Title:', '').strip()
                elif line.startswith('Caption:'):
                    caption = line.replace('Caption:', '').strip()
                elif line.startswith('Hashtags:'):
                    hashtags = line.replace('Hashtags:', '').strip()
            
            return jsonify({
                'success': True,
                'title': title,
                'caption': caption,
                'hashtags': hashtags,
                'filename': filename
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Caption not found'
            })
        
    except Exception as e:
        logging.error(f"Error loading caption: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate_caption_variations', methods=['POST'])
def generate_caption_variations():
    """Generate multiple caption variations for a video using Gemini AI"""
    try:
        data = request.get_json()
        if not data or 'filename' not in data:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        filename = data['filename']
        
        # Extract video information from filename
        video_name = os.path.splitext(filename)[0]
        
        # Analyze filename for context clues
        context_keywords = analyze_filename_for_context(video_name)
        
        # Generate multiple caption variations using Gemini AI
        prompt = f"""
        Generate 3 different compelling, story-focused social media captions for a video titled "{video_name}".
        
        Context clues from filename: {', '.join(context_keywords) if context_keywords else 'No specific context detected'}
        
        Requirements for each caption:
        1. Create captivating captions that tell a story and make people want to watch
        2. Make them emotional, relatable, and engaging
        3. Each caption should have a different tone/style:
           - Caption 1: Emotional and personal
           - Caption 2: Humorous and entertaining
           - Caption 3: Inspirational and motivational
        4. Keep each caption under 200 characters for better engagement
        5. Include 8-12 highly relevant hashtags for each that are:
           - Specific to the video content/theme
           - Trending in social media
           - Relevant to the target audience
        6. Use emojis strategically to enhance engagement
        
        Format the response exactly as:
        Caption 1 (Emotional): [emotional caption here]
        Hashtags 1: [relevant hashtags here]
        
        Caption 2 (Humorous): [humorous caption here]
        Hashtags 2: [relevant hashtags here]
        
        Caption 3 (Inspirational): [inspirational caption here]
        Hashtags 3: [relevant hashtags here]
        
        Make each caption feel personal and authentic, like it's coming from a real person sharing a meaningful story.
        Use the context clues to make the captions more relevant and specific.
        """
        
        try:
            response = model.generate_content(prompt)
            content = response.text
            
            # Parse the response to extract multiple captions and hashtags
            variations = []
            current_caption = ""
            current_hashtags = ""
            
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('Caption 1 (Emotional):'):
                    current_caption = line.replace('Caption 1 (Emotional):', '').strip()
                elif line.startswith('Hashtags 1:'):
                    current_hashtags = line.replace('Hashtags 1:', '').strip()
                    if current_caption and current_hashtags:
                        variations.append({
                            'type': 'Emotional',
                            'caption': current_caption,
                            'hashtags': current_hashtags
                        })
                        current_caption = ""
                        current_hashtags = ""
                elif line.startswith('Caption 2 (Humorous):'):
                    current_caption = line.replace('Caption 2 (Humorous):', '').strip()
                elif line.startswith('Hashtags 2:'):
                    current_hashtags = line.replace('Hashtags 2:', '').strip()
                    if current_caption and current_hashtags:
                        variations.append({
                            'type': 'Humorous',
                            'caption': current_caption,
                            'hashtags': current_hashtags
                        })
                        current_caption = ""
                        current_hashtags = ""
                elif line.startswith('Caption 3 (Inspirational):'):
                    current_caption = line.replace('Caption 3 (Inspirational):', '').strip()
                elif line.startswith('Hashtags 3:'):
                    current_hashtags = line.replace('Hashtags 3:', '').strip()
                    if current_caption and current_hashtags:
                        variations.append({
                            'type': 'Inspirational',
                            'caption': current_caption,
                            'hashtags': current_hashtags
                        })
                        current_caption = ""
                        current_hashtags = ""
            
            # If parsing failed, create fallback variations
            if not variations:
                variations = [
                    {
                        'type': 'Emotional',
                        'caption': f"🎬 This moment changed everything... {video_name} is the story you need to see right now! 💫 What's your take on this? 👇",
                        'hashtags': "#storytime #viral #trending #mustwatch #amazing #inspiration #life #motivation #viralvideo #trendingnow #amazing #inspiring #story #viralcontent #trendingvideo"
                    },
                    {
                        'type': 'Humorous',
                        'caption': f"😂 You won't believe what happened in {video_name}! This is absolutely priceless! 🤣 Watch till the end! 👀",
                        'hashtags': "#funny #humor #comedy #laugh #hilarious #viral #trending #funnyvideo #comedy #laughoutloud #viralcontent #trendingnow #funny #humor"
                    },
                    {
                        'type': 'Inspirational',
                        'caption': f"🌟 Sometimes the smallest moments create the biggest impact. {video_name} taught me this today. 💪 What inspires you? ✨",
                        'hashtags': "#inspiration #motivation #success #life #goals #dreams #inspirational #motivational #success #life #goals #dreams #inspirational #motivational"
                    }
                ]
            
            return jsonify({
                'success': True,
                'variations': variations,
                'filename': filename
            })
            
        except Exception as e:
            logging.error(f"Gemini AI error: {e}")
            # Fallback variations
            fallback_variations = [
                {
                    'type': 'Emotional',
                    'caption': f"🎬 This moment changed everything... {video_name} is the story you need to see right now! 💫 What's your take on this? 👇",
                    'hashtags': "#storytime #viral #trending #mustwatch #amazing #inspiration #life #motivation #viralvideo #trendingnow #amazing #inspiring #story #viralcontent #trendingvideo"
                },
                {
                    'type': 'Humorous',
                    'caption': f"😂 You won't believe what happened in {video_name}! This is absolutely priceless! 🤣 Watch till the end! 👀",
                    'hashtags': "#funny #humor #comedy #laugh #hilarious #viral #trending #funnyvideo #comedy #laughoutloud #viralcontent #trendingnow #funny #humor"
                },
                {
                    'type': 'Inspirational',
                    'caption': f"🌟 Sometimes the smallest moments create the biggest impact. {video_name} taught me this today. 💪 What inspires you? ✨",
                    'hashtags': "#inspiration #motivation #success #life #goals #dreams #inspirational #motivational #success #life #goals #dreams #inspirational #motivational"
                }
            ]
            
            return jsonify({
                'success': True,
                'variations': fallback_variations,
                'filename': filename
            })
            
    except Exception as e:
        logging.error(f"Error generating caption variations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/api/youtube/upload', methods=['POST'])
def youtube_upload():
    """Upload video to YouTube"""
    try:
        data = request.get_json()
        logging.info(f"Received upload request: {data}")  # Debug logging
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        video_path = data.get('video_path')
        title = data.get('title', 'Untitled Video')
        description = data.get('description', '')
        tags = data.get('tags', [])
        privacy = data.get('privacy', 'private')  # Default to private for safety
        
        logging.info(f"Original video path: {video_path}")  # Debug logging
        
        if not video_path:
            return jsonify({'success': False, 'error': 'Video path is required'}), 400
        
        # Better path resolution - per-user only to prevent cross-user access
        full_path = None
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        possible_paths = []
        user_trimmed = get_user_subdir(user['id'], 'trimmed')
        user_videos = get_user_subdir(user['id'], 'videos')
        possible_paths.extend([
            os.path.join(user_trimmed, os.path.basename(video_path)),
            os.path.join(user_videos, os.path.basename(video_path)),
            os.path.join(user_trimmed, video_path),
            os.path.join(user_videos, video_path)
        ])
        
        for path in possible_paths:
            if os.path.exists(path):
                full_path = path
                break
        
        logging.info(f"Resolved video path: {full_path}")
        
        if not full_path or not os.path.exists(full_path):
            logging.error(f"Video file not found: {video_path}")
            logging.error(f"Tried paths: {possible_paths}")
            return jsonify({'success': False, 'error': f'Video file not found: {video_path}', 'paths_tried': possible_paths}), 404
        
        # Check file size
        file_size = os.path.getsize(full_path)
        logging.info(f"Video file size: {file_size / (1024*1024):.2f} MB")
        
        # Use the simplified upload function
        logging.info(f"Starting YouTube upload for: {title}")
        result = upload_video_simple(full_path, title, description, tags, privacy)

        # Enrich unknown errors with hints
        if not result.get('success'):
            client_secrets_exists = os.path.exists('client_secrets.json')
            token_path = _get_youtube_token_file(for_save=False)
            token_exists = os.path.exists(token_path)
            result.setdefault('details', {})
            result['details'].update({
                'client_secrets_exists': client_secrets_exists,
                'token_file': token_path,
                'token_exists': token_exists
            })
        
        if result['success']:
            logging.info(f"YouTube upload successful: {result.get('video_url', 'No URL')}")
            # Save upload record scoped to user
            if user:
                save_upload_record(user['id'], video_path, result)
            try:
                # Set uploaded flag in scheduled posts if present
                schedules = _load_schedules()
                for job in schedules:
                    if job.get('user_id') == (user['id'] if user else None) and os.path.basename(job.get('video_path','')) == os.path.basename(video_path):
                        job['status'] = 'uploaded'
                _save_schedules(schedules)
            except Exception:
                pass
        else:
            logging.error(f"YouTube upload failed: {result.get('error', 'Unknown error')}")
            
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"YouTube upload error: {e}")
        token_path = _get_youtube_token_file(for_save=False)
        return jsonify({'success': False, 'error': str(e), 'token_file': token_path, 'client_secrets_exists': os.path.exists('client_secrets.json')}), 500

@app.route('/api/youtube/channel', methods=['GET'])
def youtube_channel_info():
    """Get YouTube channel information"""
    try:
        youtube = authenticate_youtube()
        if not youtube:
            return jsonify({'success': False, 'error': 'Failed to authenticate with YouTube'}), 500
        
        # Get channel info
        channels_response = youtube.channels().list(
            part='snippet,statistics',
            mine=True
        ).execute()
        
        if channels_response['items']:
            channel = channels_response['items'][0]
            return jsonify({
                "success": True,
                "channel_id": channel['id'],
                "channel_title": channel['snippet']['title'],
                "subscriber_count": channel['statistics'].get('subscriberCount', 'Unknown'),
                "video_count": channel['statistics'].get('videoCount', 'Unknown'),
                "view_count": channel['statistics'].get('viewCount', 'Unknown')
            })
        else:
            return jsonify({"success": False, "error": "No channel found"}), 404
            
    except Exception as e:
        logging.error(f"YouTube channel info error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/youtube/status', methods=['GET'])
def youtube_status():
    """Get YouTube service status"""
    try:
        # Check if files exist
        client_secrets_exists = os.path.exists('client_secrets.json')
        token_path = _get_youtube_token_file(for_save=False)
        token_exists = os.path.exists(token_path)

        # Optionally try a lightweight auth build without network
        can_auth = False
        token_metadata = {
            'expired': None,
            'has_refresh_token': None
        }
        try:
            # Parse token to inspect meta
            if token_exists:
                try:
                    from google.oauth2.credentials import Credentials as _Cred
                    creds = _Cred.from_authorized_user_file(token_path)
                    token_metadata['expired'] = getattr(creds, 'expired', None)
                    token_metadata['has_refresh_token'] = bool(getattr(creds, 'refresh_token', None))
                    # Attempt auto-refresh if expired and refresh token is present
                    if getattr(creds, 'expired', False) and getattr(creds, 'refresh_token', None):
                        try:
                            creds.refresh(Request())
                            save_path = _get_youtube_token_file(for_save=True)
                            with open(save_path, 'w') as f:
                                f.write(creds.to_json())
                            token_metadata['expired'] = getattr(creds, 'expired', None)
                        except Exception:
                            pass
                except Exception:
                    pass
            yt = authenticate_youtube()
            can_auth = yt is not None
        except Exception:
            can_auth = False

        payload = {
            'available': True,
            'client_secrets_exists': client_secrets_exists,
            'token_file': token_path,
            'token_exists': token_exists,
            'token_metadata': token_metadata,
            'can_authenticate': can_auth,
            'message': 'YouTube service ready' if (client_secrets_exists and token_exists and can_auth) else 'Missing credentials or token'
        }
        if not token_exists:
            try:
                # Provide an auth URL to complete OAuth on deployed site
                redirect_uri = _get_redirect_uri()
                import google_auth_oauthlib.flow as _flow
                if client_secrets_exists:
                    flow = _flow.Flow.from_client_secrets_file('client_secrets.json', scopes=_get_youtube_scopes(), redirect_uri=redirect_uri)
                    auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
                    payload['auth_url'] = auth_url
            except Exception:
                pass
        return jsonify(payload)
        
    except Exception as e:
        logging.error(f"YouTube status error: {e}")
        return jsonify({'available': False, 'error': str(e)})

@app.route('/api/youtube/refresh', methods=['POST'])
def youtube_refresh():
    """Force a refresh of the YouTube OAuth token if possible."""
    try:
        token_path = _get_youtube_token_file(for_save=False)
        if not os.path.exists(token_path):
            return jsonify({'success': False, 'error': 'Token file not found'}), 404
        try:
            from google.oauth2.credentials import Credentials as _Cred
            creds = _Cred.from_authorized_user_file(token_path)
            if not getattr(creds, 'refresh_token', None):
                return jsonify({'success': False, 'error': 'No refresh token available'}), 400
            creds.refresh(Request())
            save_path = _get_youtube_token_file(for_save=True)
            with open(save_path, 'w') as f:
                f.write(creds.to_json())
            return jsonify({'success': True, 'token_file': save_path})
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to refresh token: {e}'}), 400
    except Exception as e:
        logging.error(f"YouTube refresh error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def save_upload_record(user_id: int, video_path: str, upload_result: dict):
    """Save YouTube upload record scoped to a user"""
    try:
        uploads_file = 'youtube_uploads.json'
        uploads = []
        
        # Load existing uploads
        if os.path.exists(uploads_file):
            with open(uploads_file, 'r') as f:
                uploads = json.load(f)
        
        # Add new upload record
        upload_record = {
            'user_id': user_id,
            'video_path': video_path,
            'filename': os.path.basename(video_path),
            'youtube_id': upload_result.get('video_id'),
            'youtube_url': upload_result.get('video_url'),
            'title': upload_result.get('title'),
            'upload_time': upload_result.get('upload_time'),
            'status': 'uploaded'
        }
        
        uploads.append(upload_record)
        
        # Save back to file
        with open(uploads_file, 'w') as f:
            json.dump(uploads, f, indent=2)
            
        logging.info(f"YouTube upload record saved: {upload_result.get('video_id')}")
        
    except Exception as e:
        logging.error(f"Error saving upload record: {e}")

@app.route('/youtube-test')
def youtube_test_page():
    """Serve YouTube upload test page"""
    return render_template('youtube_test.html')

@app.route('/test-caption')
def test_caption_page():
    """Serve caption upload test page"""
    return send_file('test_caption_upload.html')

@app.route('/api/load-credentials', methods=['GET'])
def load_credentials():
    """Load credentials for a specific platform"""
    try:
        platform = request.args.get('platform', '').lower()
        
        if platform == 'youtube':
            # Check if client secrets file exists
            client_secrets_exists = os.path.exists('client_secrets.json')
            
            if client_secrets_exists:
                return jsonify({
                    'success': True,
                    'message': 'YouTube credentials found',
                    'configured': True
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'YouTube client_secrets.json not found',
                    'configured': False
                })
        else:
            return jsonify({
                'success': False,
                'message': f'Platform {platform} not supported',
                'configured': False
            })
            
    except Exception as e:
        logging.error(f"Error loading credentials: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule-post', methods=['POST'])
def api_schedule_post():
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        data = request.get_json() or {}
        platform = (data.get('platform') or 'youtube').lower()
        if platform != 'youtube':
            return jsonify({'success': False, 'message': 'Only YouTube scheduling is supported at the moment'}), 400
        filename = data.get('filename') or data.get('video') or ''
        video_path = data.get('video_path') or f"trimmed/{filename}"
        date_str = data.get('date')
        time_str = data.get('time')
        caption = data.get('caption') or ''
        hashtags = data.get('hashtags') or ''
        title = data.get('title') or ''
        if not date_str or not time_str or not filename:
            return jsonify({'success': False, 'message': 'Missing required fields (filename, date, time)'}), 400
        # Build run time (local date/time -> ISO) and validate it's in the future
        run_at_iso = f"{date_str}T{time_str}:00"
        try:
            run_ts = datetime.fromisoformat(run_at_iso).timestamp()
            if run_ts <= datetime.now().timestamp():
                return jsonify({'success': False, 'message': 'Scheduled time must be in the future'}), 400
        except Exception:
            return jsonify({'success': False, 'message': 'Invalid date/time format'}), 400
        # Prepare job
        tags = [t.lstrip('#') for t in (hashtags or '').split() if t.startswith('#')]
        job = {
            'id': str(uuid.uuid4()),
            'user_id': user['id'],
            'platform': platform,
            'video_path': video_path,
            'filename': filename,
            'title': title,
            'description': caption + ("\n\n" + hashtags if hashtags else ''),
            'tags': tags,
            'privacy': 'public',
            'run_at_iso': run_at_iso,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat()
        }
        schedules = _load_schedules()
        schedules.append(job)
        _save_schedules(schedules)
        return jsonify({'success': True, 'job': job})
    except Exception as e:
        logging.error(f"Schedule post error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/scheduled-posts', methods=['GET'])
def api_list_scheduled_posts():
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        schedules = [j for j in _load_schedules() if j.get('user_id') == user['id']]
        return jsonify({'success': True, 'jobs': schedules})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/scheduled-posts')
def scheduled_posts_page():
    """Scheduled posts page"""
    try:
        user = get_session_user()
        if not user:
            return redirect(url_for('navigate', page='login'))
        
        # Load scheduled posts for the user
        schedules = _load_schedules()
        user_schedules = [j for j in schedules if j.get('user_id') == user['id']]
        
        # Convert to template-friendly format
        posts = []
        for job in user_schedules:
            post = {
                'timestamp': job.get('id'),
                'video_name': job.get('filename', 'Unknown'),
                'platform': job.get('platform', 'youtube'),
                'scheduled_time': job.get('run_at_iso', ''),
                'status': job.get('status', 'pending'),
                'caption': job.get('description', ''),
                'hashtags': ' '.join([f'#{tag}' for tag in job.get('tags', [])]),
                'title': job.get('title', ''),
                'executed_time': job.get('executed_at', ''),
                'error': job.get('error', '')
            }
            posts.append(post)
        
        return render_template('scheduled_posts.html', posts=posts)
    except Exception as e:
        logger.error(f"Error loading scheduled posts page: {e}")
        return render_template('scheduled_posts.html', posts=[])

@app.route('/api/cancel-scheduled', methods=['POST'])
def api_cancel_scheduled():
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        data = request.get_json() or {}
        job_id = data.get('id')
        if not job_id:
            return jsonify({'success': False, 'message': 'Missing id'}), 400
        schedules = _load_schedules()
        updated = False
        for j in schedules:
            if j.get('id') == job_id and j.get('user_id') == user['id'] and j.get('status') == 'pending':
                j['status'] = 'cancelled'
                updated = True
                break
        _save_schedules(schedules)
        return jsonify({'success': updated})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/execute-post', methods=['POST'])
def api_execute_post():
    """Execute a scheduled post immediately"""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
        data = request.get_json() or {}
        job_id = data.get('timestamp') or data.get('id')
        
        if not job_id:
            return jsonify({'success': False, 'message': 'Missing job ID'}), 400
        
        schedules = _load_schedules()
        job = None
        
        for j in schedules:
            if j.get('id') == job_id and j.get('user_id') == user['id']:
                job = j
                break
        
        if not job:
            return jsonify({'success': False, 'message': 'Job not found'}), 404
        
        if job.get('status') != 'pending':
            return jsonify({'success': False, 'message': 'Job is not pending'}), 400
        
        # Execute the job
        try:
            video_path = job.get('video_path', '')
            title = job.get('title', '')
            description = job.get('description', '')
            tags = job.get('tags', [])
            privacy = job.get('privacy', 'public')
            
            # Resolve per-user path
            user_trimmed = get_user_subdir(user['id'], 'trimmed')
            candidate = os.path.join(user_trimmed, os.path.basename(video_path))
            resolved = candidate if os.path.exists(candidate) else video_path
            
            result = upload_video_simple(resolved, title, description, tags, privacy)
            
            if result.get('success'):
                job['status'] = 'posted'
                job['executed_at'] = datetime.utcnow().isoformat()
                job['youtube_url'] = result.get('url', '')
            else:
                job['status'] = 'failed'
                job['executed_at'] = datetime.utcnow().isoformat()
                job['error'] = result.get('error', 'Unknown error')
            
            _save_schedules(schedules)
            return jsonify({'success': True, 'result': result})
            
        except Exception as e:
            job['status'] = 'failed'
            job['executed_at'] = datetime.utcnow().isoformat()
            job['error'] = str(e)
            _save_schedules(schedules)
            return jsonify({'success': False, 'message': str(e)}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/delete-scheduled-post', methods=['POST'])
def api_delete_scheduled_post():
    """Delete a scheduled post"""
    try:
        user = get_session_user()
        if not user:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
        data = request.get_json() or {}
        job_id = data.get('timestamp') or data.get('id')
        
        if not job_id:
            return jsonify({'success': False, 'message': 'Missing job ID'}), 400
        
        schedules = _load_schedules()
        updated = False
        
        for i, j in enumerate(schedules):
            if j.get('id') == job_id and j.get('user_id') == user['id']:
                schedules.pop(i)
                updated = True
                break
        
        if updated:
            _save_schedules(schedules)
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Job not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Main execution
if __name__ == '__main__':
    try:
        # Test database connection on startup
        db_status_result = check_db_connection()
        if db_status_result['status'] == 'success':
            logger.info("Database connection successful on startup")
        else:
            logger.warning(f"Database connection warning on startup: {db_status_result['message']}")
        # Ensure schema exists
        initialize_database_schema()
        
        # Start background scheduler
        start_scheduler()

        # Start the Flask application
        port = int(os.environ.get('PORT', PORT))
        logger.info(f"Starting AI Auto-Posting application on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
        
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise
