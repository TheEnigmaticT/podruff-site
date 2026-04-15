import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
LATE_API_KEY = os.environ.get("LATE_API_KEY", "")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "video-pipeline")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "")

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")

WORK_DIR = os.environ.get("PIPELINE_WORK_DIR", "/tmp/video-pipeline")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "")

GOOGLE_CREDS_PATH = os.environ.get(
    "GOOGLE_CREDS_PATH",
    os.path.expanduser("~/.google_workspace_mcp/credentials/tlongino@crowdtamers.com.json"),
)
ZENCASTR_FOLDER_ID = os.environ.get("ZENCASTR_FOLDER_ID", "")
DRIVE_CLIENTS_ROOT = os.environ.get("DRIVE_CLIENTS_ROOT", "")
