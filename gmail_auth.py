import os
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'


def get_gmail_service():
    creds = None

    # Load existing token
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # If no valid credentials, refresh or re-login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Auto-refresh without opening browser again
            creds.refresh(Request())
        else:
            # First-time login — opens browser
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
    
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
    
        # Save token for next run
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())

    service = build('gmail', 'v1', credentials=creds)
    return service


def is_authenticated():
    if not os.path.exists(TOKEN_FILE):
        return False
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.valid:
            return True
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, 'w') as f:
                f.write(creds.to_json())
            return True
    except Exception:
        return False
    return False


def logout():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)