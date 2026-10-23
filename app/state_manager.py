import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import LIBRARY_FILE, SETTINGS_FILE, DEFAULT_VOICE, DEFAULT_RATE, DEFAULT_PITCH, DEFAULT_VOLUME

DEFAULT_SETTINGS = {
    "engine": "cloud",       # "cloud" (Edge-TTS) or "browser" (In-Browser SpeechSynthesis)
    "voice": DEFAULT_VOICE,
    "rate": DEFAULT_RATE,
    "pitch": DEFAULT_PITCH,
    "volume": DEFAULT_VOLUME,
    "theme": "dark",         # "dark", "light", "sepia", "amoled"
    "font_family": "sans",   # "sans", "serif", "dyslexic", "mono"
    "font_size": 18,         # 14 to 32 px
    "line_height": 1.6,      # 1.3 to 2.2
    "auto_scroll": True,
    "highlight_word": False, # Orange background highlight OFF by default!
    "highlight_sentence": True,
    "underline_word": True,  # Word underline ON by default!
    "sleep_timer_minutes": 0 # 0 means off
}


class StateManager:
    def __init__(self):
        self.settings: Dict[str, Any] = {}
        self.library: Dict[str, Any] = {"documents": {}, "last_active_doc_id": None}
        self.load_all()

    def load_all(self):
        # Load settings
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    self.settings = json.load(f)
            except Exception as e:
                print(f"[State] Error loading settings: {e}")
                self.settings = dict(DEFAULT_SETTINGS)
        else:
            self.settings = dict(DEFAULT_SETTINGS)
            self.save_settings()

        # Load library & reading positions
        if LIBRARY_FILE.exists():
            try:
                with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
                    self.library = json.load(f)
            except Exception as e:
                print(f"[State] Error loading library: {e}")
                self.library = {"documents": {}, "last_active_doc_id": None}
        else:
            self.library = {"documents": {}, "last_active_doc_id": None}
            self.save_library()

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"[State] Error saving settings: {e}")

    def save_library(self):
        try:
            with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.library, f, indent=2)
        except Exception as e:
            print(f"[State] Error saving library: {e}")

    def get_settings(self) -> Dict[str, Any]:
        return self.settings

    def update_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        self.settings.update(updates)
        self.save_settings()
        return self.settings

    def register_document(
        self,
        doc_id: str,
        filename: str,
        title: str,
        total_pages: int,
        file_path: str
    ) -> Dict[str, Any]:
        docs = self.library.setdefault("documents", {})
        if doc_id not in docs:
            docs[doc_id] = {
                "doc_id": doc_id,
                "filename": filename,
                "title": title or filename,
                "total_pages": total_pages,
                "file_path": str(file_path),
                "current_page": 0,
                "current_sentence": 0,
                "progress_percent": 0.0,
                "last_opened": time.time(),
                "bookmarks": []
            }
        else:
            docs[doc_id]["last_opened"] = time.time()
            docs[doc_id]["file_path"] = str(file_path)

        self.library["last_active_doc_id"] = doc_id
        self.save_library()
        return docs[doc_id]

    def update_progress(
        self,
        doc_id: str,
        current_page: int,
        current_sentence: int = 0
    ) -> Optional[Dict[str, Any]]:
        docs = self.library.get("documents", {})
        if doc_id in docs:
            doc = docs[doc_id]
            doc["current_page"] = current_page
            doc["current_sentence"] = current_sentence
            doc["last_opened"] = time.time()
            total_pages = doc.get("total_pages", 1)
            if total_pages > 0:
                doc["progress_percent"] = round(((current_page + 1) / total_pages) * 100, 1)
            self.library["last_active_doc_id"] = doc_id
            self.save_library()
            return doc
        return None

    def get_library(self) -> List[Dict[str, Any]]:
        docs = list(self.library.get("documents", {}).values())
        docs.sort(key=lambda d: d.get("last_opened", 0), reverse=True)
        return docs

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self.library.get("documents", {}).get(doc_id)

    def get_last_active_doc(self) -> Optional[Dict[str, Any]]:
        last_id = self.library.get("last_active_doc_id")
        if last_id and last_id in self.library.get("documents", {}):
            return self.library["documents"][last_id]
        return None

    def close_document(self, doc_id: Optional[str] = None, reset_progress: bool = False) -> None:
        target_id = doc_id or self.library.get("last_active_doc_id")
        if target_id and reset_progress:
            self.reset_progress(target_id)
        self.library["last_active_doc_id"] = None
        self.save_library()

    def reset_progress(self, doc_id: str) -> bool:
        docs = self.library.get("documents", {})
        if doc_id in docs:
            docs[doc_id]["current_page"] = 0
            docs[doc_id]["current_sentence"] = 0
            docs[doc_id]["progress_percent"] = 0.0
            self.save_library()
            return True
        return False

    def remove_document(self, doc_id: str) -> bool:
        docs = self.library.get("documents", {})
        if doc_id in docs:
            del docs[doc_id]
            if self.library.get("last_active_doc_id") == doc_id:
                self.library["last_active_doc_id"] = None
            self.save_library()
            return True
        return False

    def add_bookmark(self, doc_id: str, page: int, sentence_idx: int, note: str = "") -> Optional[Dict[str, Any]]:
        doc = self.get_document(doc_id)
        if not doc:
            return None
        bookmarks = doc.setdefault("bookmarks", [])
        bm = {
            "id": f"bm-{int(time.time()*1000)}",
            "page": page,
            "sentence_idx": sentence_idx,
            "note": note,
            "created_at": time.time()
        }
        bookmarks.append(bm)
        self.save_library()
        return bm

    def remove_bookmark(self, doc_id: str, bookmark_id: str) -> bool:
        doc = self.get_document(doc_id)
        if not doc:
            return False
        bookmarks = doc.get("bookmarks", [])
        initial_len = len(bookmarks)
        doc["bookmarks"] = [b for b in bookmarks if b.get("id") != bookmark_id]
        if len(doc["bookmarks"]) != initial_len:
            self.save_library()
            return True
        return False


state_manager = StateManager()
