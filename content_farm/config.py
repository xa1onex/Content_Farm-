"""Configuration resolved relative to the project, independent of working directory."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

def path_setting(name, default):
    value = Path(os.getenv(name, default)).expanduser()
    return value if value.is_absolute() else BASE_DIR / value

JOB_DIR = path_setting('JOB_DIR', 'jobs')
BG_VIDEO = path_setting('BG_VIDEO', 'assets/jackcraft_bg.mp4')
VIDEO_LAYOUT = os.getenv('VIDEO_LAYOUT', 'split')
if VIDEO_LAYOUT not in {'portrait', 'split'}:
    raise ValueError('VIDEO_LAYOUT must be portrait or split')
GEMINI_MODEL = 'gemini-3.5-flash'
WHISPER_MODEL = 'base'
MAX_SOURCE_SECONDS = int(os.getenv('MAX_SOURCE_SECONDS', '7200'))
MAX_INPUT_BYTES = int(os.getenv('MAX_INPUT_BYTES', str(2 * 1024**3)))
MAX_QUEUE_SIZE = int(os.getenv('MAX_QUEUE_SIZE', '10'))
FFMPEG_THREADS = int(os.getenv('FFMPEG_THREADS', '2'))
TELEGRAM_VIDEO_BYTES = 49_000_000
TELEGRAM_INPUT_BYTES = 20_000_000
MIN_FREE_BYTES = 3 * 1024**3
GDRIVE_FOLDER_ID = os.getenv('GDRIVE_FOLDER_ID', '').strip()
GDRIVE_TOKEN_FILE = path_setting('GDRIVE_TOKEN_FILE', 'drive_token.json')
GDRIVE_CLIENT_FILE = path_setting('GDRIVE_CLIENT_FILE', 'oauth_client.json')
GDRIVE_SERVICE_FILE = path_setting('GDRIVE_SERVICE_FILE', 'service_account.json')

# Server-side integration. The farm token is never shipped to Windows.
AUTOPOST_ENABLED = os.getenv('AUTOPOST_ENABLED', 'false').lower() in {'1','true','yes'}
AUTOPOST_API_URL = os.getenv('AUTOPOST_API_URL', 'https://origin.xdoublegroup.online:8443/api/v1').rstrip('/')
AUTOPOST_TOKEN_FILE = path_setting('AUTOPOST_TOKEN_FILE', 'autopost_credentials.json')
AUTOPOST_CATEGORY_ID = os.getenv('AUTOPOST_CATEGORY_ID', '').strip()
AUTOPOST_FORMAT_ID = os.getenv('AUTOPOST_FORMAT_ID', '').strip()
