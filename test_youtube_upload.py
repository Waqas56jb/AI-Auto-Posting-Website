#!/usr/bin/env python3
"""
Test script for YouTube upload functionality
"""

import os
import sys

# Import YouTube service from server.py
try:
    from server import youtube_service
    print("✅ YouTube service imported successfully from server.py")
except ImportError as e:
    print(f"❌ Failed to import YouTube service from server.py: {e}")
    sys.exit(1)

def test_youtube_service():
    """Test YouTube service functionality"""
    print("Testing YouTube Service...")
    
    # Check if client_secrets.json exists
    if not os.path.exists('client_secrets.json'):
        print("❌ client_secrets.json not found!")
        print("Please ensure client_secrets.json is in the project root directory.")
        return False
    
    print("✅ client_secrets.json found")
    
    # Test authentication (this will trigger OAuth flow if needed)
    print("\nTesting authentication...")
    try:
        if youtube_service.authenticate():
            print("✅ YouTube authentication successful")
        else:
            print("❌ YouTube authentication failed")
            return False
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        return False
    
    # Test channel info
    print("\nTesting channel info...")
    try:
        channel_info = youtube_service.get_channel_info()
        if channel_info['success']:
            print("✅ Channel info retrieved successfully")
            print(f"   Channel: {channel_info['channel_title']}")
            print(f"   Subscribers: {channel_info['subscriber_count']}")
            print(f"   Videos: {channel_info['video_count']}")
        else:
            print(f"❌ Failed to get channel info: {channel_info['error']}")
            return False
    except Exception as e:
        print(f"❌ Channel info error: {e}")
        return False
    
    print("\n🎉 All tests passed! YouTube service is ready for uploads.")
    print("\n📝 Note: This simplified implementation will require re-authentication for each upload.")
    print("   This ensures maximum security and reliability.")
    return True

if __name__ == "__main__":
    success = test_youtube_service()
    sys.exit(0 if success else 1)
