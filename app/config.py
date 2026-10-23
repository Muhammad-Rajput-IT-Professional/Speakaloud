import os
import shutil
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent

def get_app_data_root() -> Path:
    """
    Returns standard OS local per-machine directory:
    Windows: %LOCALAPPDATA%\\Speakaloud
    Fallback: ~/.speakaloud
    This ensures that each computer has its own independent library, rules,
    reading progress, and cache, even when the project folder is synced via OneDrive or cloned.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / "Speakaloud"
    else:
        p = Path.home() / ".speakaloud"
    p.mkdir(parents=True, exist_ok=True)
    return p

APP_DATA_DIR = get_app_data_root()
DATA_DIR = APP_DATA_DIR / "data"
CACHE_DIR = APP_DATA_DIR / "cache"
UPLOADS_DIR = DATA_DIR / "uploads"
MODELS_DIR = DATA_DIR / "models"
RULES_FILE = DATA_DIR / "rules.json"
LIBRARY_FILE = DATA_DIR / "library.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
NOTEPAD_DRAFT_FILE = DATA_DIR / "notepad_draft.json"

# Ensure directories exist in %LOCALAPPDATA%
for d in [APP_DATA_DIR, DATA_DIR, CACHE_DIR, UPLOADS_DIR, MODELS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# One-time migration: If user has data in the legacy in-repo data/ directory,
# copy it over to their local %LOCALAPPDATA% profile so they don't lose anything.
legacy_data_dir = BASE_DIR / "data"
if legacy_data_dir.exists() and legacy_data_dir.resolve() != DATA_DIR.resolve():
    for filename in ["rules.json", "library.json", "settings.json", "notepad_draft.json"]:
        old_f = legacy_data_dir / filename
        new_f = DATA_DIR / filename
        if old_f.exists() and not new_f.exists():
            try:
                shutil.copy2(old_f, new_f)
            except Exception:
                pass
    old_uploads = legacy_data_dir / "uploads"
    if old_uploads.exists():
        for up_file in old_uploads.glob("*"):
            target_up = UPLOADS_DIR / up_file.name
            if up_file.is_file() and not target_up.exists():
                try:
                    shutil.copy2(up_file, target_up)
                except Exception:
                    pass

# Default TTS settings
DEFAULT_VOICE = "en-US-JennyNeural"
DEFAULT_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_VOLUME = "+0%"
