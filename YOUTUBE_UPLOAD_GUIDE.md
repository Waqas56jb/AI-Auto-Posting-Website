# YouTube Upload Guide

This guide explains how to set up and use the simplified YouTube upload functionality in the AI Auto Posting application.

## Features

- ✅ **Direct YouTube Upload**: Click the YouTube icon on any video to upload it directly to your channel
- ✅ **Simple Authentication**: Uses OAuth2 with fresh authentication for each upload
- ✅ **Caption Integration**: Uploads videos with generated captions and hashtags
- ✅ **Visual Feedback**: YouTube button turns green after successful upload
- ✅ **Reliable Uploads**: Simplified implementation ensures maximum reliability
- ✅ **Integrated Backend**: All YouTube functionality is built into `server.py`

## Setup Instructions

### 1. Prerequisites

- Python 3.7+ with required dependencies installed
- Google Cloud Project with YouTube Data API v3 enabled
- `client_secrets.json` file from Google Cloud Console

### 2. Install Dependencies

The required dependencies are already included in `requirements.txt`:

```bash
pip install -r requirements.txt
```

Required packages:
- `google-auth==2.32.0`
- `google-auth-oauthlib==1.2.1`
- `google-auth-httplib2==0.2.0`
- `google-api-python-client==2.139.0`

### 3. Google Cloud Setup

1. **Create a Google Cloud Project** (if you don't have one)
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one

2. **Enable YouTube Data API v3**
   - Go to "APIs & Services" > "Library"
   - Search for "YouTube Data API v3"
   - Click "Enable"

3. **Create OAuth 2.0 Credentials**
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth 2.0 Client IDs"
   - Choose "Desktop application"
   - Download the JSON file and rename it to `client_secrets.json`
   - Place it in the project root directory

### 4. File Structure

Ensure your project has this structure:
```
AI-Auto-Posting/
├── client_secrets.json          # Your Google OAuth credentials
├── server.py                    # Main Flask application with integrated YouTube service
├── templates/
│   ├── edit.html               # Video dashboard with YouTube upload
│   └── settings.html           # Settings page (no YouTube config needed)
└── static/
    ├── videos/                 # Original videos
    └── trimmed/                # Trimmed videos
```

## How It Works

### Authentication Flow

1. **Fresh Authentication**: Each upload starts with a fresh OAuth authentication:
   - A browser window opens for Google OAuth authentication
   - Grant permissions to upload videos to your channel
   - Authentication is completed for the current upload session

2. **Simple and Reliable**: 
   - No token storage or refresh complexity
   - Each upload is independent and secure
   - Works reliably without token expiration issues

### Upload Process

1. **Click YouTube Icon**: On any video card in the dashboard
2. **Automatic Processing**:
   - Loads any saved captions for the video
   - Extracts hashtags from captions
   - Opens browser for OAuth authentication (if needed)
   - Uploads video with title, description, and tags
3. **Success Feedback**:
   - YouTube button turns green
   - "View on YouTube" link appears
   - Success notification is shown

### Video Metadata

The system automatically sets:
- **Title**: Video filename (without extension)
- **Description**: Generated caption + hashtags
- **Tags**: Extracted hashtags from captions
- **Privacy**: Public (can be changed in code)
- **Category**: People & Blogs (category ID 22)

## Usage

### 1. Start the Application

```bash
python server.py
```

### 2. Navigate to Dashboard

Go to `http://localhost:5000/edit` to access the video dashboard.

### 3. Upload Videos

1. **Upload a video** or **create a clip** using the existing functionality
2. **Generate captions** using the "Generate Caption" button (optional but recommended)
3. **Click the YouTube icon** on any video card
4. **Authenticate** - browser will open for Google OAuth (first time or each session)
5. **Wait for upload** - the button will show a spinner during upload
6. **Success!** - Button turns green and shows a checkmark

### 4. View Uploaded Videos

- Click the "View on YouTube" link that appears after successful upload
- Or check your YouTube channel directly

## Advantages of Integrated Approach

### ✅ **Single File Backend**
- All YouTube functionality is in `server.py`
- No separate modules to manage
- Everything works from one main file

### ✅ **Reliability**
- No token expiration issues
- No complex refresh token management
- Each upload is independent and secure

### ✅ **Security**
- Fresh authentication for each session
- No stored credentials to compromise
- OAuth flow ensures proper permissions

### ✅ **Simplicity**
- Less code complexity
- Fewer potential failure points
- Easier to debug and maintain

### ✅ **User Experience**
- Single click upload
- Clear authentication flow
- Immediate visual feedback

## Troubleshooting

### Common Issues

1. **"Client secrets file not found"**
   - Ensure `client_secrets.json` is in the project root directory
   - Verify the file name is exactly `client_secrets.json`

2. **"Authentication failed"**
   - Check that your Google Cloud project has YouTube Data API v3 enabled
   - Verify OAuth 2.0 credentials are set up correctly
   - Ensure you're using a desktop application credential type

3. **"Upload failed"**
   - Check your internet connection
   - Verify the video file exists and is accessible
   - Check YouTube API quota limits

4. **"Permission denied"**
   - Ensure you granted the correct permissions during OAuth flow
   - The app needs permission to upload videos to your channel

### Testing

Run the test script to verify everything is working:

```bash
python test_youtube_upload.py
```

This will:
- Check if `client_secrets.json` exists
- Test authentication
- Verify channel access
- Confirm the service is ready for uploads

## File Management

### Credential Files

- `client_secrets.json`: Your Google OAuth credentials (keep secure)
- `youtube_token.json`: Temporary token file (auto-generated and removed)

### Upload Records

- `youtube_uploads.json`: Tracks all uploaded videos with metadata

## Security Notes

- Keep `client_secrets.json` secure and don't share it
- The application only requests necessary permissions (video upload)
- No long-term token storage
- Fresh authentication for each upload session

## API Endpoints

The following endpoints are available in `server.py`:

- `POST /api/youtube/upload`: Upload video to YouTube
- `GET /api/youtube/channel`: Get channel information
- `GET /api/youtube/status`: Check service status

## Code Structure in server.py

The YouTube functionality is integrated into `server.py` with:

1. **YouTube API Imports**: At the top of the file
2. **YouTubeService Class**: Complete YouTube API service class
3. **Global Instance**: `youtube_service = YouTubeService()`
4. **API Endpoints**: Three endpoints for upload, channel info, and status
5. **Helper Functions**: `save_upload_record()` for tracking uploads

## Customization

### Change Video Privacy

Edit the `upload_video` method in `server.py` around line ~150:
```python
privacy_status: str = "public"  # Options: "public", "private", "unlisted"
```

### Change Video Category

Edit the `upload_video` method in `server.py` around line ~149:
```python
category_id: str = "22"  # 22 = People & Blogs, 10 = Music, 20 = Gaming, etc.
```

### Modify Upload Metadata

Edit the `upload_video` method in the `YouTubeService` class in `server.py` to customize:
- Title format
- Description template
- Default tags
- Video settings

## Support

If you encounter issues:

1. Check the browser console for error messages
2. Check the server logs for detailed error information
3. Run `python test_youtube_upload.py` to verify setup
4. Ensure all dependencies are installed correctly

The integrated YouTube upload functionality is designed to be robust, secure, and user-friendly, with fresh authentication for each upload session ensuring maximum reliability. All functionality is contained within your main `server.py` file for easy management and deployment.
