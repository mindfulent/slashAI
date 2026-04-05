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
from voice.resampler import AudioResampler, StreamResampler
from voice.text_processor import EmotionInference, TextPreprocessor
from voice.vad import VADConfig, VoiceActivityDetector

logger = logging.getLogger(__name__)


def _ms(seconds: float) -> str:
    """Format seconds as milliseconds string."""
    return f"{seconds * 1000:.0f}ms"


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
        self._is_speaking = False  # Mute reception while bot is playing
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
        if not self._running or self._is_speaking:
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
            vad_trigger_time = time.monotonic()
            logger.info(f"[{self._persona.display_name}] VAD triggered: {len(utterance)} bytes audio")
            # Schedule async processing on the event loop
            loop = self._client.loop
            asyncio.run_coroutine_threadsafe(
                self._handle_utterance(user_id, utterance, vad_trigger_time),
                loop,
            )

    async def _handle_utterance(self, user_id: int, pcm_16k_mono: bytes, t0: float = 0) -> None:
        """Process a completed utterance: STT → echo check → LLM → TTS → play."""
        logger.info(f"[{self._persona.display_name}] Processing utterance: {len(pcm_16k_mono)} bytes")
        try:
            await self._handle_utterance_inner(user_id, pcm_16k_mono, t0)
        except Exception as e:
            logger.error(f"[{self._persona.display_name}] Utterance pipeline error: {e}", exc_info=True)

    async def _handle_utterance_inner(self, user_id: int, pcm_16k_mono: bytes, t0: float) -> None:
        logger.info(f"  Acquiring processing lock (is_speaking={self._is_speaking})")
        async with self._processing_lock:
            if not self._running:
                logger.info("  Not running, skipping")
                return

            t_lock = time.monotonic()
            logger.info(f"  Lock acquired, starting STT ({len(pcm_16k_mono)} bytes)")

            # Wrap PCM in WAV for STT
            wav_data = self._resampler.pcm_to_wav(pcm_16k_mono)

            # Transcribe
            transcript = await self._stt.transcribe(wav_data)
            t_stt = time.monotonic()

            if not transcript:
                return

            # Echo guard
            if self._echo_guard.should_reject(transcript):
                return

            # Clean transcript
            cleaned = self._preprocessor.clean_for_tts(transcript)
            if not cleaned:
                return

            logger.info(
                f"[{self._persona.display_name}] Voice from user {user_id}: {cleaned}"
            )

            # Streaming LLM → TTS → playback pipeline
            channel_id = (
                str(self._voice_client.channel.id)
                if self._voice_client
                else "0"
            )
            await self._speak_streaming(
                user_id=str(user_id),
                channel_id=channel_id,
                content=cleaned,
                t0=t0,
                t_stt=t_stt,
                t_lock=t_lock,
            )

    async def _speak_streaming(
        self,
        user_id: str,
        channel_id: str,
        content: str,
        t0: float = 0,
        t_stt: float = 0,
        t_lock: float = 0,
    ) -> None:
        """Stream LLM response sentence-by-sentence through TTS to voice.

        Combines LLM streaming + TTS synthesis + playback in one pipeline.
        Each sentence plays as soon as it's synthesized, while the LLM
        continues generating the next sentence.
        """
        if not self._voice_client or not self._voice_client.is_connected():
            return

        # Voice config
        cartesia_voice = self._persona.voice.cartesia
        emotion = cartesia_voice.default_emotion if cartesia_voice else None
        speed = cartesia_voice.speed if cartesia_voice else 1.0

        # Mute audio reception while speaking (prevents echo feedback)
        self._is_speaking = True
        for vad in self._user_vads.values():
            vad.reset()

        resampler = StreamResampler()
        source = StreamingAudioSource()
        play_started = False
        sentences = []
        sentence_count = 0

        try:
            async for sentence in self._claude.chat_streaming(
                user_id=user_id,
                channel_id=channel_id,
                content=content,
                channel=self._voice_client.channel if self._voice_client else None,
            ):
                sentence_count += 1
                sentences.append(sentence)

                # Infer emotion from this sentence
                sent_emotion = self._emotion.infer(sentence) or emotion

                # TTS this sentence immediately
                async for pcm_24k in self._tts.synthesize(
                    sentence, emotion=sent_emotion, speed=speed
                ):
                    pcm_48k = resampler.tts_to_discord(pcm_24k)
                    source.feed(pcm_48k)

                    if not play_started and self._voice_client:
                        self._voice_client.play(source, signal_type="voice")
                        play_started = True
                        t_play = time.monotonic()

                        if t0:
                            logger.info(
                                f"LATENCY: lock_wait={_ms(t_lock - t0)} "
                                f"stt={_ms(t_stt - t_lock)} "
                                f"llm_first_sentence={_ms(t_play - t_stt)} "
                                f"TOTAL={_ms(t_play - t0)}"
                            )

            source.finish()

            # Echo guard with full response
            full_text = " ".join(sentences)
            self._echo_guard.add_bot_text(full_text)

            # Wait for playback to complete
            if play_started:
                while self._voice_client and self._voice_client.is_playing():
                    await asyncio.sleep(0.1)

                logger.info(
                    f"TTS: {sentence_count} sentence(s), {len(full_text)} chars, "
                    f"playback={_ms(time.monotonic() - t_play)}"
                )
        finally:
            self._is_speaking = False

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
