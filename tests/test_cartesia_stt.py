# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Cartesia STT client (mocked HTTP)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.cartesia_stt import API_VERSION, STT_MODEL, STT_URL, CartesiaSTTClient


@pytest.fixture
def client():
    return CartesiaSTTClient(api_key="test-key-123")


class TestCartesiaSTT:
    @pytest.mark.asyncio
    async def test_transcribe_sends_correct_headers(self, client):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"text": "hello world"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False

        client._session = mock_session

        result = await client.transcribe(b"fake-wav-data")

        # Verify post was called
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
        assert headers["X-API-Key"] == "test-key-123"
        assert headers["Cartesia-Version"] == API_VERSION

        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_transcribe_returns_text(self, client):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"text": "the quick brown fox"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False

        client._session = mock_session

        result = await client.transcribe(b"fake-wav")
        assert result == "the quick brown fox"

    @pytest.mark.asyncio
    async def test_transcribe_empty_response(self, client):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"text": ""})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False

        client._session = mock_session

        result = await client.transcribe(b"fake-wav")
        assert result == ""

    @pytest.mark.asyncio
    async def test_transcribe_strips_whitespace(self, client):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"text": "  hello  "})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False

        client._session = mock_session

        result = await client.transcribe(b"fake-wav")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_transcribe_handles_error(self, client):
        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=Exception("Connection failed"))
        mock_session.closed = False

        client._session = mock_session

        result = await client.transcribe(b"fake-wav")
        assert result == ""

    @pytest.mark.asyncio
    async def test_close(self, client):
        mock_session = AsyncMock()
        mock_session.closed = False

        client._session = mock_session

        await client.close()
        mock_session.close.assert_awaited_once()
        assert client._session is None
