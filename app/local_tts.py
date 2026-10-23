"""
Speakaloud Local TTS Engine Manager
Supports Kokoro-82M and Piper TTS with on-demand model download and local ONNX synthesis.
"""
import os
import sys
import json
import asyncio
import hashlib
import threading
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional
import soundfile as sf
import numpy as np

from app.config import DATA_DIR, CACHE_DIR
from app.speech_rules import rule_engine

MODELS_DIR = DATA_DIR / "models"
KOKORO_DIR = MODELS_DIR / "kokoro"
PIPER_DIR = MODELS_DIR / "piper"

for d in [MODELS_DIR, KOKORO_DIR, PIPER_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Catalog of supported local models
KOKORO_VOICES = [
    {"id": "kokoro_af_bella", "voice_key": "af_bella", "name": "Kokoro - Bella", "gender": "Female", "accent": "American", "sample_desc": "Warm & expressive American female"},
    {"id": "kokoro_af_sarah", "voice_key": "af_sarah", "name": "Kokoro - Sarah", "gender": "Female", "accent": "American", "sample_desc": "Clear & professional American female"},
    {"id": "kokoro_am_adam", "voice_key": "am_adam", "name": "Kokoro - Adam", "gender": "Male", "accent": "American", "sample_desc": "Natural & calm American male"},
    {"id": "kokoro_am_michael", "voice_key": "am_michael", "name": "Kokoro - Michael", "gender": "Male", "accent": "American", "sample_desc": "Authoritative & deep American male"},
    {"id": "kokoro_bf_emma", "voice_key": "bf_emma", "name": "Kokoro - Emma", "gender": "Female", "accent": "British", "sample_desc": "Refined British female narrator"},
    {"id": "kokoro_bf_isabella", "voice_key": "bf_isabella", "name": "Kokoro - Isabella", "gender": "Female", "accent": "British", "sample_desc": "Gentle British female voice"},
    {"id": "kokoro_bm_george", "voice_key": "bm_george", "name": "Kokoro - George", "gender": "Male", "accent": "British", "sample_desc": "Classic British audiobook narrator"},
    {"id": "kokoro_bm_lewis", "voice_key": "bm_lewis", "name": "Kokoro - Lewis", "gender": "Male", "accent": "British", "sample_desc": "Warm conversational British male"}
]

PIPER_VOICES = [
    {
        "id": "piper_en_US_lessac_medium",
        "voice_key": "en_US-lessac-medium",
        "name": "Piper - Lessac",
        "gender": "Female",
        "accent": "American",
        "sample_desc": "Clear, fast American English audiobook voice (63MB)",
        "onnx_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
    },
    {
        "id": "piper_en_US_amy_medium",
        "voice_key": "en_US-amy-medium",
        "name": "Piper - Amy",
        "gender": "Female",
        "accent": "American",
        "sample_desc": "Natural conversational American female (60MB)",
        "onnx_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    },
    {
        "id": "piper_en_US_ryan_medium",
        "voice_key": "en_US-ryan-medium",
        "name": "Piper - Ryan",
        "gender": "Male",
        "accent": "American",
        "sample_desc": "Crisp American male narrator (60MB)",
        "onnx_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json"
    }
]

# Download tracking
_DOWNLOAD_PROGRESS: Dict[str, Dict[str, Any]] = {}
_KOKORO_INSTANCE = None
_KOKORO_LOCK = threading.Lock()
_PIPER_INSTANCES: Dict[str, Any] = {}


def is_kokoro_downloaded() -> bool:
    model_path = KOKORO_DIR / "kokoro-v1.0.onnx"
    voices_path = KOKORO_DIR / "voices-v1.0.bin"
    return model_path.exists() and voices_path.exists() and model_path.stat().st_size > 50_000_000


def is_piper_voice_downloaded(voice_key: str) -> bool:
    onnx_path = PIPER_DIR / f"{voice_key}.onnx"
    json_path = PIPER_DIR / f"{voice_key}.onnx.json"
    return onnx_path.exists() and json_path.exists() and onnx_path.stat().st_size > 10_000_000


def get_local_catalog() -> Dict[str, Any]:
    """Returns local engines and voices with installation status."""
    kokoro_installed = is_kokoro_downloaded()
    kokoro_list = []
    for v in KOKORO_VOICES:
        item = dict(v)
        item["engine"] = "kokoro"
        item["installed"] = kokoro_installed
        item["progress"] = _DOWNLOAD_PROGRESS.get("kokoro", {}).get("percent", 0 if not kokoro_installed else 100)
        item["status"] = _DOWNLOAD_PROGRESS.get("kokoro", {}).get("status", "ready" if kokoro_installed else "not_installed")
        kokoro_list.append(item)

    piper_list = []
    for v in PIPER_VOICES:
        item = dict(v)
        item["engine"] = "piper"
        installed = is_piper_voice_downloaded(v["voice_key"])
        item["installed"] = installed
        item["progress"] = _DOWNLOAD_PROGRESS.get(v["voice_key"], {}).get("percent", 0 if not installed else 100)
        item["status"] = _DOWNLOAD_PROGRESS.get(v["voice_key"], {}).get("status", "ready" if installed else "not_installed")
        piper_list.append(item)

    return {
        "kokoro": {
            "name": "Kokoro-82M",
            "desc": "Top-tier 82M open-weight neural model. Incredible human cadence.",
            "installed": kokoro_installed,
            "voices": kokoro_list
        },
        "piper": {
            "name": "Piper TTS",
            "desc": "Lightweight, ultra-fast local neural TTS trained by Rhasspy.",
            "voices": piper_list
        }
    }


def download_file(url: str, dest_path: Path, progress_key: str, sub_weight: float = 1.0, sub_offset: float = 0.0):
    """Download file with real-time percentage tracking."""
    req = urllib.request.Request(url, headers={"User-Agent": "Speakaloud-TTS-Downloader/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 1024 * 512
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = round(sub_offset + (downloaded / total_size) * sub_weight * 100, 1)
                    if progress_key in _DOWNLOAD_PROGRESS:
                        _DOWNLOAD_PROGRESS[progress_key]["percent"] = min(99.9, pct)


def start_download_task(engine: str, voice_key: Optional[str] = None) -> Dict[str, Any]:
    """Start background download thread for requested engine model."""
    if engine == "kokoro":
        if is_kokoro_downloaded():
            return {"status": "ready", "percent": 100}
        
        if "kokoro" in _DOWNLOAD_PROGRESS and _DOWNLOAD_PROGRESS["kokoro"].get("status") == "downloading":
            return _DOWNLOAD_PROGRESS["kokoro"]

        _DOWNLOAD_PROGRESS["kokoro"] = {"status": "downloading", "percent": 0.0}

        def worker():
            try:
                model_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
                voices_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
                
                # Model is 92% of payload, voices is 8%
                download_file(model_url, KOKORO_DIR / "kokoro-v1.0.onnx", "kokoro", sub_weight=0.92, sub_offset=0.0)
                download_file(voices_url, KOKORO_DIR / "voices-v1.0.bin", "kokoro", sub_weight=0.08, sub_offset=92.0)
                
                _DOWNLOAD_PROGRESS["kokoro"] = {"status": "ready", "percent": 100.0}
            except Exception as e:
                print(f"[LocalTTS] Kokoro download error: {e}")
                _DOWNLOAD_PROGRESS["kokoro"] = {"status": "error", "error": str(e), "percent": 0.0}

        threading.Thread(target=worker, daemon=True).start()
        return _DOWNLOAD_PROGRESS["kokoro"]

    elif engine == "piper":
        if not voice_key:
            voice_key = "en_US-lessac-medium"

        if is_piper_voice_downloaded(voice_key):
            return {"status": "ready", "percent": 100}

        if voice_key in _DOWNLOAD_PROGRESS and _DOWNLOAD_PROGRESS[voice_key].get("status") == "downloading":
            return _DOWNLOAD_PROGRESS[voice_key]

        target_info = next((v for v in PIPER_VOICES if v["voice_key"] == voice_key), None)
        if not target_info:
            return {"status": "error", "error": f"Voice {voice_key} not in catalog"}

        _DOWNLOAD_PROGRESS[voice_key] = {"status": "downloading", "percent": 0.0}

        def worker_piper():
            try:
                onnx_dest = PIPER_DIR / f"{voice_key}.onnx"
                json_dest = PIPER_DIR / f"{voice_key}.onnx.json"
                download_file(target_info["onnx_url"], onnx_dest, voice_key, sub_weight=0.98, sub_offset=0.0)
                download_file(target_info["json_url"], json_dest, voice_key, sub_weight=0.02, sub_offset=98.0)
                _DOWNLOAD_PROGRESS[voice_key] = {"status": "ready", "percent": 100.0}
            except Exception as e:
                print(f"[LocalTTS] Piper download error: {e}")
                _DOWNLOAD_PROGRESS[voice_key] = {"status": "error", "error": str(e), "percent": 0.0}

        threading.Thread(target=worker_piper, daemon=True).start()
        return _DOWNLOAD_PROGRESS[voice_key]

    return {"status": "error", "error": "Unknown engine"}


def get_kokoro_instance():
    global _KOKORO_INSTANCE
    with _KOKORO_LOCK:
        if _KOKORO_INSTANCE is not None:
            return _KOKORO_INSTANCE

        if not is_kokoro_downloaded():
            return None

        try:
            from kokoro_onnx import Kokoro
            model_path = str(KOKORO_DIR / "kokoro-v1.0.onnx")
            voices_path = str(KOKORO_DIR / "voices-v1.0.bin")
            _KOKORO_INSTANCE = Kokoro(model_path, voices_path)
            return _KOKORO_INSTANCE
        except Exception as e:
            print(f"[LocalTTS] Could not initialize Kokoro: {e}")
            return None


def get_piper_instance(voice_key: str):
    global _PIPER_INSTANCES
    if voice_key in _PIPER_INSTANCES:
        return _PIPER_INSTANCES[voice_key]

    if not is_piper_voice_downloaded(voice_key):
        return None

    try:
        from piper import PiperVoice
        onnx_path = str(PIPER_DIR / f"{voice_key}.onnx")
        json_path = str(PIPER_DIR / f"{voice_key}.onnx.json")
        voice = PiperVoice.load(onnx_path, config_path=json_path)
        _PIPER_INSTANCES[voice_key] = voice
        return voice
    except Exception as e:
        print(f"[LocalTTS] Could not initialize Piper voice {voice_key}: {e}")
        return None


def estimate_word_boundaries(text: str, total_duration_sec: float) -> List[Dict[str, Any]]:
    """Calculates clean proportional word boundary timestamps for synchronized highlighting."""
    words = text.strip().split()
    if not words:
        return []

    # Weight each word duration proportionally by its length + base pause
    weights = [max(1, len(w)) + 1 for w in words]
    total_weight = sum(weights)

    boundaries = []
    current_ms = 80.0
    usable_ms = max(100.0, (total_duration_sec * 1000.0) - 160.0)

    for i, w in enumerate(words):
        dur_ms = round((weights[i] / total_weight) * usable_ms, 2)
        boundaries.append({
            "word": w,
            "offset_ms": round(current_ms, 2),
            "duration_ms": dur_ms,
            "end_ms": round(current_ms + dur_ms, 2)
        })
        current_ms += dur_ms

    return boundaries


def chunk_text_for_kokoro(text: str, max_chars: int = 220) -> List[str]:
    """
    Split text into natural clause-based chunks to keep phoneme count well below
    Kokoro's 510 limit, preventing 'index 510 is out of bounds' errors.
    """
    import re
    if len(text) <= max_chars:
        return [text]

    parts = re.split(r'([.?!:;,\n—]+(?:\s+|$))', text)
    chunks = []
    current = ""

    for p in parts:
        if not p:
            continue
        if len(current) + len(p) <= max_chars:
            current += p
        else:
            if current.strip():
                chunks.append(current.strip())
            current = p

    if current.strip():
        chunks.append(current.strip())

    final_chunks = []
    for c in chunks:
        if len(c) <= max_chars:
            final_chunks.append(c)
        else:
            words = c.split()
            sub = ""
            for w in words:
                if len(sub) + len(w) + 1 <= max_chars:
                    sub += (" " if sub else "") + w
                else:
                    if sub:
                        final_chunks.append(sub)
                    sub = w
            if sub:
                final_chunks.append(sub)

    return [fc for fc in final_chunks if fc.strip()]


async def synthesize_kokoro(text: str, voice_key: str = "af_bella", speed: float = 1.0) -> Dict[str, Any]:
    """Synthesize speech using Kokoro-82M ONNX model with auto-chunking."""
    import soundfile as sf
    import numpy as np
    kokoro = get_kokoro_instance()
    if kokoro is None:
        raise RuntimeError("Kokoro model files are not downloaded yet.")

    speech_text, rules = rule_engine.apply_rules(text)
    if not speech_text.strip():
        return {"audio_url": None, "word_boundaries": [], "speech_text": "", "is_empty": True}

    cache_hash = hashlib.sha256(f"kokoro|{speech_text}|{voice_key}|{speed}".encode()).hexdigest()[:20]
    out_mp3 = CACHE_DIR / f"{cache_hash}.mp3"
    meta_json = CACHE_DIR / f"{cache_hash}.json"

    if out_mp3.exists() and meta_json.exists():
        with open(meta_json, "r", encoding="utf-8") as f:
            cached = json.load(f)
        return {
            "cache_id": cache_hash,
            "audio_url": f"/api/tts/audio/{cache_hash}",
            "word_boundaries": cached.get("word_boundaries", []),
            "speech_text": speech_text,
            "is_empty": False
        }

    # Split into safe chunks to prevent Kokoro 510-phoneme out-of-bounds error
    chunks = chunk_text_for_kokoro(speech_text, max_chars=220)

    loop = asyncio.get_event_loop()

    def generate_kokoro():
        all_samples = []
        sr = 24000
        for c in chunks:
            with _KOKORO_LOCK:
                s, sr = kokoro.create(c, voice=voice_key, speed=speed, lang="en-us")
            all_samples.append(s)
            if len(chunks) > 1:
                silence = np.zeros(int(sr * 0.08), dtype=np.float32)
                all_samples.append(silence)
        if not all_samples:
            return np.zeros(int(sr * 0.5), dtype=np.float32), sr
        return np.concatenate(all_samples), sr

    samples, sample_rate = await loop.run_in_executor(None, generate_kokoro)

    # Save to audio file
    temp_wav = CACHE_DIR / f"{cache_hash}.wav"
    sf.write(str(temp_wav), samples, sample_rate, format="WAV")

    # Transcode or keep as mp3
    import subprocess
    try:
        subprocess.run(f"ffmpeg -y -i \"{temp_wav}\" -codec:a libmp3lame -b:a 128k \"{out_mp3}\"", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if temp_wav.exists() and out_mp3.exists():
            temp_wav.unlink()
    except Exception:
        out_mp3 = temp_wav

    duration_sec = len(samples) / float(sample_rate)
    boundaries = estimate_word_boundaries(speech_text, duration_sec)

    with open(meta_json, "w", encoding="utf-8") as f:
        json.dump({"word_boundaries": boundaries, "duration_sec": duration_sec}, f, indent=2)

    return {
        "cache_id": cache_hash,
        "audio_url": f"/api/tts/audio/{cache_hash}",
        "word_boundaries": boundaries,
        "speech_text": speech_text,
        "is_empty": False
    }


async def synthesize_piper(text: str, voice_key: str = "en_US-lessac-medium", speed: float = 1.0) -> Dict[str, Any]:
    """Synthesize speech using Piper TTS ONNX model."""
    piper_voice = get_piper_instance(voice_key)
    if piper_voice is None:
        raise RuntimeError(f"Piper voice '{voice_key}' model is not downloaded yet.")

    speech_text, rules = rule_engine.apply_rules(text)
    if not speech_text.strip():
        return {"audio_url": None, "word_boundaries": [], "is_empty": True}

    cache_hash = hashlib.sha256(f"piper|{speech_text}|{voice_key}|{speed}".encode()).hexdigest()[:20]
    out_file = CACHE_DIR / f"{cache_hash}.wav"
    meta_json = CACHE_DIR / f"{cache_hash}.json"

    if out_file.exists() and meta_json.exists():
        with open(meta_json, "r", encoding="utf-8") as f:
            cached = json.load(f)
        return {
            "cache_id": cache_hash,
            "audio_url": f"/api/tts/audio/{cache_hash}",
            "word_boundaries": cached.get("word_boundaries", []),
            "speech_text": speech_text,
            "is_empty": False
        }

    loop = asyncio.get_event_loop()
    import wave
    
    def generate_piper():
        with wave.open(str(out_file), "wb") as wav_file:
            piper_voice.synthesize(speech_text, wav_file)
        with wave.open(str(out_file), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate)

    duration_sec = await loop.run_in_executor(None, generate_piper)
    boundaries = estimate_word_boundaries(speech_text, duration_sec)

    with open(meta_json, "w", encoding="utf-8") as f:
        json.dump({"word_boundaries": boundaries, "duration_sec": duration_sec}, f, indent=2)

    return {
        "cache_id": cache_hash,
        "audio_url": f"/api/tts/audio/{cache_hash}",
        "word_boundaries": boundaries,
        "speech_text": speech_text,
        "is_empty": False
    }
