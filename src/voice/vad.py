# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Voice Activity Detection for Discord voice audio.

Ported from SoulCraft's SvcAudioListener.java VAD logic.
Accumulates per-user audio and detects speech boundaries via RMS energy.
"""

from dataclasses import dataclass
from typing import Optional

import audioop


@dataclass
class VADConfig:
    """Configuration for voice activity detection."""

    rms_threshold: float = 500.0  # Minimum RMS for speech (speech: 1000-5000, music: 50-300)
    silence_timeout_ms: int = 1000  # Milliseconds of silence before finalizing
    min_audio_bytes: int = 48000  # Minimum utterance size (~1.5s at 16kHz mono s16le, avoids STT fragments)


class VoiceActivityDetector:
    """RMS-based voice activity detector.

    Accumulates audio from a single user and returns completed utterances
    when silence exceeds the configured timeout.
    """

    def __init__(self, config: VADConfig | None = None):
        self._config = config or VADConfig()
        self._audio_buffer = bytearray()
        self._is_speaking = False
        self._last_voice_time = 0.0

    def process(self, pcm_chunk: bytes, timestamp: float) -> Optional[bytes]:
        """Feed a chunk of s16le PCM audio.

        Returns completed utterance bytes when silence is detected, else None.
        """
        if not pcm_chunk or len(pcm_chunk) < 2:
            return None

        rms = audioop.rms(pcm_chunk, 2)

        if rms >= self._config.rms_threshold:
            # Voice detected
            self._is_speaking = True
            self._last_voice_time = timestamp
            self._audio_buffer.extend(pcm_chunk)
            return None

        if self._is_speaking:
            # Silence while previously speaking
            self._audio_buffer.extend(pcm_chunk)
            elapsed_ms = (timestamp - self._last_voice_time) * 1000

            if elapsed_ms >= self._config.silence_timeout_ms:
                # Speech segment complete
                if len(self._audio_buffer) >= self._config.min_audio_bytes:
                    result = bytes(self._audio_buffer)
                    self.reset()
                    return result
                else:
                    # Too short — discard
                    self.reset()
                    return None

        return None

    def reset(self) -> None:
        """Clear accumulated audio and speaking state."""
        self._audio_buffer.clear()
        self._is_speaking = False
        self._last_voice_time = 0.0
