# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Cartesia TTS client (mocked WebSocket)."""

import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.cartesia_tts import CartesiaTTSClient


@pytest.fixture
def client():
    return CartesiaTTSClient(
        api_key="test-key",
        voice_id="test-voice-id",
        model="sonic-3",
    )


def _make_ws_text_msg(data: dict) -> MagicMock:
    """Create a mock aiohttp WebSocket message."""
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.TEXT
    msg.data = json.dumps(data)
    return msg


class _AsyncIter:
    """Helper to make a list of items work as an async iterator."""

    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


def _mock_ws(messages: list) -> AsyncMock:
    """Create a mock WebSocket that yields the given messages."""
    mock = AsyncMock()
    mock.closed = False

    async def aiter_impl(self_arg=None):
        for msg in messages:
            yield msg

    # Make the mock itself async-iterable
    mock.__aiter__ = lambda self=None: _AsyncIter(messages)
    return mock


class TestCartesiaTTS:
    @pytest.mark.asyncio
    async def test_synthesize_sends_correct_payload(self, client):
        ws = _mock_ws([
            _make_ws_text_msg({"context_id": "slashai-1", "type": "done"}),
        ])
        client._ws = ws

        chunks = []
        async for chunk in client.synthesize("Hello world"):
            chunks.append(chunk)

        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["model_id"] == "sonic-3"
        assert payload["transcript"] == "Hello world"
        assert payload["voice"]["id"] == "test-voice-id"
        assert payload["output_format"]["encoding"] == "pcm_s16le"
        assert payload["output_format"]["sample_rate"] == 24000

    @pytest.mark.asyncio
    async def test_synthesize_yields_pcm_chunks(self, client):
        pcm_data = b"\x01\x02\x03\x04" * 100
        b64_data = base64.b64encode(pcm_data).decode()

        ws = _mock_ws([
            _make_ws_text_msg({
                "context_id": "slashai-1",
                "type": "chunk",
                "data": b64_data,
            }),
            _make_ws_text_msg({"context_id": "slashai-1", "type": "done"}),
        ])
        client._ws = ws

        chunks = []
        async for chunk in client.synthesize("Test"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == pcm_data

    @pytest.mark.asyncio
    async def test_speed_clamping(self, client):
        ws = _mock_ws([
            _make_ws_text_msg({"context_id": "slashai-1", "type": "done"}),
        ])
        client._ws = ws

        async for _ in client.synthesize("Test", speed=0.1):
            pass
        payload = ws.send_json.call_args[0][0]
        assert payload["generation_config"]["speed"] == 0.6

    @pytest.mark.asyncio
    async def test_speed_clamping_high(self, client):
        ws = _mock_ws([
            _make_ws_text_msg({"context_id": "slashai-2", "type": "done"}),
        ])
        client._ws = ws

        async for _ in client.synthesize("Test", speed=5.0):
            pass
        payload = ws.send_json.call_args[0][0]
        assert payload["generation_config"]["speed"] == 1.5

    @pytest.mark.asyncio
    async def test_emotion_included_when_provided(self, client):
        ws = _mock_ws([
            _make_ws_text_msg({"context_id": "slashai-1", "type": "done"}),
        ])
        client._ws = ws

        async for _ in client.synthesize("Wow!", emotion="excited"):
            pass

        payload = ws.send_json.call_args[0][0]
        assert payload["generation_config"]["emotions"] == ["excited"]

    @pytest.mark.asyncio
    async def test_emotion_absent_when_none(self, client):
        ws = _mock_ws([
            _make_ws_text_msg({"context_id": "slashai-1", "type": "done"}),
        ])
        client._ws = ws

        async for _ in client.synthesize("Normal text"):
            pass

        payload = ws.send_json.call_args[0][0]
        assert "emotions" not in payload["generation_config"]

    @pytest.mark.asyncio
    async def test_not_connected_raises(self, client):
        with pytest.raises(RuntimeError, match="not connected"):
            async for _ in client.synthesize("Test"):
                pass

    @pytest.mark.asyncio
    async def test_error_response_stops(self, client):
        ws = _mock_ws([
            _make_ws_text_msg({
                "context_id": "slashai-1",
                "type": "error",
                "error": "Invalid voice ID",
            }),
        ])
        client._ws = ws

        chunks = []
        async for chunk in client.synthesize("Test"):
            chunks.append(chunk)

        assert chunks == []
