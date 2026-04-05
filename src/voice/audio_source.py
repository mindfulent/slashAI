# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Custom AudioSource for streaming TTS audio to Discord voice channels.

Feeds PCM frames from an async producer (TTS) to discord.py's AudioPlayer thread.
"""

import collections
import threading

import audioop
from discord import AudioSource


# Discord expects 48kHz stereo s16le, 20ms frames
FRAME_SIZE = 3840  # 48000 * 2 channels * 2 bytes * 0.020 seconds


class StreamingAudioSource(AudioSource):
    """AudioSource fed by an async TTS producer.

    read() is called from discord.py's AudioPlayer thread every 20ms.
    feed() is called from async context to push resampled TTS audio.
    """

    def __init__(self, *, volume: float = 1.0):
        self._buffer: collections.deque[bytes] = collections.deque()
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._volume = max(0.0, min(2.0, volume))

    def feed(self, pcm_48k_stereo: bytes) -> None:
        """Push resampled TTS audio. Chunks into FRAME_SIZE pieces."""
        if not pcm_48k_stereo:
            return

        with self._lock:
            # Chunk into frame-sized pieces
            offset = 0
            while offset < len(pcm_48k_stereo):
                chunk = pcm_48k_stereo[offset : offset + FRAME_SIZE]
                if len(chunk) < FRAME_SIZE:
                    # Pad last chunk with silence
                    chunk = chunk + b"\x00" * (FRAME_SIZE - len(chunk))
                self._buffer.append(chunk)
                offset += FRAME_SIZE

    def finish(self) -> None:
        """Signal that no more audio will be fed."""
        self._finished.set()

    def read(self) -> bytes:
        """Called by AudioPlayer thread every 20ms.

        Returns FRAME_SIZE bytes of audio, silence if waiting, or b"" to stop.
        """
        with self._lock:
            if self._buffer:
                frame = self._buffer.popleft()
                if self._volume != 1.0:
                    frame = audioop.mul(frame, 2, self._volume)
                return frame

        # Buffer empty
        if self._finished.is_set():
            return b""  # Signal AudioPlayer to stop

        # Still waiting for more data — return silence to keep player alive
        return b"\x00" * FRAME_SIZE

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        with self._lock:
            self._buffer.clear()

    @property
    def buffered_bytes(self) -> int:
        """Total bytes currently in the buffer."""
        with self._lock:
            return sum(len(f) for f in self._buffer)

    @property
    def is_speaking(self) -> bool:
        """True if there is still audio to play."""
        with self._lock:
            has_data = len(self._buffer) > 0
        return has_data or not self._finished.is_set()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = max(0.0, min(2.0, value))
