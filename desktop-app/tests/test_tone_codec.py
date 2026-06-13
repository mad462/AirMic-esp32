import unittest

import numpy as np

from core.tone_decode.tone_codec import (
    EVENT_START,
    EVENT_TONE_A,
    EVENT_TONE_B,
    EVENT_TONE_C,
    SAMPLE_RATE,
    detect_events,
    mix_event,
)


def fake_speech(seconds: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    samples = int(SAMPLE_RATE * seconds)
    t = np.arange(samples, dtype=np.float32) / SAMPLE_RATE
    speechish = (
        0.035 * np.sin(2 * np.pi * 180 * t)
        + 0.025 * np.sin(2 * np.pi * 310 * t)
        + 0.018 * np.sin(2 * np.pi * 760 * t)
    )
    noise = rng.normal(0.0, 0.008, samples).astype(np.float32)
    envelope = 0.65 + 0.35 * np.sin(2 * np.pi * 3.0 * t)
    return ((speechish * envelope) + noise).astype(np.float32)


class ToneCodecTest(unittest.TestCase):
    def test_detects_start_and_aux_tones_in_noisy_speech(self):
        audio = fake_speech(3.0)
        audio = mix_event(audio, EVENT_START, int(0.45 * SAMPLE_RATE))
        audio = mix_event(audio, EVENT_TONE_A, int(1.10 * SAMPLE_RATE))
        audio = mix_event(audio, EVENT_TONE_B, int(1.70 * SAMPLE_RATE))
        audio = mix_event(audio, EVENT_TONE_C, int(2.20 * SAMPLE_RATE))

        events = detect_events(audio)

        self.assertEqual([event.event for event in events], [EVENT_START, EVENT_TONE_A, EVENT_TONE_B, EVENT_TONE_C])
        self.assertLess(abs(events[0].start_sample - int(0.45 * SAMPLE_RATE)), int(0.08 * SAMPLE_RATE))
        self.assertLess(abs(events[1].start_sample - int(1.10 * SAMPLE_RATE)), int(0.08 * SAMPLE_RATE))
        self.assertLess(abs(events[2].start_sample - int(1.70 * SAMPLE_RATE)), int(0.08 * SAMPLE_RATE))
        self.assertLess(abs(events[3].start_sample - int(2.20 * SAMPLE_RATE)), int(0.08 * SAMPLE_RATE))

    def test_does_not_false_trigger_on_speech_like_audio(self):
        audio = fake_speech(5.0, seed=12)

        events = detect_events(audio)

        self.assertEqual(events, [])

    def test_detects_repeated_same_event_when_spaced(self):
        audio = fake_speech(4.0, seed=20)
        audio = mix_event(audio, EVENT_START, int(0.50 * SAMPLE_RATE))
        audio = mix_event(audio, EVENT_START, int(2.50 * SAMPLE_RATE))

        events = detect_events(audio)

        self.assertEqual([event.event for event in events], [EVENT_START, EVENT_START])


if __name__ == "__main__":
    unittest.main()
