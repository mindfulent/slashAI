# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for voice activity detection."""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.vad import VADConfig, VoiceActivityDetector


def _make_silence(num_samples: int) -> bytes:
    """Zero-valued PCM s16le."""
    return b"\x00\x00" * num_samples


def _make_loud(num_samples: int, amplitude: int = 20000) -> bytes:
    """Loud PCM s16le (alternating +/- for high RMS)."""
    samples = []
    for i in range(num_samples):
        val = amplitude if i % 2 == 0 else -amplitude
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


class TestVAD:
    def test_silence_returns_none(self):
        vad = VoiceActivityDetector()
        result = vad.process(_make_silence(800), 0.0)
        assert result is None

    def test_speech_then_silence_returns_utterance(self):
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=500.0,
            silence_timeout_ms=100,
            min_audio_bytes=100,
        ))
        # Feed loud audio
        vad.process(_make_loud(1600), 0.0)
        vad.process(_make_loud(1600), 0.05)
        # Feed silence past timeout
        result = vad.process(_make_silence(800), 0.2)
        assert result is not None
        assert len(result) > 0

    def test_short_utterance_rejected(self):
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=500.0,
            silence_timeout_ms=100,
            min_audio_bytes=100000,  # Very high minimum
        ))
        # Feed small amount of loud audio
        vad.process(_make_loud(100), 0.0)
        # Silence past timeout
        result = vad.process(_make_silence(100), 0.2)
        assert result is None

    def test_continuous_speech_accumulates(self):
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=500.0,
            silence_timeout_ms=800,
        ))
        # Keep feeding loud audio — should never return
        for i in range(10):
            result = vad.process(_make_loud(1600), i * 0.02)
            assert result is None

    def test_custom_thresholds(self):
        # With very low threshold, even quiet audio counts as speech
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=1.0,
            silence_timeout_ms=50,
            min_audio_bytes=4,
        ))
        # Feed very quiet (but non-zero) audio
        quiet = struct.pack("<h", 10) * 100
        vad.process(quiet, 0.0)
        # Then silence
        result = vad.process(_make_silence(100), 0.1)
        assert result is not None

    def test_reset_clears_state(self):
        vad = VoiceActivityDetector()
        vad.process(_make_loud(1600), 0.0)
        assert vad._is_speaking is True
        vad.reset()
        assert vad._is_speaking is False
        assert len(vad._audio_buffer) == 0

    def test_flush_returns_utterance_without_new_audio(self):
        """Simulate Discord DTX: speech arrives, then no more packets.
        flush() should return the utterance after the silence timeout."""
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=500.0,
            silence_timeout_ms=100,
            min_audio_bytes=100,
        ))
        # Feed loud audio at t=0
        vad.process(_make_loud(1600), 0.0)
        assert vad._is_speaking is True

        # No more packets arrive (Discord DTX). Call flush after timeout.
        result = vad.flush(0.2)  # 200ms later > 100ms timeout
        assert result is not None
        assert len(result) > 0
        assert vad._is_speaking is False  # Reset after flush

    def test_flush_returns_none_before_timeout(self):
        """flush() should not trigger before the silence timeout."""
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=500.0,
            silence_timeout_ms=1500,
            min_audio_bytes=100,
        ))
        vad.process(_make_loud(1600), 0.0)
        result = vad.flush(0.5)  # 500ms < 1500ms timeout
        assert result is None
        assert vad._is_speaking is True  # Still waiting

    def test_flush_returns_none_when_not_speaking(self):
        """flush() should be a no-op when no speech has been detected."""
        vad = VoiceActivityDetector()
        assert vad.flush(1.0) is None

    def test_max_utterance_forces_flush(self):
        """Long continuous speech should be flushed at max_utterance_bytes."""
        vad = VoiceActivityDetector(VADConfig(
            rms_threshold=500.0,
            max_utterance_bytes=6400,  # Small limit for testing
            min_audio_bytes=100,
        ))
        # Feed loud audio until we exceed max
        result = None
        for i in range(10):
            result = vad.process(_make_loud(800), i * 0.02)
            if result is not None:
                break
        assert result is not None
        assert vad._is_speaking is False  # Reset after forced flush

    def test_empty_chunk_returns_none(self):
        vad = VoiceActivityDetector()
        assert vad.process(b"", 0.0) is None
        assert vad.process(b"\x00", 0.0) is None  # Single byte, < 2
