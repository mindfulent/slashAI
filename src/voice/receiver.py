# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Audio receiver for Discord voice channels.

Hooks into discord.py's internal SocketReader to receive, decrypt,
and decode voice audio from other users in the channel.
"""

import logging
import struct
from typing import Callable, Optional

import nacl.secret

logger = logging.getLogger(__name__)

# RTP constants
RTP_HEADER_SIZE = 12
RTP_VERSION_BYTE = 0x80
RTP_PAYLOAD_TYPE = 0x78


class AudioReceiver:
    """Receives and decodes voice audio from Discord voice channels.

    Uses discord.py internal APIs:
    - VoiceConnectionState.add_socket_listener() for raw UDP packets
    - VoiceConnectionState.hook for SPEAKING opcode (SSRC→user mapping)
    - VoiceClient.secret_key and .mode for decryption
    - discord.opus.Decoder for Opus→PCM
    """

    def __init__(self, voice_client):
        """Initialize receiver.

        Args:
            voice_client: discord.VoiceClient instance (already connected).
        """
        self._vc = voice_client
        self._decoders: dict[int, object] = {}  # ssrc -> opus.Decoder
        self._ssrc_to_user: dict[int, int] = {}
        self._on_audio: Optional[Callable[[int, bytes], None]] = None
        self._callback: Optional[Callable[[bytes], None]] = None
        self._original_hook = None
        self._packet_count = 0

    def start(self, on_audio: Callable[[int, bytes], None]) -> None:
        """Start receiving audio.

        Args:
            on_audio: Callback(user_id, pcm_48k_stereo) for each decoded frame.
                Called from SocketReader daemon thread — must be fast.
        """
        self._on_audio = on_audio
        self._callback = self._handle_packet
        self._vc._connection.add_socket_listener(self._callback)

        # Hook into voice WebSocket for SPEAKING opcode (SSRC mapping).
        # Must patch both _connection.hook (for future reconnects) AND
        # the already-connected ws._hook (for the current session).
        self._original_hook = self._vc._connection.hook
        self._vc._connection.hook = self._speaking_hook
        if hasattr(self._vc._connection, "ws") and self._vc._connection.ws:
            self._original_ws_hook = self._vc._connection.ws._hook
            self._vc._connection.ws._hook = self._speaking_hook

        logger.info("AudioReceiver started")

    async def _speaking_hook(self, ws, msg):
        """Intercept voice WebSocket messages for SSRC→user mapping."""
        if msg.get("op") == 5:  # SPEAKING
            d = msg.get("d", {})
            ssrc = d.get("ssrc")
            user_id = d.get("user_id")
            if ssrc is not None and user_id is not None:
                self._ssrc_to_user[ssrc] = int(user_id)
                logger.info(f"SSRC {ssrc} -> user {user_id}")

        # Call original hook if present
        if self._original_hook:
            await self._original_hook(ws, msg)

    def stop(self) -> None:
        """Stop receiving audio and clean up."""
        if self._callback:
            self._vc._connection.remove_socket_listener(self._callback)
            self._callback = None

        # Restore original hooks
        if hasattr(self._vc, "_connection"):
            self._vc._connection.hook = self._original_hook
            if hasattr(self._vc._connection, "ws") and self._vc._connection.ws:
                self._vc._connection.ws._hook = getattr(
                    self, "_original_ws_hook", self._original_hook
                )
        self._original_hook = None

        self._decoders.clear()
        self._ssrc_to_user.clear()
        self._on_audio = None

        logger.info("AudioReceiver stopped")

    def register_ssrc(self, ssrc: int, user_id: int) -> None:
        """Manually map SSRC to user ID."""
        self._ssrc_to_user[ssrc] = user_id

    def _handle_packet(self, data: bytes) -> None:
        """Socket reader callback. Parse RTP, decrypt, decode, dispatch."""
        self._packet_count += 1
        if self._packet_count <= 3 or self._packet_count % 1000 == 0:
            logger.info(
                f"Packet #{self._packet_count}: {len(data)} bytes, "
                f"header=[{data[0]:02x} {data[1]:02x}] SSRCs known={list(self._ssrc_to_user.keys())}"
            )

        if len(data) < RTP_HEADER_SIZE + 1:
            return

        # Check RTP version and payload type
        if data[0] != RTP_VERSION_BYTE or data[1] != RTP_PAYLOAD_TYPE:
            return

        # Extract SSRC from RTP header
        ssrc = struct.unpack_from(">I", data, 8)[0]

        # Skip our own SSRC
        try:
            if ssrc == self._vc.ssrc:
                return
        except Exception:
            return

        # Look up user
        user_id = self._ssrc_to_user.get(ssrc)
        if user_id is None:
            return

        # Decrypt
        header = data[:RTP_HEADER_SIZE]
        encrypted = data[RTP_HEADER_SIZE:]
        try:
            decrypted = self._decrypt(header, encrypted)
        except Exception:
            logger.debug(f"Failed to decrypt packet from SSRC {ssrc}", exc_info=True)
            return

        # Handle RTP header extensions (strip before Opus decode)
        opus_data = self._strip_rtp_extensions(decrypted)

        # DAVE decryption (end-to-end voice encryption, discord.py 2.7+)
        opus_data = self._dave_decrypt(user_id, opus_data)
        if opus_data is None:
            return

        # Decode Opus -> PCM
        try:
            from discord.opus import Decoder as OpusDecoder

            decoder = self._decoders.get(ssrc)
            if decoder is None:
                decoder = OpusDecoder()
                self._decoders[ssrc] = decoder

            pcm = decoder.decode(opus_data)
            if pcm and self._on_audio:
                self._on_audio(user_id, pcm)
        except Exception:
            logger.debug(f"Failed to decode Opus from SSRC {ssrc}", exc_info=True)

    def _decrypt(self, header: bytes, encrypted: bytes) -> bytes:
        """Decrypt voice data based on negotiated encryption mode."""
        mode = self._vc.mode
        secret_key = bytes(self._vc.secret_key)

        if mode == "aead_xchacha20_poly1305_rtpsize":
            # Nonce is last 4 bytes, ciphertext is everything before
            nonce_bytes = encrypted[-4:]
            ciphertext = encrypted[:-4]
            nonce = bytearray(24)
            nonce[:4] = nonce_bytes
            box = nacl.secret.Aead(secret_key)
            return box.decrypt(bytes(ciphertext), aad=bytes(header), nonce=bytes(nonce))

        elif mode == "xsalsa20_poly1305_lite":
            nonce_bytes = encrypted[-4:]
            ciphertext = encrypted[:-4]
            nonce = bytearray(24)
            nonce[:4] = nonce_bytes
            box = nacl.secret.SecretBox(secret_key)
            return box.decrypt(bytes(ciphertext), nonce=bytes(nonce))

        elif mode == "xsalsa20_poly1305_suffix":
            nonce = encrypted[-24:]
            ciphertext = encrypted[:-24]
            box = nacl.secret.SecretBox(secret_key)
            return box.decrypt(bytes(ciphertext), nonce=bytes(nonce))

        elif mode == "xsalsa20_poly1305":
            nonce = bytearray(24)
            nonce[:12] = header
            box = nacl.secret.SecretBox(secret_key)
            return box.decrypt(bytes(encrypted), nonce=bytes(nonce))

        raise ValueError(f"Unsupported encryption mode: {mode}")

    def _dave_decrypt(self, user_id: int, opus_data: bytes) -> Optional[bytes]:
        """Decrypt DAVE-encrypted Opus data if a DAVE session is active.

        Returns decrypted Opus bytes, or original bytes if DAVE is not active.
        Returns None if decryption fails.
        """
        dave_session = getattr(self._vc._connection, "dave_session", None)
        if dave_session is None or not getattr(dave_session, "ready", False):
            return opus_data  # No DAVE — pass through

        try:
            # media_type 1 = audio (davey convention)
            return dave_session.decrypt(user_id, 1, opus_data)
        except Exception:
            logger.debug(f"DAVE decrypt failed for user {user_id}", exc_info=True)
            return None

    @staticmethod
    def _strip_rtp_extensions(data: bytes) -> bytes:
        """Strip RTP header extensions from decrypted payload if present.

        Some Discord packets include a 4-byte extension header after decryption.
        The first two bytes are the profile-specific ID, next two are the length
        in 32-bit words.
        """
        if len(data) < 4:
            return data

        # Check for the Discord RTP extension profile (0xBEDE)
        if data[0] == 0xBE and data[1] == 0xDE:
            ext_length = struct.unpack_from(">H", data, 2)[0]
            skip = 4 + ext_length * 4
            if skip < len(data):
                return data[skip:]
            return b""

        return data
