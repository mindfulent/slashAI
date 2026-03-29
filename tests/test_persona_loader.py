# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for persona configuration loading and system prompt building."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agents.persona_loader import (
    CartesiaVoice,
    DiscordConfig,
    KokoroVoice,
    MemoryConfig,
    PersonaConfig,
    PersonaIdentity,
    VoiceConfig,
)

# Minimal valid persona JSON
MINIMAL_PERSONA = {
    "schema_version": 1,
    "name": "test_bot",
    "display_name": "Test Bot",
    "identity": {"personality": "Friendly and helpful."},
    "memory": {"agent_id": "test_bot"},
}

# Full persona JSON (mirrors personas/lena.json structure)
FULL_PERSONA = {
    "schema_version": 1,
    "name": "lena",
    "display_name": "Lena",
    "identity": {
        "personality": "Warm, curious, slightly sarcastic.",
        "background": "A veteran redstone engineer.",
        "speech_style": "Casual, uses contractions.",
        "behavioral_traits": ["helpful", "perfectionist"],
        "interests": ["redstone", "automation"],
    },
    "discord": {
        "status_text": "Building something clever...",
        "activity_type": "playing",
    },
    "voice": {
        "kokoro": {"speaker_id": 2, "speaker_name": "af_nicole", "speed": 1.0},
        "cartesia": {
            "voice_id": "ca9095ca-987f-4c63-bdc6-cc418167ea00",
            "model": "sonic-3",
            "language": "en",
            "default_emotion": "positivity:moderate",
            "speed": 1.0,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 24000,
            },
        },
        "default_provider": "cartesia",
    },
    "memory": {"agent_id": "lena", "cross_platform": True},
}


def _write_persona(tmp_path, name, data):
    """Write a persona JSON file and return its path."""
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


class TestPersonaLoad:
    """Tests for PersonaConfig.load()."""

    def test_load_full_persona(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.schema_version == 1
        assert persona.name == "lena"
        assert persona.display_name == "Lena"

    def test_identity_fields(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        assert "sarcastic" in persona.identity.personality
        assert persona.identity.background == "A veteran redstone engineer."
        assert persona.identity.speech_style == "Casual, uses contractions."
        assert persona.identity.behavioral_traits == ["helpful", "perfectionist"]
        assert persona.identity.interests == ["redstone", "automation"]

    def test_discord_config(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.discord.status_text == "Building something clever..."
        assert persona.discord.activity_type == "playing"

    def test_kokoro_voice(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.voice.kokoro is not None
        assert persona.voice.kokoro.speaker_id == 2
        assert persona.voice.kokoro.speaker_name == "af_nicole"
        assert persona.voice.kokoro.speed == 1.0

    def test_cartesia_voice(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.voice.cartesia is not None
        assert persona.voice.cartesia.voice_id == "ca9095ca-987f-4c63-bdc6-cc418167ea00"
        assert persona.voice.cartesia.model == "sonic-3"
        assert persona.voice.cartesia.default_emotion == "positivity:moderate"

    def test_cartesia_output_format_filtered(self, tmp_path):
        """output_format is a SoulCraft-only field and should not be passed to CartesiaVoice."""
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        # CartesiaVoice dataclass has no output_format field — if filtering
        # didn't work, load() would raise TypeError on unknown kwarg
        assert persona.voice.cartesia is not None
        assert not hasattr(persona.voice.cartesia, "output_format")

    def test_voice_default_provider(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)
        assert persona.voice.default_provider == "cartesia"

    def test_memory_config(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.memory.agent_id == "lena"
        assert persona.memory.cross_platform is True


class TestPersonaDefaults:
    """Tests for default values when optional fields are missing."""

    def test_minimal_persona_loads(self, tmp_path):
        path = _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.name == "test_bot"
        assert persona.display_name == "Test Bot"
        assert persona.identity.personality == "Friendly and helpful."

    def test_missing_optional_identity_fields(self, tmp_path):
        path = _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.identity.background is None
        assert persona.identity.speech_style is None
        assert persona.identity.behavioral_traits == []
        assert persona.identity.interests == []

    def test_missing_discord_config_defaults(self, tmp_path):
        path = _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.discord.status_text is None
        assert persona.discord.activity_type == "playing"

    def test_missing_voice_config_defaults(self, tmp_path):
        path = _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)
        persona = PersonaConfig.load(path)

        assert persona.voice.kokoro is None
        assert persona.voice.cartesia is None
        assert persona.voice.default_provider == "kokoro"

    def test_name_falls_back_to_stem(self, tmp_path):
        data = {
            "schema_version": 1,
            "identity": {"personality": "Test."},
            "memory": {"agent_id": "fallback"},
        }
        path = _write_persona(tmp_path, "my_bot", data)
        persona = PersonaConfig.load(path)

        assert persona.name == "my_bot"

    def test_display_name_falls_back_to_name(self, tmp_path):
        data = {
            "schema_version": 1,
            "name": "dean",
            "identity": {"personality": "Test."},
            "memory": {"agent_id": "dean"},
        }
        path = _write_persona(tmp_path, "dean", data)
        persona = PersonaConfig.load(path)

        assert persona.display_name == "dean"


class TestBuildSystemPrompt:
    """Tests for PersonaConfig.build_system_prompt()."""

    def test_includes_personality(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)
        prompt = persona.build_system_prompt()

        assert "You are Lena." in prompt
        assert "Warm, curious, slightly sarcastic." in prompt

    def test_includes_background(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)
        prompt = persona.build_system_prompt()

        assert "Background: A veteran redstone engineer." in prompt

    def test_includes_speech_style(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)
        prompt = persona.build_system_prompt()

        assert "Communication style: Casual, uses contractions." in prompt

    def test_omits_background_when_none(self, tmp_path):
        path = _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)
        persona = PersonaConfig.load(path)
        prompt = persona.build_system_prompt()

        assert "Background:" not in prompt

    def test_omits_speech_style_when_none(self, tmp_path):
        path = _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)
        persona = PersonaConfig.load(path)
        prompt = persona.build_system_prompt()

        assert "Communication style:" not in prompt

    def test_includes_discord_context(self, tmp_path):
        path = _write_persona(tmp_path, "lena", FULL_PERSONA)
        persona = PersonaConfig.load(path)
        prompt = persona.build_system_prompt()

        assert "Discord" in prompt
        assert "NO TRAILING QUESTIONS" in prompt


class TestLoadAll:
    """Tests for PersonaConfig.load_all()."""

    def test_loads_multiple_personas(self, tmp_path):
        _write_persona(tmp_path, "lena", FULL_PERSONA)
        _write_persona(tmp_path, "test_bot", MINIMAL_PERSONA)

        personas = PersonaConfig.load_all(tmp_path)
        assert len(personas) == 2
        assert "lena" in personas
        assert "test_bot" in personas

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        personas = PersonaConfig.load_all(tmp_path / "nonexistent")
        assert personas == {}

    def test_skips_malformed_json(self, tmp_path):
        _write_persona(tmp_path, "good", MINIMAL_PERSONA)
        (tmp_path / "bad.json").write_text("{invalid json")

        personas = PersonaConfig.load_all(tmp_path)
        assert len(personas) == 1
        assert "test_bot" in personas

    def test_ignores_non_json_files(self, tmp_path):
        _write_persona(tmp_path, "good", MINIMAL_PERSONA)
        (tmp_path / "readme.txt").write_text("not a persona")

        personas = PersonaConfig.load_all(tmp_path)
        assert len(personas) == 1


class TestLoadRealPersona:
    """Test loading the actual personas/lena.json file."""

    def test_load_lena_persona(self):
        lena_path = Path(__file__).parent.parent / "personas" / "lena.json"
        if not lena_path.exists():
            pytest.skip("personas/lena.json not found")

        persona = PersonaConfig.load(lena_path)
        assert persona.name == "lena"
        assert persona.display_name == "Lena"
        assert persona.memory.agent_id == "lena"
        assert persona.voice.default_provider == "cartesia"
        assert persona.voice.cartesia is not None
        assert persona.voice.kokoro is not None
