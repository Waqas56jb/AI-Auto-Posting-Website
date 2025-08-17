#!/usr/bin/env python3
"""
Simple YouTube API test script
"""

import os
import sys
import json

def test_youtube_setup():
    """Test YouTube API setup and authentication"""
    
    print("🔍 Testing YouTube API Setup...")
    
    # Check if client_secrets.json exists
    if not os.path.exists('client_secrets.json'):
        print("❌ client_secrets.json not found!")
        print("Please ensure client_secrets.json is in the project root directory")
        return False
    
    print("✅ client_secrets.json found")
    
    # Check client_secrets.json format
    try:
        with open('client_secrets.json', 'r') as f:
            secrets = json.load(f)
        
        if 'installed' not in secrets:
            print("❌ Invalid client_secrets.json format - missing 'installed' section")
            return False
        
        client_id = secrets['installed'].get('client_id')
        client_secret = secrets['installed'].get('client_secret')
        
        if not client_id or not client_secret:
            print("❌ Missing client_id or client_secret in client_secrets.json")
            return False
        
        print("✅ client_secrets.json format is valid")
        print(f"   Client ID: {client_id[:20]}...")
        
    except json.JSONDecodeError:
        print("❌ Invalid JSON in client_secrets.json")
        return False
    except Exception as e:
        print(f"❌ Error reading client_secrets.json: {e}")
        return False
    
    # Test importing YouTube service
    try:
        from server import youtube_service
        print("✅ YouTube service imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import YouTube service: {e}")
        return False
    
    # Test authentication
    print("\n🔐 Testing YouTube Authentication...")
    try:
        auth_result = youtube_service.authenticate()
        if auth_result:
            print("✅ YouTube authentication successful")
            
            # Test channel info
            print("\n📺 Testing Channel Info...")
            channel_info = youtube_service.get_channel_info()
            if channel_info['success']:
                print("✅ Channel info retrieved successfully")
                print(f"   Channel: {channel_info['channel_title']}")
                print(f"   Subscribers: {channel_info['subscriber_count']}")
                print(f"   Videos: {channel_info['video_count']}")
            else:
                print(f"❌ Failed to get channel info: {channel_info['error']}")
                return False
        else:
            print("❌ YouTube authentication failed")
            return False
            
    except Exception as e:
        print(f"❌ Authentication test failed: {e}")
        return False
    
    print("\n🎉 All tests passed! YouTube API is ready for uploads.")
    return True

if __name__ == "__main__":
    success = test_youtube_setup()
    sys.exit(0 if success else 1)
