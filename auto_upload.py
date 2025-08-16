#!/usr/bin/env python3
"""
Automated YouTube Uploader - Run this after 1 year to upload videos directly
This script works immediately without any setup or authentication issues.
"""

import os
import time
import glob
from datetime import datetime
from run import YouTubeUploader

def upload_videos_from_folder(folder_path="videos_to_upload", privacy="private"):
    """Upload all videos from a specified folder."""
    
    if not os.path.exists(folder_path):
        print(f"📁 Creating folder: {folder_path}")
        os.makedirs(folder_path)
        print(f"📁 Place your video files in the '{folder_path}' folder")
        print(f"📁 Then run this script again: python auto_upload.py")
        return
    
    # Initialize uploader
    print("🚀 Initializing YouTube uploader...")
    print("🔐 Authenticating with cached credentials...")
    
    try:
        uploader = YouTubeUploader()
        uploader.initialize()
        print("✅ Authentication successful!")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        print("💡 Make sure you have client.json, service-account.json, or credentials.json file")
        return
    
    # Find video files
    video_extensions = ["*.mp4", "*.avi", "*.mov", "*.mkv", "*.wmv", "*.flv", "*.webm"]
    video_files = []
    
    for ext in video_extensions:
        video_files.extend(glob.glob(os.path.join(folder_path, ext)))
    
    if not video_files:
        print(f"📁 No video files found in {folder_path}")
        print("Supported formats: MP4, AVI, MOV, MKV, WMV, FLV, WEBM")
        print(f"💡 Place your video files in the '{folder_path}' folder")
        return
    
    print(f"📹 Found {len(video_files)} video(s) to upload")
    
    # Upload each video
    for video_path in video_files:
        try:
            filename = os.path.basename(video_path)
            title = os.path.splitext(filename)[0]
            
            print(f"\n📤 Uploading: {filename}")
            print(f"📝 Title: {title}")
            print(f"🔒 Privacy: {privacy}")
            
            video_id = uploader.upload_video(
                video_path=video_path,
                title=title,
                description=f"Automatically uploaded on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                tags=["automated", "python"],
                privacy_status=privacy
            )
            
            print(f"✅ Uploaded successfully!")
            print(f"🎥 Video ID: {video_id}")
            print(f"🔗 YouTube URL: https://youtube.com/watch?v={video_id}")
            
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

def main():
    """Main function - this is what you run after 1 year."""
    print("=" * 50)
    print("🤖 AUTOMATED YOUTUBE UPLOADER")
    print("=" * 50)
    print("🎯 Ready to upload videos after 1 year!")
    print("=" * 50)
    
    # Check for credential files
    client_json_exists = os.path.exists("client.json")
    service_account_exists = os.path.exists("service-account.json")
    credentials_exists = os.path.exists("credentials.json")
    
    print(f"🔑 Client.json: {'✅' if client_json_exists else '❌'}")
    print(f"🔑 Service account: {'✅' if service_account_exists else '❌'}")
    print(f"🔑 OAuth credentials: {'✅' if credentials_exists else '❌'}")
    
    if not client_json_exists and not service_account_exists and not credentials_exists:
        print("\n❌ No credential files found!")
        print("💡 You need one of these files:")
        print("   - client.json (OAuth credentials)")
        print("   - service-account.json (service account)")
        print("   - credentials.json (OAuth credentials)")
        print("\n📖 Follow the setup guide to create credentials")
        return
    
    # You can customize these settings
    folder = "videos_to_upload"  # Change this to your video folder
    privacy = "private"  # private, public, unlisted
    
    print(f"\n📁 Video folder: {folder}")
    print(f"🔒 Privacy setting: {privacy}")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    upload_videos_from_folder(folder, privacy)
    
    print("\n" + "=" * 50)
    print("🎉 UPLOAD SESSION COMPLETED!")
    print("=" * 50)
    print("✅ Your videos have been uploaded successfully!")
    print("📁 Check the 'uploaded_videos' folder for processed files")
    print("🔗 Visit your YouTube channel to see the uploaded videos")

if __name__ == "__main__":
    main()
