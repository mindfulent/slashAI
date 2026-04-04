# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Audio format conversion between Cartesia and Discord.

Cartesia TTS outputs 24kHz mono PCM s16le.
Cartesia STT expects 16kHz mono PCM s16le (in WAV).
Discord sends/receives 48kHz stereo PCM s16le.
"""

import struct

import audioop


class AudioResampler:
    """Convert between Cartesia audio formats and Discord audio formats."""

    @staticmethod
    def tts_to_discord(pcm_24k_mono: bytes) -> bytes:
        """Convert Cartesia TTS output to Discord playback format.

        24kHz mono s16le -> 48kHz stereo s16le (4x size increase).
        """
        if not pcm_24k_mono:
            return b""
        # Upsample 24kHz -> 48kHz
        upsampled, _ = audioop.ratecv(pcm_24k_mono, 2, 1, 24000, 48000, None)
        # Mono -> stereo (duplicate channel)
        stereo = audioop.tostereo(upsampled, 2, 1, 1)
        return stereo

    @staticmethod
    def discord_to_stt(pcm_48k_stereo: bytes) -> bytes:
        """Convert Discord received audio to Cartesia STT input format.

        48kHz stereo s16le -> 16kHz mono s16le (1/6 size).
        """
        if not pcm_48k_stereo:
            return b""
        # Stereo -> mono
        mono = audioop.tomono(pcm_48k_stereo, 2, 1, 0)
        # Downsample 48kHz -> 16kHz
        downsampled, _ = audioop.ratecv(mono, 2, 1, 48000, 16000, None)
        return downsampled

    @staticmethod
    def pcm_to_wav(pcm_16k_mono: bytes) -> bytes:
        """Wrap raw PCM in a WAV header for Cartesia STT REST endpoint.

        Input: 16kHz, 1 channel, 16-bit signed little-endian.
        Output: Complete WAV file bytes.
        """
        sample_rate = 16000
        num_channels = 1
        bits_per_sample = 16
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        data_size = len(pcm_16k_mono)
        file_size = 36 + data_size  # 44 - 8 (RIFF header)

        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            file_size,
            b"WAVE",
            b"fmt ",
            16,  # fmt chunk size
            1,  # PCM format
            num_channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b"data",
            data_size,
        )
        return header + pcm_16k_mono
