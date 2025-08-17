#!/usr/bin/env python3
"""
Simple test script to verify the API endpoints are working
"""

import requests
import json
import os

# Test configuration
BASE_URL = "http://localhost:5000"

def test_health_endpoint():
    """Test the health check endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/api/health")
        print(f"Health Check: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Status: {data.get('status')}")
            print(f"  FFmpeg Available: {data.get('ffmpeg_available')}")
            print(f"  Directories: {data.get('directories')}")
        else:
            print(f"  Error: {response.text}")
    except Exception as e:
        print(f"Health Check Error: {e}")

def test_trimmed_videos_dashboard():
    """Test the trimmed videos dashboard endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/api/trimmed-videos-dashboard")
        print(f"\nTrimmed Videos Dashboard: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Success: {data.get('success')}")
            print(f"  Total Count: {data.get('total_count')}")
            print(f"  Dashboard Data Keys: {list(data.get('dashboard_data', {}).keys())}")
            
            # Show some video details
            videos = data.get('videos', [])
            if videos:
                print(f"  Sample Video: {videos[0]}")
            else:
                print("  No videos found")
        else:
            print(f"  Error: {response.text}")
    except Exception as e:
        print(f"Dashboard Error: {e}")

def test_existing_videos():
    """Test the existing videos endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/api/existing-videos")
        print(f"\nExisting Videos: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Success: {data.get('success')}")
            print(f"  Video Count: {len(data.get('videos', []))}")
        else:
            print(f"  Error: {response.text}")
    except Exception as e:
        print(f"Existing Videos Error: {e}")

def check_directories():
    """Check if required directories exist"""
    print("\nDirectory Check:")
    directories = ['static/trimmed', 'static/videos', 'static/uploads']
    for directory in directories:
        exists = os.path.exists(directory)
        print(f"  {directory}: {'✓' if exists else '✗'}")
        
        if exists:
            files = [f for f in os.listdir(directory) if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv'))]
            print(f"    Video files: {len(files)}")
            if files:
                print(f"    Sample: {files[0]}")

if __name__ == "__main__":
    print("Testing AI Auto Posting API Endpoints")
    print("=" * 50)
    
    test_health_endpoint()
    test_trimmed_videos_dashboard()
    test_existing_videos()
    check_directories()
    
    print("\n" + "=" * 50)
    print("Test completed!")
