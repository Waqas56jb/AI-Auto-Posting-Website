# Caption Generation API Documentation

## Overview

The Caption Generation API provides AI-powered caption generation for video content using Google Gemini AI. This system automatically generates engaging, professional captions suitable for social media platforms like Instagram, TikTok, YouTube, and LinkedIn.

## Features

- 🤖 **AI-Powered Captions**: Uses Google Gemini AI to generate unique, engaging captions
- 🇬🇧 **British English**: Generates content using British English (no Americanisms)
- 🏠 **Property Investment Focus**: Specialized for property investment and wealth building content
- 💾 **Persistent Storage**: Saves generated captions for future use
- 📱 **Multi-Platform Ready**: Captions optimized for various social media platforms
- 🔄 **Bulk Processing**: Generate captions for multiple videos at once
- 📊 **Health Monitoring**: Built-in health checks and status monitoring

## API Endpoints

### 1. Generate Caption
**POST** `/api/generate_caption`

Generates a new caption for a video file.

**Request Body:**
```json
{
    "filename": "video_name.mp4"
}
```

**Response:**
```json
{
    "success": true,
    "caption": "Generated caption text here...",
    "hashtags": "#PropertyInvestment #UKProperty #WealthBuilding",
    "generated_at": "2024-01-15T10:30:00"
}
```

### 2. Save Caption
**POST** `/api/save_caption`

Saves a generated caption to persistent storage.

**Request Body:**
```json
{
    "filename": "video_name.mp4",
    "caption": "Caption text to save",
    "hashtags": "#Hashtags #To #Save"
}
```

**Response:**
```json
{
    "success": true,
    "message": "Caption saved successfully",
    "saved_filename": "video_name.mp4",
    "saved_at": "2024-01-15T10:30:00"
}
```

### 3. Load Caption
**GET** `/api/load_caption?filename=video_name.mp4`

Loads a previously saved caption.

**Response:**
```json
{
    "success": true,
    "caption": "Saved caption text",
    "hashtags": "#Saved #Hashtags",
    "generated_at": "2024-01-15T10:30:00",
    "original_filename": "video_name.mp4",
    "loaded_at": "2024-01-15T10:35:00"
}
```

### 4. Service Status
**GET** `/api/captions/status`

Gets the current status of the caption generation service.

**Response:**
```json
{
    "success": true,
    "ai_service": {
        "configured": true,
        "model": "gemini-1.5-flash"
    },
    "captions_stored": 25,
    "captions_directory": "captions",
    "server_time": "2024-01-15T10:30:00"
}
```

### 5. Service Health
**GET** `/api/captions/health`

Performs a comprehensive health check of the caption service.

**Response:**
```json
{
    "success": true,
    "healthy": true,
    "services": {
        "gemini_ai_configured": true,
        "gemini_ai_responding": true,
        "captions_directory": true
    },
    "timestamp": "2024-01-15T10:30:00",
    "status": "healthy"
}
```

### 6. Bulk Caption Generation
**POST** `/api/captions/bulk_generate`

Generates captions for multiple videos in a single request.

**Request Body:**
```json
{
    "filenames": [
        "video1.mp4",
        "video2.mp4",
        "video3.mp4"
    ]
}
```

**Response:**
```json
{
    "success": true,
    "results": [
        {
            "filename": "video1.mp4",
            "success": true,
            "caption": "Generated caption for video1",
            "hashtags": "#Hashtags #For #Video1"
        }
    ],
    "total_processed": 3,
    "successful": 3,
    "failed": 0,
    "completed_at": "2024-01-15T10:30:00"
}
```

### 7. List Captions
**GET** `/api/captions/list`

Lists all available captions with metadata.

**Response:**
```json
{
    "success": true,
    "captions": [
        {
            "filename": "video1",
            "size": 1024,
            "modified": "2024-01-15T10:30:00",
            "caption_preview": "Generated caption preview...",
            "generated_at": "2024-01-15T10:30:00"
        }
    ],
    "total_count": 1
}
```

## Frontend Integration

### Video Card Caption Button

The caption generation is integrated into video cards with a dedicated "Generate Caption" button:

```html
<button class="caption-btn" type="button" onclick="generateCaption('${filename}')">
    <i class="fas fa-comment-dots"></i> Generate Caption
</button>
```

### JavaScript Function

The frontend uses the `generateCaption()` function to interact with the API:

```javascript
function generateCaption(filename) {
    // Show loading state
    showNotification('Generating caption with Gemini AI...', 'info');
    
    fetch('/api/generate_caption', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            // Display generated caption
            displayCaption(data.caption, data.hashtags);
            // Save caption for persistence
            saveCaptionToFile(filename, data.caption, data.hashtags);
            showNotification('Professional caption generated successfully!', 'success');
        } else {
            showNotification(`Failed to generate caption: ${data.error}`, 'error');
        }
    })
    .catch(error => {
        console.error('Error generating caption:', error);
        showNotification('Error generating caption. Please try again.', 'error');
    });
}
```

## Configuration

### Environment Variables

The following environment variables must be set:

```bash
# Google Gemini AI API Key
GOOGLE_API_KEY=your_gemini_api_key_here

# Flask Secret Key
SECRET_KEY=your_secret_key_here
```

### Dependencies

Required Python packages (already in `requirements.txt`):

```txt
google-generativeai
flask
flask-cors
python-dotenv
```

## File Storage

### Caption Files

Generated captions are stored in the `captions/` directory with the following structure:

```
captions/
├── video1.mp4.txt
├── video2.mp4.txt
└── video3.mp4.txt
```

Each caption file contains:
- Caption text
- Hashtags
- Generation timestamp
- Original filename

### File Format

```
Caption: [Generated caption text here]
Hashtags: #PropertyInvestment #UKProperty #WealthBuilding
Generated: 2024-01-15T10:30:00
Original_Filename: video_name.mp4
```

## Error Handling

The API includes comprehensive error handling:

- **Input Validation**: Checks for required parameters and valid filenames
- **AI Service Errors**: Handles Gemini AI API failures gracefully
- **File System Errors**: Manages file I/O errors and directory creation
- **Security**: Sanitizes filenames to prevent path traversal attacks
- **Logging**: Detailed logging for debugging and monitoring

## Testing

### Test Script

Use the provided test script to verify API functionality:

```bash
python test_caption_api.py
```

The test script will:
- Test all API endpoints
- Verify caption generation
- Check file persistence
- Validate error handling
- Provide detailed results

### Manual Testing

Test the caption generation manually:

1. Start the Flask server
2. Navigate to the video editing page
3. Click the "Generate Caption" button on any video card
4. Verify the caption is generated and displayed
5. Check that the caption is saved to the `captions/` directory

## Monitoring and Debugging

### Logs

The API provides detailed logging for monitoring:

```python
logger.info(f"Generating caption for video: {filename}")
logger.info(f"Caption generated successfully for {filename}: {caption[:50]}...")
logger.error(f"Error generating caption for {filename}: {str(e)}")
```

### Health Checks

Regular health checks can be performed using:

```bash
curl http://localhost:5000/api/captions/health
```

### Status Monitoring

Check service status:

```bash
curl http://localhost:5000/api/captions/status
```

## Performance Considerations

- **Rate Limiting**: Bulk operations limited to 10 videos per request
- **Timeout Handling**: API calls include appropriate timeouts
- **Error Recovery**: Failed operations don't affect successful ones
- **Caching**: Generated captions are cached in files for quick access

## Security Features

- **Filename Sanitization**: Uses `secure_filename()` to prevent path traversal
- **Input Validation**: Validates all input parameters
- **Error Masking**: Internal errors are not exposed to clients
- **CORS Support**: Configured for cross-origin requests

## Troubleshooting

### Common Issues

1. **API Key Not Set**
   - Ensure `GOOGLE_API_KEY` is set in your `.env` file
   - Verify the API key is valid and has sufficient quota

2. **Caption Generation Fails**
   - Check server logs for detailed error messages
   - Verify internet connectivity for AI service calls
   - Ensure the `captions/` directory is writable

3. **Frontend Integration Issues**
   - Check browser console for JavaScript errors
   - Verify the API endpoints are accessible
   - Check CORS configuration if testing from different domains

### Debug Mode

Enable debug logging by setting the log level:

```python
logging.basicConfig(level=logging.DEBUG)
```

## Future Enhancements

Potential improvements for future versions:

- **Template System**: Pre-defined caption templates for different content types
- **A/B Testing**: Generate multiple caption variations for testing
- **Analytics**: Track caption performance and engagement
- **Customization**: Allow users to customize AI prompts
- **Batch Scheduling**: Schedule bulk caption generation during off-peak hours

## Support

For issues or questions:

1. Check the server logs for error details
2. Run the test script to identify specific problems
3. Verify environment variable configuration
4. Check API key validity and quota limits

---

**Note**: This caption generation system is specifically designed for property investment content and uses British English. For other content types, the AI prompts may need to be customized.
