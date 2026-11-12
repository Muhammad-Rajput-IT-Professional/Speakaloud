import asyncio
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import edge_tts
from app.config import CACHE_DIR, DEFAULT_VOICE, DEFAULT_RATE, DEFAULT_PITCH, DEFAULT_VOLUME
from app.speech_rules import rule_engine

_VOICE_CACHE: Optional[List[Dict[str, Any]]] = None


async def get_all_voices() -> List[Dict[str, Any]]:
    """
    Fetch all available Microsoft Edge Cloud Neural Voices and enrich with structured fields.
    """
    global _VOICE_CACHE
    if _VOICE_CACHE is not None:
        return _VOICE_CACHE

    cache_file = CACHE_DIR / "voices.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _VOICE_CACHE = json.load(f)
                return _VOICE_CACHE
        except Exception:
            pass

    try:
        raw_voices = await edge_tts.list_voices()
        formatted = []
        for v in raw_voices:
            short_name = v.get("ShortName", "")
            friendly_name = v.get("FriendlyName", "")
            gender = v.get("Gender", "Unknown")
            locale = v.get("Locale", "")
            
            # Extract language name and country from friendly name
            # e.g. "Microsoft Jenny Online (Natural) - English (United States)"
            lang_country = ""
            if " - " in friendly_name:
                lang_country = friendly_name.split(" - ")[-1]

            formatted.append({
                "name": short_name,
                "friendly_name": friendly_name,
                "gender": gender,
                "locale": locale,
                "display_name": f"{short_name} ({gender}, {locale})",
                "label": f"{friendly_name}",
                "description": lang_country
            })

        # Sort: English first, then alphabetically by locale and name
        def sort_key(item):
            loc = item["locale"]
            is_en = loc.startswith("en-")
            return (0 if is_en else 1, loc, item["name"])

        formatted.sort(key=sort_key)
        _VOICE_CACHE = formatted

        # Persist to cache file
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(_VOICE_CACHE, f, indent=2)
        except Exception as e:
            print(f"[TTS] Warning: could not cache voices to disk: {e}")

        return _VOICE_CACHE
    except Exception as e:
        print(f"[TTS] Error fetching voices: {e}")
        # Fallback to standard Edge neural voices if offline
        fallback = [
            {"name": "en-US-JennyNeural", "friendly_name": "Microsoft Jenny - English (US)", "gender": "Female", "locale": "en-US"},
            {"name": "en-US-GuyNeural", "friendly_name": "Microsoft Guy - English (US)", "gender": "Male", "locale": "en-US"},
            {"name": "en-US-AriaNeural", "friendly_name": "Microsoft Aria - English (US)", "gender": "Female", "locale": "en-US"},
            {"name": "en-GB-SoniaNeural", "friendly_name": "Microsoft Sonia - English (UK)", "gender": "Female", "locale": "en-GB"},
            {"name": "en-GB-RyanNeural", "friendly_name": "Microsoft Ryan - English (UK)", "gender": "Male", "locale": "en-GB"}
        ]
        return fallback


def compute_audio_cache_key(text: str, voice: str, rate: str, pitch: str, volume: str, speed: float = 1.0) -> str:
    """Create unique hash for TTS generation parameters."""
    raw = f"{text}|{voice}|{rate}|{pitch}|{volume}|{speed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


async def synthesize_text(
    original_text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    volume: str = DEFAULT_VOLUME,
    apply_rules: bool = True,
    speed: float = 1.0
) -> Dict[str, Any]:
    """
    Synthesizes sentence text using Microsoft Edge Cloud Voices with word boundary tracking.
    Applies custom speech rules (e.g. 'PBUH' -> 'peace be upon him', regex citations removal).
    Caches audio to prevent redundant network calls.
    """
    if isinstance(rate, (int, float)):
        if speed == 1.0 and rate != 1.0:
            speed = float(rate)
        rate = f"{int(round((float(rate) - 1.0) * 100)):+d}%"
    elif isinstance(rate, str) and rate.endswith("%") and speed == 1.0:
        try:
            pct = int(rate.replace("%", "").replace("+", ""))
            computed_spd = 1.0 + (pct / 100.0)
            if computed_spd > 1.0:
                speed = round(computed_spd, 2)
        except Exception:
            pass
    elif speed != 1.0 and rate == DEFAULT_RATE:
        rate = f"{int(round((min(speed, 2.0) - 1.0) * 100)):+d}%"
    # 1. Apply speech & regex rules
    if apply_rules:
        speech_text, applied_rules = rule_engine.apply_rules(original_text)
    else:
        speech_text = original_text
        applied_rules = []

    # Ensure all whitespace/newlines within sentence are collapsed to single space for fluent speech
    import re
    speech_text = re.sub(r"\s+", " ", speech_text).strip()

    # If the text became empty (e.g. sentence only contained a removed citation [1]), return silence info
    if not speech_text.strip():
        return {
            "cache_id": "empty",
            "audio_url": None,
            "word_boundaries": [],
            "original_text": original_text,
            "speech_text": "",
            "rules_applied": applied_rules,
            "is_empty": True
        }

    # 2. Check cache (checking both .wav and .mp3 for post-stretched rates)
    cache_id = compute_audio_cache_key(speech_text, voice, rate, pitch, volume, speed)
    audio_wav = CACHE_DIR / f"{cache_id}.wav"
    audio_mp3 = CACHE_DIR / f"{cache_id}.mp3"
    audio_path = audio_wav if audio_wav.exists() else audio_mp3
    meta_path = CACHE_DIR / f"{cache_id}.json"

    if (audio_wav.exists() or audio_mp3.exists()) and meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            return {
                "cache_id": cache_id,
                "audio_url": f"/api/tts/audio/{cache_id}",
                "word_boundaries": cached_data.get("word_boundaries", []),
                "original_text": original_text,
                "speech_text": speech_text,
                "rules_applied": applied_rules,
                "is_empty": False
            }
        except Exception as e:
            print(f"[TTS] Cache read error for {cache_id}: {e}")

    # 3. Call Microsoft Edge Cloud Voices via edge_tts
    # If speed > 2.0x, Edge TTS caps at +100% (2.0x). We synthesize at +100% and scale the remainder via SOLA
    post_factor = round(speed / 2.0, 3) if speed > 2.0 else 1.0
    edge_rate = "+100%" if speed > 2.0 else rate

    max_retries = 2
    audio_buffer = bytearray()
    word_boundaries = []

    for attempt in range(max_retries):
        try:
            comm = edge_tts.Communicate(
                text=speech_text,
                voice=voice,
                rate=edge_rate,
                pitch=pitch,
                volume=volume,
                boundary="WordBoundary",
                receive_timeout=15
            )
            audio_buffer.clear()
            word_boundaries.clear()

            async for chunk in comm.stream():
                chunk_type = chunk.get("type")
                if chunk_type == "audio":
                    audio_buffer.extend(chunk.get("data", b""))
                elif chunk_type == "WordBoundary":
                    # offset and duration in 100ns units -> convert to milliseconds
                    offset_ms = chunk.get("offset", 0) / 10000.0
                    duration_ms = chunk.get("duration", 0) / 10000.0
                    word_boundaries.append({
                        "word": chunk.get("text", ""),
                        "offset_ms": round(offset_ms, 2),
                        "duration_ms": round(duration_ms, 2),
                        "end_ms": round(offset_ms + duration_ms, 2)
                    })
            if audio_buffer:
                break
        except Exception as conn_err:
            if attempt < max_retries - 1:
                print(f"[EdgeTTS] Connection glitch ({conn_err}), retrying...")
                await asyncio.sleep(0.5)
            else:
                raise conn_err

    # 4. Save to cache with SOLA time-stretching if speed > 2.0x
    try:
        if post_factor > 1.0 and audio_buffer:
            import io
            import soundfile as sf
            from app.audio_dsp import time_stretch_sola, scale_word_boundaries
            raw_data, sr = sf.read(io.BytesIO(audio_buffer), dtype="float32")
            stretched_data = time_stretch_sola(raw_data, post_factor, sr)
            out_target = CACHE_DIR / f"{cache_id}.wav"
            sf.write(str(out_target), stretched_data, sr, format="WAV")
            word_boundaries = scale_word_boundaries(word_boundaries, post_factor)
        else:
            out_target = CACHE_DIR / f"{cache_id}.mp3"
            with open(out_target, "wb") as f:
                f.write(audio_buffer)
        
        meta = {
            "cache_id": cache_id,
            "voice": voice,
            "rate": rate,
            "speed": speed,
            "pitch": pitch,
            "volume": volume,
            "speech_text": speech_text,
            "word_boundaries": word_boundaries
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"[TTS] Cache write error for {cache_id}: {e}")

    return {
        "cache_id": cache_id,
        "audio_url": f"/api/tts/audio/{cache_id}",
        "word_boundaries": word_boundaries,
        "original_text": original_text,
        "speech_text": speech_text,
        "rules_applied": applied_rules,
        "is_empty": False
    }


def get_cached_audio_path(cache_id: str) -> Optional[Path]:
    """Return path to cached audio file if it exists."""
    p = CACHE_DIR / f"{cache_id}.mp3"
    if p.exists():
        return p
    return None
