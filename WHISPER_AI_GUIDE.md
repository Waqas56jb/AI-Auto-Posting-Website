# 🎤 Whisper AI Integration Guide

## Overview
This guide explains how to use the Whisper AI integration for automatic transcript generation from audio and video files in your AI Auto-Posting project.

## ✨ Features
- **Audio Transcription**: Convert speech from audio files (MP3, WAV, FLAC, AAC, OGG)
- **Video Transcription**: Extract audio from video files and generate transcripts
- **Multiple Formats**: Support for MP4, MOV, AVI, MKV, WEBM video formats
- **Real-time Processing**: Upload files and get transcripts instantly
- **Language Detection**: Automatic language detection and transcription
- **Progress Tracking**: Visual progress indicators during processing

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install openai-whisper
pip install -r requirements.txt
```

### 2. Start the Server
```bash
python server.py
```

### 3. Test the Integration
Visit: `http://localhost:5000/test-whisper`

## 📁 API Endpoints

### POST `/api/transcribe`
Upload an audio or video file for transcription.

**Request:**
- Method: `POST`
- Content-Type: `multipart/form-data`
- Body: `file` (audio/video file)

**Response:**
```json
{
  "success": true,
  "transcript": "This is the transcribed text from your audio or video file.",
  "word_count": 15,
  "duration": "00:01:30",
  "language": "en"
}
```

**Error Response:**
```json
{
  "success": false,
  "error": "Error message describing what went wrong"
}
```

### POST `/api/upload-file`
Upload a file for later processing.

**Request:**
- Method: `POST`
- Content-Type: `multipart/form-data`
- Body: `file` (audio/video file)

**Response:**
```json
{
  "success": true,
  "message": "File uploaded successfully",
  "filename": "audio.wav",
  "file_path": "static/audio/audio.wav"
}
```

## 🎯 Usage Examples

### Python Script Example
```python
import requests

# Transcribe an audio file
def transcribe_audio(file_path):
    with open(file_path, 'rb') as f:
        files = {'file': f}
        response = requests.post('http://localhost:5000/api/transcribe', files=files)
        return response.json()

# Example usage
result = transcribe_audio('path/to/audio.wav')
if result['success']:
    print(f"Transcript: {result['transcript']}")
    print(f"Word count: {result['word_count']}")
    print(f"Duration: {result['duration']}")
else:
    print(f"Error: {result['error']}")
```

### JavaScript/HTML Example
```html
<input type="file" id="audioFile" accept="audio/*,video/*">
<button onclick="transcribeFile()">Transcribe</button>

<script>
async function transcribeFile() {
    const fileInput = document.getElementById('audioFile');
    const file = fileInput.files[0];
    
    if (!file) {
        alert('Please select a file');
        return;
    }
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch('/api/transcribe', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            console.log('Transcript:', result.transcript);
            console.log('Word count:', result.word_count);
            console.log('Duration:', result.duration);
        } else {
            console.error('Error:', result.error);
        }
    } catch (error) {
        console.error('Request failed:', error);
    }
}
</script>
```

## 🔧 Configuration

### Whisper Model Selection
The default model is "base" which provides a good balance of speed and accuracy. You can modify this in `server.py`:

```python
# Change model size as needed
whisper_model = whisper.load_model("base")  # Options: "tiny", "base", "small", "medium", "large"
```

**Model Comparison:**
- **tiny**: Fastest, least accurate (39M parameters)
- **base**: Fast, good accuracy (74M parameters) ⭐ **Recommended**
- **small**: Balanced (244M parameters)
- **medium**: Slower, better accuracy (769M parameters)
- **large**: Slowest, most accurate (1550M parameters)

### Supported File Formats
- **Audio**: MP3, WAV, FLAC, AAC, OGG
- **Video**: MP4, MOV, AVI, MKV, WEBM

## 🧪 Testing

### 1. Test Page
Visit `/test-whisper` for an interactive testing interface.

### 2. Command Line Testing
```bash
python test_whisper.py
```

### 3. API Testing with curl
```bash
# Test transcription
curl -X POST -F "file=@audio.wav" http://localhost:5000/api/transcribe

# Test file upload
curl -X POST -F "file=@video.mp4" http://localhost:5000/api/upload-file
```

## 🐛 Troubleshooting

### Common Issues

#### 1. "Whisper AI model not available"
**Solution:** Install the openai-whisper library
```bash
pip install openai-whisper
```

#### 2. "FFmpeg error"
**Solution:** Install FFmpeg
- **Windows:** Download from https://ffmpeg.org/download.html
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`

#### 3. "File type not supported"
**Solution:** Check that your file format is in the `ALLOWED_EXTENSIONS` list in `server.py`

#### 4. Slow transcription
**Solutions:**
- Use a smaller Whisper model (e.g., "tiny" instead of "base")
- Ensure you have sufficient RAM and CPU
- Consider using GPU acceleration if available

### Performance Tips
1. **Model Selection**: Use "tiny" for quick tests, "base" for production
2. **File Size**: Smaller files process faster
3. **Audio Quality**: Clear audio with minimal background noise works best
4. **Format**: WAV files are processed fastest

## 🔒 Security Considerations
- File uploads are temporarily stored and automatically cleaned up
- Maximum file size can be configured in `config.py`
- Supported file types are strictly validated
- Consider implementing user authentication for production use

## 📊 Monitoring and Logs
The server logs all transcription activities:
```python
logger.info(f"Successfully generated transcript with {word_count} words")
logger.error(f"Error generating transcript: {str(e)}")
```

Check the console output for detailed information about the transcription process.

## 🚀 Production Deployment
For production use:
1. Use a larger Whisper model for better accuracy
2. Implement proper error handling and retry logic
3. Add rate limiting to prevent abuse
4. Consider using a CDN for file storage
5. Monitor resource usage (CPU, RAM, disk)

## 📚 Additional Resources
- [OpenAI Whisper Documentation](https://github.com/openai/whisper)
- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
- [Flask File Uploads](https://flask.palletsprojects.com/en/2.3.x/patterns/fileuploads/)

## 🤝 Support
If you encounter issues:
1. Check the server logs for error messages
2. Verify all dependencies are installed
3. Test with a simple audio file first
4. Check file format compatibility
5. Ensure sufficient system resources

---

**Happy Transcribing! 🎤✨**
