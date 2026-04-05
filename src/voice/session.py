# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Voice session orchestrator.

Manages a single voice channel connection for a persona agent.
Ties together: receiver → VAD → STT → LLM → TTS → playback.
"""

import asyncio
import logging
import os
import time
from typing import Optional

import discord

from agents.persona_loader import PersonaConfig
from claude_client import ClaudeClient
from voice.audio_source import StreamingAudioSource
from voice.cartesia_stt import CartesiaSTTClient
from voice.cartesia_tts import CartesiaTTSClient
from voice.echo_guard import EchoGuard
from voice.receiver import AudioReceiver
from voice.resampler import AudioResampler
from voice.text_processor import EmotionInference, TextPreprocessor
from voice.vad import VADConfig, VoiceActivityDetector

logger = logging.getLogger(__name__)


class VoiceSession:
    """Manages a single voice channel session for a persona agent.

    Orchestrates: join → receive audio → VAD → STT → LLM → TTS → play → leave
    """

    def __init__(
        self,
        client: discord.Client,
        persona: PersonaConfig,
        claude: ClaudeClient,
    ):
        self._client = client
        self._persona = persona
        self._claude = claude

        # Cartesia config from persona
        cartesia_voice = persona.voice.cartesia
        api_key = os.getenv("CARTESIA_API_KEY", "")

        # Components
        self._tts = CartesiaTTSClient(
            api_key=api_key,
            voice_id=cartesia_voice.voice_id or "",
            model=cartesia_voice.model if cartesia_voice else "sonic-3",
        )
        self._stt = CartesiaSTTClient(api_key=api_key)
        self._resampler = AudioResampler()
        self._preprocessor = TextPreprocessor()
        self._emotion = EmotionInference()
        self._echo_guard = EchoGuard()

        # Per-user VAD instances
        self._user_vads: dict[int, VoiceActivityDetector] = {}

        # State
        self._voice_client: Optional[discord.VoiceClient] = None
        self._receiver: Optional[AudioReceiver] = None
        self._running = False
        self._processing_lock = asyncio.Lock()

    async def join(self, channel: discord.VoiceChannel) -> None:
        """Join a voice channel and start the listening loop."""
        if self._running:
            raise RuntimeError("Already in a voice session")

        self._voice_client = await channel.connect()
        await self._tts.connect()

        # Set up audio receiver
        self._receiver = AudioReceiver(self._voice_client)
        self._running = True
        self._receiver.start(self._on_audio_received)

        logger.info(
            f"[{self._persona.display_name}] Joined voice channel: {channel.name}"
        )

    def _on_audio_received(self, user_id: int, pcm_48k_stereo: bytes) -> None:
        """Called from SocketReader thread. Must be fast.

        Downsample, feed to per-user VAD, and schedule async processing.
        """
        if not self._running:
            return

        # Downsample for STT
        pcm_16k_mono = self._resampler.discord_to_stt(pcm_48k_stereo)

        # Get or create VAD for this user
        vad = self._user_vads.get(user_id)
        if vad is None:
            vad = VoiceActivityDetector()
            self._user_vads[user_id] = vad

        # Feed to VAD
        utterance = vad.process(pcm_16k_mono, time.monotonic())
        if utterance is not None:
            # Schedule async processing on the event loop
            loop = self._client.loop
            asyncio.run_coroutine_threadsafe(
                self._handle_utterance(user_id, utterance),
                loop,
            )

    async def _handle_utterance(self, user_id: int, pcm_16k_mono: bytes) -> None:
        """Process a completed utterance: STT → echo check → LLM → TTS → play."""
        try:
            await self._handle_utterance_inner(user_id, pcm_16k_mono)
        except Exception as e:
            logger.error(f"[{self._persona.display_name}] Utterance pipeline error: {e}", exc_info=True)

    async def _handle_utterance_inner(self, user_id: int, pcm_16k_mono: bytes) -> None:
        async with self._processing_lock:
            if not self._running:
                return

            # Wrap PCM in WAV for STT
            wav_data = self._resampler.pcm_to_wav(pcm_16k_mono)

            # Transcribe
            transcript = await self._stt.transcribe(wav_data)
            if not transcript:
                logger.debug("Empty transcript, skipping")
                return

            # Echo guard
            if self._echo_guard.should_reject(transcript):
                logger.debug(f"Echo guard rejected: {transcript[:50]}")
                return

            # Clean transcript
            cleaned = self._preprocessor.clean_for_tts(transcript)
            if not cleaned:
                return

            logger.info(
                f"[{self._persona.display_name}] Voice from user {user_id}: {cleaned}"
            )

            # LLM response — use the voice channel's ID as channel_id
            channel_id = (
                str(self._voice_client.channel.id)
                if self._voice_client
                else "0"
            )
            result = await self._claude.chat(
                user_id=str(user_id),
                channel_id=channel_id,
                content=cleaned,
            )

            logger.info(f"[{self._persona.display_name}] LLM result: text={bool(result.text)}, len={len(result.text) if result.text else 0}, type={type(result)}")
            if hasattr(result, '__dict__'):
                logger.info(f"  result attrs: {list(result.__dict__.keys())}")
            if not result.text:
                logger.warning(f"[{self._persona.display_name}] LLM returned empty text, result={result}")
                return

            # Track for echo guard
            self._echo_guard.add_bot_text(result.text)

            # TTS and play
            logger.info(f"[{self._persona.display_name}] LLM response ({len(result.text)} chars), starting TTS...")
            try:
                await self._speak(result.text)
            except Exception as e:
                logger.error(f"[{self._persona.display_name}] Speak failed: {e}", exc_info=True)

    async def _speak(self, text: str) -> None:
        """Convert text to speech and play through voice channel."""
        if not self._voice_client or not self._voice_client.is_connected():
            logger.warning("Cannot speak — not connected to voice")
            return

        # Clean and chunk for TTS
        cleaned = self._preprocessor.clean_for_tts(text)
        chunks = self._preprocessor.chunk_for_tts(cleaned)
        if not chunks:
            logger.warning("No TTS chunks after cleaning")
            return

        logger.info(f"TTS: {len(chunks)} chunk(s), first: {chunks[0][:60]}...")

        source = StreamingAudioSource()
        play_started = False
        total_bytes = 0

        # Estimate speech duration for echo guard (~60ms per char)
        self._echo_guard.mark_bot_speaking(len(cleaned) * 0.06)

        for i, chunk_text in enumerate(chunks):
            # Infer emotion from text
            emotion = self._emotion.infer(chunk_text)
            cartesia_voice = self._persona.voice.cartesia
            if not emotion and cartesia_voice and cartesia_voice.default_emotion:
                emotion = cartesia_voice.default_emotion

            speed = cartesia_voice.speed if cartesia_voice else 1.0

            logger.info(f"TTS chunk {i+1}/{len(chunks)}: synthesizing ({len(chunk_text)} chars)...")
            chunk_bytes = 0
            chunk_count = 0
            async for pcm_24k in self._tts.synthesize(
                chunk_text, emotion=emotion, speed=speed
            ):
                chunk_count += 1
                if chunk_count <= 2:
                    import audioop as _ao
                    rms_24k = _ao.rms(pcm_24k, 2) if len(pcm_24k) >= 2 else 0
                    logger.info(f"  TTS raw chunk {chunk_count}: {len(pcm_24k)} bytes, RMS={rms_24k}")

                pcm_48k_stereo = self._resampler.tts_to_discord(pcm_24k)

                if chunk_count <= 2:
                    rms_48k = _ao.rms(pcm_48k_stereo, 2) if len(pcm_48k_stereo) >= 2 else 0
                    logger.info(f"  After resample: {len(pcm_48k_stereo)} bytes, RMS={rms_48k}")

                source.feed(pcm_48k_stereo)
                chunk_bytes += len(pcm_48k_stereo)

                if not play_started and self._voice_client:
                    # Log DAVE outgoing encryption state
                    conn = self._voice_client._connection
                    dave_s = getattr(conn, "dave_session", None)
                    can_enc = getattr(conn, "can_encrypt", "N/A")
                    logger.info(
                        f"Playback starting: dave_session={'yes' if dave_s else 'no'} "
                        f"can_encrypt={can_enc} "
                        f"dave_ready={getattr(dave_s, 'ready', 'N/A')}"
                    )
                    self._voice_client.play(source, signal_type="voice")
                    play_started = True
                    logger.info("Playback started")

            total_bytes += chunk_bytes
            logger.info(f"TTS chunk {i+1} done: {chunk_bytes} bytes, {chunk_count} ws chunks")

        source.finish()
        logger.info(f"TTS complete: {total_bytes} total bytes, play_started={play_started}")

        # Wait for playback to complete
        if play_started:
            while self._voice_client and self._voice_client.is_playing():
                await asyncio.sleep(0.1)

    async def leave(self) -> None:
        """Disconnect from voice and clean up all resources."""
        self._running = False

        if self._receiver:
            self._receiver.stop()
            self._receiver = None

        if self._voice_client:
            if self._voice_client.is_playing():
                self._voice_client.stop()
            await self._voice_client.disconnect()
            self._voice_client = None

        await self._tts.close()
        await self._stt.close()
        self._user_vads.clear()

        logger.info(f"[{self._persona.display_name}] Left voice channel")

    @property
    def is_connected(self) -> bool:
        """Whether the bot is currently in a voice channel."""
        return (
            self._voice_client is not None and self._voice_client.is_connected()
        )

    @property
    def channel(self) -> Optional[discord.VoiceChannel]:
        """The current voice channel, or None."""
        if self._voice_client:
            return self._voice_client.channel
        return None
