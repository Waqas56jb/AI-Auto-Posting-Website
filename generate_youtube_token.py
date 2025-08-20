"""
Generate a shared YouTube OAuth token (youtube_token.json) for the app.

Usage:
  1) Ensure client_secrets.json is in the project root
  2) Run:  python generate_youtube_token.py
  3) A browser window will open. Complete the Google OAuth consent.
  4) The token will be saved to static/youtube_token.json

This token will be reused by the server for all users to upload videos
to the same YouTube channel.
"""

import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS = "client_secrets.json"
DEFAULT_TOKEN_PATH = os.path.join("static", "youtube_token.json")


def ensure_dir(path: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)


def main() -> None:
    print("\n=== YouTube Token Generator ===\n")
    if not os.path.exists(CLIENT_SECRETS):
        print("❌ client_secrets.json not found in project root.")
        print("   Make sure the file is named exactly 'client_secrets.json' and is in this directory.")
        raise SystemExit(1)

    token_path = os.environ.get("YOUTUBE_TOKEN_FILE", DEFAULT_TOKEN_PATH)
    ensure_dir(token_path)

    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            print(f"Found existing token at: {token_path}")
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            print("Refreshing existing token...")
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())
            print(f"✅ Token refreshed and saved to: {token_path}")
            return
        except Exception as e:
            print(f"⚠️  Token refresh failed, re-authenticating: {e}")
            creds = None

    if not creds or not creds.valid:
        print("Starting OAuth consent flow in your browser...")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
        # Use a non-standard port to avoid conflicts
        creds = flow.run_local_server(port=8090, prompt='consent', authorization_prompt_message='')
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"\n✅ Token generated and saved to: {token_path}")
        print("All users will now upload to the linked YouTube channel using this token.")


if __name__ == "__main__":
    main()


