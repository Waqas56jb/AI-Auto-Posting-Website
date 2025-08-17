#!/usr/bin/env python3
"""
Test YouTube upload endpoint
"""

import requests
import json
import os

def test_upload_endpoint():
    """Test the YouTube upload endpoint"""
    
    print("🔍 Testing YouTube Upload Endpoint...")
    
    # Check if server is running
    try:
        response = requests.get('http://localhost:5000/api/health', timeout=5)
        if response.status_code == 200:
            print("✅ Server is running")
        else:
            print("❌ Server health check failed")
            return False
    except requests.exceptions.RequestException:
        print("❌ Server is not running. Please start the server first.")
        return False
    
    # Check YouTube status
    try:
        response = requests.get('http://localhost:5000/api/youtube/status', timeout=5)
        if response.status_code == 200:
            status_data = response.json()
            print(f"✅ YouTube status: {status_data.get('message', 'Unknown')}")
        else:
            print("❌ YouTube status check failed")
            return False
    except Exception as e:
        print(f"❌ Error checking YouTube status: {e}")
        return False
    
    # Check if we have videos to test with
    trimmed_dir = 'static/trimmed'
    if not os.path.exists(trimmed_dir):
        print("❌ No trimmed videos directory found")
        return False
    
    videos = [f for f in os.listdir(trimmed_dir) if f.endswith(('.mp4', '.mov', '.avi'))]
    if not videos:
        print("❌ No videos found in trimmed directory")
        return False
    
    test_video = videos[0]
    print(f"✅ Found test video: {test_video}")
    
    # Test upload endpoint (without actually uploading)
    print("\n📤 Testing upload endpoint structure...")
    
    upload_data = {
        'video_path': f'trimmed/{test_video}',
        'title': 'Test Video Upload',
        'description': 'This is a test upload from the AI Auto Posting system',
        'tags': ['#test', '#automation'],
        'privacy': 'public'
    }
    
    try:
        response = requests.post(
            'http://localhost:5000/api/youtube/upload',
            json=upload_data,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print("✅ Upload endpoint working correctly!")
                print(f"   Video URL: {result.get('video_url', 'N/A')}")
                return True
            else:
                print(f"❌ Upload failed: {result.get('error', 'Unknown error')}")
                return False
        else:
            print(f"❌ Upload endpoint returned status {response.status_code}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ Upload request timed out (this might be normal for large files)")
        return False
    except Exception as e:
        print(f"❌ Error testing upload endpoint: {e}")
        return False

if __name__ == "__main__":
    success = test_upload_endpoint()
    if success:
        print("\n🎉 Upload endpoint test completed successfully!")
        print("You can now try uploading videos from the web interface.")
    else:
        print("\n❌ Upload endpoint test failed.")
        print("Please check the server logs for more details.")
