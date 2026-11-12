"""
High-performance, pitch-preserving audio time-scaling (SOLA) for Speakaloud.
Enables true high-speed playback (up to 3.0x and beyond) across all TTS engines
without pitch distortion or external binary dependencies.
"""

import numpy as np
from typing import List, Dict, Any


def time_stretch_sola(signal: np.ndarray, speed_factor: float, sr: int = 24000) -> np.ndarray:
    """
    Time-scale audio signal by speed_factor using SOLA (Synchronized Overlap-Add).
    Preserves pitch and natural formants cleanly with zero dependencies outside NumPy.
    """
    if abs(speed_factor - 1.0) < 0.01:
        return signal

    # 25ms speech analysis window
    win_len = int(sr * 0.025)
    if win_len % 2 != 0:
        win_len += 1
    hop_syn = win_len // 2
    hop_ana = int(hop_syn * speed_factor)

    # Search window for maximum correlation (±5ms)
    max_delta = int(sr * 0.005)
    window = np.hanning(win_len).astype(np.float32)

    num_samples = len(signal)
    num_frames = (num_samples - win_len - max_delta) // hop_ana
    if num_frames <= 0:
        return signal

    out_len = int(num_samples / speed_factor) + win_len * 2
    output = np.zeros(out_len, dtype=np.float32)
    norm = np.zeros(out_len, dtype=np.float32)

    # First frame
    output[0:win_len] = signal[0:win_len] * window
    norm[0:win_len] = window

    syn_pos = hop_syn
    ana_pos = hop_ana

    for _ in range(1, num_frames):
        if ana_pos + win_len > num_samples:
            break

        prev_overlap = output[syn_pos : syn_pos + win_len]

        best_delta = 0
        best_corr = -1e9
        start_d = max(-max_delta, -ana_pos)
        end_d = min(max_delta, num_samples - (ana_pos + win_len))

        # Correlation search for seamless phase alignment
        for d in range(start_d, end_d + 1, 2):
            cand = signal[ana_pos + d : ana_pos + d + win_len]
            corr = np.dot(prev_overlap, cand)
            if corr > best_corr:
                best_corr = corr
                best_delta = d

        aligned = signal[ana_pos + best_delta : ana_pos + best_delta + win_len] * window
        output[syn_pos : syn_pos + win_len] += aligned
        norm[syn_pos : syn_pos + win_len] += window

        syn_pos += hop_syn
        ana_pos += hop_ana

    nz = norm > 1e-4
    output[nz] /= norm[nz]
    return output[:syn_pos]


def scale_word_boundaries(boundaries: List[Dict[str, Any]], speed_factor: float) -> List[Dict[str, Any]]:
    """Scale word boundary timing offsets proportionally with time-scaled audio."""
    if abs(speed_factor - 1.0) < 0.01 or not boundaries:
        return boundaries

    scaled = []
    inv = 1.0 / speed_factor
    for b in boundaries:
        off = round(b.get("offset_ms", 0.0) * inv, 2)
        dur = round(b.get("duration_ms", 0.0) * inv, 2)
        scaled.append({
            "word": b.get("word", ""),
            "offset_ms": off,
            "duration_ms": dur,
            "end_ms": round(off + dur, 2)
        })
    return scaled
