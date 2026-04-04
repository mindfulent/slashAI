# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for AudioReceiver (mocked VoiceClient)."""

import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.receiver import AudioReceiver, RTP_HEADER_SIZE


def _make_mock_vc(ssrc: int = 12345, mode: str = "aead_xchacha20_poly1305_rtpsize"):
    """Create a mock VoiceClient with necessary attributes."""
    vc = MagicMock()
    vc.ssrc = ssrc
    vc.mode = mode
    vc.secret_key = [0] * 32

    connection = MagicMock()
    connection.hook = None
    vc._connection = connection

    return vc


class TestAudioReceiver:
    def test_registers_listener(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)

        receiver.start(on_audio=MagicMock())

        vc._connection.add_socket_listener.assert_called_once()

    def test_unregisters_on_stop(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)
        receiver.start(on_audio=MagicMock())

        receiver.stop()

        vc._connection.remove_socket_listener.assert_called_once()

    def test_ssrc_mapping(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)

        receiver.register_ssrc(100, 999)
        assert receiver._ssrc_to_user[100] == 999

    def test_ignores_own_ssrc(self):
        vc = _make_mock_vc(ssrc=12345)
        receiver = AudioReceiver(vc)
        callback = MagicMock()
        receiver.start(on_audio=callback)

        # Build an RTP packet with our own SSRC
        header = bytearray(12)
        header[0] = 0x80  # RTP version
        header[1] = 0x78  # Payload type
        struct.pack_into(">I", header, 8, 12345)  # Our SSRC
        packet = bytes(header) + b"\x00" * 50

        receiver._handle_packet(packet)
        callback.assert_not_called()

    def test_ignores_unknown_ssrc(self):
        vc = _make_mock_vc(ssrc=12345)
        receiver = AudioReceiver(vc)
        callback = MagicMock()
        receiver.start(on_audio=callback)

        # Build an RTP packet with unknown SSRC
        header = bytearray(12)
        header[0] = 0x80
        header[1] = 0x78
        struct.pack_into(">I", header, 8, 99999)  # Unknown SSRC
        packet = bytes(header) + b"\x00" * 50

        receiver._handle_packet(packet)
        callback.assert_not_called()

    def test_ignores_non_rtp_packets(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)
        callback = MagicMock()
        receiver.start(on_audio=callback)

        # Wrong version byte
        receiver._handle_packet(b"\x00" * 50)
        callback.assert_not_called()

    def test_ignores_short_packets(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)
        callback = MagicMock()
        receiver.start(on_audio=callback)

        receiver._handle_packet(b"\x80\x78\x00")  # Too short
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_speaking_hook_maps_ssrc(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)
        receiver.start(on_audio=MagicMock())

        # Simulate SPEAKING opcode (op 5)
        msg = {"op": 5, "d": {"ssrc": 42, "user_id": "123456", "speaking": 1}}
        await receiver._speaking_hook(MagicMock(), msg)

        assert receiver._ssrc_to_user[42] == 123456

    @pytest.mark.asyncio
    async def test_speaking_hook_calls_original(self):
        vc = _make_mock_vc()
        original_hook = MagicMock()

        async def async_original(ws, msg):
            original_hook(ws, msg)

        vc._connection.hook = async_original
        receiver = AudioReceiver(vc)
        receiver.start(on_audio=MagicMock())

        msg = {"op": 5, "d": {"ssrc": 42, "user_id": "123456", "speaking": 1}}
        await receiver._speaking_hook(MagicMock(), msg)

        original_hook.assert_called_once()

    def test_strip_rtp_extensions(self):
        # No extension
        assert AudioReceiver._strip_rtp_extensions(b"\x01\x02\x03") == b"\x01\x02\x03"

        # BEDE extension with 1 word (4 bytes)
        ext_data = b"\xBE\xDE\x00\x01" + b"\x00" * 4 + b"opus_payload"
        result = AudioReceiver._strip_rtp_extensions(ext_data)
        assert result == b"opus_payload"

    def test_stop_restores_hook(self):
        vc = _make_mock_vc()

        async def original(ws, msg):
            pass

        vc._connection.hook = original
        receiver = AudioReceiver(vc)
        receiver.start(on_audio=MagicMock())

        # Hook should have been replaced
        assert vc._connection.hook != original

        receiver.stop()
        # Hook should be restored
        assert vc._connection.hook == original

    def test_stop_clears_state(self):
        vc = _make_mock_vc()
        receiver = AudioReceiver(vc)
        receiver.start(on_audio=MagicMock())
        receiver.register_ssrc(1, 100)
        receiver._decoders[1] = "fake_decoder"

        receiver.stop()

        assert len(receiver._ssrc_to_user) == 0
        assert len(receiver._decoders) == 0
        assert receiver._on_audio is None
