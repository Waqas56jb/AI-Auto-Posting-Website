#!/usr/bin/env python3
"""
Simple YouTube Upload Test Script
Tests the YouTube upload functionality in server.py
"""

import os
import sys
import json
import requests
from pathlib import Path

def test_youtube_upload():
    """Test YouTube upload functionality"""
    
    # Server URL
    base_url = "http://127.0.0.1:5000"
    
    print("Testing YouTube Upload Functionality")
    print("=" * 50)
    
    # Test 1: Check server health
    print("\n1. Testing server health...")
    try:
        response = requests.get(f"{base_url}/api/health")
        if response.status_code == 200:
            print("✅ Server is running")
        else:
            print(f"❌ Server health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Cannot connect to server: {e}")
        return False
    
    # Test 2: Check YouTube status
    print("\n2. Testing YouTube service status...")
    try:
        response = requests.get(f"{base_url}/api/youtube/status")
        data = response.json()
        print(f"Response: {data}")
        
        if data.get('available'):
            print("✅ YouTube service is available")
        else:
            print("❌ YouTube service is not available")
            return False
    except Exception as e:
        print(f"❌ YouTube status check failed: {e}")
        return False
    
    # Test 3: Check credentials
    print("\n3. Testing YouTube credentials...")
    try:
        response = requests.get(f"{base_url}/api/load-credentials?platform=youtube")
        data = response.json()
        print(f"Response: {data}")
        
        if data.get('configured'):
            print("✅ YouTube credentials are configured")
        else:
            print("❌ YouTube credentials are not configured")
            print("Please ensure client_secrets.json exists in the project root")
            return False
    except Exception as e:
        print(f"❌ Credentials check failed: {e}")
        return False
    
    # Test 4: Look for test video files
    print("\n4. Looking for test video files...")
    video_dirs = ['static/trimmed', 'static/videos', 'static']
    test_videos = []
    
    for video_dir in video_dirs:
        if os.path.exists(video_dir):
            for file in os.listdir(video_dir):
                if file.lower().endswith(('.mp4', '.mov', '.avi')):
                    video_path = os.path.join(video_dir, file)
                    test_videos.append(video_path)
                    print(f"Found video: {video_path}")
    
    if not test_videos:
        print("❌ No test videos found")
        print("Please add some video files to static/trimmed or static/videos directories")
        return False
    
    # Test 5: Test upload with first video found
    print(f"\n5. Testing upload with video: {test_videos[0]}")
    
    upload_data = {
        "video_path": test_videos[0],
        "title": "Test Upload - AI Auto Posting",
        "description": "This is a test upload from the AI Auto Posting application",
        "tags": ["test", "ai", "automation"],
        "privacy": "private"  # Use private for testing
    }
    
    try:
        response = requests.post(
            f"{base_url}/api/youtube/upload",
            json=upload_data,
            headers={'Content-Type': 'application/json'}
        )
        
        data = response.json()
        print(f"Upload response: {data}")
        
        if data.get('success'):
            print("✅ YouTube upload test successful!")
            print(f"Video URL: {data.get('video_url', 'N/A')}")
            return True
        else:
            print(f"❌ YouTube upload failed: {data.get('error', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"❌ Upload test failed: {e}")
        return False

def main():
    """Main function"""
    print("YouTube Upload Test Script")
    print("Make sure the server is running on http://127.0.0.1:5000")
    print()
    
    success = test_youtube_upload()
    
    if success:
        print("\n🎉 All tests passed! YouTube upload is working correctly.")
    else:
        print("\n❌ Some tests failed. Please check the errors above.")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
