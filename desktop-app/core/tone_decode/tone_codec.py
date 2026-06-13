from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 8000
SYMBOL_MS = 90
GAP_MS = 20
AMPLITUDE = 0.22

LOW_FREQS = {
    0: 1200.0,
    1: 1450.0,
}

HIGH_FREQS = {
    0: 2300.0,
    1: 2750.0,
}

EVENT_START = "START"
EVENT_TONE_A = "A"
EVENT_TONE_B = "B"
EVENT_TONE_C = "C"

EVENT_SYMBOLS = {
    EVENT_START: (0, 1),
    EVENT_TONE_A: (0, 0),
    EVENT_TONE_B: (1, 0),
    EVENT_TONE_C: (1, 1),
}

SYMBOL_EVENTS = {value: key for key, value in EVENT_SYMBOLS.items()}


@dataclass(frozen=True)
class DetectedEvent:
    event: str
    start_sample: int
    end_sample: int
    score: float
    low_bit: int
    high_bit: int


def synth_tone_symbol(low_bit: int, high_bit: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    duration_samples = int(sample_rate * SYMBOL_MS / 1000)
    t = np.arange(duration_samples, dtype=np.float32) / sample_rate
    low = np.sin(2.0 * math.pi * LOW_FREQS[low_bit] * t)
    high = np.sin(2.0 * math.pi * HIGH_FREQS[high_bit] * t)
    envelope = np.sin(np.linspace(0, math.pi, duration_samples, dtype=np.float32))
    return ((low + high) * 0.5 * AMPLITUDE * envelope).astype(np.float32)


def synth_event(event: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    if event not in EVENT_SYMBOLS:
        raise ValueError(f"unknown event: {event}")
    symbol = synth_tone_symbol(*EVENT_SYMBOLS[event], sample_rate=sample_rate)
    gap = np.zeros(int(sample_rate * GAP_MS / 1000), dtype=np.float32)
    return np.concatenate([symbol, gap])


def mix_event(base: np.ndarray, event: str, offset_sample: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    out = np.array(base, dtype=np.float32, copy=True)
    tone = synth_event(event, sample_rate=sample_rate)
    end = min(len(out), offset_sample + len(tone))
    if end > offset_sample:
        out[offset_sample:end] += tone[: end - offset_sample]
    return np.clip(out, -1.0, 1.0)


def goertzel_power(samples: np.ndarray, freq: float, sample_rate: int = SAMPLE_RATE) -> float:
    if samples.size == 0:
        return 0.0
    k = int(0.5 + (samples.size * freq / sample_rate))
    omega = (2.0 * math.pi * k) / samples.size
    coeff = 2.0 * math.cos(omega)
    q0 = q1 = q2 = 0.0
    for sample in samples:
        q0 = coeff * q1 - q2 + float(sample)
        q2 = q1
        q1 = q0
    return q1 * q1 + q2 * q2 - coeff * q1 * q2


def _best_bit(samples: np.ndarray, freqs: dict[int, float], sample_rate: int) -> tuple[int, float, float]:
    powers = {bit: goertzel_power(samples, freq, sample_rate) for bit, freq in freqs.items()}
    ranked = sorted(powers.items(), key=lambda item: item[1], reverse=True)
    best_bit, best_power = ranked[0]
    second_power = ranked[1][1] if len(ranked) > 1 else 1e-12
    return best_bit, best_power, second_power


def detect_events(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[DetectedEvent]:
    mono = np.asarray(samples, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono[:, 0]

    frame = int(sample_rate * SYMBOL_MS / 1000)
    hop = int(sample_rate * 20 / 1000)
    if mono.size < frame:
        return []

    detections: list[DetectedEvent] = []
    last_end = -1
    window = np.hanning(frame).astype(np.float32)

    for start in range(0, mono.size - frame + 1, hop):
        if start < last_end:
            continue
        chunk = mono[start : start + frame] * window
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        if rms < 0.015:
            continue

        low_bit, low_power, low_second = _best_bit(chunk, LOW_FREQS, sample_rate)
        high_bit, high_power, high_second = _best_bit(chunk, HIGH_FREQS, sample_rate)
        low_ratio = low_power / max(low_second, 1e-12)
        high_ratio = high_power / max(high_second, 1e-12)
        score = min(low_ratio, high_ratio)
        total_power = float(np.sum(chunk * chunk) * frame)
        tone_share = (low_power + high_power) / max(total_power, 1e-12)

        event = SYMBOL_EVENTS.get((low_bit, high_bit))
        if event is None:
            continue
        if score < 12.0 or tone_share < 0.18:
            continue

        end = start + frame
        detections.append(
            DetectedEvent(
                event=event,
                start_sample=start,
                end_sample=end,
                score=score,
                low_bit=low_bit,
                high_bit=high_bit,
            )
        )
        last_end = end + int(sample_rate * 120 / 1000)

    return detections


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    path = Path(path)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())


def read_wav(path: str | Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        raw = wav.readframes(wav.getnframes())
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)[:, 0]
    return sample_rate, pcm
