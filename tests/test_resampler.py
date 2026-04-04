# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for audio format conversion between Cartesia and Discord."""

import struct
import sys
from pathlib import Path

import audioop
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.resampler import AudioResampler


def _make_silence(num_samples: int, channels: int = 1) -> bytes:
    """Create silent PCM s16le audio."""
    return b"\x00\x00" * num_samples * channels


def _make_tone(num_samples: int, amplitude: int = 10000) -> bytes:
    """Create a simple PCM tone (alternating +/- amplitude) for mono s16le."""
    samples = []
    for i in range(num_samples):
        val = amplitude if i % 2 == 0 else -amplitude
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


class TestTtsToDiscord:
    def test_output_length(self):
        # 24kHz mono -> 48kHz stereo = 4x bytes
        mono_24k = _make_silence(24000)  # 1 second = 48000 bytes
        stereo_48k = AudioResampler.tts_to_discord(mono_24k)
        # ratecv may produce slightly different lengths due to interpolation
        # but should be approximately 4x
        expected = 48000 * 4  # 48000 samples * 2 channels * 2 bytes
        assert abs(len(stereo_48k) - expected) < 100

    def test_empty_input(self):
        assert AudioResampler.tts_to_discord(b"") == b""

    def test_preserves_energy(self):
        # A non-silent signal should remain non-silent
        mono_24k = _make_tone(2400)  # 0.1 second
        stereo_48k = AudioResampler.tts_to_discord(mono_24k)
        rms = audioop.rms(stereo_48k, 2)
        assert rms > 0


class TestDiscordToStt:
    def test_output_length(self):
        # 48kHz stereo -> 16kHz mono = 1/6 bytes
        stereo_48k = _make_silence(48000, channels=2)  # 1 second = 192000 bytes
        mono_16k = AudioResampler.discord_to_stt(stereo_48k)
        expected = 16000 * 2  # 16000 samples * 2 bytes (mono)
        assert abs(len(mono_16k) - expected) < 100

    def test_empty_input(self):
        assert AudioResampler.discord_to_stt(b"") == b""

    def test_preserves_energy(self):
        stereo_48k = _make_tone(4800)  # Make mono tone, then fake stereo
        stereo = audioop.tostereo(stereo_48k, 2, 1, 1)
        mono_16k = AudioResampler.discord_to_stt(stereo)
        rms = audioop.rms(mono_16k, 2)
        assert rms > 0


class TestPcmToWav:
    def test_valid_wav_header(self):
        pcm = _make_silence(1600)  # 0.1 second at 16kHz
        wav = AudioResampler.pcm_to_wav(pcm)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert wav[12:16] == b"fmt "
        assert wav[36:40] == b"data"

    def test_correct_params(self):
        pcm = _make_silence(1600)
        wav = AudioResampler.pcm_to_wav(pcm)
        # Parse fmt chunk
        fmt_size = struct.unpack_from("<I", wav, 16)[0]
        assert fmt_size == 16
        audio_format = struct.unpack_from("<H", wav, 20)[0]
        assert audio_format == 1  # PCM
        num_channels = struct.unpack_from("<H", wav, 22)[0]
        assert num_channels == 1
        sample_rate = struct.unpack_from("<I", wav, 24)[0]
        assert sample_rate == 16000
        bits_per_sample = struct.unpack_from("<H", wav, 34)[0]
        assert bits_per_sample == 16

    def test_data_integrity(self):
        pcm = b"\x01\x02" * 100
        wav = AudioResampler.pcm_to_wav(pcm)
        # Data starts at byte 44
        assert wav[44:] == pcm

    def test_data_size_in_header(self):
        pcm = _make_silence(1600)
        wav = AudioResampler.pcm_to_wav(pcm)
        data_size = struct.unpack_from("<I", wav, 40)[0]
        assert data_size == len(pcm)

    def test_roundtrip_preserves_energy(self):
        # Upsample then downsample shouldn't collapse to silence
        mono_24k = _make_tone(2400, amplitude=5000)
        stereo_48k = AudioResampler.tts_to_discord(mono_24k)
        mono_16k = AudioResampler.discord_to_stt(stereo_48k)
        rms = audioop.rms(mono_16k, 2)
        assert rms > 0
