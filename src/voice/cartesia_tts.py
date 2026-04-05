# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Cartesia Text-to-Speech client via WebSocket.

Ported from SoulCraft's CartesiaTtsEngine.java. Streams text to PCM audio.
"""

import base64
import json
import logging
from typing import AsyncIterator, Optional

import aiohttp

logger = logging.getLogger(__name__)

WS_URL = "wss://api.cartesia.ai/tts/websocket"
API_VERSION = "2025-04-16"


class CartesiaTTSClient:
    """Cartesia TTS via WebSocket. Streams text -> PCM chunks (24kHz mono s16le)."""

    def __init__(self, api_key: str, voice_id: str, model: str = "sonic-3"):
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._context_counter = 0

    async def connect(self) -> None:
        """Open WebSocket connection to Cartesia TTS."""
        url = f"{WS_URL}?api_key={self._api_key}&cartesia_version={API_VERSION}"
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(url)
        logger.info("Connected to Cartesia TTS WebSocket")

    async def synthesize(
        self,
        text: str,
        *,
        emotion: Optional[str] = None,
        speed: float = 1.0,
        language: str = "en",
    ) -> AsyncIterator[bytes]:
        """Send text, yield raw PCM chunks (24kHz mono s16le).

        Args:
            text: Text to synthesize.
            emotion: Optional Cartesia emotion tag (e.g., "excited", "sad").
            speed: Speech speed multiplier (clamped to [0.6, 1.5]).
            language: Language code.

        Yields:
            Bytes of PCM audio data (24kHz mono s16le).
        """
        if not self._ws or self._ws.closed:
            raise RuntimeError("WebSocket not connected. Call connect() first.")

        self._context_counter += 1
        context_id = f"slashai-{self._context_counter}"

        # Clamp speed
        speed = max(0.6, min(1.5, speed))

        # Build request
        request = {
            "model_id": self._model,
            "transcript": text,
            "context_id": context_id,
            "continue": False,
            "language": language,
            "voice": {
                "mode": "id",
                "id": self._voice_id,
            },
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 24000,
            },
        }

        # Generation config
        gen_config: dict = {"speed": speed}
        if emotion:
            gen_config["emotions"] = [emotion]
        request["generation_config"] = gen_config

        await self._ws.send_json(request)

        # Read response chunks
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                resp_context = data.get("context_id", "")

                if resp_context != context_id:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "chunk":
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        yield base64.b64decode(audio_b64)

                elif msg_type == "done":
                    break

                elif msg_type == "error":
                    error = data.get("error", "Unknown error")
                    logger.error(f"Cartesia TTS error: {error}")
                    break

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                logger.error("Cartesia TTS WebSocket closed unexpectedly")
                break

    async def synthesize_stream(
        self,
        chunks: list[str],
        *,
        emotion: Optional[str] = None,
        speed: float = 1.0,
        language: str = "en",
    ) -> AsyncIterator[bytes]:
        """Synthesize multiple text chunks as one continuous audio stream.

        Uses Cartesia's `continue` flag to chain chunks in a single voice
        context, producing seamless audio without gaps between segments.

        Args:
            chunks: List of text segments to synthesize in order.
            emotion: Optional Cartesia emotion tag.
            speed: Speech speed multiplier (clamped to [0.6, 1.5]).
            language: Language code.

        Yields:
            Bytes of PCM audio data (24kHz mono s16le).
        """
        if not self._ws or self._ws.closed:
            raise RuntimeError("WebSocket not connected. Call connect() first.")

        if not chunks:
            return

        self._context_counter += 1
        context_id = f"slashai-{self._context_counter}"
        speed = max(0.6, min(1.5, speed))

        for i, chunk_text in enumerate(chunks):
            request = {
                "model_id": self._model,
                "transcript": chunk_text,
                "context_id": context_id,
                "continue": i > 0,
                "language": language,
                "voice": {
                    "mode": "id",
                    "id": self._voice_id,
                },
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 24000,
                },
            }

            gen_config: dict = {"speed": speed}
            if emotion:
                gen_config["emotions"] = [emotion]
            request["generation_config"] = gen_config

            await self._ws.send_json(request)

            # Read audio chunks until "done" for this segment
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("context_id") != context_id:
                        continue

                    msg_type = data.get("type", "")
                    if msg_type == "chunk":
                        audio_b64 = data.get("data", "")
                        if audio_b64:
                            yield base64.b64decode(audio_b64)
                    elif msg_type == "done":
                        break
                    elif msg_type == "error":
                        logger.error(f"Cartesia TTS error: {data.get('error')}")
                        return  # Abort entire stream on error

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.error("Cartesia TTS WebSocket closed unexpectedly")
                    return

    async def close(self) -> None:
        """Close WebSocket and HTTP session."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
