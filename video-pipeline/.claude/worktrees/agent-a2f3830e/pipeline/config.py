import os
from dotenv import load_dotenv

load_dotenv()

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
