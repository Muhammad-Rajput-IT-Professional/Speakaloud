import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
RULES_FILE = DATA_DIR / "rules.json"
LIBRARY_FILE = DATA_DIR / "library.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

def get_cache_dir() -> Path:
    """
    Returns standard OS user cache directory:
    Windows: %LOCALAPPDATA%\\Speakaloud\\cache
    Fallback: ~/.cache/Speakaloud/cache
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / "Speakaloud" / "cache"
    else:
        p = Path.home() / ".cache" / "Speakaloud" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p

CACHE_DIR = get_cache_dir()

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Default TTS settings
DEFAULT_VOICE = "en-US-JennyNeural"
DEFAULT_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_VOLUME = "+0%"
