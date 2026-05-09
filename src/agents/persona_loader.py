# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Persona configuration loader for multi-agent Discord bots.
Loads persona JSON files (shared format with SoulCraft) and builds system prompts.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PersonaIdentity:
    personality: str = ""
    background: Optional[str] = None
    speech_style: Optional[str] = None
    behavioral_traits: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)


@dataclass
class DiscordConfig:
    status_text: Optional[str] = None
    activity_type: str = "playing"


@dataclass
class KokoroVoice:
    speaker_id: int = -1
    speaker_name: Optional[str] = None
    speed: float = 1.0


@dataclass
class CartesiaVoice:
    voice_id: Optional[str] = None
    model: str = "sonic-3"
    language: str = "en"
    default_emotion: Optional[str] = None
    speed: float = 1.0


@dataclass
class VoiceConfig:
    kokoro: Optional[KokoroVoice] = None
    cartesia: Optional[CartesiaVoice] = None
    default_provider: str = "kokoro"
    name_aliases: list[str] = field(default_factory=list)  # STT name variants for multi-user filtering


@dataclass
class MemoryConfig:
    agent_id: str = ""
    cross_platform: bool = True


@dataclass
class ProactiveBudgets:
    reactions_per_day: int = 15
    replies_per_day: int = 3
    new_topics_per_day: int = 1
    inter_agent_turns_per_day: int = 4


@dataclass
class ProactiveCooldowns:
    reaction_seconds: int = 600
    reply_seconds: int = 1800
    new_topic_seconds: int = 43200


@dataclass
class ProactiveQuietHours:
    timezone: str = "America/Los_Angeles"
    start: str = "22:00"
    end: str = "07:00"


@dataclass
class ProactiveConfig:
    """Per-persona proactive-interaction policy (Enhancement 015)."""
    enabled: bool = False
    channel_allowlist: list[int] = field(default_factory=list)
    budgets: ProactiveBudgets = field(default_factory=ProactiveBudgets)
    cooldowns: ProactiveCooldowns = field(default_factory=ProactiveCooldowns)
    quiet_hours: ProactiveQuietHours = field(default_factory=ProactiveQuietHours)
    engagement_temperature: float = 0.85
    decider_model: str = "claude-haiku-4-5-20251001"
    actor_model: str = "claude-sonnet-4-6"
    silence_threshold_hours: int = 4
    engages_with_personas: list[str] = field(default_factory=list)


@dataclass
class PersonaConfig:
    schema_version: int = 1
    name: str = ""
    display_name: str = ""
    identity: PersonaIdentity = field(default_factory=PersonaIdentity)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)

    @classmethod
    def load(cls, path: Path) -> "PersonaConfig":
        """Load a persona from a JSON file."""
        with open(path) as f:
            data = json.load(f)

        identity_data = data.get("identity", {})
        discord_data = data.get("discord", {})
        voice_data = data.get("voice", {})
        memory_data = data.get("memory", {})
        proactive_data = data.get("proactive", {})

        # Parse nested voice configs
        kokoro = None
        if "kokoro" in voice_data:
            kokoro = KokoroVoice(**voice_data["kokoro"])

        cartesia = None
        if "cartesia" in voice_data:
            cart_data = voice_data["cartesia"]
            # Filter out output_format (not needed in Python)
            cart_data = {k: v for k, v in cart_data.items() if k != "output_format"}
            cartesia = CartesiaVoice(**cart_data)

        proactive = cls._parse_proactive(proactive_data)

        return cls(
            schema_version=data.get("schema_version", 1),
            name=data.get("name", path.stem),
            display_name=data.get("display_name", data.get("name", path.stem)),
            identity=PersonaIdentity(**identity_data),
            discord=DiscordConfig(**discord_data),
            voice=VoiceConfig(
                kokoro=kokoro,
                cartesia=cartesia,
                default_provider=voice_data.get("default_provider", "kokoro"),
                name_aliases=voice_data.get("name_aliases", []),
            ),
            memory=MemoryConfig(**memory_data),
            proactive=proactive,
        )

    @staticmethod
    def _parse_proactive(data: dict) -> "ProactiveConfig":
        """Parse the optional `proactive` block. Defaults applied for missing fields."""
        if not data:
            return ProactiveConfig()

        budgets_data = data.get("budgets", {})
        cooldowns_data = data.get("cooldowns", {})
        quiet_hours_data = data.get("quiet_hours", {})

        # Coerce channel IDs to int (JSON may carry them as strings)
        raw_allowlist = data.get("channel_allowlist", [])
        channel_allowlist: list[int] = []
        for cid in raw_allowlist:
            try:
                channel_allowlist.append(int(cid))
            except (TypeError, ValueError):
                logger.warning(f"Skipping invalid channel_id in proactive.channel_allowlist: {cid!r}")

        return ProactiveConfig(
            enabled=bool(data.get("enabled", False)),
            channel_allowlist=channel_allowlist,
            budgets=ProactiveBudgets(**budgets_data),
            cooldowns=ProactiveCooldowns(**cooldowns_data),
            quiet_hours=ProactiveQuietHours(**quiet_hours_data),
            engagement_temperature=float(data.get("engagement_temperature", 0.85)),
            decider_model=data.get("decider_model", "claude-haiku-4-5-20251001"),
            actor_model=data.get("actor_model", "claude-sonnet-4-6"),
            silence_threshold_hours=int(data.get("silence_threshold_hours", 4)),
            engages_with_personas=list(data.get("engages_with_personas", [])),
        )

    def build_system_prompt(self) -> str:
        """Construct a Discord-appropriate system prompt from identity fields."""
        parts = [f"You are {self.display_name}. {self.identity.personality}"]

        if self.identity.background:
            parts.append(f"\n\nBackground: {self.identity.background}")

        if self.identity.speech_style:
            parts.append(f"\n\nCommunication style: {self.identity.speech_style}")

        parts.append(
            f"\n\n## Context"
            f"\nYou are {self.display_name}, a Discord bot in a server. "
            f"You have your own bot account and appear as a member of the server. "
            f"Users interact with you by mentioning @{self.display_name} or sending you DMs."
            f"\n\n## Communication Style"
            f"\nYou're chatting on Discord, not writing essays. Match how humans actually use Discord:"
            f"\n- Short, punchy messages. A few sentences is usually enough."
            f"\n- Don't over-explain or pad responses. Get to the point."
            f"\n- Skip the preamble — no \"Great question!\" or \"I'd be happy to help!\""
            f"\n- Minimal emojis — maybe one occasionally for emphasis, never decoration."
            f"\n- Hard limit: 2000 characters (Discord max)."
            f"\n- **NO TRAILING QUESTIONS.** Make your point, then stop."
            f"\n\n## What You're Not"
            f"\n- Not a generic AI chatbot in a window. You are a bot member of this Discord server."
            f"\n- Not condescending. Assume the person is intelligent."
            f"\n- Not evasive. If you don't know, say so directly."
            f"\n- Not a conversation prolonger. No trailing questions. No \"let me know if you need anything.\""
        )

        return "".join(parts)

    @staticmethod
    def load_all(directory: Path) -> dict[str, "PersonaConfig"]:
        """Load all persona files from a directory."""
        personas = {}
        if not directory.exists():
            return personas
        for path in directory.glob("*.json"):
            try:
                persona = PersonaConfig.load(path)
                personas[persona.name] = persona
                logger.info(f"Loaded persona '{persona.display_name}' from {path.name}")
            except Exception as e:
                logger.error(f"Failed to load persona {path}: {e}")
        return personas
