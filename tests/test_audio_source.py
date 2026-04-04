# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for StreamingAudioSource."""

import struct
import sys
import threading
from pathlib import Path

import audioop
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.audio_source import FRAME_SIZE, StreamingAudioSource


class TestStreamingAudioSource:
    def test_read_empty_unfinished_returns_silence(self):
        source = StreamingAudioSource()
        frame = source.read()
        assert len(frame) == FRAME_SIZE
        assert frame == b"\x00" * FRAME_SIZE

    def test_read_empty_finished_returns_empty(self):
        source = StreamingAudioSource()
        source.finish()
        frame = source.read()
        assert frame == b""

    def test_feed_and_read(self):
        source = StreamingAudioSource()
        data = b"\x01\x02" * (FRAME_SIZE // 2)
        source.feed(data)
        frame = source.read()
        assert len(frame) == FRAME_SIZE
        assert frame == data

    def test_chunking(self):
        source = StreamingAudioSource()
        # Feed exactly 2 frames
        data = b"\x01\x02" * FRAME_SIZE  # 2 * FRAME_SIZE bytes
        source.feed(data)
        frame1 = source.read()
        frame2 = source.read()
        assert len(frame1) == FRAME_SIZE
        assert len(frame2) == FRAME_SIZE

    def test_partial_frame_padded(self):
        source = StreamingAudioSource()
        # Feed less than one frame
        data = b"\x01\x02" * 100  # 200 bytes
        source.feed(data)
        frame = source.read()
        assert len(frame) == FRAME_SIZE
        # First 200 bytes should be our data, rest silence
        assert frame[:200] == data
        assert frame[200:] == b"\x00" * (FRAME_SIZE - 200)

    def test_is_opus_false(self):
        source = StreamingAudioSource()
        assert source.is_opus() is False

    def test_is_speaking_property(self):
        source = StreamingAudioSource()
        # Not finished, no data — still "speaking" (waiting)
        assert source.is_speaking is True
        source.feed(b"\x01" * FRAME_SIZE)
        assert source.is_speaking is True
        source.read()  # Drain buffer
        source.finish()
        assert source.is_speaking is False

    def test_volume_scaling(self):
        source = StreamingAudioSource(volume=0.5)
        # Create a frame with known amplitude
        amplitude = 20000
        samples = struct.pack("<h", amplitude) * (FRAME_SIZE // 2)
        source.feed(samples)
        frame = source.read()
        # Check RMS is roughly halved
        original_rms = audioop.rms(samples, 2)
        scaled_rms = audioop.rms(frame, 2)
        assert scaled_rms < original_rms
        assert scaled_rms > 0

    def test_cleanup(self):
        source = StreamingAudioSource()
        source.feed(b"\x01" * FRAME_SIZE * 5)
        source.cleanup()
        # After cleanup, buffer is empty
        source.finish()
        assert source.read() == b""

    def test_thread_safety(self):
        """Feed from one thread, read from another."""
        source = StreamingAudioSource()
        frames_read = []
        num_frames = 50

        def reader():
            while True:
                frame = source.read()
                if frame == b"":
                    break
                if frame != b"\x00" * FRAME_SIZE:  # Skip silence frames
                    frames_read.append(frame)

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        # Feed frames from main thread
        for _ in range(num_frames):
            source.feed(b"\x01\x02" * (FRAME_SIZE // 2))

        source.finish()
        reader_thread.join(timeout=5.0)
        assert not reader_thread.is_alive()
        assert len(frames_read) == num_frames
