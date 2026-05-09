# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the decider sanitization layer (Enhancement 015 / v0.14.0).

Sanitization is the actor's defense against MAST FM-2.6 (Reasoning-Action
Mismatch) and FM-1.2 (Disobey Role Specification): the decider can suggest,
but the actor enforces.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agents.persona_loader import (
    PersonaConfig,
    PersonaIdentity,
    ProactiveConfig,
)
from proactive.decider import (
    ProactiveDecider,
    _extract_json_block,
    _looks_like_emoji,
)
from proactive.observer import DeciderInput, FormattedMsg
from proactive.store import BudgetSummary


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _persona(engages: list[str] | None = None) -> PersonaConfig:
    return PersonaConfig(
        schema_version=2,
        name="lena",
        display_name="Lena",
        identity=PersonaIdentity(personality="warm farmer"),
        proactive=ProactiveConfig(
            enabled=True,
            engages_with_personas=engages or ["slashai"],
        ),
    )


def _ctx(
    *,
    persona: PersonaConfig | None = None,
    recent_message_ids: list[int] | None = None,
    budget: BudgetSummary | None = None,
) -> DeciderInput:
    if persona is None:
        persona = _persona()
    if recent_message_ids is None:
        recent_message_ids = [101, 102, 103]
    msgs = [
        FormattedMsg(
            message_id=mid,
            author_id=1000 + i,
            author_name=f"user{i}",
            is_bot=False,
            content=f"message {mid}",
            created_at=datetime.now(timezone.utc),
        )
        for i, mid in enumerate(recent_message_ids)
    ]
    return DeciderInput(
        persona=persona,
        channel_id=1453800829986279554,
        guild_id=999,
        channel_name="test-channel",
        trigger="activity",
        recent_messages=msgs,
        triggering_message=msgs[-1] if msgs else None,
        budget_remaining=budget or BudgetSummary(reactions=15, replies=3, new_topics=1),
    )


def _decider() -> ProactiveDecider:
    """A decider that doesn't actually call the API; we test _sanitize directly."""
    return ProactiveDecider(anthropic_client=None)  # type: ignore[arg-type]


def _sanitize_json(decider: ProactiveDecider, payload: dict, ctx: DeciderInput):
    """Helper to call _sanitize with a JSON payload."""
    return decider._sanitize(
        text=json.dumps(payload),
        ctx=ctx,
        input_tokens=10,
        output_tokens=20,
        decider_model="claude-haiku-test",
    )


# ---------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------

class TestExtractJsonBlock:
    def test_plain_json(self):
        assert _extract_json_block('{"a": 1}') == '{"a": 1}'

    def test_with_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert _extract_json_block(text) == '{"a": 1}'

    def test_with_unlabeled_fence(self):
        text = '```\n{"a": 1}\n```'
        assert _extract_json_block(text) == '{"a": 1}'

    def test_with_surrounding_prose(self):
        text = 'Here is the answer:\n{"a": 1}\nLet me know if that works.'
        assert _extract_json_block(text) == '{"a": 1}'

    def test_no_json(self):
        assert _extract_json_block("no json here") is None

    def test_unmatched_braces(self):
        assert _extract_json_block("{ broken") is None


# ---------------------------------------------------------------
# Emoji validation
# ---------------------------------------------------------------

class TestLooksLikeEmoji:
    def test_simple_emoji(self):
        assert _looks_like_emoji("🔥") is True
        assert _looks_like_emoji("👍") is True
        assert _looks_like_emoji("✨") is True

    def test_compound_emoji_with_zwj(self):
        assert _looks_like_emoji("👨‍🌾") is True  # man farmer (ZWJ)

    def test_text_rejected(self):
        assert _looks_like_emoji("fire") is False
        assert _looks_like_emoji(":fire:") is False

    def test_custom_discord_emoji_rejected(self):
        assert _looks_like_emoji("<:custom:123>") is False

    def test_empty_or_none(self):
        assert _looks_like_emoji("") is False
        assert _looks_like_emoji(None) is False

    def test_long_text_rejected(self):
        assert _looks_like_emoji("this is a long sentence") is False


# ---------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------

class TestSanitize:
    def test_valid_none_passes(self):
        decider = _decider()
        ctx = _ctx()
        decision = _sanitize_json(
            decider,
            {"action": "none", "reasoning": "nothing to add", "confidence": 0.9},
            ctx,
        )
        assert decision.action == "none"
        assert decision.confidence == 0.9
        assert "actor_sanitization_rejected" not in decision.reasoning

    def test_valid_react_passes(self):
        decider = _decider()
        ctx = _ctx()
        decision = _sanitize_json(
            decider,
            {
                "action": "react",
                "target_message_id": 101,
                "emoji": "🔥",
                "reasoning": "spicy take",
                "confidence": 0.7,
            },
            ctx,
        )
        assert decision.action == "react"
        assert decision.target_message_id == 101
        assert decision.emoji == "🔥"

    def test_react_target_not_in_window_rejected(self):
        decider = _decider()
        ctx = _ctx(recent_message_ids=[101, 102])
        decision = _sanitize_json(
            decider,
            {
                "action": "react",
                "target_message_id": 999,
                "emoji": "🔥",
                "reasoning": "stale target",
                "confidence": 0.5,
            },
            ctx,
        )
        assert decision.action == "none"
        assert "react_target_message_id_not_in_window" in decision.reasoning

    def test_react_invalid_emoji_rejected(self):
        decider = _decider()
        ctx = _ctx()
        decision = _sanitize_json(
            decider,
            {
                "action": "react",
                "target_message_id": 101,
                "emoji": "fire",
                "reasoning": "fake emoji",
                "confidence": 0.5,
            },
            ctx,
        )
        assert decision.action == "none"
        assert "react_invalid_emoji" in decision.reasoning

    def test_invalid_action_rejected(self):
        decider = _decider()
        ctx = _ctx()
        decision = _sanitize_json(
            decider,
            {"action": "explode", "reasoning": "nope", "confidence": 0.0},
            ctx,
        )
        assert decision.action == "none"
        assert "invalid_action" in decision.reasoning

    def test_engage_persona_not_in_allowlist_rejected(self):
        decider = _decider()
        persona = _persona(engages=["slashai"])
        ctx = _ctx(persona=persona)
        decision = _sanitize_json(
            decider,
            {
                "action": "engage_persona",
                "target_persona_id": "frank",  # NOT in allowlist
                "reasoning": "let's chat",
                "confidence": 0.5,
            },
            ctx,
        )
        assert decision.action == "none"
        assert "engage_persona_not_in_allowlist" in decision.reasoning

    def test_engage_persona_in_allowlist_passes(self):
        decider = _decider()
        persona = _persona(engages=["slashai"])
        ctx = _ctx(persona=persona)
        decision = _sanitize_json(
            decider,
            {
                "action": "engage_persona",
                "target_persona_id": "slashai",
                "reasoning": "ping the friend",
                "confidence": 0.6,
            },
            ctx,
        )
        assert decision.action == "engage_persona"
        assert decision.target_persona_id == "slashai"

    def test_action_with_no_remaining_budget_rejected(self):
        decider = _decider()
        ctx = _ctx(budget=BudgetSummary(reactions=0, replies=0, new_topics=1))
        decision = _sanitize_json(
            decider,
            {
                "action": "react",
                "target_message_id": 101,
                "emoji": "🔥",
                "reasoning": "wants to react",
                "confidence": 0.5,
            },
            ctx,
        )
        assert decision.action == "none"
        assert "budget_exhausted_for: react" in decision.reasoning

    def test_engage_persona_uses_reply_budget(self):
        """engage_persona requires reply budget per Part 11 of the spec."""
        decider = _decider()
        ctx = _ctx(budget=BudgetSummary(reactions=10, replies=0, new_topics=1))
        persona = _persona(engages=["slashai"])
        ctx.persona = persona
        decision = _sanitize_json(
            decider,
            {
                "action": "engage_persona",
                "target_persona_id": "slashai",
                "reasoning": "no reply budget",
                "confidence": 0.5,
            },
            ctx,
        )
        assert decision.action == "none"
        assert "budget_exhausted_for: engage_persona" in decision.reasoning

    def test_malformed_json_falls_back_to_none(self):
        decider = _decider()
        ctx = _ctx()
        decision = decider._sanitize(
            text="this is not json at all",
            ctx=ctx,
            input_tokens=10,
            output_tokens=20,
            decider_model="claude-haiku-test",
        )
        assert decision.action == "none"
        assert "no_json_in_response" in decision.reasoning

    def test_confidence_clamped(self):
        decider = _decider()
        ctx = _ctx()
        decision = _sanitize_json(
            decider,
            {"action": "none", "reasoning": "ok", "confidence": 99.0},
            ctx,
        )
        assert decision.confidence == 1.0

    def test_negative_confidence_clamped(self):
        decider = _decider()
        ctx = _ctx()
        decision = _sanitize_json(
            decider,
            {"action": "none", "reasoning": "ok", "confidence": -5.0},
            ctx,
        )
        assert decision.confidence == 0.0

    def test_non_object_json_rejected(self):
        decider = _decider()
        ctx = _ctx()
        decision = decider._sanitize(
            text="[1, 2, 3]",
            ctx=ctx,
            input_tokens=10,
            output_tokens=20,
            decider_model="claude-haiku-test",
        )
        assert decision.action == "none"
        # Could be json_not_object OR no_json_in_response depending on extract path;
        # what matters is it didn't crash and fell back safely.
        assert decision.action == "none"
