## AI Auto Posting

Make short-form content creation and publishing fast. This project provides a Flask-based web app that helps you prepare videos (trim, caption, organize) and upload them to YouTube with a clean OAuth flow. It also includes Whisper AI transcription, a simple asset dashboard, basic scheduling scaffolding, and production-ready Docker/Fly.io/Railway deployment.

### Key Features
- **Web UI for video workflow**: Upload, trim, review, and manage assets under `static/` via pages like `edit.html`, `clip_video.html`, `video.html`.
- **YouTube uploads (OAuth)**: Click-to-upload from the dashboard with a fresh, reliable OAuth session for each upload.
- **AI captions (Whisper)**: Generate transcripts/captions for audio/video files with ffmpeg + Whisper. Fallbacks available.
- **Health and dashboard APIs**: System health, existing video discovery, and per-folder dashboards.
- **Deployment ready**: Docker image, `fly.toml`, `railway.json`, and helper scripts for CI/CD or hosted deployment.

### Tech Stack
- **Language/Runtime**: Python 3.11 (Docker), works locally with Python 3.9+ as well
- **Web Framework**: Flask (+ Flask-Session, Flask-CORS)
- **AI / ML**: openai-whisper, Torch (CPU), optional Google Speech as fallback
- **Media Processing**: ffmpeg, moviepy
- **Google APIs**: google-auth, google-auth-oauthlib, google-api-python-client
- **Frontend**: Jinja2 templates in `templates/` (+ minimal JS/CSS under `static/`)
- **Infra/Deploy**: Docker, Fly.io, Railway. PostgreSQL config scaffolding is present but optional


## Repository Structure
```
AI-Auto-Posting/
├── server.py                   # Main Flask app with routes, YouTube service, Whisper endpoints
├── config.py                   # Centralized configuration (env-driven)
├── requirements.txt            # Locked runtime deps (prod Docker uses this)
├── requirements_production.txt # Looser ranges for hosted installs (optional)
├── Dockerfile                  # Production image (Python 3.11 slim)
├── fly.toml                    # Fly.io app config (health checks, volume mount)
├── railway.json                # Railway deploy config
├── templates/                  # Jinja2 templates (UI)
│   ├── edit.html               # Dashboard for trimmed videos and uploads
│   ├── clip_video.html         # Trimming UI
│   ├── test_whisper.html       # Whisper test UI
│   └── ...
├── static/
│   ├── videos/                 # Original/source videos
│   ├── trimmed/                # Ready-to-publish clips
│   ├── uploads/                # Uploaded assets (form uploads)
│   ├── youtube_token.json      # Transient OAuth token (if produced)
│   └── ... (css/js)
├── captions/                   # Generated captions/transcripts (txt)
├── credentials/                # CSV credential placeholders for social platforms (optional)
├── tokens/                     # Token cache directory (if used)
├── tests and utilities
│   ├── test_api.py             # Smoke tests for core endpoints
│   ├── test_upload_endpoint.py # YouTube upload endpoint test
│   ├── test_youtube_upload*.py # Additional YouTube tests/utilities
│   └── fix_whisper.py          # Auto-fix script for NumPy/Whisper issues on Windows
├── docs & guides
│   ├── YOUTUBE_UPLOAD_GUIDE.md
│   ├── WHISPER_AI_GUIDE.md
│   └── TROUBLESHOOTING_WHISPER.md
├── env_template.txt            # Copy/adjust to set environment variables
├── youtube_uploads.json        # Record of uploaded video metadata
├── scheduled_posts.csv|json    # Skeleton for future scheduling features
├── client_secrets.json         # Google OAuth client (place locally; do not commit sensitive creds)
└── README.md                   # This file
```


## Architecture & Workflow

### High-level flow
1. Place or upload media into `static/videos/` (or upload via UI)
2. Use the UI to trim/prepare clips, generating outputs in `static/trimmed/`
3. (Optional) Generate captions/transcripts via Whisper
4. Click the YouTube icon on a clip to start OAuth and upload
5. Successful uploads are recorded in `youtube_uploads.json`

### Backend design
- Single Flask app entrypoint: `server.py`
  - YouTube upload service and API endpoints are integrated in this file
  - Whisper integration endpoints handle transcription via ffmpeg + Whisper
  - Health/dashboard endpoints expose server readiness and media listings
- Configuration comes from environment variables with defaults in `config.py`
- Storage is filesystem-first (local `static/`), with an optional Fly.io volume mount


## Prerequisites
- Python 3.9+ (3.11 used in Docker)
- pip
- ffmpeg installed and on PATH
  - Windows: download from `https://ffmpeg.org/download.html` and add `bin` to PATH
- For Whisper on Windows, see troubleshooting section or use `fix_whisper.py`
- Google Cloud project with YouTube Data API v3 enabled + `client_secrets.json`


## Quick Start (Local)
```bash
# 1) Clone
git clone <your-repo-url>
cd AI-Auto-Posting

# 2) Create a virtual environment
python -m venv .venv
. .venv/Scripts/activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# 3) Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4) Configure environment
copy env_template.txt .env  # Windows (or create your own .env)
# Fill values as needed (see Environment section)

# 5) Place OAuth client file in project root
#   client_secrets.json  (downloaded from Google Cloud Console)

# 6) Create required folders (if not already present)
mkdir -p static/uploads static/trimmed static/videos captions

# 7) Run
python server.py
# Visit http://localhost:5000/
```


## Environment Configuration
Environment variables are read by `config.py`. Use `.env` with the following keys as needed (see `env_template.txt`):
- `SECRET_KEY`: Flask secret key
- `FLASK_ENV`: `development` or `production`
- `FLASK_DEBUG`: `true`/`false`
- `GOOGLE_API_KEY`: Gemini or other Google API keys if using additional AI features
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`: If not fully relying on `client_secrets.json`
- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`: Optional PostgreSQL config (not required by default)
- `MAX_CONTENT_LENGTH`, `UPLOAD_FOLDER`: Upload tuning
- `PORT`: For hosted environments (Docker/Fly.io uses 8080)

Note: `client_secrets.json` is required for YouTube OAuth (desktop app client). Keep it private.


## Using the App

### Web UI
- `GET /` or `GET /edit`: Dashboard for viewing clips and uploading to YouTube
- `GET /clip_video`: Video trimming page (inputs from `static/videos/`)
- `GET /test-whisper`: Quick UI for transcription testing

### YouTube Uploads
1. Ensure `client_secrets.json` is in project root
2. From the dashboard, click the YouTube icon on a clip
3. A browser window will open to complete OAuth
4. On success, the button turns green and a YouTube link appears
5. Upload records are appended to `youtube_uploads.json`

More details: see `YOUTUBE_UPLOAD_GUIDE.md`.

### Whisper Transcription
- REST: `POST /api/transcribe` with `multipart/form-data` and `file=@...`
- REST: `POST /api/upload-file` to store a file for later processing
- UI: visit `/test-whisper`

More details: see `WHISPER_AI_GUIDE.md`.


## API Reference (Core)
- `GET /api/health` — health, ffmpeg availability, directory checks
- `GET /api/trimmed-videos-dashboard` — dashboard data for clips under `static/trimmed/`
- `GET /api/existing-videos` — list of available videos/clips
- `GET /api/youtube/status` — YouTube service readiness
- `GET /api/youtube/channel` — Channel information (after OAuth)
- `POST /api/youtube/upload` — Upload a clip to YouTube
- `POST /api/transcribe` — Transcribe audio/video via Whisper
- `POST /api/upload-file` — Upload a file for later processing

Tip: inspect `test_api.py` and `test_upload_endpoint.py` for example calls and expected responses.


## Testing
```bash
# API smoke tests
python test_api.py

# YouTube upload endpoint test (requires trimmed video and OAuth)
python test_upload_endpoint.py

# Additional YouTube utility tests
python test_youtube_upload.py
python test_youtube_simple*.py
```


## Deployment

### Docker
```bash
# Build image
docker build -t ai-auto-posting:latest .

# Run container (maps 8080 inside to 8080 outside)
docker run --rm -p 8080:8080 \
  -e FLASK_ENV=production \
  -v "${PWD}/static:/app/static" \
  ai-auto-posting:latest

# Access at http://localhost:8080
```
Notes:
- The image creates `static/` directories and copies `static/youtube_token.json` to app root if present
- Mounting `static/` as a volume persists your media and upload records

### Fly.io
- Configured via `fly.toml` (internal port 8080, health check `/api/health`)
- Volume mount example is included for `/app/static`
- See `FLY_DEPLOYMENT_GUIDE.md` for step-by-step deployment

### Railway
- Uses `railway.json` and the same Dockerfile
- Start command: `python server.py`


## Troubleshooting & Known Issues
- Whisper/NumPy compatibility on Windows
  - Run the helper: `python fix_whisper.py`
  - Or see `TROUBLESHOOTING_WHISPER.md` for manual steps (pin numpy 1.24.x, CPU Torch, reinstall Whisper)
- ffmpeg must be installed and discoverable on your PATH
- Ensure `client_secrets.json` exists for YouTube OAuth; use Desktop App credential type
- Quota errors on upload come from YouTube API limits; check GCP Console


## Security
- Do not commit secrets. Keep `client_secrets.json`, `.env`, and any tokens private
- Review CORS and session settings before exposing publicly
- If deploying publicly, add authentication/rate limiting as needed


## Roadmap (Modules)
- The `app/` package (`config/`, `models/`, `routes/`, `services/`, `utils/`) exists for future modularization
- CSVs in `credentials/` (facebook/linkedin/tiktok/youtube) are placeholders for future multi-platform posting
- Current production logic lives primarily in `server.py`


## License
This project is licensed under the terms of the license in `LICENSE`.


## Acknowledgements
- OpenAI Whisper team and community
- Google API client libraries
- Flask maintainers