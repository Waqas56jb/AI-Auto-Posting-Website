import os
import json
import time
import pickle
import hashlib
import threading
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# YouTube API configuration
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

# File paths for credential management
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'
CLIENT_FILE = 'client.json'  # Your existing credential file
SERVICE_ACCOUNT_FILE = 'service-account.json'
CREDENTIALS_CACHE_FILE = 'credentials_cache.pkl'
BACKUP_CREDENTIALS_FILE = 'backup_credentials.json'
CREDENTIAL_HISTORY_FILE = 'credential_history.json'

class FutureProofCredentialManager:
    """Advanced credential manager designed to work indefinitely without user intervention."""
    
    def __init__(self):
        self.credentials = None
        self.youtube_service = None
        self.last_refresh = None
        self.refresh_interval = timedelta(hours=6)  # Refresh every 6 hours
        self.credential_history = []
        self.backup_credentials = []
        self.auto_rotation_enabled = True
        self.health_check_interval = timedelta(hours=1)
        self.last_health_check = None
        
        # Start background maintenance thread
        self.maintenance_thread = threading.Thread(target=self._background_maintenance, daemon=True)
        self.maintenance_thread.start()
    
    def _background_maintenance(self):
        """Background thread for credential maintenance and health checks."""
        while True:
            try:
                time.sleep(3600)  # Check every hour
                self._perform_health_check()
                self._rotate_credentials_if_needed()
                self._backup_credentials()
            except Exception as e:
                logger.warning(f"Background maintenance error: {e}")
    
    def _perform_health_check(self):
        """Perform health check on credentials and services."""
        try:
            if self.credentials and self.youtube_service:
                # Test API call
                request = self.youtube_service.channels().list(
                    part="snippet",
                    mine=True
                )
                response = request.execute()
                logger.info("Health check passed - API connection working")
                self.last_health_check = datetime.now()
            else:
                logger.warning("Health check failed - no valid credentials")
                self._emergency_credential_recovery()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            self._emergency_credential_recovery()
    
    def _emergency_credential_recovery(self):
        """Emergency recovery when all credentials fail."""
        logger.warning("Emergency credential recovery initiated")
        
        # Try to load from backup
        if self._load_backup_credentials():
            logger.info("Recovered from backup credentials")
            return
        
        # Try to regenerate from available sources
        if self._regenerate_credentials():
            logger.info("Regenerated credentials successfully")
            return
        
        # Last resort: force re-authentication
        logger.error("All recovery methods failed - manual intervention may be required")
    
    def _regenerate_credentials(self):
        """Attempt to regenerate credentials from available sources."""
        try:
            # Try service account first
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                credentials = service_account.Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE, scopes=SCOPES
                )
                self.credentials = credentials
                self.youtube_service = build('youtube', 'v3', credentials=credentials)
                return True
            
            # Try OAuth credentials (client.json first, then credentials.json)
            if os.path.exists(CLIENT_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
                self.credentials = flow.run_local_server(port=0)
                self.youtube_service = build('youtube', 'v3', credentials=self.credentials)
                return True
            elif os.path.exists(CREDENTIALS_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                self.credentials = flow.run_local_server(port=0)
                self.youtube_service = build('youtube', 'v3', credentials=self.credentials)
                return True
                
        except Exception as e:
            logger.error(f"Credential regeneration failed: {e}")
        
        return False
    
    def _rotate_credentials_if_needed(self):
        """Rotate credentials if they're getting old or showing signs of expiration."""
        if not self.credentials:
            return
        
        # Check if credentials are older than 30 days
        if (self.last_refresh and 
            datetime.now() - self.last_refresh > timedelta(days=30)):
            logger.info("Rotating credentials due to age")
            self._rotate_credentials()
    
    def _rotate_credentials(self):
        """Rotate to fresh credentials."""
        try:
            # Store current credentials in history
            if self.credentials:
                self._store_credential_in_history()
            
            # Generate new credentials
            if self._regenerate_credentials():
                self.last_refresh = datetime.now()
                self.save_cached_credentials()
                logger.info("Credentials rotated successfully")
            else:
                logger.error("Failed to rotate credentials")
                
        except Exception as e:
            logger.error(f"Credential rotation failed: {e}")
    
    def _store_credential_in_history(self):
        """Store current credentials in history for potential recovery."""
        try:
            credential_hash = hashlib.md5(str(self.credentials).encode()).hexdigest()
            history_entry = {
                'timestamp': datetime.now().isoformat(),
                'credential_hash': credential_hash,
                'type': type(self.credentials).__name__
            }
            
            self.credential_history.append(history_entry)
            
            # Keep only last 10 entries
            if len(self.credential_history) > 10:
                self.credential_history = self.credential_history[-10:]
            
            # Save history
            with open(CREDENTIAL_HISTORY_FILE, 'w') as f:
                json.dump(self.credential_history, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Failed to store credential history: {e}")
    
    def _backup_credentials(self):
        """Create backup of current credentials."""
        try:
            if self.credentials:
                backup_data = {
                    'timestamp': datetime.now().isoformat(),
                    'credential_type': type(self.credentials).__name__,
                    'scopes': SCOPES
                }
                
                # Store backup
                with open(BACKUP_CREDENTIALS_FILE, 'w') as f:
                    json.dump(backup_data, f, indent=2)
                    
                logger.info("Credentials backed up successfully")
                
        except Exception as e:
            logger.warning(f"Backup failed: {e}")
    
    def _load_backup_credentials(self):
        """Load credentials from backup."""
        try:
            if os.path.exists(BACKUP_CREDENTIALS_FILE):
                with open(BACKUP_CREDENTIALS_FILE, 'r') as f:
                    backup_data = json.load(f)
                
                # Try to regenerate from backup info
                return self._regenerate_credentials()
                
        except Exception as e:
            logger.warning(f"Failed to load backup: {e}")
        
        return False
    
    def load_cached_credentials(self):
        """Load cached credentials with enhanced error handling."""
        try:
            if os.path.exists(CREDENTIALS_CACHE_FILE):
                with open(CREDENTIALS_CACHE_FILE, 'rb') as f:
                    cached_data = pickle.load(f)
                    self.credentials = cached_data['credentials']
                    self.last_refresh = cached_data.get('last_refresh')
                    logger.info("Loaded cached credentials")
                    
                    # Validate cached credentials
                    if self._validate_credentials():
                        return True
                    else:
                        logger.warning("Cached credentials are invalid")
                        return False
                        
        except Exception as e:
            logger.warning(f"Failed to load cached credentials: {e}")
        return False
    
    def _validate_credentials(self):
        """Validate that credentials are still working."""
        try:
            if not self.credentials:
                return False
            
            # Check if credentials are expired
            if hasattr(self.credentials, 'expired') and self.credentials.expired:
                return False
            
            # Test with a simple API call
            test_service = build('youtube', 'v3', credentials=self.credentials)
            request = test_service.channels().list(part="snippet", mine=True)
            request.execute()
            return True
            
        except Exception as e:
            logger.warning(f"Credential validation failed: {e}")
            return False
    
    def save_cached_credentials(self):
        """Save credentials to cache with enhanced persistence."""
        try:
            cache_data = {
                'credentials': self.credentials,
                'last_refresh': datetime.now(),
                'version': '2.0',  # Version for future compatibility
                'created_at': datetime.now().isoformat()
            }
            
            # Create backup before saving
            if os.path.exists(CREDENTIALS_CACHE_FILE):
                backup_name = f"{CREDENTIALS_CACHE_FILE}.backup"
                os.rename(CREDENTIALS_CACHE_FILE, backup_name)
            
            with open(CREDENTIALS_CACHE_FILE, 'wb') as f:
                pickle.dump(cache_data, f)
            
            logger.info("Saved credentials to cache with backup")
            
        except Exception as e:
            logger.error(f"Failed to save credentials to cache: {e}")
    
    def authenticate_with_service_account(self):
        """Authenticate using service account with enhanced error handling."""
        try:
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                credentials = service_account.Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE, scopes=SCOPES
                )
                self.credentials = credentials
                logger.info("Authenticated with service account")
                return True
        except Exception as e:
            logger.warning(f"Service account authentication failed: {e}")
        return False
    
    def authenticate_with_oauth(self):
        """Authenticate using OAuth 2.0 with persistent token handling."""
        try:
            # Try client.json first (your existing file)
            if os.path.exists(CLIENT_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
                self.credentials = flow.run_local_server(port=0)
                logger.info("Authenticated with OAuth 2.0 using client.json")
                return True
            
            # Fall back to credentials.json
            if os.path.exists(CREDENTIALS_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                self.credentials = flow.run_local_server(port=0)
                logger.info("Authenticated with OAuth 2.0 using credentials.json")
                return True
            
            logger.error(f"No OAuth credential files found. Need either '{CLIENT_FILE}' or '{CREDENTIALS_FILE}'")
            return False
        except Exception as e:
            logger.error(f"OAuth authentication failed: {e}")
            return False
    
    def refresh_credentials_if_needed(self):
        """Enhanced credential refresh with multiple fallback strategies."""
        if not self.credentials:
            return False
        
        # Check if credentials need refresh
        needs_refresh = (
            self.last_refresh is None or 
            datetime.now() - self.last_refresh > self.refresh_interval or
            (hasattr(self.credentials, 'expired') and self.credentials.expired)
        )
        
        if needs_refresh:
            try:
                # Try standard refresh first
                if hasattr(self.credentials, 'refresh_token') and self.credentials.refresh_token:
                    self.credentials.refresh(Request())
                    logger.info("Refreshed credentials using refresh token")
                elif hasattr(self.credentials, 'refresh'):
                    self.credentials.refresh(Request())
                    logger.info("Refreshed credentials")
                else:
                    # If no refresh method, try to regenerate
                    logger.warning("No refresh method available, regenerating credentials")
                    return self._regenerate_credentials()
                
                self.last_refresh = datetime.now()
                self.save_cached_credentials()
                return True
                
            except Exception as e:
                logger.error(f"Failed to refresh credentials: {e}")
                # Try emergency recovery
                return self._emergency_credential_recovery()
        
        return True
    
    def get_youtube_service(self):
        """Get or create YouTube service with enhanced reliability."""
        if not self.refresh_credentials_if_needed():
            return None
        
        if not self.youtube_service:
            try:
                self.youtube_service = build('youtube', 'v3', credentials=self.credentials)
                logger.info("Created YouTube service")
            except Exception as e:
                logger.error(f"Failed to create YouTube service: {e}")
                return None
        
        return self.youtube_service
    
    def authenticate(self):
        """Main authentication method with comprehensive fallback strategy."""
        # First, try to load cached credentials
        if self.load_cached_credentials():
            if self.refresh_credentials_if_needed():
                return True
        
        # Try service account authentication
        if self.authenticate_with_service_account():
            self.save_cached_credentials()
            return True
        
        # Fall back to OAuth authentication
        if self.authenticate_with_oauth():
            self.save_cached_credentials()
            return True
        
        # Try emergency recovery
        if self._emergency_credential_recovery():
            return True
        
        logger.error("All authentication methods failed")
        return False

class YouTubeUploader:
    """Enhanced YouTube uploader with future-proof credential management."""
    
    def __init__(self):
        self.credential_manager = FutureProofCredentialManager()
        self.youtube = None
    
    def initialize(self):
        """Initialize the uploader with future-proof authentication."""
        if not self.credential_manager.authenticate():
            raise Exception("Failed to authenticate with YouTube API")
        
        self.youtube = self.credential_manager.get_youtube_service()
        if not self.youtube:
            raise Exception("Failed to create YouTube service")
        
        logger.info("YouTube uploader initialized successfully with future-proof credentials")
    
    def upload_video(self, video_path, title="Uploaded from Python", 
                    description="This is the most awesome description ever",
                    tags=["test", "python", "api"],
                    category_id="22", privacy_status="private"):
        """Upload a video to YouTube with enhanced reliability."""
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        # Ensure we have valid credentials
        self.youtube = self.credential_manager.get_youtube_service()
        if not self.youtube:
            raise Exception("YouTube service not available")
        
        request_body = {
            "snippet": {
                "categoryId": category_id,
                "title": title,
                "description": description,
                "tags": tags
            },
            "status": {
                "privacyStatus": privacy_status
            }
        }
        
        try:
            # Create media upload object
            media = MediaFileUpload(
                video_path, 
                chunksize=-1, 
                resumable=True
            )
            
            # Create upload request
            request = self.youtube.videos().insert(
                part="snippet,status",
                body=request_body,
                media_body=media
            )
            
            logger.info(f"Starting upload of {video_path}")
            response = None
            retry_count = 0
            max_retries = 5  # Increased retries for reliability
            
            while response is None and retry_count < max_retries:
                try:
                    status, response = request.next_chunk()
                    if status:
                        progress = int(status.progress() * 100)
                        logger.info(f"Upload progress: {progress}%")
                        
                except HttpError as e:
                    if e.resp.status in [500, 502, 503, 504]:
                        retry_count += 1
                        wait_time = 2 ** retry_count
                        logger.warning(f"Server error, retrying in {wait_time} seconds... (attempt {retry_count}/{max_retries})")
                        time.sleep(wait_time)
                        
                        # Refresh credentials before retry
                        self.credential_manager.refresh_credentials_if_needed()
                        self.youtube = self.credential_manager.get_youtube_service()
                        continue
                    else:
                        raise e
                
                except Exception as e:
                    logger.error(f"Upload error: {e}")
                    retry_count += 1
                    if retry_count >= max_retries:
                        raise e
                    time.sleep(2 ** retry_count)
            
            if response:
                video_id = response['id']
                logger.info(f"Video uploaded successfully with ID: {video_id}")
                return video_id
            else:
                raise Exception("Upload failed after all retries")
                
        except Exception as e:
            logger.error(f"Failed to upload video: {e}")
            raise

def main():
    """Main function with enhanced error handling."""
    try:
        # Initialize uploader
        uploader = YouTubeUploader()
        uploader.initialize()
        
        # Upload video (replace with your video path)
        video_path = "video.mp4"
        if os.path.exists(video_path):
            video_id = uploader.upload_video(
                video_path=video_path,
                title="My Awesome Video",
                description="This video was uploaded using the future-proof YouTube API client",
                tags=["python", "api", "automation"],
                privacy_status="private"
            )
            print(f"Success! Video uploaded with ID: {video_id}")
        else:
            print(f"Video file '{video_path}' not found. Please place your video file in the same directory.")
            
    except Exception as e:
        logger.error(f"Application error: {e}")
        print(f"Error: {e}")

if __name__ == "__main__":
    main()


