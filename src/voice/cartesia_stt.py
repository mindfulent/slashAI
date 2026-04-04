# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Cartesia Speech-to-Text client.

Ported from SoulCraft's SttClient.java. REST multipart upload.
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

STT_URL = "https://api.cartesia.ai/stt"
API_VERSION = "2026-03-01"
STT_MODEL = "ink-whisper"
TIMEOUT_SECONDS = 10


class CartesiaSTTClient:
    """Cartesia STT via REST. Sends WAV audio, receives transcript."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            )
        return self._session

    async def transcribe(self, wav_data: bytes, language: str = "en") -> str:
        """Transcribe WAV audio to text.

        Args:
            wav_data: Complete WAV file (16kHz mono s16le with header).
            language: Language code (default "en").

        Returns:
            Transcript string, or empty string on failure.
        """
        session = await self._ensure_session()

        headers = {
            "X-API-Key": self._api_key,
            "Cartesia-Version": API_VERSION,
        }

        form = aiohttp.FormData()
        form.add_field(
            "file",
            wav_data,
            filename="audio.wav",
            content_type="audio/wav",
        )
        form.add_field("model", STT_MODEL)
        form.add_field("language", language)

        try:
            async with session.post(STT_URL, headers=headers, data=form) as resp:
                resp.raise_for_status()
                result = await resp.json()
                return result.get("text", "").strip()
        except Exception as e:
            logger.error(f"STT transcription failed: {e}")
            return ""

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
