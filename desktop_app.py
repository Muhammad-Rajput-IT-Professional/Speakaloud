"""
Speakaloud - Native Windows Desktop PDF Reader
Built with CustomTkinter, PyMuPDF, Edge Cloud Voices & Kokoro-82M Local Neural TTS.
High-precision pixel-perfect word alignment with zero browser overhead.
"""
import os
import sys
import re
import time
import json
import asyncio
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import pymupdf  # Fast C++ MuPDF engine
from PIL import Image, ImageTk, ImageChops, ImageDraw
import sounddevice as sd
import soundfile as sf

# Project roots and directories
ROOT_DIR = Path(__file__).resolve().parent

# Import centralized per-machine paths & core modules
from app.config import (
    APP_DATA_DIR, DATA_DIR, CACHE_DIR, UPLOADS_DIR, NOTEPAD_DRAFT_FILE, MODELS_DIR
)
from app.tts_engine import synthesize_text, get_all_voices
from app.local_tts import synthesize_kokoro, is_kokoro_downloaded, KOKORO_VOICES
from app.speech_rules import rule_engine
from app.state_manager import state_manager

# Migrate voices.json and clean up any legacy in-repo cache folder
legacy_cache = ROOT_DIR / "data" / "cache"
if legacy_cache.exists():
    legacy_voices = legacy_cache / "voices.json"
    target_voices = CACHE_DIR / "voices.json"
    if legacy_voices.exists() and not target_voices.exists():
        try:
            import shutil
            shutil.copy2(legacy_voices, target_voices)
        except Exception:
            pass
    for lf in legacy_cache.glob("*"):
        try:
            lf.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        legacy_cache.rmdir()
    except Exception:
        pass


# Global Voice Cache for Instant Zero-Latency Loading
GLOBAL_VOICES_CACHE: List[Dict[str, Any]] = []
VOICES_LOADING: bool = False

def _preload_voices_background():
    global GLOBAL_VOICES_CACHE, VOICES_LOADING
    if GLOBAL_VOICES_CACHE or VOICES_LOADING:
        return
    VOICES_LOADING = True
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        raw_edge = loop.run_until_complete(get_all_voices())
        loop.close()

        voices = []
        kokoro_installed = is_kokoro_downloaded()
        for kv in KOKORO_VOICES:
            voices.append({
                "engine": "kokoro",
                "voice_key": kv["voice_key"],
                "name": kv["name"],
                "gender": kv.get("gender", "Unknown"),
                "locale": "en-US" if kv.get("accent") == "American" else "en-GB",
                "desc": kv.get("sample_desc", "Kokoro Neural Voice"),
                "installed": kokoro_installed
            })

        for ev in raw_edge:
            voices.append({
                "engine": "cloud",
                "voice_key": ev.get("name", ""),
                "name": ev.get("friendly_name", ev.get("name", "")),
                "gender": ev.get("gender", "Unknown"),
                "locale": ev.get("locale", ""),
                "desc": ev.get("description", ev.get("locale", "")),
                "installed": True
            })
        GLOBAL_VOICES_CACHE = voices
    except Exception as e:
        print(f"[Preload] Voices error: {e}")
    finally:
        VOICES_LOADING = False

# Kick off background preload immediately
threading.Thread(target=_preload_voices_background, daemon=True).start()


def _resolve_voice_name(voice_key: str, engine: str = "cloud") -> str:
    """Returns a clean, human-friendly display name for a voice key."""
    if engine == "kokoro":
        for kv in KOKORO_VOICES:
            if kv.get("voice_key") == voice_key:
                clean_name = kv.get("name", "").replace("Kokoro - ", "")
                return f"{clean_name} ({kv.get('gender')})"
        return f"Kokoro ({voice_key})"

    # Check global cache or voices.json
    cache_file = CACHE_DIR / "voices.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                for item in raw:
                    if item.get("name") == voice_key:
                        friendly = item.get("friendly_name", voice_key)
                        clean = friendly.replace("Microsoft", "").split("Online")[0].strip()
                        gender = item.get("gender", "")
                        loc = item.get("locale", "")
                        short_loc = "US" if "US" in loc else ("UK" if "GB" in loc else loc)
                        if gender and short_loc:
                            return f"{clean} ({short_loc} {gender})"
                        return clean or voice_key
        except Exception:
            pass

    # Fallback to key parsing
    parts = voice_key.split("-")
    if len(parts) >= 3:
        name = parts[2].replace("Neural", "")
        loc = "US" if "US" in parts[1] else parts[1]
        return f"{name} ({loc})"
    return voice_key


class VoiceManagerDialog(ctk.CTkToplevel):
    """Rich Voice Picker with Search, Gender/Accent Filters, and Sample Audition."""
    def __init__(self, parent, on_select_voice):
        super().__init__(parent)
        self.title("Voice & Speech Manager - Speakaloud")
        self.geometry("740x630")
        self.minsize(580, 480)
        self.on_select_voice = on_select_voice
        self.parent = parent

        # Guarantee dialog opens in front and takes focus immediately with full maximize/minimize buttons
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False) if self.winfo_exists() else None)

        self.all_voices = list(GLOBAL_VOICES_CACHE)
        self.filtered_voices = []
        self._filter_timer = None

        self._build_ui()
        if not self.all_voices:
            self.count_label.configure(text="Loading voice catalog...")
            self.after(100, self._check_cache_ready)
        else:
            self._apply_filter()

    def _check_cache_ready(self):
        if GLOBAL_VOICES_CACHE:
            self.all_voices = list(GLOBAL_VOICES_CACHE)
            self._apply_filter()
        else:
            self.after(150, self._check_cache_ready)

    def _build_ui(self):
        # Header / Search Filter Bar
        header = ctk.CTkFrame(self, height=110, corner_radius=0)
        header.pack(fill="x", padx=14, pady=(12, 6))

        title = ctk.CTkLabel(header, text="Select Reading Voice", font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w", padx=10, pady=(6, 2))

        # Search box with debounce
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self._on_search_changed())
        search_box = ctk.CTkEntry(
            header, textvariable=self.search_var, placeholder_text="🔍 Search voice by name, country, or accent (e.g. English, Jenny, Guy, UK)...",
            height=34
        )
        search_box.pack(fill="x", padx=10, pady=4)

        # Filters Row
        filter_row = ctk.CTkFrame(header, fg_color="transparent")
        filter_row.pack(fill="x", padx=10, pady=(2, 6))

        # Engine filter
        self.filter_engine = ctk.CTkSegmentedButton(
            filter_row, values=["All Engines", "Edge Cloud", "Kokoro Local"],
            command=lambda v: self._apply_filter()
        )
        self.filter_engine.set("All Engines")
        self.filter_engine.pack(side="left", padx=(0, 8))

        # Gender filter
        self.filter_gender = ctk.CTkSegmentedButton(
            filter_row, values=["All", "Female", "Male"],
            command=lambda v: self._apply_filter()
        )
        self.filter_gender.set("All")
        self.filter_gender.pack(side="left", padx=8)

        # English Only checkbox
        self.english_only_var = tk.BooleanVar(value=True)
        chk_en = ctk.CTkCheckBox(
            filter_row, text="English Only", variable=self.english_only_var,
            command=self._apply_filter
        )
        chk_en.pack(side="right", padx=4)

        # Scrollable voice list
        self.voice_scroll = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.voice_scroll.pack(fill="both", expand=True, padx=14, pady=(4, 4))

        # Storage & Cache Management Card
        self.storage_card = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "#24242d"))
        self.storage_card.pack(fill="x", padx=14, pady=(2, 4))

        self.lbl_storage_info = ctk.CTkLabel(
            self.storage_card, text="Calculating storage...",
            font=("Segoe UI", 11), anchor="w"
        )
        self.lbl_storage_info.pack(side="left", padx=12, pady=6)

        self.btn_delete_models = ctk.CTkButton(
            self.storage_card, text="🗑️ Delete Local Models", width=155, height=26,
            font=("Segoe UI", 11), fg_color="#991b1b", hover_color="#7f1d1d",
            command=self._confirm_delete_models
        )
        self.btn_delete_models.pack(side="right", padx=(4, 10), pady=6)

        self.btn_clear_cache = ctk.CTkButton(
            self.storage_card, text="🧹 Clear Audio Cache", width=135, height=26,
            font=("Segoe UI", 11), fg_color="gray30", hover_color="gray40",
            command=self._clear_audio_cache
        )
        self.btn_clear_cache.pack(side="right", padx=4, pady=6)

        # Footer
        footer = ctk.CTkFrame(self, height=38, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(2, 8))

        self.count_label = ctk.CTkLabel(footer, text="Loading voices...", font=("Segoe UI", 11), text_color="gray60")
        self.count_label.pack(side="left", padx=6)

        btn_close = ctk.CTkButton(footer, text="Close", width=80, height=28, command=self.destroy)
        btn_close.pack(side="right", padx=6)

        # Initial storage refresh
        self.after(50, self._refresh_storage_ui)

    def _refresh_storage_ui(self):
        try:
            cache_mb, cache_count = self._calc_cache_stats()
            models_mb, kokoro_installed = self._calc_models_stats()

            models_str = f"Kokoro AI: {models_mb:.1f} MB" if kokoro_installed else "Kokoro AI: Not Installed (0 MB)"
            self.lbl_storage_info.configure(
                text=f"💾 Storage (%LocalAppData%): Cache: {cache_mb:.1f} MB ({cache_count} files)  |  {models_str}"
            )

            if not kokoro_installed or models_mb < 1.0:
                self.btn_delete_models.configure(
                    text="Local Models: None", state="disabled", fg_color="gray40"
                )
            else:
                self.btn_delete_models.configure(
                    text=f"🗑️ Delete Models ({models_mb:.0f} MB)", state="normal", fg_color="#991b1b"
                )

            if cache_count == 0:
                self.btn_clear_cache.configure(state="disabled")
            else:
                self.btn_clear_cache.configure(state="normal")
        except Exception as e:
            print(f"[Storage] Refresh error: {e}")

    def _calc_cache_stats(self) -> Tuple[float, int]:
        if not CACHE_DIR.exists():
            return 0.0, 0
        files = [f for f in CACHE_DIR.glob("*") if f.is_file() and f.name != "voices.json"]
        total_bytes = sum(f.stat().st_size for f in files)
        return total_bytes / (1024 * 1024), len(files)

    def _calc_models_stats(self) -> Tuple[float, bool]:
        installed = is_kokoro_downloaded()
        models_dir = DATA_DIR / "models"
        if not models_dir.exists():
            return 0.0, False
        files = [f for f in models_dir.rglob("*") if f.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        return total_bytes / (1024 * 1024), installed

    def _clear_audio_cache(self):
        count = 0
        freed_bytes = 0
        if CACHE_DIR.exists():
            for f in CACHE_DIR.glob("*"):
                if f.is_file() and f.name != "voices.json":
                    try:
                        freed_bytes += f.stat().st_size
                        f.unlink()
                        count += 1
                    except Exception:
                        pass
        freed_mb = freed_bytes / (1024 * 1024)
        messagebox.showinfo(
            "Cache Cleared",
            f"Cleared {count} cached audio files ({freed_mb:.1f} MB freed).\n\n"
            "Your PDF reading progress, bookmarks, and speech rules are completely preserved."
        )
        self._refresh_storage_ui()

    def _confirm_delete_models(self):
        models_mb, installed = self._calc_models_stats()
        if not installed and models_mb < 1.0:
            return
        ans = messagebox.askyesno(
            "Delete Local Models",
            f"Are you sure you want to delete local neural models ({models_mb:.1f} MB)?\n\n"
            "• Microsoft Edge Cloud Voices will continue to work normally with zero disk space.\n"
            "• Local Kokoro voices can be re-downloaded anytime on demand if needed."
        )
        if not ans:
            return

        models_dir = DATA_DIR / "models"
        freed = 0
        if models_dir.exists():
            for f in models_dir.rglob("*"):
                if f.is_file():
                    try:
                        freed += f.stat().st_size
                        f.unlink()
                    except Exception:
                        pass

        import app.local_tts as local_tts
        local_tts._KOKORO_INSTANCE = None

        for v in self.all_voices:
            if v.get("engine") == "kokoro":
                v["installed"] = False

        messagebox.showinfo(
            "Models Removed",
            f"Deleted local models ({freed / (1024 * 1024):.1f} MB freed)!\n\n"
            "Edge Cloud Voices remain active."
        )
        self._apply_filter()
        self._refresh_storage_ui()

    def _on_search_changed(self):
        if self._filter_timer:
            self.after_cancel(self._filter_timer)
        self._filter_timer = self.after(100, self._apply_filter)

    def _apply_filter(self):
        query = (self.search_var.get() or "").strip().lower()
        engine_filter = self.filter_engine.get()
        gender_filter = self.filter_gender.get()
        english_only = self.english_only_var.get()

        filtered = []
        for v in self.all_voices:
            if engine_filter == "Edge Cloud" and v["engine"] != "cloud":
                continue
            if engine_filter == "Kokoro Local" and v["engine"] != "kokoro":
                continue

            if gender_filter != "All" and v["gender"].lower() != gender_filter.lower():
                continue

            if english_only and not v["locale"].lower().startswith("en-"):
                continue

            if query:
                haystack = f"{v['name']} {v['voice_key']} {v['desc']} {v['locale']}".lower()
                if query not in haystack:
                    continue

            filtered.append(v)

        self.filtered_voices = filtered
        show_count = min(len(filtered), 45)
        self.count_label.configure(text=f"Showing {show_count} of {len(filtered)} voices")
        self._render_voice_cards()

    def _render_voice_cards(self):
        for widget in self.voice_scroll.winfo_children():
            widget.destroy()

        if not self.filtered_voices:
            lbl = ctk.CTkLabel(self.voice_scroll, text="No voices match the filter criteria.", font=("Segoe UI", 12), text_color="gray60")
            lbl.pack(pady=30)
            return

        # Render top 45 matching cards for instant 60fps responsiveness
        for v in self.filtered_voices[:45]:
            card = ctk.CTkFrame(self.voice_scroll, corner_radius=6)
            card.pack(fill="x", pady=3, padx=2)

            info_f = ctk.CTkFrame(card, fg_color="transparent")
            info_f.pack(side="left", fill="x", expand=True, padx=8, pady=6)

            tag = "[Kokoro ONNX]" if v["engine"] == "kokoro" else "[Edge Cloud]"
            color = "#f59e0b" if v["engine"] == "kokoro" else "#3b82f6"

            name_lbl = ctk.CTkLabel(
                info_f, text=f"{v['name']} ", font=("Segoe UI", 12, "bold"), anchor="w"
            )
            name_lbl.pack(anchor="w")

            tag_lbl = ctk.CTkLabel(
                info_f, text=f"{tag} · {v['gender']} · {v['desc']}",
                font=("Segoe UI", 10), text_color=color, anchor="w"
            )
            tag_lbl.pack(anchor="w")

            btn_f = ctk.CTkFrame(card, fg_color="transparent")
            btn_f.pack(side="right", padx=6, pady=6)

            btn_sample = ctk.CTkButton(
                btn_f, text="▶ Sample", width=68, height=28, font=("Segoe UI", 11),
                fg_color="gray40", hover_color="gray50",
                command=lambda item=v: self._play_sample(item)
            )
            btn_sample.pack(side="left", padx=4)

            btn_select = ctk.CTkButton(
                btn_f, text="Select", width=62, height=28, font=("Segoe UI", 11, "bold"),
                command=lambda item=v: self._select_voice(item)
            )
            btn_select.pack(side="left", padx=4)

    def _play_sample(self, voice_item):
        """Auditions a voice sample."""
        try:
            self.parent._stop_audio()
        except Exception:
            pass

        def worker():
            try:
                clean_name = voice_item['name'].replace("Kokoro - ", "").split()[0]
                text = f"Hello! This is {clean_name} reading with Speakaloud."
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                if voice_item["engine"] == "kokoro":
                    res = loop.run_until_complete(synthesize_kokoro(text, voice_key=voice_item["voice_key"], speed=1.0))
                else:
                    res = loop.run_until_complete(synthesize_text(text, voice=voice_item["voice_key"], rate="+0%"))
                loop.close()

                if res and res.get("cache_id"):
                    mp3_p = CACHE_DIR / f"{res['cache_id']}.mp3"
                    wav_p = CACHE_DIR / f"{res['cache_id']}.wav"
                    aud_p = mp3_p if mp3_p.exists() else wav_p
                    if aud_p.exists():
                        data, sr = sf.read(str(aud_p), dtype="float32")
                        sd.stop()
                        sd.play(data, sr)
            except Exception as e:
                print(f"[VoiceDialog] Sample audition error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _select_voice(self, voice_item):
        self.on_select_voice(voice_item)
        self.destroy()


class RuleEditorDialog(ctk.CTkToplevel):
    """Dialog to create or edit a pronunciation / speech rule."""
    def __init__(self, parent, rule: Optional[Dict[str, Any]] = None, on_save = None):
        super().__init__(parent)
        self.rule = rule or {}
        self.on_save = on_save
        self.is_edit = bool(rule)

        self.title("Edit Rule - Speakaloud" if self.is_edit else "Add New Speech Rule - Speakaloud")
        self.geometry("520x470")
        self.minsize(460, 420)
        self.resizable(False, False)

        self.transient(parent)
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False) if self.winfo_exists() else None)

        self._build_ui()

    def _build_ui(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=24, pady=20)

        title_text = "✏️ Edit Pronunciation Rule" if self.is_edit else "➕ Add Pronunciation Rule"
        lbl_title = ctk.CTkLabel(f, text=title_text, font=("Segoe UI", 16, "bold"))
        lbl_title.pack(anchor="w", pady=(0, 14))

        # Word / Pattern to match
        ctk.CTkLabel(f, text="Word or Pattern to match:", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(4, 2))
        self.pattern_var = tk.StringVar(value=self.rule.get("pattern", ""))
        self.ent_pattern = ctk.CTkEntry(f, textvariable=self.pattern_var, placeholder_text="e.g. AMA, PBUH, Dr., etc.", height=34)
        self.ent_pattern.pack(fill="x", pady=(0, 10))

        # Replacement / Pronounce As
        ctk.CTkLabel(f, text="Pronounce as / Spoken replacement:", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(4, 2))
        self.replacement_var = tk.StringVar(value=self.rule.get("replacement", ""))
        self.ent_replacement = ctk.CTkEntry(f, textvariable=self.replacement_var, placeholder_text="e.g. Azure Monitor Agent, peace be upon him", height=34)
        self.ent_replacement.pack(fill="x", pady=(0, 10))

        # Rule Label / Name
        ctk.CTkLabel(f, text="Rule Description / Label (Optional):", font=("Segoe UI", 11)).pack(anchor="w", pady=(2, 2))
        self.label_var = tk.StringVar(value=self.rule.get("label", ""))
        self.ent_label = ctk.CTkEntry(f, textvariable=self.label_var, placeholder_text="e.g. AMA abbreviation expansion", height=30)
        self.ent_label.pack(fill="x", pady=(0, 12))

        # Match Type (Whole Word vs Contains vs Regex)
        ctk.CTkLabel(f, text="Matching Behavior:", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(2, 2))
        init_type = "Whole Word"
        if self.rule.get("type") == "regex":
            init_type = "Regular Expression"
        elif not self.rule.get("match_whole_word", True):
            init_type = "Contains Anywhere"

        self.type_seg = ctk.CTkSegmentedButton(f, values=["Whole Word", "Contains Anywhere", "Regular Expression"])
        self.type_seg.set(init_type)
        self.type_seg.pack(fill="x", pady=(0, 12))

        # Options: Case sensitive & Enabled
        opt_frame = ctk.CTkFrame(f, fg_color="transparent")
        opt_frame.pack(fill="x", pady=(0, 16))

        self.case_var = tk.BooleanVar(value=self.rule.get("case_sensitive", False))
        chk_case = ctk.CTkCheckBox(opt_frame, text="Case sensitive", variable=self.case_var)
        chk_case.pack(side="left", padx=(0, 16))

        self.enabled_var = tk.BooleanVar(value=self.rule.get("enabled", True))
        chk_en = ctk.CTkCheckBox(opt_frame, text="Rule active / enabled", variable=self.enabled_var)
        chk_en.pack(side="left")

        # Buttons
        btn_frame = ctk.CTkFrame(f, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom")

        btn_cancel = ctk.CTkButton(btn_frame, text="Cancel", width=90, fg_color="gray30", hover_color="gray40", command=self.destroy)
        btn_cancel.pack(side="right", padx=6)

        btn_save = ctk.CTkButton(btn_frame, text="Save Rule", width=110, fg_color="#10b981", hover_color="#059669", font=("Segoe UI", 12, "bold"), command=self._save_rule)
        btn_save.pack(side="right", padx=6)

    def _save_rule(self):
        pat = self.pattern_var.get().strip()
        rep = self.replacement_var.get()
        if not pat:
            messagebox.showwarning("Validation Error", "Please provide a word or pattern to match.", parent=self)
            return

        match_choice = self.type_seg.get()
        rule_type = "regex" if match_choice == "Regular Expression" else "dictionary"
        match_whole = match_choice == "Whole Word"

        if rule_type == "regex":
            try:
                re.compile(pat)
            except re.error as e:
                messagebox.showerror("Invalid Regex", f"Regular expression syntax error:\n{e}", parent=self)
                return

        label = self.label_var.get().strip()
        if not label:
            label = f"{pat} -> {rep}" if rep else f"Remove '{pat}'"

        updated = {
            "id": self.rule.get("id") or f"rule-{re.sub(r'[^a-zA-Z0-9]', '', pat).lower()[:12]}-{int(time.time())}",
            "label": label,
            "pattern": pat,
            "replacement": rep,
            "type": rule_type,
            "match_whole_word": match_whole,
            "case_sensitive": self.case_var.get(),
            "enabled": self.enabled_var.get()
        }

        if self.on_save:
            self.on_save(updated, is_new=not self.is_edit)
        self.destroy()


class SpeechRulesDialog(ctk.CTkToplevel):
    """Pronunciation & Speech Rules Manager with Live Testing, Editing & Audio Audition."""
    def __init__(self, parent, on_rules_changed=None):
        super().__init__(parent)
        self.title("Speech & Pronunciation Rules - Speakaloud")
        self.geometry("780x640")
        self.minsize(640, 500)
        self.parent = parent
        self.on_rules_changed = on_rules_changed
        self.editor_dialog = None

        # Ensure dialog opens in front with full maximize/minimize buttons
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False) if self.winfo_exists() else None)

        self._build_ui()
        self._refresh_rules()

    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, height=70, corner_radius=0)
        header.pack(fill="x", padx=14, pady=(12, 6))

        h_left = ctk.CTkFrame(header, fg_color="transparent")
        h_left.pack(side="left", padx=10, pady=8)

        ctk.CTkLabel(h_left, text="🗣️ Speech & Pronunciation Rules", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ctk.CTkLabel(h_left, text="Customize how Speakaloud reads acronyms, abbreviations, and symbols.", font=("Segoe UI", 11), text_color="gray60").pack(anchor="w")

        h_right = ctk.CTkFrame(header, fg_color="transparent")
        h_right.pack(side="right", padx=10, pady=8)

        btn_add = ctk.CTkButton(
            h_right, text="＋ Add Rule", width=105, height=32,
            font=("Segoe UI", 12, "bold"), fg_color=("#10b981", "#059669"), hover_color=("#059669", "#047857"),
            command=self._open_add_rule
        )
        btn_add.pack(side="left", padx=4)

        btn_reset = ctk.CTkButton(
            h_right, text="↺ Defaults", width=80, height=32,
            fg_color="gray30", hover_color="gray40",
            command=self._reset_defaults
        )
        btn_reset.pack(side="left", padx=4)

        # Interactive Live Rule Tester Box
        test_box = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "#27272a"))
        test_box.pack(fill="x", padx=14, pady=4)

        t_header = ctk.CTkFrame(test_box, fg_color="transparent")
        t_header.pack(fill="x", padx=10, pady=(6, 2))
        ctk.CTkLabel(t_header, text="🧪 Live Pronunciation Tester", font=("Segoe UI", 11, "bold"), text_color=("#2563eb", "#60a5fa")).pack(side="left")

        self.test_var = tk.StringVar(value="The AMA agent was configured e.g. for Dr. Smith.")
        self.test_var.trace_add("write", lambda *args: self._update_test_output())

        t_row = ctk.CTkFrame(test_box, fg_color="transparent")
        t_row.pack(fill="x", padx=10, pady=(2, 6))

        self.ent_test = ctk.CTkEntry(t_row, textvariable=self.test_var, placeholder_text="Type sample text to test active rules...", height=32)
        self.ent_test.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_speak = ctk.CTkButton(t_row, text="▶ Speak Test", width=95, height=32, fg_color="#3b82f6", hover_color="#2563eb", command=self._speak_test)
        btn_speak.pack(side="right")

        self.lbl_test_result = ctk.CTkLabel(test_box, text="", font=("Segoe UI", 11), text_color=("#10b981", "#34d399"), anchor="w")
        self.lbl_test_result.pack(fill="x", padx=12, pady=(0, 6))

        # Search Bar
        search_f = ctk.CTkFrame(self, fg_color="transparent")
        search_f.pack(fill="x", padx=14, pady=(6, 4))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self._filter_rules())
        search_box = ctk.CTkEntry(search_f, textvariable=self.search_var, placeholder_text="🔍 Filter rules by pattern, word, or replacement...", height=32)
        search_box.pack(fill="x")

        # Scrollable list of rules
        self.rules_scroll = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.rules_scroll.pack(fill="both", expand=True, padx=14, pady=6)

        # Footer
        footer = ctk.CTkFrame(self, height=40, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=6)

        self.lbl_count = ctk.CTkLabel(footer, text="", font=("Segoe UI", 11), text_color="gray60")
        self.lbl_count.pack(side="left", padx=6)

        btn_close = ctk.CTkButton(footer, text="Close", width=80, command=self.destroy)
        btn_close.pack(side="right", padx=6)

        self._update_test_output()

    def _update_test_output(self):
        text = self.test_var.get().strip()
        if not text:
            self.lbl_test_result.configure(text="")
            return
        transformed, applied = rule_engine.apply_rules(text)
        if applied:
            self.lbl_test_result.configure(text=f"🗣️ Output: \"{transformed}\" ({len(applied)} rule(s) applied)")
        else:
            self.lbl_test_result.configure(text=f"🗣️ Output: \"{transformed}\" (No rules matched)")

    def _speak_test(self):
        text = self.test_var.get().strip()
        if not text:
            return
        try:
            self.parent._stop_audio()
        except Exception:
            pass
        transformed, _ = rule_engine.apply_rules(text)
        def worker():
            try:
                engine = self.parent.active_engine
                voice_key = self.parent.active_voice_key
                speed = round(float(self.parent.speed_slider.get()), 2)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                if engine == "kokoro":
                    res = loop.run_until_complete(synthesize_kokoro(transformed, voice_key=voice_key, speed=speed))
                else:
                    pct = int((speed - 1.0) * 100)
                    rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
                    res = loop.run_until_complete(synthesize_text(transformed, voice=voice_key, rate=rate_str))
                loop.close()
                if res and res.get("cache_id"):
                    mp3_p = CACHE_DIR / f"{res['cache_id']}.mp3"
                    wav_p = CACHE_DIR / f"{res['cache_id']}.wav"
                    aud_p = mp3_p if mp3_p.exists() else wav_p
                    if aud_p.exists():
                        data, sr = sf.read(str(aud_p), dtype="float32")
                        sd.stop()
                        sd.play(data, sr)
            except Exception as e:
                print(f"[RulesDialog] Speak test error: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_rules(self):
        self.all_rules = rule_engine.get_rules()
        self._filter_rules()

    def _filter_rules(self):
        q = (self.search_var.get() or "").strip().lower()
        if not q:
            self.filtered_rules = list(self.all_rules)
        else:
            self.filtered_rules = [
                r for r in self.all_rules
                if q in r.get("pattern", "").lower()
                or q in r.get("replacement", "").lower()
                or q in r.get("label", "").lower()
            ]
        self.lbl_count.configure(text=f"Showing {len(self.filtered_rules)} of {len(self.all_rules)} rules")
        self._render_rule_cards()

    def _render_rule_cards(self):
        for w in self.rules_scroll.winfo_children():
            w.destroy()

        if not self.filtered_rules:
            lbl = ctk.CTkLabel(self.rules_scroll, text="No pronunciation rules found.", font=("Segoe UI", 12), text_color="gray60")
            lbl.pack(pady=30)
            return

        for r in self.filtered_rules:
            card = ctk.CTkFrame(self.rules_scroll, corner_radius=6)
            card.pack(fill="x", pady=3, padx=2)

            info_f = ctk.CTkFrame(card, fg_color="transparent")
            info_f.pack(side="left", fill="x", expand=True, padx=8, pady=6)

            title_f = ctk.CTkFrame(info_f, fg_color="transparent")
            title_f.pack(fill="x")

            lbl_pat = ctk.CTkLabel(title_f, text=r.get("pattern", ""), font=("Segoe UI", 12, "bold"), text_color=("#2563eb", "#60a5fa"))
            lbl_pat.pack(side="left")

            lbl_arrow = ctk.CTkLabel(title_f, text="  ➔  ", font=("Segoe UI", 11), text_color="gray50")
            lbl_arrow.pack(side="left")

            rep_text = f'"{r.get("replacement", "")}"' if r.get("replacement") != "" else "(Silent / Removed)"
            lbl_rep = ctk.CTkLabel(title_f, text=rep_text, font=("Segoe UI", 12, "bold"), text_color=("#10b981", "#34d399"))
            lbl_rep.pack(side="left")

            b_texts = []
            if r.get("type") == "regex":
                b_texts.append("Regex")
            elif r.get("match_whole_word", True):
                b_texts.append("Whole Word")
            else:
                b_texts.append("Contains")

            if r.get("case_sensitive"):
                b_texts.append("Case Sensitive")

            badge_str = " · ".join(b_texts)
            lbl_sub = ctk.CTkLabel(info_f, text=f"{badge_str} · {r.get('label', '')}", font=("Segoe UI", 10), text_color="gray60", anchor="w")
            lbl_sub.pack(anchor="w")

            actions_f = ctk.CTkFrame(card, fg_color="transparent")
            actions_f.pack(side="right", padx=8, pady=6)

            sw = ctk.CTkSwitch(actions_f, text="Active", width=46)
            if r.get("enabled", True):
                sw.select()
            else:
                sw.deselect()
            sw.configure(command=lambda rule=r, switch=sw: self._toggle_rule(rule, bool(switch.get())))
            sw.pack(side="left", padx=8)

            btn_edit = ctk.CTkButton(
                actions_f, text="✏️", width=34, height=28, fg_color="gray30", hover_color="gray40",
                command=lambda rule=r: self._open_edit_rule(rule)
            )
            btn_edit.pack(side="left", padx=3)

            btn_del = ctk.CTkButton(
                actions_f, text="🗑️", width=34, height=28, fg_color="#ef4444", hover_color="#dc2626",
                command=lambda rule=r: self._delete_rule(rule)
            )
            btn_del.pack(side="left", padx=3)

    def _open_add_rule(self):
        if self.editor_dialog and self.editor_dialog.winfo_exists():
            self.editor_dialog.lift()
            return
        self.editor_dialog = RuleEditorDialog(self, on_save=self._on_rule_saved)

    def _open_edit_rule(self, rule):
        if self.editor_dialog and self.editor_dialog.winfo_exists():
            self.editor_dialog.lift()
            return
        self.editor_dialog = RuleEditorDialog(self, rule=rule, on_save=self._on_rule_saved)

    def _on_rule_saved(self, rule_data, is_new=False):
        if is_new:
            rule_engine.add_rule(rule_data)
        else:
            rule_engine.update_rule(rule_data["id"], rule_data)
        self._refresh_rules()
        self._update_test_output()
        if self.on_rules_changed:
            self.on_rules_changed()

    def _toggle_rule(self, rule, enabled_val):
        rule_engine.update_rule(rule["id"], {"enabled": enabled_val})
        self._update_test_output()
        if self.on_rules_changed:
            self.on_rules_changed()

    def _delete_rule(self, rule):
        if messagebox.askyesno("Delete Rule", f"Are you sure you want to delete rule '{rule.get('label')}'?", parent=self):
            rule_engine.delete_rule(rule["id"])
            self._refresh_rules()
            self._update_test_output()
            if self.on_rules_changed:
                self.on_rules_changed()

    def _reset_defaults(self):
        if messagebox.askyesno("Reset Rules", "Reset all pronunciation rules to application defaults?", parent=self):
            rule_engine.reset_to_defaults()
            self._refresh_rules()
            self._update_test_output()
            if self.on_rules_changed:
                self.on_rules_changed()


class SpeakaloudDesktopApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window setup
        self.title("Speakaloud - Desktop PDF Reader")
        self.geometry("1300x860")
        self.minsize(960, 640)

        # Theme
        self.settings = state_manager.get_settings()
        self.theme = self.settings.get("theme", "dark")
        ctk.set_appearance_mode("Dark" if self.theme == "dark" else "Light")
        ctk.set_default_color_theme("blue")

        # PDF & Reading State
        self.doc: Optional[pymupdf.Document] = None
        self.current_doc_id: Optional[str] = None
        self.current_page_idx: int = 0
        self.total_pages: int = 1
        self.page_words: List[Tuple[float, float, float, float, str, int, int, int]] = []
        self.sentences: List[Dict[str, Any]] = []
        self.zoom_scale: float = 1.35
        self.zoom_mode: str = "fit-width"

        # Active Engine & Voice
        self.active_engine: str = self.settings.get("engine", "cloud")  # 'cloud' | 'kokoro'
        self.active_voice_key: str = self.settings.get("voice", "en-US-JennyNeural")
        self.active_voice_name: str = _resolve_voice_name(self.active_voice_key, self.active_engine)

        # Playback State & Thread Safety
        self.is_playing: bool = False
        self.current_sentence_idx: int = 0
        self.current_word_idx: int = 0
        self.audio_data: Optional[Any] = None
        self.audio_sr: int = 24000
        self.start_playback_time: float = 0.0
        self.seek_offset_ms: float = 0.0
        self.total_sentence_dur_ms: float = 0.0
        self.word_boundaries: List[Dict[str, Any]] = []
        self.playback_seq: int = 0
        self.prefetch_cache: Dict[str, Any] = {}
        self.is_synthesizing: bool = False
        self._speed_timer = None
        self._is_closing = False
        self.voice_dialog = None
        self.rules_dialog = None

        self.page_turns_since_cleanup: int = 0

        # Visual elements
        self.current_photo: Optional[ImageTk.PhotoImage] = None
        self.base_page_img: Optional[Image.Image] = None
        self.current_highlighted_word_idx: Optional[int] = None
        self.sentence_word_timings: List[Tuple[float, float]] = []
        self.last_scrolled_sentence_idx: Optional[int] = None

        # Notepad / App Mode
        self.app_mode: str = "pdf"
        self.notepad_sentences: List[Dict[str, Any]] = []
        self.pdf_sentences: List[Dict[str, Any]] = []
        self._notepad_save_timer = None
        self._notepad_reparse_timer = None
        self._last_notepad_s_idx: Optional[int] = None

        # Build UI layout
        self._build_ui()
        self._bind_shortcuts()

        # Load notepad draft
        self._load_notepad_draft()

        # Load initial document
        self.after(100, self._load_initial_document)

        # Polling loop for audio synchronization (every 30ms)
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.after(30, self._audio_sync_loop)

    def _on_app_close(self):
        self._is_closing = True
        try:
            self._save_notepad_draft()
        except Exception:
            pass
        try:
            sd.stop()
        except Exception:
            pass
        self.destroy()

    def _build_ui(self):
        """Constructs the desktop interface."""
        # Top Header Bar
        self.header_frame = ctk.CTkFrame(self, height=52, corner_radius=0)
        self.header_frame.pack(side="top", fill="x")

        # Sidebar toggle
        self.btn_toggle_sidebar = ctk.CTkButton(
            self.header_frame, text="◀ Library", width=84, height=32,
            font=("Segoe UI", 12, "bold"), command=self._toggle_sidebar
        )
        self.btn_toggle_sidebar.pack(side="left", padx=(10, 6), pady=8)

        # Brand Title
        self.brand_label = ctk.CTkLabel(
            self.header_frame, text="Speakaloud", font=("Segoe UI", 16, "bold"), text_color=("#2563eb", "#60a5fa")
        )
        self.brand_label.pack(side="left", padx=4, pady=8)

        # Mode Selector: PDF Reader vs Notepad
        self.mode_selector = ctk.CTkSegmentedButton(
            self.header_frame,
            values=["📖 PDF Reader", "📝 Notepad"],
            command=self._on_mode_change,
            font=("Segoe UI", 12, "bold"),
            width=210, height=32,
            selected_color=("#2563eb", "#1d4ed8"),
            selected_hover_color=("#1d4ed8", "#1e40af")
        )
        self.mode_selector.set("📖 PDF Reader")
        self.mode_selector.pack(side="left", padx=(10, 8), pady=8)

        # PDF Header Controls (grouped for switching)
        self.pdf_header_frame = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.pdf_header_frame.pack(side="left", fill="x", padx=4)

        # Open File Button
        self.btn_open_file = ctk.CTkButton(
            self.pdf_header_frame, text="📂 Open PDF", width=95, height=32,
            command=self._choose_pdf_file
        )
        self.btn_open_file.pack(side="left", padx=6, pady=8)

        # Close File Button
        self.btn_close_file = ctk.CTkButton(
            self.pdf_header_frame, text="✕ Close", width=68, height=32,
            fg_color="gray30", hover_color="gray40",
            command=self._close_current_pdf
        )
        self.btn_close_file.pack(side="left", padx=4, pady=8)

        # Active Document Label
        self.doc_title_label = ctk.CTkLabel(
            self.pdf_header_frame, text="No document open", font=("Segoe UI", 13, "bold"),
            text_color=("gray20", "gray85"), width=160, anchor="w"
        )
        self.doc_title_label.pack(side="left", padx=6, pady=8)

        # Page Navigation in Header
        self.nav_frame = ctk.CTkFrame(self.pdf_header_frame, fg_color="transparent")
        self.nav_frame.pack(side="left", padx=6)

        self.btn_prev_page = ctk.CTkButton(self.nav_frame, text="◀", width=30, height=30, command=self._prev_page)
        self.btn_prev_page.pack(side="left", padx=2)

        self.page_entry = ctk.CTkEntry(self.nav_frame, width=44, height=30, justify="center")
        self.page_entry.insert(0, "1")
        self.page_entry.bind("<Return>", self._jump_page_event)
        self.page_entry.pack(side="left", padx=2)

        self.total_pages_label = ctk.CTkLabel(self.nav_frame, text="/ 1", font=("Segoe UI", 12))
        self.total_pages_label.pack(side="left", padx=2)

        self.btn_next_page = ctk.CTkButton(self.nav_frame, text="▶", width=30, height=30, command=self._next_page)
        self.btn_next_page.pack(side="left", padx=2)

        # Zoom Controls
        self.zoom_frame = ctk.CTkFrame(self.pdf_header_frame, fg_color="transparent")
        self.zoom_frame.pack(side="left", padx=6)

        self.btn_zoom_out = ctk.CTkButton(self.zoom_frame, text="－", width=28, height=30, command=self._zoom_out)
        self.btn_zoom_out.pack(side="left", padx=2)

        self.btn_zoom_fit = ctk.CTkButton(self.zoom_frame, text="Fit Width", width=68, height=30, command=self._zoom_fit_width)
        self.btn_zoom_fit.pack(side="left", padx=2)

        self.btn_zoom_in = ctk.CTkButton(self.zoom_frame, text="＋", width=28, height=30, command=self._zoom_in)
        self.btn_zoom_in.pack(side="left", padx=2)

        # Notepad Header Controls (shown in notepad mode)
        self.notepad_header_frame = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.notepad_title_label = ctk.CTkLabel(
            self.notepad_header_frame, text="📝 Rich Text & Email Drafter",
            font=("Segoe UI", 13, "bold"), text_color=("gray20", "gray85")
        )
        self.notepad_title_label.pack(side="left", padx=6)

        self.notepad_autosave_label = ctk.CTkLabel(
            self.notepad_header_frame, text="• Draft saved",
            font=("Segoe UI", 11), text_color=("#059669", "#10b981")
        )
        self.notepad_autosave_label.pack(side="left", padx=8)

        # Right-side Header Settings
        self.header_right = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.header_right.pack(side="right", padx=10)

        # Speech Rules Button (Opens Rule Manager)
        self.btn_open_rules = ctk.CTkButton(
            self.header_right, text="🗣️ Speech Rules", width=125, height=32,
            fg_color=("#059669", "#047857"), hover_color=("#047857", "#065f46"),
            command=self._open_rules_manager
        )
        self.btn_open_rules.pack(side="left", padx=4)

        # Voice Selector Button (Opens Rich Voice Manager)
        self.btn_open_voices = ctk.CTkButton(
            self.header_right, text="🎙️ Voices & Audio", width=145, height=32,
            fg_color=("#2563eb", "#1d4ed8"), hover_color=("#1d4ed8", "#1e40af"),
            command=self._open_voice_manager
        )
        self.btn_open_voices.pack(side="left", padx=4)

        # Active Voice Badge Label
        self.active_voice_lbl = ctk.CTkLabel(
            self.header_right, text=f"Voice: {self.active_voice_name}", font=("Segoe UI", 11),
            text_color=("gray30", "gray70"), width=150, anchor="w"
        )
        self.active_voice_lbl.pack(side="left", padx=4)

        # Theme toggle
        self.btn_theme = ctk.CTkButton(
            self.header_right, text="☀️" if self.theme == "dark" else "🌙", width=32, height=30,
            command=self._toggle_theme
        )
        self.btn_theme.pack(side="left", padx=4)

        # Main Workspace Container (Sidebar + Center Canvas)
        self.main_container = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray92", "#18181b"))
        self.main_container.pack(side="top", fill="both", expand=True)

        # Left Collapsible Sidebar
        self.sidebar_frame = ctk.CTkFrame(self.main_container, width=300, corner_radius=0)
        self.sidebar_frame.pack(side="left", fill="y", padx=0, pady=0)
        self.sidebar_visible = True

        # Sidebar Header with direct Collapse Button
        self.sidebar_header = ctk.CTkFrame(self.sidebar_frame, height=36, fg_color="transparent")
        self.sidebar_header.pack(fill="x", padx=8, pady=(8, 2))

        lbl_side = ctk.CTkLabel(self.sidebar_header, text="📚 Library & Outline", font=("Segoe UI", 12, "bold"))
        lbl_side.pack(side="left", padx=4)

        btn_collapse = ctk.CTkButton(
            self.sidebar_header, text="◀ Hide", width=58, height=26, font=("Segoe UI", 11),
            fg_color="gray30", hover_color="gray40", command=self._toggle_sidebar
        )
        btn_collapse.pack(side="right", padx=2)

        self.sidebar_tabview = ctk.CTkTabview(self.sidebar_frame, width=290)
        self.sidebar_tabview.pack(fill="both", expand=True, padx=5, pady=(5, 2))
        self.tab_library = self.sidebar_tabview.add("📚 Library")
        self.tab_toc = self.sidebar_tabview.add("📑 Bookmarks")

        self.library_scroll = ctk.CTkScrollableFrame(self.tab_library, width=270)
        self.library_scroll.pack(fill="both", expand=True)

        self.toc_scroll = ctk.CTkScrollableFrame(self.tab_toc, width=270)
        self.toc_scroll.pack(fill="both", expand=True)

        # Quick Cache Cleaner at bottom of sidebar
        self.sidebar_footer = ctk.CTkFrame(self.sidebar_frame, height=36, fg_color="transparent")
        self.sidebar_footer.pack(fill="x", padx=8, pady=(2, 6))

        self.btn_quick_clear_cache = ctk.CTkButton(
            self.sidebar_footer, text="🧹 Clear Audio Cache", height=28,
            font=("Segoe UI", 11), fg_color="gray30", hover_color="gray40",
            command=self._quick_clear_audio_cache
        )
        self.btn_quick_clear_cache.pack(fill="x", padx=2)

        # Center PDF Viewport with Scrollbars
        self.pdf_center_frame = ctk.CTkFrame(self.main_container, corner_radius=0, fg_color=("gray85", "#1e1e24"))
        self.center_frame = self.pdf_center_frame
        self.pdf_center_frame.pack(side="left", fill="both", expand=True)

        self.canvas_scroll_y = tk.Scrollbar(self.pdf_center_frame, orient="vertical")
        self.canvas_scroll_y.pack(side="right", fill="y")

        self.canvas_scroll_x = tk.Scrollbar(self.pdf_center_frame, orient="horizontal")
        self.canvas_scroll_x.pack(side="bottom", fill="x")

        self.pdf_canvas = tk.Canvas(
            self.pdf_center_frame, bg="#2b2e38" if self.theme == "dark" else "#e2e8f0",
            highlightthickness=0,
            xscrollcommand=self.canvas_scroll_x.set,
            yscrollcommand=self.canvas_scroll_y.set
        )
        self.pdf_canvas.pack(side="left", fill="both", expand=True)

        self.canvas_scroll_y.config(command=self.pdf_canvas.yview)
        self.canvas_scroll_x.config(command=self.pdf_canvas.xview)

        # Mouse Events on Canvas
        self.pdf_canvas.bind("<Motion>", self._on_canvas_mouse_move)
        self.pdf_canvas.bind("<Button-1>", self._on_canvas_mouse_click)
        self.pdf_canvas.bind("<MouseWheel>", self._on_mouse_wheel)

        # Center Notepad Viewport (built and initialized)
        self.notepad_center_frame = ctk.CTkFrame(self.main_container, corner_radius=0, fg_color=("gray92", "#18181b"))
        self._build_notepad_ui()

        # Bottom Audio Player Bar
        self.player_bar = ctk.CTkFrame(self, height=72, corner_radius=0)
        self.player_bar.pack(side="bottom", fill="x")

        # Transport Buttons on Left
        self.btn_prev_sentence = ctk.CTkButton(
            self.player_bar, text="⏮", width=42, height=36, font=("Segoe UI", 16),
            command=self._prev_sentence
        )
        self.btn_prev_sentence.pack(side="left", padx=(16, 6), pady=16)

        self.btn_play_pause = ctk.CTkButton(
            self.player_bar, text="▶ Play", width=90, height=38, font=("Segoe UI", 14, "bold"),
            fg_color="#2563eb", hover_color="#1d4ed8",
            command=self._toggle_playback
        )
        self.btn_play_pause.pack(side="left", padx=6, pady=16)

        self.btn_next_sentence = ctk.CTkButton(
            self.player_bar, text="⏭", width=42, height=36, font=("Segoe UI", 16),
            command=self._next_sentence
        )
        self.btn_next_sentence.pack(side="left", padx=6, pady=16)

        # Speed frame on Right FIRST (Guarantees it is NEVER cut off!)
        self.speed_frame = ctk.CTkFrame(self.player_bar, fg_color="transparent")
        self.speed_frame.pack(side="right", padx=(8, 16), pady=10)

        self.speed_label = ctk.CTkLabel(self.speed_frame, text="Speed: 1.0x", font=("Segoe UI", 11))
        self.speed_label.pack(side="top")

        self.speed_slider = ctk.CTkSlider(
            self.speed_frame, from_=0.5, to=2.0, width=95, number_of_steps=15,
            command=self._on_speed_change
        )
        self.speed_slider.set(1.0)
        self.speed_slider.pack(side="bottom")

        # Scrubber container in Center (takes remaining available width)
        self.scrubber_container = ctk.CTkFrame(self.player_bar, fg_color="transparent")
        self.scrubber_container.pack(side="left", fill="x", expand=True, padx=16, pady=8)

        self.scrubber_slider = ctk.CTkSlider(
            self.scrubber_container, from_=0, to=1, number_of_steps=1,
            command=self._on_scrubber_slide
        )
        self.scrubber_slider.pack(fill="x", pady=(2, 2))
        self.scrubber_slider.set(0)

        self.status_label = ctk.CTkLabel(
            self.scrubber_container, text="Ready. Click any word on the PDF to read aloud.",
            font=("Segoe UI", 12), text_color=("gray30", "gray70"), anchor="w"
        )
        self.status_label.pack(fill="x")

    def _set_status(self, text: str):
        """Sets status text with clean truncation to protect layout."""
        display = (text[:82] + "...") if len(text) > 85 else text
        self.status_label.configure(text=display)

    def _build_notepad_ui(self):
        """Constructs the rich text editor workspace, formatting toolbar, and bindings."""
        # Formatting Toolbar Frame
        self.notepad_toolbar = ctk.CTkFrame(self.notepad_center_frame, height=44, corner_radius=0)
        self.notepad_toolbar.pack(side="top", fill="x", padx=0, pady=0)

        # Style buttons
        self.btn_bold = ctk.CTkButton(
            self.notepad_toolbar, text="𝐁", width=34, height=30,
            font=("Segoe UI", 13, "bold"), fg_color="gray30", hover_color="gray40",
            command=self._toggle_bold
        )
        self.btn_bold.pack(side="left", padx=(10, 2), pady=6)

        self.btn_italic = ctk.CTkButton(
            self.notepad_toolbar, text="𝘐", width=34, height=30,
            font=("Segoe UI", 13, "italic"), fg_color="gray30", hover_color="gray40",
            command=self._toggle_italic
        )
        self.btn_italic.pack(side="left", padx=2, pady=6)

        self.btn_underline = ctk.CTkButton(
            self.notepad_toolbar, text="U̲", width=34, height=30,
            font=("Segoe UI", 13, "bold"), fg_color="gray30", hover_color="gray40",
            command=self._toggle_underline
        )
        self.btn_underline.pack(side="left", padx=2, pady=6)

        ctk.CTkLabel(self.notepad_toolbar, text="|", text_color="gray50").pack(side="left", padx=4)

        # Bullets & Lists
        self.btn_bullet = ctk.CTkButton(
            self.notepad_toolbar, text="• Bullets", width=74, height=30,
            font=("Segoe UI", 12), fg_color="gray30", hover_color="gray40",
            command=self._insert_bullet
        )
        self.btn_bullet.pack(side="left", padx=2, pady=6)

        self.btn_numlist = ctk.CTkButton(
            self.notepad_toolbar, text="1. List", width=64, height=30,
            font=("Segoe UI", 12), fg_color="gray30", hover_color="gray40",
            command=self._insert_numbered_list
        )
        self.btn_numlist.pack(side="left", padx=2, pady=6)

        self.btn_h1 = ctk.CTkButton(
            self.notepad_toolbar, text="H1", width=38, height=30,
            font=("Segoe UI", 12, "bold"), fg_color="gray30", hover_color="gray40",
            command=lambda: self._toggle_heading("h1")
        )
        self.btn_h1.pack(side="left", padx=2, pady=6)

        self.btn_h2 = ctk.CTkButton(
            self.notepad_toolbar, text="H2", width=38, height=30,
            font=("Segoe UI", 12, "bold"), fg_color="gray30", hover_color="gray40",
            command=lambda: self._toggle_heading("h2")
        )
        self.btn_h2.pack(side="left", padx=2, pady=6)

        ctk.CTkLabel(self.notepad_toolbar, text="|", text_color="gray50").pack(side="left", padx=4)

        # Read from cursor action button
        self.btn_read_cursor = ctk.CTkButton(
            self.notepad_toolbar, text="▶ Read From Cursor", width=145, height=30,
            font=("Segoe UI", 12, "bold"),
            fg_color=("#10b981", "#059669"), hover_color=("#059669", "#047857"),
            command=self._read_from_cursor
        )
        self.btn_read_cursor.pack(side="left", padx=4, pady=6)

        # Utility Buttons
        self.btn_paste = ctk.CTkButton(
            self.notepad_toolbar, text="📋 Paste", width=68, height=30,
            font=("Segoe UI", 11), fg_color="gray30", hover_color="gray40",
            command=self._paste_text
        )
        self.btn_paste.pack(side="left", padx=2, pady=6)

        self.btn_copy_all = ctk.CTkButton(
            self.notepad_toolbar, text="📑 Copy All", width=76, height=30,
            font=("Segoe UI", 11), fg_color="gray30", hover_color="gray40",
            command=self._copy_all_text
        )
        self.btn_copy_all.pack(side="left", padx=2, pady=6)

        self.btn_clear = ctk.CTkButton(
            self.notepad_toolbar, text="🗑️ Clear", width=68, height=30,
            font=("Segoe UI", 11), fg_color="#991b1b", hover_color="#7f1d1d",
            command=self._clear_notepad
        )
        self.btn_clear.pack(side="left", padx=2, pady=6)

        # Stats label on right
        self.notepad_stats_lbl = ctk.CTkLabel(
            self.notepad_toolbar, text="0 words | 0 sentences",
            font=("Segoe UI", 11), text_color=("gray40", "gray60")
        )
        self.notepad_stats_lbl.pack(side="right", padx=16, pady=6)

        # Notepad Canvas / Editor Container with Padding
        self.notepad_card = ctk.CTkFrame(
            self.notepad_center_frame, corner_radius=8,
            fg_color=("#ffffff", "#24242d")
        )
        self.notepad_card.pack(fill="both", expand=True, padx=14, pady=12)

        self.notepad_scroll = tk.Scrollbar(self.notepad_card, orient="vertical")
        self.notepad_scroll.pack(side="right", fill="y")

        self.notepad_text = tk.Text(
            self.notepad_card,
            wrap="word",
            font=("Segoe UI", 13),
            padx=28, pady=24,
            spacing1=4, spacing2=3, spacing3=6,
            undo=True,
            bd=0, highlightthickness=0,
            yscrollcommand=self.notepad_scroll.set,
            bg="#24242d" if self.theme == "dark" else "#ffffff",
            fg="#f1f5f9" if self.theme == "dark" else "#0f172a",
            insertbackground="#60a5fa" if self.theme == "dark" else "#2563eb",
            selectbackground="#3b82f6" if self.theme == "dark" else "#bfdbfe",
            selectforeground="#ffffff" if self.theme == "dark" else "#0f172a"
        )
        self.notepad_text.pack(side="left", fill="both", expand=True)
        self.notepad_scroll.config(command=self.notepad_text.yview)

        # Tag configurations
        self.notepad_text.tag_configure("bold", font=("Segoe UI", 13, "bold"))
        self.notepad_text.tag_configure("italic", font=("Segoe UI", 13, "italic"))
        self.notepad_text.tag_configure("underline", underline=True)
        self.notepad_text.tag_configure("h1", font=("Segoe UI", 18, "bold"), spacing3=10)
        self.notepad_text.tag_configure("h2", font=("Segoe UI", 15, "bold"), spacing3=8)
        self.notepad_text.tag_configure("bullet", lmargin1=16, lmargin2=36)
        self.notepad_text.tag_configure("sentence_hl", background="#b2d6f3", foreground="#000000")
        self.notepad_text.tag_configure("word_hl", background="#ffff00", foreground="#000000")
        self.notepad_text.tag_raise("sentence_hl")
        self.notepad_text.tag_raise("word_hl")

        # Bindings
        self.notepad_text.bind("<KeyPress>", self._on_notepad_key_press)
        self.notepad_text.bind("<KeyRelease>", self._on_notepad_key_release)
        self.notepad_text.bind("<Return>", self._on_notepad_return)
        self.notepad_text.bind("<Double-Button-1>", self._on_notepad_double_click)
        self.notepad_text.bind("<Button-3>", self._show_notepad_context_menu)
        self.notepad_text.bind("<Control-b>", lambda e: (self._toggle_bold(), "break")[1])
        self.notepad_text.bind("<Control-B>", lambda e: (self._toggle_bold(), "break")[1])
        self.notepad_text.bind("<Control-i>", lambda e: (self._toggle_italic(), "break")[1])
        self.notepad_text.bind("<Control-I>", lambda e: (self._toggle_italic(), "break")[1])
        self.notepad_text.bind("<Control-u>", lambda e: (self._toggle_underline(), "break")[1])
        self.notepad_text.bind("<Control-U>", lambda e: (self._toggle_underline(), "break")[1])
        self.notepad_text.bind("<Control-Return>", lambda e: (self._read_from_cursor(), "break")[1])

        # Right-click context menu
        self.notepad_context_menu = tk.Menu(self, tearoff=0)
        self.notepad_context_menu.add_command(label="Cut", command=lambda: self.notepad_text.event_generate("<<Cut>>"))
        self.notepad_context_menu.add_command(label="Copy", command=lambda: self.notepad_text.event_generate("<<Copy>>"))
        self.notepad_context_menu.add_command(label="Paste", command=self._paste_text)
        self.notepad_context_menu.add_command(label="Select All", command=self._select_all_notepad)
        self.notepad_context_menu.add_separator()
        self.notepad_context_menu.add_command(label="▶ Read from Here", command=self._read_from_cursor)

    def _on_mode_change(self, mode: str):
        """Switches between PDF Reader mode and Notepad mode."""
        self._stop_audio()
        if "Notepad" in mode:
            self.app_mode = "notepad"
            # Switch header controls
            self.pdf_header_frame.pack_forget()
            self.notepad_header_frame.pack(side="left", fill="x", padx=4)

            # Switch center viewports
            self.pdf_center_frame.pack_forget()
            self.notepad_center_frame.pack(side="left", fill="both", expand=True)

            self._parse_notepad_sentences()
            self._update_player_for_current_mode()
        else:
            self.app_mode = "pdf"
            # Switch header controls
            self.notepad_header_frame.pack_forget()
            self.pdf_header_frame.pack(side="left", fill="x", padx=4)

            # Switch center viewports
            self.notepad_center_frame.pack_forget()
            self.pdf_center_frame.pack(side="left", fill="both", expand=True)

            self._update_player_for_current_mode()

    def _update_player_for_current_mode(self):
        """Updates scrubber slider and status label for the active mode."""
        if self.app_mode == "notepad":
            self.sentences = self.notepad_sentences
            self.current_sentence_idx = 0
            self.current_word_idx = 0
            max_s = max(1, len(self.sentences) - 1)
            self.scrubber_slider.configure(from_=0, to=max_s, number_of_steps=max_s)
            self.scrubber_slider.set(0)
            self._set_status(f"Notepad ready ({len(self.sentences)} sentences). Click ▶ Play or double-click to read.")
        else:
            self.sentences = self.pdf_sentences
            self.current_sentence_idx = 0
            self.current_word_idx = 0
            max_s = max(1, len(self.sentences) - 1)
            self.scrubber_slider.configure(from_=0, to=max_s, number_of_steps=max_s)
            self.scrubber_slider.set(0)
            self._set_status(f"Page {self.current_page_idx + 1} ({len(self.sentences)} sentences). Click any word to read.")

    def _toggle_bold(self):
        try:
            sel_first = self.notepad_text.index("sel.first")
            sel_last = self.notepad_text.index("sel.last")
            if "bold" in self.notepad_text.tag_names(sel_first):
                self.notepad_text.tag_remove("bold", sel_first, sel_last)
            else:
                self.notepad_text.tag_add("bold", sel_first, sel_last)
            self._schedule_save_draft()
        except tk.TclError:
            pass

    def _toggle_italic(self):
        try:
            sel_first = self.notepad_text.index("sel.first")
            sel_last = self.notepad_text.index("sel.last")
            if "italic" in self.notepad_text.tag_names(sel_first):
                self.notepad_text.tag_remove("italic", sel_first, sel_last)
            else:
                self.notepad_text.tag_add("italic", sel_first, sel_last)
            self._schedule_save_draft()
        except tk.TclError:
            pass

    def _toggle_underline(self):
        try:
            sel_first = self.notepad_text.index("sel.first")
            sel_last = self.notepad_text.index("sel.last")
            if "underline" in self.notepad_text.tag_names(sel_first):
                self.notepad_text.tag_remove("underline", sel_first, sel_last)
            else:
                self.notepad_text.tag_add("underline", sel_first, sel_last)
            self._schedule_save_draft()
        except tk.TclError:
            pass

    def _insert_bullet(self):
        line_start = self.notepad_text.index("insert linestart")
        line_end = self.notepad_text.index("insert lineend")
        line_text = self.notepad_text.get(line_start, line_end)
        if line_text.startswith("• "):
            self.notepad_text.delete(line_start, f"{line_start} + 2 chars")
            self.notepad_text.tag_remove("bullet", line_start, f"{line_start} lineend")
        else:
            self.notepad_text.insert(line_start, "• ")
            self.notepad_text.tag_add("bullet", line_start, f"{line_start} lineend")
        self._schedule_reparse_and_stats()
        self._schedule_save_draft()

    def _insert_numbered_list(self):
        line_start = self.notepad_text.index("insert linestart")
        line_end = self.notepad_text.index("insert lineend")
        line_text = self.notepad_text.get(line_start, line_end)
        m = re.match(r'^\d+\.\s*', line_text)
        if m:
            self.notepad_text.delete(line_start, f"{line_start} + {len(m.group(0))} chars")
        else:
            self.notepad_text.insert(line_start, "1. ")
        self._schedule_reparse_and_stats()
        self._schedule_save_draft()

    def _toggle_heading(self, level: str):
        line_start = self.notepad_text.index("insert linestart")
        line_end = self.notepad_text.index(f"{line_start} lineend")
        for h in ["h1", "h2"]:
            self.notepad_text.tag_remove(h, line_start, line_end)
        if level in ["h1", "h2"]:
            if level not in self.notepad_text.tag_names(line_start):
                self.notepad_text.tag_add(level, line_start, line_end)
        self._schedule_save_draft()

    def _read_from_cursor(self):
        """Starts reading immediately from the current cursor position."""
        if self.app_mode != "notepad":
            return
        if not self.notepad_sentences:
            self._parse_notepad_sentences()
        if not self.notepad_sentences:
            self._set_status("Notepad is empty. Write or paste text to read.")
            return
        char_pos = self._get_notepad_cursor_char_pos()
        s_idx, w_idx = self._find_notepad_word_at_char(char_pos)
        self.play_sentence(s_idx, w_idx)

    def _get_notepad_cursor_char_pos(self) -> int:
        try:
            index = self.notepad_text.index("insert")
            counts = self.notepad_text.count("1.0", index, "chars")
            return counts[0] if counts else 0
        except Exception:
            return 0

    def _find_notepad_word_at_char(self, char_pos: int) -> Tuple[int, int]:
        if not self.notepad_sentences:
            return 0, 0
        for s in self.notepad_sentences:
            if s["start_char"] <= char_pos <= s["end_char"]:
                for w in s["words"]:
                    if w["start_char"] <= char_pos <= w["end_char"]:
                        return s["s_idx"], w["w_idx"]
                return s["s_idx"], 0
        return len(self.notepad_sentences) - 1, 0

    def _paste_text(self):
        """Pastes clipboard text cleanly into the notepad and schedules re-parse."""
        try:
            clip = self.clipboard_get()
            if clip:
                try:
                    self.notepad_text.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass
                self.notepad_text.insert("insert", clip)
                self._schedule_reparse_and_stats()
                self._schedule_save_draft()
        except Exception:
            pass

    def _copy_all_text(self):
        """Copies all text from notepad to clipboard."""
        try:
            text = self.notepad_text.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status("All text copied to clipboard.")
        except Exception:
            pass

    def _clear_notepad(self):
        if messagebox.askyesno("Clear Notepad", "Are you sure you want to clear all text in the notepad?"):
            self._stop_audio()
            self.notepad_text.delete("1.0", "end")
            self._schedule_reparse_and_stats()
            self._save_notepad_draft()

    def _select_all_notepad(self):
        self.notepad_text.tag_add("sel", "1.0", "end")

    def _show_notepad_context_menu(self, event):
        try:
            self.notepad_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.notepad_context_menu.grab_release()

    def _on_notepad_key_press(self, event):
        if self.is_playing:
            self._stop_audio()

    def _on_notepad_key_release(self, event):
        self._schedule_reparse_and_stats()
        self._schedule_save_draft()

    def _schedule_reparse_and_stats(self):
        if self._notepad_reparse_timer:
            try: self.after_cancel(self._notepad_reparse_timer)
            except Exception: pass
        self._notepad_reparse_timer = self.after(300, self._apply_reparse_and_stats)

    def _apply_reparse_and_stats(self):
        self._parse_notepad_sentences()
        self._update_notepad_stats()
        if self.app_mode == "notepad" and not self.is_playing:
            max_s = max(1, len(self.sentences) - 1)
            self.scrubber_slider.configure(from_=0, to=max_s, number_of_steps=max_s)

    def _on_notepad_return(self, event):
        """Smart Enter key handler: continues bullet points and numbered lists naturally."""
        line_start = self.notepad_text.index("insert linestart")
        line_end = self.notepad_text.index("insert lineend")
        line_text = self.notepad_text.get(line_start, line_end)

        # Bullet line
        if line_text.strip() == "•":
            self.notepad_text.delete(line_start, line_end)
            self.notepad_text.tag_remove("bullet", line_start, f"{line_start} lineend")
            self._schedule_reparse_and_stats()
            return "break"
        elif line_text.startswith("• "):
            self.notepad_text.insert("insert", "\n• ")
            new_line_start = self.notepad_text.index("insert linestart")
            self.notepad_text.tag_add("bullet", new_line_start, f"{new_line_start} lineend")
            self._schedule_reparse_and_stats()
            return "break"

        # Numbered list line
        m = re.match(r'^(\d+)\.\s*(.*)', line_text)
        if m:
            num = int(m.group(1))
            rest = m.group(2)
            if not rest.strip():
                self.notepad_text.delete(line_start, line_end)
                self._schedule_reparse_and_stats()
                return "break"
            else:
                self.notepad_text.insert("insert", f"\n{num + 1}. ")
                self._schedule_reparse_and_stats()
                return "break"

        self._schedule_reparse_and_stats()
        return None

    def _on_notepad_double_click(self, event):
        """Double-clicking any word immediately starts reading aloud from that exact word!"""
        index = self.notepad_text.index(f"@{event.x},{event.y}")
        self.notepad_text.mark_set("insert", index)
        counts = self.notepad_text.count("1.0", index, "chars")
        char_pos = counts[0] if counts else 0
        self._parse_notepad_sentences()
        s_idx, w_idx = self._find_notepad_word_at_char(char_pos)
        self.play_sentence(s_idx, w_idx)

    def _update_notepad_stats(self):
        try:
            text = self.notepad_text.get("1.0", "end-1c")
            words = len(re.findall(r'\S+', text))
            sentences = len(self.notepad_sentences)
            self.notepad_stats_lbl.configure(text=f"{words} words | {sentences} sentences")
        except Exception:
            pass

    def _save_notepad_draft(self):
        """Saves current text and tag formatting to JSON draft file."""
        if not hasattr(self, "notepad_text"):
            return
        try:
            text = self.notepad_text.get("1.0", "end-1c")
            tag_ranges = {}
            for tag in ["bold", "italic", "underline", "h1", "h2", "bullet"]:
                ranges = self.notepad_text.tag_ranges(tag)
                if ranges:
                    tag_ranges[tag] = [str(r) for r in ranges]
            data = {
                "text": text,
                "tags": tag_ranges,
                "updated_at": time.time()
            }
            with open(NOTEPAD_DRAFT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if hasattr(self, "notepad_autosave_label"):
                self.notepad_autosave_label.configure(text="• Draft saved", text_color=("#059669", "#10b981"))
        except Exception as e:
            print(f"[Notepad] Save error: {e}")

    def _schedule_save_draft(self):
        if self._notepad_save_timer:
            try: self.after_cancel(self._notepad_save_timer)
            except Exception: pass
        if hasattr(self, "notepad_autosave_label"):
            self.notepad_autosave_label.configure(text="• Saving...", text_color="#d97706")
        self._notepad_save_timer = self.after(1500, self._save_notepad_draft)

    def _load_notepad_draft(self):
        """Loads saved draft from JSON or populates initial welcoming content."""
        if not hasattr(self, "notepad_text"):
            return
        loaded = False
        if NOTEPAD_DRAFT_FILE.exists():
            try:
                with open(NOTEPAD_DRAFT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                text = data.get("text", "")
                if text:
                    self.notepad_text.delete("1.0", "end")
                    self.notepad_text.insert("1.0", text)
                    for tag, pairs in data.get("tags", {}).items():
                        for i in range(0, len(pairs), 2):
                            try:
                                self.notepad_text.tag_add(tag, pairs[i], pairs[i+1])
                            except Exception:
                                pass
                    loaded = True
            except Exception as e:
                print(f"[Notepad] Load draft error: {e}")

        if not loaded:
            welcome_text = (
                "Welcome to Speakaloud Notepad & Email Drafter!\n\n"
                "You can write, format, or paste anything here:\n"
                "• Draft emails and listen to them out loud to catch awkward phrasing and mistakes.\n"
                "• Use bullet points, bold formatting, and headings to structure your text.\n"
                "• Paste articles, documents, or notes from the web.\n\n"
                "Click '▶ Read From Cursor' or double-click any word to start reading aloud!"
            )
            self.notepad_text.delete("1.0", "end")
            self.notepad_text.insert("1.0", welcome_text)
            self.notepad_text.tag_add("h1", "1.0", "1.end")
            self.notepad_text.tag_add("bullet", "5.0", "5.end")
            self.notepad_text.tag_add("bullet", "6.0", "6.end")
            self.notepad_text.tag_add("bullet", "7.0", "7.end")

        self._parse_notepad_sentences()
        self._update_notepad_stats()

    def _parse_notepad_sentences(self):
        """Tokenizes the current notepad text into sentences and words with character positions."""
        if not hasattr(self, "notepad_text"):
            return
        text_content = self.notepad_text.get("1.0", "end-1c")
        if not text_content or not text_content.strip():
            self.notepad_sentences = []
            if self.app_mode == "notepad":
                self.sentences = []
            return

        lines = text_content.splitlines(keepends=True)
        sentences = []
        current_char = 0
        s_idx = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                current_char += len(line)
                continue

            # If line starts with a bullet symbol or numbered list, treat as individual sentence
            is_bullet = bool(re.match(r'^([•\-\*]|\d+\.)\s+', stripped))
            if is_bullet:
                subs = [stripped]
            else:
                subs = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'\(\[])', stripped)
                if len(subs) == 1:
                    subs = [stripped]

            offset = 0
            for raw_s in subs:
                s_clean = raw_s.strip()
                if not s_clean:
                    continue

                s_start = line.find(s_clean, offset)
                if s_start == -1:
                    s_start = offset
                start_char = current_char + s_start
                end_char = start_char + len(s_clean)
                offset = s_start + len(s_clean)

                words = []
                for w_idx, match in enumerate(re.finditer(r'\S+', s_clean)):
                    w_text = match.group(0)
                    words.append({
                        "text": w_text,
                        "clean": re.sub(r'\W+', '', w_text.lower()),
                        "start_char": start_char + match.start(),
                        "end_char": start_char + match.end(),
                        "w_idx": w_idx,
                        "s_idx": s_idx,
                        "b_no": 0,
                        "l_no": 0,
                        "bbox": (0, 0, 0, 0)
                    })

                if words:
                    sentences.append({
                        "s_idx": s_idx,
                        "text": s_clean,
                        "start_char": start_char,
                        "end_char": end_char,
                        "words": words
                    })
                    s_idx += 1

            current_char += len(line)

        self.notepad_sentences = sentences
        if self.app_mode == "notepad":
            self.sentences = self.notepad_sentences

    def _clear_notepad_highlight(self):
        """Removes sentence and word highlight tags from the notepad text."""
        try:
            self.notepad_text.tag_remove("sentence_hl", "1.0", "end")
            self.notepad_text.tag_remove("word_hl", "1.0", "end")
        except Exception:
            pass
        self._last_notepad_s_idx = None
        self.current_highlighted_word_idx = None

    def _highlight_notepad_sentence(self, sentence_idx: int, word_idx: Optional[int] = None):
        """Highlights the active sentence with pastel blue (#b2d6f3)
        and active word with vibrant yellow (#ffff00) in the notepad text."""
        if not self.notepad_sentences or sentence_idx < 0 or sentence_idx >= len(self.notepad_sentences):
            return

        if sentence_idx == self.current_sentence_idx and word_idx == self.current_highlighted_word_idx:
            return

        sentence = self.notepad_sentences[sentence_idx]
        s_start = f"1.0 + {sentence['start_char']} chars"
        s_end = f"1.0 + {sentence['end_char']} chars"

        if self._last_notepad_s_idx != sentence_idx:
            self.notepad_text.tag_remove("sentence_hl", "1.0", "end")
            self.notepad_text.tag_add("sentence_hl", s_start, s_end)
            self._last_notepad_s_idx = sentence_idx
            try:
                self.notepad_text.see(s_start)
            except Exception:
                pass

        self.notepad_text.tag_remove("word_hl", "1.0", "end")
        if word_idx is not None and 0 <= word_idx < len(sentence["words"]):
            w = sentence["words"][word_idx]
            w_start = f"1.0 + {w['start_char']} chars"
            w_end = f"1.0 + {w['end_char']} chars"
            self.notepad_text.tag_add("word_hl", w_start, w_end)
            try:
                self.notepad_text.see(w_start)
            except Exception:
                pass

        self.current_highlighted_word_idx = word_idx

    def _bind_shortcuts(self):
        """Keyboard shortcuts."""
        self.bind("<space>", lambda e: self._toggle_playback())
        self.bind("<Left>", lambda e: self._prev_sentence())
        self.bind("<Right>", lambda e: self._next_sentence())
        self.bind("<Prior>", lambda e: self._prev_page())
        self.bind("<Next>", lambda e: self._next_page())
        self.bind("<plus>", lambda e: self._zoom_in())
        self.bind("<equal>", lambda e: self._zoom_in())
        self.bind("<minus>", lambda e: self._zoom_out())

    def _toggle_sidebar(self):
        if self.sidebar_visible:
            self.sidebar_frame.pack_forget()
            self.sidebar_visible = False
            self.btn_toggle_sidebar.configure(text="▶ Library")
        else:
            target = self.pdf_center_frame if self.app_mode == "pdf" else self.notepad_center_frame
            self.sidebar_frame.pack(side="left", fill="y", padx=0, pady=0, before=target)
            self.sidebar_visible = True
            self.btn_toggle_sidebar.configure(text="◀ Library")

    def _toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        ctk.set_appearance_mode("Light" if self.theme == "light" else "Dark")
        self.btn_theme.configure(text="☀️" if self.theme == "dark" else "🌙")
        self.pdf_canvas.configure(bg="#2b2e38" if self.theme == "dark" else "#e2e8f0")
        if hasattr(self, "notepad_text"):
            self.notepad_text.configure(
                bg="#24242d" if self.theme == "dark" else "#ffffff",
                fg="#f1f5f9" if self.theme == "dark" else "#0f172a",
                insertbackground="#60a5fa" if self.theme == "dark" else "#2563eb",
                selectbackground="#3b82f6" if self.theme == "dark" else "#bfdbfe",
                selectforeground="#ffffff" if self.theme == "dark" else "#0f172a"
            )
            self.notepad_card.configure(fg_color=("#ffffff", "#24242d"))
        state_manager.update_settings({"theme": self.theme})
        self._render_current_page()

    def _quick_clear_audio_cache(self):
        """Clears all cached audio files while preserving reading position, library, rules, and settings."""
        count = 0
        freed_bytes = 0
        if CACHE_DIR.exists():
            for f in CACHE_DIR.glob("*"):
                if f.is_file() and f.name != "voices.json":
                    try:
                        freed_bytes += f.stat().st_size
                        f.unlink()
                        count += 1
                    except Exception:
                        pass
        freed_mb = freed_bytes / (1024 * 1024)
        self.prefetch_cache.clear()
        self._set_status(f"Audio cache cleared: {count} clips ({freed_mb:.1f} MB freed). Progress preserved.")

    def _open_voice_manager(self):
        """Opens voice dialog on top, preventing multiple windows."""
        if self.voice_dialog and self.voice_dialog.winfo_exists():
            self.voice_dialog.deiconify()
            self.voice_dialog.lift()
            self.voice_dialog.focus_force()
            self.voice_dialog.attributes("-topmost", True)
            self.voice_dialog.after(150, lambda: self.voice_dialog.attributes("-topmost", False) if self.voice_dialog and self.voice_dialog.winfo_exists() else None)
            return

        self.voice_dialog = VoiceManagerDialog(self, on_select_voice=self._apply_chosen_voice)

    def _open_rules_manager(self):
        """Opens pronunciation rules dialog on top, preventing multiple windows."""
        if self.rules_dialog and self.rules_dialog.winfo_exists():
            self.rules_dialog.deiconify()
            self.rules_dialog.lift()
            self.rules_dialog.focus_force()
            self.rules_dialog.attributes("-topmost", True)
            self.rules_dialog.after(150, lambda: self.rules_dialog.attributes("-topmost", False) if self.rules_dialog and self.rules_dialog.winfo_exists() else None)
            return

        self.rules_dialog = SpeechRulesDialog(self, on_rules_changed=self._on_rules_changed)

    def _on_rules_changed(self):
        """Clears audio cache when rules are toggled, added, or edited."""
        self.prefetch_cache.clear()

    def _apply_chosen_voice(self, v_item):
        self.active_engine = v_item["engine"]
        self.active_voice_key = v_item["voice_key"]
        self.active_voice_name = _resolve_voice_name(self.active_voice_key, self.active_engine)
        self.active_voice_lbl.configure(text=f"Voice: {self.active_voice_name}")
        self.prefetch_cache.clear()
        state_manager.update_settings({"engine": self.active_engine, "voice": self.active_voice_key})

        if self.is_playing:
            self.play_sentence(self.current_sentence_idx, self.current_word_idx)

    def _on_speed_change(self, val):
        rounded = round(float(val), 2)
        self.speed_label.configure(text=f"Speed: {rounded}x")
        self.prefetch_cache.clear()

        if self.is_playing and not self.is_synthesizing:
            if self._speed_timer:
                self.after_cancel(self._speed_timer)
            self._speed_timer = self.after(350, lambda: self.play_sentence(self.current_sentence_idx, self.current_word_idx))

    def _load_initial_document(self):
        """Loads the last active document or first library item."""
        self._refresh_library_list()
        last_active = state_manager.get_last_active_doc()
        if last_active and "file_path" in last_active and Path(last_active["file_path"]).exists():
            self._open_pdf_path(last_active["file_path"], last_active.get("current_page", 0))
        else:
            lib = state_manager.get_library()
            if lib and Path(lib[0]["file_path"]).exists():
                self._open_pdf_path(lib[0]["file_path"], 0)

    def _choose_pdf_file(self):
        f = filedialog.askopenfilename(
            title="Select PDF Document",
            filetypes=[("PDF Files", "*.pdf")]
        )
        if f:
            self._open_pdf_path(f, 0)

    def _close_current_pdf(self):
        """Properly closes document and returns to clean view."""
        self._stop_audio()
        if self.doc:
            try: self.doc.close()
            except Exception: pass
            self.doc = None

        self.current_doc_id = None
        self.sentences = []
        self.page_words = []
        self.pdf_canvas.delete("all")
        self.doc_title_label.configure(text="No document open")
        self.total_pages_label.configure(text="/ 0")
        self.page_entry.delete(0, "end")
        self.page_entry.insert(0, "0")
        self._set_status("Document closed. Select a document from the Library or Open PDF.")
        self._refresh_toc()
        self._refresh_library_list()

    def _open_pdf_path(self, file_path: str, start_page: int = 0):
        try:
            self._stop_audio()
            p = Path(file_path)
            if not p.exists():
                messagebox.showerror("File Not Found", f"Could not find: {file_path}")
                return

            if self.doc:
                try: self.doc.close()
                except Exception: pass

            self.doc = pymupdf.open(str(p))
            self.total_pages = len(self.doc)
            self.current_page_idx = min(max(0, start_page), self.total_pages - 1)

            entry = state_manager.register_document(
                doc_id=p.stem,
                filename=p.name,
                title=p.stem,
                total_pages=self.total_pages,
                file_path=str(p)
            )
            self.current_doc_id = entry.get("doc_id", p.stem)
            self.doc_title_label.configure(text=p.name)
            self.total_pages_label.configure(text=f"/ {self.total_pages}")
            self.page_entry.delete(0, "end")
            self.page_entry.insert(0, str(self.current_page_idx + 1))

            self._refresh_toc()
            self._refresh_library_list()
            self._load_page(self.current_page_idx)

        except Exception as e:
            messagebox.showerror("Error Opening PDF", f"Failed to open PDF:\n{e}")

    def _refresh_library_list(self):
        for widget in self.library_scroll.winfo_children():
            widget.destroy()

        library = state_manager.get_library()
        if not library:
            lbl = ctk.CTkLabel(self.library_scroll, text="No recent documents.", font=("Segoe UI", 11), text_color="gray60")
            lbl.pack(pady=20)
            return

        for doc in library:
            card = ctk.CTkFrame(self.library_scroll, corner_radius=6)
            card.pack(fill="x", pady=4, padx=2)

            top_row = ctk.CTkFrame(card, fg_color="transparent")
            top_row.pack(fill="x", padx=6, pady=(4, 0))

            t_lbl = ctk.CTkLabel(top_row, text=doc.get("title", "Document"), font=("Segoe UI", 11, "bold"), anchor="w")
            t_lbl.pack(side="left", fill="x", expand=True)

            btn_del = ctk.CTkButton(
                top_row, text="✕", width=22, height=20, font=("Segoe UI", 10),
                fg_color="transparent", hover_color=("#fee2e2", "#7f1d1d"), text_color=("gray40", "gray70"),
                command=lambda d_id=doc["doc_id"]: self._remove_from_library(d_id)
            )
            btn_del.pack(side="right")

            bot_row = ctk.CTkFrame(card, fg_color="transparent")
            bot_row.pack(fill="x", padx=6, pady=(2, 6))

            p_lbl = ctk.CTkLabel(bot_row, text=f"Page {doc.get('current_page', 0) + 1}/{doc.get('total_pages', 1)} ({int(doc.get('progress_percent', 0))}%)", font=("Segoe UI", 10), text_color="gray60", anchor="w")
            p_lbl.pack(side="left")

            btn_read = ctk.CTkButton(
                bot_row, text="Read", width=52, height=22, font=("Segoe UI", 11),
                command=lambda p=doc["file_path"], pg=doc.get("current_page", 0): self._open_pdf_path(p, pg)
            )
            btn_read.pack(side="right", padx=2)

            btn_reset = ctk.CTkButton(
                bot_row, text="↺", width=24, height=22, font=("Segoe UI", 11),
                fg_color="gray40", hover_color="gray50",
                command=lambda d_id=doc["doc_id"], p=doc["file_path"]: self._reset_doc_progress(d_id, p)
            )
            btn_reset.pack(side="right", padx=2)

    def _remove_from_library(self, doc_id: str):
        state_manager.remove_document(doc_id)
        if self.current_doc_id == doc_id:
            self._close_current_pdf()
        else:
            self._refresh_library_list()

    def _reset_doc_progress(self, doc_id: str, file_path: str):
        state_manager.reset_progress(doc_id)
        self._refresh_library_list()
        if self.current_doc_id == doc_id:
            self._load_page(0)

    def _refresh_toc(self):
        for widget in self.toc_scroll.winfo_children():
            widget.destroy()

        if not self.doc:
            return

        toc = self.doc.get_toc()
        if not toc:
            lbl = ctk.CTkLabel(self.toc_scroll, text="No PDF bookmarks found", font=("Segoe UI", 11), text_color="gray60")
            lbl.pack(pady=12)
            return

        for item in toc:
            lvl, title, page = item[0], item[1], item[2] - 1
            btn = ctk.CTkButton(
                self.toc_scroll, text=f"{'  ' * (lvl - 1)}{title} (p.{page + 1})",
                anchor="w", height=26, font=("Segoe UI", 11), fg_color="transparent",
                hover_color=("gray80", "gray30"), text_color=("gray10", "gray90"),
                command=lambda pg=page: self._load_page(pg)
            )
            btn.pack(fill="x", pady=1)

    def _load_page(self, page_idx: int):
        """Loads and parses sentences and words for page_idx."""
        if not self.doc or page_idx < 0 or page_idx >= self.total_pages:
            return

        self._stop_audio()
        self.current_page_idx = page_idx
        self.current_sentence_idx = 0
        self.current_word_idx = 0
        self.current_highlighted_word_idx = None
        self.sentence_word_timings = []
        self.last_scrolled_sentence_idx = None
        self.prefetch_cache.clear()

        self.page_entry.delete(0, "end")
        self.page_entry.insert(0, str(page_idx + 1))

        if self.current_doc_id:
            state_manager.update_progress(self.current_doc_id, page_idx, 0)

        page = self.doc[page_idx]
        raw_words = page.get_text("words")
        self.page_words = raw_words
        self.pdf_sentences = self._group_words_into_sentences(raw_words)

        if self.app_mode == "pdf":
            self.sentences = self.pdf_sentences
            num_sentences = len(self.sentences)
            max_slider = max(1, num_sentences - 1)
            self.scrubber_slider.configure(
                from_=0, to=max_slider,
                number_of_steps=max_slider
            )
            self.scrubber_slider.set(0)
            self._set_status(f"Page {page_idx + 1} loaded ({len(self.sentences)} sentences). Click any word to read.")

        # Auto-prune stale audio clips every 3 pages so disk space stays minimal
        self.page_turns_since_cleanup += 1
        if self.page_turns_since_cleanup >= 3:
            self.page_turns_since_cleanup = 0
            self._auto_prune_audio_cache()

        self._render_current_page()

    def _auto_prune_audio_cache(self):
        """Automatically prunes stale audio cache files in background every 3 pages."""
        def _worker():
            try:
                if not CACHE_DIR.exists():
                    return
                # Protect audio files created in the last 15 seconds (e.g. current page audio)
                cutoff_time = time.time() - 15.0
                deleted_count = 0
                freed_bytes = 0
                for f in CACHE_DIR.glob("*"):
                    if f.is_file() and f.name != "voices.json":
                        try:
                            if f.stat().st_mtime < cutoff_time:
                                freed_bytes += f.stat().st_size
                                f.unlink(missing_ok=True)
                                deleted_count += 1
                        except Exception:
                            pass
                if deleted_count > 0:
                    freed_mb = freed_bytes / (1024 * 1024)
                    print(f"[CACHE] Auto-pruned {deleted_count} stale clips ({freed_mb:.2f} MB freed) from {CACHE_DIR}")
            except Exception as e:
                print(f"[CACHE] Auto-prune error: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _group_words_into_sentences(self, raw_words: List[Any]) -> List[Dict[str, Any]]:
        """Groups raw word tuples into sentences with exact pixel bounding boxes."""
        if not raw_words:
            return []

        sentences = []
        cur_words = []
        cur_tokens = []
        s_idx = 0

        for w in raw_words:
            x0, y0, x1, y1, text, b_no, l_no, w_no = w
            cur_tokens.append(text)
            cur_words.append({
                "text": text,
                "clean": re.sub(r'\W+', '', text.lower()),
                "bbox": (x0, y0, x1, y1),
                "b_no": b_no,
                "l_no": l_no,
                "s_idx": s_idx,
                "w_idx": len(cur_words)
            })

            is_break = False
            if text and text[-1] in ".?!":
                if len(text) > 2 and text not in ["Mr.", "Ms.", "Dr.", "Jr.", "Sr.", "vs.", "al."]:
                    is_break = True

            if is_break and len(cur_words) >= 1:
                sentences.append({
                    "s_idx": s_idx,
                    "text": " ".join(cur_tokens),
                    "words": cur_words
                })
                s_idx += 1
                cur_words = []
                cur_tokens = []

        if cur_words:
            sentences.append({
                "s_idx": s_idx,
                "text": " ".join(cur_tokens),
                "words": cur_words
            })

        return sentences

    def _render_current_page(self):
        """Renders the PDF page to canvas with exact zoom scaling."""
        if not self.doc:
            return

        page = self.doc[self.current_page_idx]
        page_rect = page.rect

        canvas_width = max(600, self.pdf_canvas.winfo_width())
        if canvas_width <= 1:
            canvas_width = 800

        page_w = max(1.0, float(page_rect.width))
        page_h = max(1.0, float(page_rect.height))

        if self.zoom_mode == "fit-width":
            self.zoom_scale = (canvas_width - 40) / page_w
        elif self.zoom_mode == "fit-page":
            canvas_height = max(500, self.pdf_canvas.winfo_height())
            self.zoom_scale = (canvas_height - 40) / page_h

        self.zoom_scale = max(0.2, min(3.0, self.zoom_scale))

        mat = pymupdf.Matrix(self.zoom_scale, self.zoom_scale)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        self.base_page_img = img
        self.current_photo = ImageTk.PhotoImage(img)

        self.pdf_canvas.delete("all")
        self.canvas_img_id = self.pdf_canvas.create_image(
            20, 20, image=self.current_photo, anchor="nw"
        )
        self.pdf_canvas.config(scrollregion=(0, 0, pix.width + 40, pix.height + 40))

        # Re-apply sentence highlight if currently playing
        if self.is_playing and 0 <= self.current_sentence_idx < len(self.sentences):
            self.current_highlighted_word_idx = None
            self._highlight_sentence(self.current_sentence_idx, self.current_word_idx)

    def _clear_sentence_highlight(self):
        """Clears all sentence and word highlight indicators from canvas or notepad, restoring the clean page."""
        if self.app_mode == "notepad":
            self._clear_notepad_highlight()
            return
        try:
            self.pdf_canvas.delete("sentence_highlight")
        except Exception:
            pass
        if self.base_page_img is not None and self.canvas_img_id is not None:
            try:
                self.current_photo = ImageTk.PhotoImage(self.base_page_img)
                self.pdf_canvas.itemconfigure(self.canvas_img_id, image=self.current_photo)
            except Exception:
                pass
        self.current_highlighted_word_idx = None

    def _highlight_sentence(self, sentence_idx: int, word_idx: Optional[int] = None):
        """Highlights the active sentence with Edge-style soft pastel blue tint (#b2d6f3)
        and the currently spoken word with vibrant highlighter yellow (#ffff00).
        Uses PIL multiply blend mode to preserve 100% crisp jet black text without obscuring letters."""
        if self.app_mode == "notepad":
            self._highlight_notepad_sentence(sentence_idx, word_idx)
            return

        if not self.sentences or sentence_idx < 0 or sentence_idx >= len(self.sentences):
            return
        if self.base_page_img is None or self.canvas_img_id is None:
            return

        # Avoid redundant redraw if sentence and word haven't changed
        if sentence_idx == self.current_sentence_idx and word_idx == self.current_highlighted_word_idx:
            return

        sentence = self.sentences[sentence_idx]
        words = sentence.get("words", [])
        if not words:
            return

        img_w, img_h = self.base_page_img.size

        # Group words by line (using block and line numbers)
        lines_map = {}
        for w in words:
            key = (w.get("b_no", 0), w.get("l_no", 0))
            if key not in lines_map:
                lines_map[key] = []
            lines_map[key].append(w)

        line_boxes = []
        for line_words in lines_map.values():
            min_x = min(w["bbox"][0] for w in line_words)
            min_y = min(w["bbox"][1] for w in line_words)
            max_x = max(w["bbox"][2] for w in line_words)
            max_y = max(w["bbox"][3] for w in line_words)

            lx0 = max(0, int(round(min_x * self.zoom_scale)) - 2)
            ly0 = max(0, int(round(min_y * self.zoom_scale)) - 1)
            lx1 = min(img_w, int(round(max_x * self.zoom_scale)) + 2)
            ly1 = min(img_h, int(round(max_y * self.zoom_scale)) + 1)
            if lx1 > lx0 and ly1 > ly0:
                line_boxes.append((lx0, ly0, lx1, ly1))

        if not line_boxes:
            return

        # Active word box (vibrant highlighter yellow #ffff00)
        word_box = None
        if word_idx is not None and 0 <= word_idx < len(words):
            aw = words[word_idx]
            wx0 = max(0, int(round(aw["bbox"][0] * self.zoom_scale)) - 1)
            wy0 = max(0, int(round(aw["bbox"][1] * self.zoom_scale)) - 1)
            wx1 = min(img_w, int(round(aw["bbox"][2] * self.zoom_scale)) + 1)
            wy1 = min(img_h, int(round(aw["bbox"][3] * self.zoom_scale)) + 1)
            if wx1 > wx0 and wy1 > wy0:
                word_box = (wx0, wy0, wx1, wy1)

        # Bounding box for regional crop
        s_x0 = max(0, min(b[0] for b in line_boxes) - 2)
        s_y0 = max(0, min(b[1] for b in line_boxes) - 2)
        s_x1 = min(img_w, max(b[2] for b in line_boxes) + 2)
        s_y1 = min(img_h, max(b[3] for b in line_boxes) + 2)
        if word_box:
            s_x0 = min(s_x0, word_box[0])
            s_y0 = min(s_y0, word_box[1])
            s_x1 = max(s_x1, word_box[2])
            s_y1 = max(s_y1, word_box[3])

        if s_x1 <= s_x0 or s_y1 <= s_y0:
            return

        try:
            cropped_base = self.base_page_img.crop((s_x0, s_y0, s_x1, s_y1))
            mask = Image.new("RGB", (cropped_base.width, cropped_base.height), (255, 255, 255))
            draw = ImageDraw.Draw(mask)

            # Draw pastel soft blue tint (RGB 178, 214, 243 / #b2d6f3) for sentence lines
            for lx0, ly0, lx1, ly1 in line_boxes:
                draw.rectangle(
                    [lx0 - s_x0, ly0 - s_y0, lx1 - s_x0, ly1 - s_y0],
                    fill=(178, 214, 243)
                )

            # Draw bright highlighter yellow (RGB 255, 255, 0 / #ffff00) for active word
            if word_box:
                wx0, wy0, wx1, wy1 = word_box
                draw.rectangle(
                    [wx0 - s_x0, wy0 - s_y0, wx1 - s_x0, wy1 - s_y0],
                    fill=(255, 255, 0)
                )

            highlighted_crop = ImageChops.multiply(cropped_base, mask)
            full_img = self.base_page_img.copy()
            full_img.paste(highlighted_crop, (s_x0, s_y0))

            self.current_photo = ImageTk.PhotoImage(full_img)
            self.pdf_canvas.itemconfigure(self.canvas_img_id, image=self.current_photo)
            self.current_highlighted_word_idx = word_idx
        except Exception as err:
            print(f"[Highlight] Error: {err}")

        # Smooth auto-scroll only if the sentence is completely off-screen
        if sentence_idx != self.last_scrolled_sentence_idx:
            self.last_scrolled_sentence_idx = sentence_idx
            try:
                min_cy0 = 20 + s_y0
                max_cy1 = 20 + s_y1
                c_height = self.pdf_canvas.winfo_height()
                if c_height > 100:
                    vis_top = self.pdf_canvas.canvasy(0)
                    vis_bottom = vis_top + c_height
                    if min_cy0 < vis_top or max_cy1 > vis_bottom:
                        scroll_region = self.pdf_canvas.cget("scrollregion")
                        if scroll_region:
                            sr = [float(x) for x in scroll_region.split()]
                            total_h = max(1.0, sr[3])
                            target_y = max(0.0, (min_cy0 - 50) / total_h)
                            self.pdf_canvas.yview_moveto(min(1.0, target_y))
            except Exception:
                pass

    def _compute_word_timings(
        self,
        words: List[Dict[str, Any]],
        word_boundaries: List[Dict[str, Any]],
        total_dur_ms: float
    ) -> List[Tuple[float, float]]:
        """Maps speech engine word boundaries accurately to page words."""
        if not words:
            return []
        n = len(words)
        if not word_boundaries or total_dur_ms <= 0:
            avg = max(50.0, total_dur_ms / max(1, n))
            return [(i * avg, (i + 1) * avg) for i in range(n)]

        wb_clean = [re.sub(r'\W+', '', wb.get("word", "").lower()) for wb in word_boundaries]
        timings: List[Optional[Tuple[float, float]]] = [None] * n
        wb_ptr = 0

        for i, w in enumerate(words):
            target = w.get("clean", "")
            if not target:
                continue
            found_idx = -1
            # Search within a lookahead window
            for j in range(wb_ptr, min(wb_ptr + 8, len(wb_clean))):
                if wb_clean[j] == target:
                    found_idx = j
                    break
            if found_idx != -1:
                start_ms = float(word_boundaries[found_idx].get("offset_ms", 0.0))
                duration_ms = float(word_boundaries[found_idx].get("duration_ms", 250.0))
                end_ms = float(word_boundaries[found_idx].get("end_ms", start_ms + duration_ms))
                timings[i] = (start_ms, end_ms)
                wb_ptr = found_idx + 1

        # Interpolate any unaligned words smoothly between known timing anchors
        idx = 0
        last_end = 0.0
        while idx < n:
            if timings[idx] is None:
                next_start = total_dur_ms
                next_idx = n
                for j in range(idx + 1, n):
                    if timings[j] is not None:
                        next_start = timings[j][0]
                        next_idx = j
                        break
                count = next_idx - idx
                span = max(50.0, next_start - last_end)
                step = span / count
                for k in range(idx, next_idx):
                    s = last_end + (k - idx) * step
                    e = s + step
                    timings[k] = (s, e)
                idx = next_idx
            else:
                last_end = timings[idx][1]
                idx += 1

        return [(t[0], t[1]) for t in timings if t is not None]

    def _find_active_word_index(self, cur_time_ms: float) -> Optional[int]:
        """Finds the index of the word currently being spoken at cur_time_ms."""
        if not self.sentence_word_timings:
            return 0
        if cur_time_ms <= self.sentence_word_timings[0][0]:
            return 0
        if cur_time_ms >= self.sentence_word_timings[-1][1]:
            return len(self.sentence_word_timings) - 1

        for i, (start, end) in enumerate(self.sentence_word_timings):
            if start <= cur_time_ms < end:
                return i

        # In a pause between words, keep the previous word until next word begins
        for i in range(len(self.sentence_word_timings) - 1):
            if self.sentence_word_timings[i][1] <= cur_time_ms < self.sentence_word_timings[i + 1][0]:
                return i

        return len(self.sentence_word_timings) - 1

    def _zoom_in(self):
        self.zoom_mode = "custom"
        self.zoom_scale = min(3.0, self.zoom_scale + 0.15)
        self._render_current_page()

    def _zoom_out(self):
        self.zoom_mode = "custom"
        self.zoom_scale = max(0.5, self.zoom_scale - 0.15)
        self._render_current_page()

    def _zoom_fit_width(self):
        self.zoom_mode = "fit-width"
        self._render_current_page()

    def _prev_page(self):
        if self.current_page_idx > 0:
            self._load_page(self.current_page_idx - 1)

    def _next_page(self):
        if self.current_page_idx < self.total_pages - 1:
            self._load_page(self.current_page_idx + 1)

    def _jump_page_event(self, event):
        try:
            target = int(self.page_entry.get().strip()) - 1
            if 0 <= target < self.total_pages:
                self._load_page(target)
            else:
                self.page_entry.delete(0, "end")
                self.page_entry.insert(0, str(self.current_page_idx + 1))
        except ValueError:
            pass

    def _on_mouse_wheel(self, event):
        if event.delta:
            self.pdf_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _canvas_to_pdf_coords(self, cx: float, cy: float) -> Tuple[float, float]:
        px = (cx - 20) / self.zoom_scale
        py = (cy - 20) / self.zoom_scale
        return px, py

    def _find_word_at_coords(self, px: float, py: float) -> Optional[Tuple[int, int, Dict[str, Any]]]:
        for s in self.sentences:
            for w in s["words"]:
                x0, y0, x1, y1 = w["bbox"]
                if x0 - 2 <= px <= x1 + 2 and y0 - 2 <= py <= y1 + 2:
                    return s["s_idx"], w["w_idx"], w
        return None

    def _on_canvas_mouse_move(self, event):
        """Shows hand cursor when hovering over clickable words without obscuring text."""
        canvas_x = self.pdf_canvas.canvasx(event.x)
        canvas_y = self.pdf_canvas.canvasy(event.y)
        px, py = self._canvas_to_pdf_coords(canvas_x, canvas_y)
        match = self._find_word_at_coords(px, py)
        self.pdf_canvas.config(cursor="hand2" if match else "")

    def _on_canvas_mouse_click(self, event):
        """Clicking any word immediately plays from that word WITHOUT jumping the viewport."""
        canvas_x = self.pdf_canvas.canvasx(event.x)
        canvas_y = self.pdf_canvas.canvasy(event.y)
        px, py = self._canvas_to_pdf_coords(canvas_x, canvas_y)

        match = self._find_word_at_coords(px, py)
        if match:
            s_idx, w_idx, word = match
            self.play_sentence(s_idx, w_idx)

    def _toggle_playback(self):
        if self.is_synthesizing or self.is_playing:
            self._stop_audio()
        else:
            if self.app_mode == "notepad":
                self._parse_notepad_sentences()
                if not self.sentences:
                    self._set_status("Notepad is empty. Write or paste text to read.")
                    return
                char_pos = self._get_notepad_cursor_char_pos()
                s_idx, w_idx = self._find_notepad_word_at_char(char_pos)
                if s_idx is not None and s_idx != self.current_sentence_idx:
                    self.play_sentence(s_idx, w_idx)
                else:
                    self.play_sentence(self.current_sentence_idx, self.current_word_idx)
            else:
                self.play_sentence(self.current_sentence_idx, self.current_word_idx)

    def _prev_sentence(self):
        self._stop_audio()
        if self.current_sentence_idx > 0:
            self.play_sentence(self.current_sentence_idx - 1, 0)
        elif self.app_mode == "pdf" and self.current_page_idx > 0:
            self._load_page(self.current_page_idx - 1)
            last_idx = max(0, len(self.sentences) - 1)
            self.after(150, lambda: self.play_sentence(last_idx, 0))

    def _next_sentence(self):
        self._stop_audio()
        if self.current_sentence_idx < len(self.sentences) - 1:
            self.play_sentence(self.current_sentence_idx + 1, 0)
        elif self.app_mode == "pdf" and self.current_page_idx < self.total_pages - 1:
            self._load_page(self.current_page_idx + 1)
            self.after(150, lambda: self.play_sentence(0, 0))
        else:
            self.btn_play_pause.configure(text="▶ Play", fg_color="#2563eb")
            self._set_status("End of text reached.")

    def _on_scrubber_slide(self, val):
        idx = int(val)
        if 0 <= idx < len(self.sentences):
            self.play_sentence(idx, 0)

    def _prefetch_upcoming(self, current_idx: int):
        """Pre-synthesizes upcoming sentences in background to ensure zero pause between sentences."""
        def worker():
            for offset in [1, 2]:
                next_idx = current_idx + offset
                if next_idx < len(self.sentences):
                    sentence = self.sentences[next_idx]
                    raw_text = sentence["text"]
                    speech_input = re.sub(r'^[•\-\*]\s*', '', raw_text)
                    spoken_text, _ = rule_engine.apply_rules(speech_input)
                    text = spoken_text if spoken_text.strip() else speech_input
                    speed = round(float(self.speed_slider.get()), 2)
                    cache_key = f"{self.active_engine}|{self.active_voice_key}|{speed}|{text}"
                    if cache_key not in self.prefetch_cache:
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            if self.active_engine == "kokoro":
                                res = loop.run_until_complete(synthesize_kokoro(text, voice_key=self.active_voice_key, speed=speed))
                            else:
                                pct = int((speed - 1.0) * 100)
                                rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
                                res = loop.run_until_complete(synthesize_text(text, voice=self.active_voice_key, rate=rate_str))
                            loop.close()
                            if res:
                                self.prefetch_cache[cache_key] = res
                        except Exception:
                            pass

        threading.Thread(target=worker, daemon=True).start()

    def play_sentence(self, sentence_idx: int, start_word_idx: int = 0):
        """Synthesizes and plays sentence audio starting directly at the clicked word."""
        if not self.sentences or sentence_idx < 0 or sentence_idx >= len(self.sentences):
            return

        self._stop_audio()
        self.playback_seq += 1
        my_seq = self.playback_seq

        self.current_sentence_idx = sentence_idx
        self.current_word_idx = start_word_idx
        self.is_playing = True
        self.is_synthesizing = True

        # Highlight the full sentence immediately on canvas
        self._highlight_sentence(sentence_idx, start_word_idx)

        self.btn_play_pause.configure(text="⏳...", fg_color="#d97706")
        self.scrubber_slider.set(sentence_idx)
        sentence = self.sentences[sentence_idx]
        self._set_status(f"[{sentence_idx + 1}/{len(self.sentences)}] {sentence['text']}")

        # Pre-fetch upcoming sentences in background
        self._prefetch_upcoming(sentence_idx)

        # Worker thread for TTS synthesis and playback
        threading.Thread(
            target=self._synthesis_and_play_worker,
            args=(sentence_idx, start_word_idx, my_seq),
            daemon=True
        ).start()

    def _synthesis_and_play_worker(self, sentence_idx: int, start_word_idx: int, my_seq: int):
        try:
            sentence = self.sentences[sentence_idx]
            raw_text = sentence["text"]
            speech_input = re.sub(r'^[•\-\*]\s*', '', raw_text)
            spoken_text, _ = rule_engine.apply_rules(speech_input)
            text = spoken_text if spoken_text.strip() else speech_input
            engine = self.active_engine
            voice_key = self.active_voice_key
            speed = round(float(self.speed_slider.get()), 2)
            cache_key = f"{engine}|{voice_key}|{speed}|{text}"

            tts_data = self.prefetch_cache.get(cache_key)
            if not tts_data:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    if engine == "kokoro":
                        try:
                            tts_data = loop.run_until_complete(
                                synthesize_kokoro(text, voice_key=voice_key, speed=speed)
                            )
                        except Exception as k_err:
                            pct = int((speed - 1.0) * 100)
                            rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
                            tts_data = loop.run_until_complete(
                                synthesize_text(text, voice="en-US-JennyNeural", rate=rate_str)
                            )
                    else:
                        pct = int((speed - 1.0) * 100)
                        rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
                        tts_data = loop.run_until_complete(
                            synthesize_text(text, voice=voice_key, rate=rate_str)
                        )
                    if tts_data:
                        self.prefetch_cache[cache_key] = tts_data
                finally:
                    loop.close()

            if self.playback_seq != my_seq:
                return

            if not tts_data or not tts_data.get("cache_id"):
                self.after(0, self._on_playback_ended)
                return

            cache_id = tts_data["cache_id"]
            mp3_path = CACHE_DIR / f"{cache_id}.mp3"
            wav_path = CACHE_DIR / f"{cache_id}.wav"
            audio_path = mp3_path if mp3_path.exists() else wav_path

            if not audio_path.exists():
                self.after(0, self._on_playback_ended)
                return

            # Read audio data into memory
            data, sr = sf.read(str(audio_path), dtype="float32")
            total_samples = len(data)
            total_dur_ms = (total_samples / float(sr)) * 1000.0

            self.word_boundaries = tts_data.get("word_boundaries", [])
            self.sentence_word_timings = self._compute_word_timings(
                sentence.get("words", []),
                self.word_boundaries,
                total_dur_ms
            )

            # Calculate seek position with precision regex and fallback index
            seek_ms = 0.0
            if start_word_idx > 0 and len(sentence["words"]) > start_word_idx:
                target_word = sentence["words"][start_word_idx]
                target_clean = re.sub(r'\W+', '', target_word["text"].lower())
                found = False

                if target_clean and self.word_boundaries:
                    target_occ = sum(1 for w in sentence["words"][:start_word_idx + 1] if re.sub(r'\W+', '', w["text"].lower()) == target_clean)
                    cur_occ = 0
                    for wb in self.word_boundaries:
                        wb_clean = re.sub(r'\W+', '', wb.get("word", "").lower())
                        if wb_clean == target_clean:
                            cur_occ += 1
                            if cur_occ == target_occ:
                                seek_ms = wb.get("offset_ms", 0.0)
                                found = True
                                break

                if not found and self.word_boundaries:
                    ratio = start_word_idx / float(len(sentence["words"]))
                    wb_idx = min(len(self.word_boundaries) - 1, int(ratio * len(self.word_boundaries)))
                    seek_ms = self.word_boundaries[wb_idx].get("offset_ms", 0.0)

            start_sample = int((seek_ms / 1000.0) * sr)
            if start_sample < total_samples:
                play_data = data[start_sample:]
            else:
                play_data = data
                seek_ms = 0.0

            self.audio_data = play_data
            self.audio_sr = sr
            self.seek_offset_ms = seek_ms
            self.total_sentence_dur_ms = total_dur_ms
            self.start_playback_time = time.time()

            # Stop any residual sound and play slice cleanly
            try:
                sd.stop()
            except Exception:
                pass
            sd.play(play_data, sr)

            self.after(0, lambda: self._on_playback_started(my_seq))

        except Exception as e:
            print(f"[DesktopApp] Playback error: {e}")
            if self.playback_seq == my_seq:
                self.after(0, self._stop_audio)
        finally:
            if self.playback_seq == my_seq and not self.is_playing:
                self.after(0, self._stop_audio)

    def _on_playback_started(self, seq: int):
        if self.playback_seq == seq:
            self.is_synthesizing = False
            self.is_playing = True
            self.btn_play_pause.configure(text="⏸ Pause", fg_color="#10b981", hover_color="#059669")

    def _audio_sync_loop(self):
        """Continuously checks audio completion and updates real-time word highlight."""
        if getattr(self, "_is_closing", False):
            return
        try:
            if not self.winfo_exists():
                return
            if self.is_playing and not self.is_synthesizing and self.audio_data is not None:
                play_dur_sec = len(self.audio_data) / float(self.audio_sr)
                elapsed_sec = time.time() - self.start_playback_time

                if elapsed_sec >= (play_dur_sec + 0.06):
                    self._on_playback_ended()
                else:
                    cur_time_ms = self.seek_offset_ms + (elapsed_sec * 1000.0)
                    active_word_idx = self._find_active_word_index(cur_time_ms)
                    if active_word_idx is not None and active_word_idx != self.current_highlighted_word_idx:
                        self.current_word_idx = active_word_idx
                        self._highlight_sentence(self.current_sentence_idx, active_word_idx)
        except Exception:
            pass
        finally:
            if not getattr(self, "_is_closing", False):
                try:
                    self.after(30, self._audio_sync_loop)
                except Exception:
                    pass

    def _on_playback_ended(self):
        """Auto-advances to the next sentence seamlessly with zero delay."""
        self._stop_audio()
        if self.current_sentence_idx < len(self.sentences) - 1:
            self.play_sentence(self.current_sentence_idx + 1, 0)
        elif self.app_mode == "pdf" and self.current_page_idx < self.total_pages - 1:
            self._load_page(self.current_page_idx + 1)
            self.after(100, lambda: self.play_sentence(0, 0))
        else:
            self.btn_play_pause.configure(text="▶ Play", fg_color="#2563eb")
            self._set_status("Finished reading.")

    def _stop_audio(self):
        self.playback_seq += 1
        self.is_playing = False
        self.is_synthesizing = False
        try:
            sd.stop()
        except Exception:
            pass
        self._clear_sentence_highlight()
        try:
            self.btn_play_pause.configure(text="▶ Play", fg_color="#2563eb")
        except Exception:
            pass


if __name__ == "__main__":
    app = SpeakaloudDesktopApp()
    app.mainloop()
