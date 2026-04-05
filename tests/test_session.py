# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for VoiceSession orchestrator (integration tests with mocks)."""

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_persona():
    """Create a minimal PersonaConfig for testing."""
    from agents.persona_loader import (
        CartesiaVoice,
        DiscordConfig,
        KokoroVoice,
        MemoryConfig,
        PersonaConfig,
        PersonaIdentity,
        VoiceConfig,
    )

    return PersonaConfig(
        schema_version=1,
        name="test_bot",
        display_name="Test Bot",
        identity=PersonaIdentity(personality="Friendly"),
        discord=DiscordConfig(),
        voice=VoiceConfig(
            cartesia=CartesiaVoice(
                voice_id="test-voice-id",
                model="sonic-3",
                default_emotion="positivity:moderate",
                speed=1.0,
            ),
            default_provider="cartesia",
        ),
        memory=MemoryConfig(agent_id="test_bot"),
    )


def _make_mock_client():
    """Create a mock discord.Client."""
    client = MagicMock()
    client.loop = asyncio.get_event_loop()
    return client


@dataclass
class FakeChatResult:
    text: str


class TestVoiceSession:
    @pytest.mark.asyncio
    async def test_join_connects_to_channel(self):
        from voice.session import VoiceSession

        persona = _make_persona()
        client = _make_mock_client()
        claude = MagicMock()

        session = VoiceSession(client, persona, claude)

        # Mock voice channel
        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.ssrc = 12345
        mock_vc.mode = "aead_xchacha20_poly1305_rtpsize"
        mock_vc.secret_key = [0] * 32
        mock_vc._connection = MagicMock()
        mock_vc._connection.hook = None

        mock_channel = AsyncMock()
        mock_channel.connect = AsyncMock(return_value=mock_vc)
        mock_channel.name = "test-voice"

        # Mock TTS connect
        with patch.object(session._tts, "connect", new_callable=AsyncMock):
            await session.join(mock_channel)

        assert session.is_connected
        assert session._running

    @pytest.mark.asyncio
    async def test_leave_disconnects(self):
        from voice.session import VoiceSession

        persona = _make_persona()
        client = _make_mock_client()
        claude = MagicMock()

        session = VoiceSession(client, persona, claude)

        # Set up as if connected
        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.disconnect = AsyncMock()
        session._voice_client = mock_vc
        session._running = True

        # Mock receiver
        mock_receiver = MagicMock()
        session._receiver = mock_receiver

        with (
            patch.object(session._tts, "close", new_callable=AsyncMock),
            patch.object(session._stt, "close", new_callable=AsyncMock),
        ):
            await session.leave()

        assert not session._running
        assert session._voice_client is None
        mock_receiver.stop.assert_called_once()
        mock_vc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_utterance_pipeline(self):
        """Test the full pipeline: STT → echo guard → LLM → TTS → play."""
        from voice.session import VoiceSession

        persona = _make_persona()
        client = _make_mock_client()
        claude = AsyncMock()
        claude.chat = AsyncMock(return_value=FakeChatResult(text="I'm here to help!"))

        session = VoiceSession(client, persona, claude)

        # Set up as if connected
        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.channel = MagicMock()
        mock_vc.channel.id = 123456
        session._voice_client = mock_vc
        session._running = True

        # Mock STT to return a transcript
        with (
            patch.object(
                session._stt,
                "transcribe",
                new_callable=AsyncMock,
                return_value="Hello can you hear me",
            ),
            patch.object(session, "_speak", new_callable=AsyncMock) as mock_speak,
        ):
            # Feed a fake utterance (already 16kHz mono PCM)
            pcm = b"\x00\x01" * 6400  # Above min_audio_bytes
            await session._handle_utterance(user_id=999, pcm_16k_mono=pcm)

        # Verify LLM was called with the transcript
        claude.chat.assert_awaited_once()
        call_kwargs = claude.chat.call_args
        assert "Hello can you hear me" in str(call_kwargs)

        # Verify TTS was triggered (with timing kwargs)
        mock_speak.assert_awaited_once()
        assert mock_speak.call_args[0][0] == "I'm here to help!"

    @pytest.mark.asyncio
    async def test_echo_guard_rejects(self):
        """Test that the echo guard prevents transcribing bot's own speech."""
        from voice.session import VoiceSession

        persona = _make_persona()
        client = _make_mock_client()
        claude = AsyncMock()

        session = VoiceSession(client, persona, claude)
        session._running = True
        session._voice_client = MagicMock()
        session._voice_client.channel = MagicMock()
        session._voice_client.channel.id = 123

        # Simulate the bot just spoke
        session._echo_guard.mark_bot_speaking(5.0)

        with patch.object(
            session._stt,
            "transcribe",
            new_callable=AsyncMock,
            return_value="some echo text",
        ):
            await session._handle_utterance(user_id=999, pcm_16k_mono=b"\x00" * 6400)

        # LLM should NOT have been called
        claude.chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_transcript_skipped(self):
        """Test that empty transcripts don't trigger LLM."""
        from voice.session import VoiceSession

        persona = _make_persona()
        client = _make_mock_client()
        claude = AsyncMock()

        session = VoiceSession(client, persona, claude)
        session._running = True
        session._voice_client = MagicMock()
        session._voice_client.channel = MagicMock()
        session._voice_client.channel.id = 123

        with patch.object(
            session._stt,
            "transcribe",
            new_callable=AsyncMock,
            return_value="",
        ):
            await session._handle_utterance(user_id=999, pcm_16k_mono=b"\x00" * 6400)

        claude.chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_running_skips_processing(self):
        """Test that stopped sessions don't process utterances."""
        from voice.session import VoiceSession

        persona = _make_persona()
        client = _make_mock_client()
        claude = AsyncMock()

        session = VoiceSession(client, persona, claude)
        session._running = False

        with patch.object(session._stt, "transcribe", new_callable=AsyncMock) as stt:
            await session._handle_utterance(user_id=999, pcm_16k_mono=b"\x00" * 6400)

        stt.assert_not_awaited()

    def test_is_connected_when_no_vc(self):
        from voice.session import VoiceSession

        persona = _make_persona()
        session = VoiceSession(MagicMock(), persona, MagicMock())
        assert session.is_connected is False

    def test_channel_when_no_vc(self):
        from voice.session import VoiceSession

        persona = _make_persona()
        session = VoiceSession(MagicMock(), persona, MagicMock())
        assert session.channel is None
