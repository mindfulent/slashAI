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
from voice.audio_source import FRAME_SIZE
from voice.resampler import AudioResampler, StreamResampler
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

            if not result.text:
                return

            # Track for echo guard
            self._echo_guard.add_bot_text(result.text)

            # TTS and play
            logger.info(f"[{self._persona.display_name}] LLM response ({len(result.text)} chars), starting TTS...")
            try:
                await self._speak(result.text)
            except Exception as e:
                logger.error(f"[{self._persona.display_name}] Speak failed: {e}", exc_info=True)

    # Pre-buffer 100ms of audio before starting playback to avoid
    # playing Cartesia's initial silence/low-energy lead-in
    MIN_PREBUFFER_BYTES = 5 * FRAME_SIZE  # 5 frames = 100ms

    async def _speak(self, text: str) -> None:
        """Convert text to speech and play through voice channel."""
        if not self._voice_client or not self._voice_client.is_connected():
            return

        # Clean and chunk for TTS
        cleaned = self._preprocessor.clean_for_tts(text)
        chunks = self._preprocessor.chunk_for_tts(cleaned)
        if not chunks:
            return

        # Infer emotion from first chunk
        emotion = self._emotion.infer(chunks[0])
        cartesia_voice = self._persona.voice.cartesia
        if not emotion and cartesia_voice and cartesia_voice.default_emotion:
            emotion = cartesia_voice.default_emotion
        speed = cartesia_voice.speed if cartesia_voice else 1.0

        # Estimate speech duration for echo guard (~60ms per char)
        self._echo_guard.mark_bot_speaking(len(cleaned) * 0.06)

        # Stateful resampler for smooth audio across TTS chunks
        resampler = StreamResampler()
        source = StreamingAudioSource()
        play_started = False

        logger.info(f"TTS: {len(chunks)} chunk(s), {len(cleaned)} chars")

        # Stream all chunks as one continuous audio context
        async for pcm_24k in self._tts.synthesize_stream(
            chunks, emotion=emotion, speed=speed
        ):
            pcm_48k_stereo = resampler.tts_to_discord(pcm_24k)
            source.feed(pcm_48k_stereo)

            # Start playback after buffering enough audio
            if not play_started and self._voice_client:
                if source.buffered_bytes >= self.MIN_PREBUFFER_BYTES:
                    self._voice_client.play(source, signal_type="voice")
                    play_started = True

        # If we never hit the pre-buffer threshold (very short response),
        # start playback now with whatever we have
        if not play_started and self._voice_client and source.buffered_bytes > 0:
            self._voice_client.play(source, signal_type="voice")
            play_started = True

        source.finish()

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
