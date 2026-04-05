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
from voice.name_filter import NameFilter
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
        self._name_filter = NameFilter(
            display_name=persona.display_name,
            aliases=persona.voice.name_aliases,
        )

        # Per-user VAD instances
        self._user_vads: dict[int, VoiceActivityDetector] = {}

        # State
        self._voice_client: Optional[discord.VoiceClient] = None
        self._receiver: Optional[AudioReceiver] = None
        self._running = False
        self._is_speaking = False  # Mute reception while bot is playing
        self._processing_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None

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

        # Background timer to flush VAD buffers when Discord stops sending
        # packets during silence (Opus DTX). Without this, utterances stay
        # buffered until the user makes another sound.
        self._flush_task = asyncio.create_task(self._vad_flush_loop())

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

    async def _vad_flush_loop(self) -> None:
        """Periodically flush VAD buffers that Discord left hanging.

        Discord uses Opus DTX — it stops sending packets when a user goes
        silent. That means process() stops being called and the silence
        timeout never fires. This loop checks every 200ms and flushes any
        pending utterance whose silence timeout has elapsed.
        """
        while self._running:
            await asyncio.sleep(0.2)
            if not self._running or self._is_speaking:
                continue
            now = time.monotonic()
            for user_id, vad in list(self._user_vads.items()):
                utterance = vad.flush(now)
                if utterance is not None:
                    vad_trigger_time = time.monotonic()
                    logger.info(
                        f"[{self._persona.display_name}] VAD flush triggered: "
                        f"{len(utterance)} bytes audio"
                    )
                    await self._handle_utterance(user_id, utterance, vad_trigger_time)

    async def _handle_utterance(self, user_id: int, pcm_16k_mono: bytes, t0: float = 0) -> None:
        """Process a completed utterance: STT → echo check → LLM → TTS → play."""
        logger.info(f"[{self._persona.display_name}] Processing utterance: {len(pcm_16k_mono)} bytes")
        try:
            await self._handle_utterance_inner(user_id, pcm_16k_mono, t0)
        except Exception as e:
            logger.error(f"[{self._persona.display_name}] Utterance pipeline error: {e}", exc_info=True)

    async def _handle_utterance_inner(self, user_id: int, pcm_16k_mono: bytes, t0: float) -> None:
        # Acquire lock only for STT + LLM + TTS synthesis (not playback)
        async with self._processing_lock:
            if not self._running:
                return

            t_lock = time.monotonic()

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

            # Name-address filter: in multi-user channels, only respond when addressed
            if self._human_count() >= 2 and not self._name_filter.is_addressed(cleaned):
                logger.debug(
                    f"[{self._persona.display_name}] Skipped (not addressed) "
                    f"from user {user_id}: {cleaned!r}"
                )
                return

            logger.info(
                f"[{self._persona.display_name}] Voice from user {user_id}: {cleaned}"
            )

            # Streaming LLM → TTS → start playback (lock released after synthesis)
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
        # Lock released here — playback continues via AudioPlayer thread
        # _is_speaking cleared by _on_playback_done callback

    def _on_playback_done(self, error) -> None:
        """Called by discord.py AudioPlayer thread when playback finishes."""
        self._is_speaking = False
        if error:
            logger.error(f"Playback error: {error}")

    async def _track_memory_async(
        self, user_id: str, channel_id: str, content: str, response: str
    ) -> None:
        """Fire-and-forget memory tracking. Runs outside the processing lock."""
        try:
            channel = self._voice_client.channel if self._voice_client else None
            await self._claude.memory.track_message(
                int(user_id),
                int(channel_id),
                channel,
                content,
                response,
                agent_id=self._persona.memory.agent_id,
            )
        except Exception as e:
            logger.warning(f"Voice memory tracking failed: {e}")

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

        Starts playback and returns after all TTS audio is fed to the buffer.
        Does NOT wait for playback to complete — that happens via the
        _on_playback_done callback. This keeps the processing lock held
        only during synthesis, not during the 10-20s playback.
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
                        # Use after callback to clear _is_speaking when done
                        self._voice_client.play(
                            source, signal_type="voice",
                            after=self._on_playback_done,
                        )
                        play_started = True

                        if t0:
                            t_play = time.monotonic()
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

            logger.info(f"TTS: {sentence_count} sentence(s), {len(full_text)} chars")

            # If no audio was produced (TTS error, single-char response, etc.),
            # playback never started so _on_playback_done won't fire.
            # Clear _is_speaking now or all future audio is silently dropped.
            if not play_started:
                self._is_speaking = False

            # Fire-and-forget memory tracking (don't block the pipeline)
            if self._claude.memory and full_text:
                asyncio.create_task(
                    self._track_memory_async(user_id, channel_id, content, full_text)
                )

        except Exception:
            # If synthesis fails, make sure we unmute
            self._is_speaking = False
            raise

    async def leave(self) -> None:
        """Disconnect from voice and clean up all resources."""
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None

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

    def _human_count(self) -> int:
        """Count non-bot members in the current voice channel."""
        if not self._voice_client or not self._voice_client.channel:
            return 0
        return sum(1 for m in self._voice_client.channel.members if not m.bot)

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
