#!/usr/bin/env python3
"""
Fix Whisper AI and NumPy compatibility issues
"""

import subprocess
import sys
import os

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"\n🔧 {description}...")
    print(f"Running: {command}")
    
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        if result.stdout:
            print(f"Output: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed")
        print(f"Error: {e.stderr}")
        return False

def check_python_version():
    """Check Python version compatibility"""
    print("🐍 Checking Python version...")
    version = sys.version_info
    print(f"Python version: {version.major}.{version.minor}.{version.micro}")
    
    if version.major == 3 and version.minor >= 8:
        print("✅ Python version is compatible")
        return True
    else:
        print("❌ Python version may have compatibility issues")
        print("Recommended: Python 3.8+")
        return False

def fix_numpy_compatibility():
    """Fix NumPy compatibility issues"""
    print("\n🔧 Fixing NumPy compatibility...")
    
    # Uninstall current NumPy
    run_command("pip uninstall numpy -y", "Uninstalling current NumPy")
    
    # Install compatible NumPy version
    if run_command("pip install numpy==1.24.3", "Installing compatible NumPy"):
        print("✅ NumPy compatibility fixed")
        return True
    else:
        print("❌ Failed to fix NumPy compatibility")
        return False

def install_whisper_dependencies():
    """Install Whisper AI dependencies"""
    print("\n📦 Installing Whisper AI dependencies...")
    
    # Install PyTorch with CPU support (more compatible)
    if run_command("pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu", "Installing PyTorch CPU version"):
        print("✅ PyTorch installed successfully")
    else:
        print("❌ PyTorch installation failed")
        return False
    
    # Install Whisper
    if run_command("pip install openai-whisper", "Installing Whisper AI"):
        print("✅ Whisper AI installed successfully")
        return True
    else:
        print("❌ Whisper AI installation failed")
        return False

def install_fallback_dependencies():
    """Install fallback transcription dependencies"""
    print("\n🔄 Installing fallback transcription dependencies...")
    
    # Install SpeechRecognition
    if run_command("pip install SpeechRecognition", "Installing SpeechRecognition"):
        print("✅ SpeechRecognition installed successfully")
    else:
        print("❌ SpeechRecognition installation failed")
    
    # Install PyAudio (may fail on Windows, that's okay)
    if run_command("pip install pyaudio", "Installing PyAudio"):
        print("✅ PyAudio installed successfully")
    else:
        print("⚠️ PyAudio installation failed (this is normal on Windows)")
        print("   You can still use Whisper AI as the primary method")

def test_installation():
    """Test if the installation works"""
    print("\n🧪 Testing installation...")
    
    try:
        import numpy
        print(f"✅ NumPy version: {numpy.__version__}")
        
        import torch
        print(f"✅ PyTorch version: {torch.__version__}")
        
        import whisper
        print("✅ Whisper AI imported successfully")
        
        # Try to load a tiny model
        print("🔍 Testing Whisper model loading...")
        model = whisper.load_model("tiny")
        print("✅ Whisper model loaded successfully!")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        return False

def main():
    """Main installation process"""
    print("🎤 Whisper AI Installation Fixer")
    print("=" * 50)
    
    # Check Python version
    if not check_python_version():
        print("\n⚠️ Continue anyway? (y/n): ", end="")
        if input().lower() != 'y':
            return
    
    # Fix NumPy compatibility
    if not fix_numpy_compatibility():
        print("\n❌ Cannot proceed without fixing NumPy")
        return
    
    # Install Whisper dependencies
    if not install_whisper_dependencies():
        print("\n❌ Cannot proceed without Whisper AI")
        return
    
    # Install fallback dependencies
    install_fallback_dependencies()
    
    # Test installation
    if test_installation():
        print("\n🎉 Installation completed successfully!")
        print("\nYou can now:")
        print("1. Start your server: python server.py")
        print("2. Test transcription at: http://localhost:5000/test-whisper")
        print("3. Use the API endpoints for transcription")
    else:
        print("\n❌ Installation test failed")
        print("Please check the error messages above")

if __name__ == "__main__":
    main()
