#!/usr/bin/env python3
"""
Simple YouTube Uploader - Works with your existing client.json
This script handles OAuth authentication efficiently and caches tokens.
"""

import os
import json
import time
import glob
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# YouTube API scopes
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

# File paths
CLIENT_FILE = 'client.json'
TOKEN_FILE = 'token.json'

def authenticate_youtube():
    """Authenticate with YouTube API using OAuth 2.0."""
    creds = None
    
    # Load existing token if available
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            print("✅ Loaded existing credentials")
        except Exception as e:
            print(f"⚠️  Could not load existing credentials: {e}")
    
    # If no valid credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("✅ Refreshed expired credentials")
            except Exception as e:
                print(f"⚠️  Could not refresh credentials: {e}")
                creds = None
        
        if not creds:
            if not os.path.exists(CLIENT_FILE):
                print("❌ client.json file not found!")
                print("💡 Make sure your client.json file is in the same directory")
                return None
            
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                print("✅ New authentication successful")
            except Exception as e:
                print(f"❌ Authentication failed: {e}")
                return None
        
        # Save the credentials for the next run
        try:
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
            print("💾 Saved credentials for future use")
        except Exception as e:
            print(f"⚠️  Could not save credentials: {e}")
    
    return creds

def upload_video(youtube, video_path, title, description, tags, privacy="private"):
    """Upload a video to YouTube."""
    
    if not os.path.exists(video_path):
        print(f"❌ Video file not found: {video_path}")
        return None
    
    request_body = {
        "snippet": {
            "categoryId": "22",
            "title": title,
            "description": description,
            "tags": tags
        },
        "status": {
            "privacyStatus": privacy
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
        request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media
        )
        
        print(f"📤 Starting upload: {os.path.basename(video_path)}")
        response = None
        retry_count = 0
        max_retries = 3
        
        while response is None and retry_count < max_retries:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    print(f"📊 Upload progress: {progress}%")
                    
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    retry_count += 1
                    wait_time = 2 ** retry_count
                    print(f"⚠️  Server error, retrying in {wait_time} seconds... (attempt {retry_count}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    raise e
            
            except Exception as e:
                print(f"❌ Upload error: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    raise e
                time.sleep(2 ** retry_count)
        
        if response:
            video_id = response['id']
            print(f"✅ Upload successful!")
            print(f"🎥 Video ID: {video_id}")
            print(f"🔗 YouTube URL: https://youtube.com/watch?v={video_id}")
            return video_id
        else:
            print("❌ Upload failed after all retries")
            return None
            
    except Exception as e:
        print(f"❌ Failed to upload video: {e}")
        return None

def main():
    """Main function."""
    print("=" * 50)
    print("🎬 SIMPLE YOUTUBE UPLOADER")
    print("=" * 50)
    print("🔧 Using your existing client.json file")
    print("=" * 50)
    
    # Check for client.json
    if not os.path.exists(CLIENT_FILE):
        print(f"❌ {CLIENT_FILE} not found!")
        print("💡 Make sure your client.json file is in the same directory")
        return
    
    # Authenticate
    print("🔐 Authenticating with YouTube...")
    creds = authenticate_youtube()
    if not creds:
        print("❌ Authentication failed")
        return
    
    # Build YouTube service
    try:
        youtube = build('youtube', 'v3', credentials=creds)
        print("✅ YouTube service ready")
    except Exception as e:
        print(f"❌ Failed to create YouTube service: {e}")
        return
    
    # Find video files
    video_extensions = ["*.mp4", "*.avi", "*.mov", "*.mkv", "*.wmv", "*.flv", "*.webm"]
    video_files = []
    
    # Look for videos in current directory first
    for ext in video_extensions:
        video_files.extend(glob.glob(ext))
    
    # If no videos in current directory, check videos_to_upload folder
    if not video_files:
        upload_folder = "videos_to_upload"
        if os.path.exists(upload_folder):
            for ext in video_extensions:
                video_files.extend(glob.glob(os.path.join(upload_folder, ext)))
        else:
            print(f"📁 Creating {upload_folder} folder...")
            os.makedirs(upload_folder)
            print(f"📁 Place your video files in the '{upload_folder}' folder")
            print(f"📁 Then run this script again")
            return
    
    if not video_files:
        print("📁 No video files found!")
        print("💡 Supported formats: MP4, AVI, MOV, MKV, WMV, FLV, WEBM")
        print("💡 Place your video files in the current directory or 'videos_to_upload' folder")
        return
    
    print(f"📹 Found {len(video_files)} video(s) to upload")
    
    # Upload each video
    for video_path in video_files:
        try:
            filename = os.path.basename(video_path)
            title = os.path.splitext(filename)[0]
            
            print(f"\n{'='*50}")
            print(f"📤 Uploading: {filename}")
            print(f"📝 Title: {title}")
            print(f"🔒 Privacy: private")
            print(f"{'='*50}")
            
            video_id = upload_video(
                youtube=youtube,
                video_path=video_path,
                title=title,
                description=f"Uploaded on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                tags=["uploaded", "python"],
                privacy="private"
            )
            
            if video_id:
                # Move to uploaded folder
                uploaded_folder = "uploaded_videos"
                if not os.path.exists(uploaded_folder):
                    os.makedirs(uploaded_folder)
                
                new_path = os.path.join(uploaded_folder, filename)
                os.rename(video_path, new_path)
                print(f"📁 Moved to: {uploaded_folder}")
            
        except Exception as e:
            print(f"❌ Failed to upload {filename}: {e}")
            continue
    
    print(f"\n{'='*50}")
    print("🎉 UPLOAD SESSION COMPLETED!")
    print("=" * 50)
    print("✅ Your videos have been uploaded!")
    print("📁 Check the 'uploaded_videos' folder")
    print("🔗 Visit your YouTube channel to see the videos")

if __name__ == "__main__":
    main()
