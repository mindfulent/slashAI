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
class PersonaConfig:
    schema_version: int = 1
    name: str = ""
    display_name: str = ""
    identity: PersonaIdentity = field(default_factory=PersonaIdentity)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @classmethod
    def load(cls, path: Path) -> "PersonaConfig":
        """Load a persona from a JSON file."""
        with open(path) as f:
            data = json.load(f)

        identity_data = data.get("identity", {})
        discord_data = data.get("discord", {})
        voice_data = data.get("voice", {})
        memory_data = data.get("memory", {})

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
