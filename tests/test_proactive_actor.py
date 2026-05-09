# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the actor execution paths (Enhancement 015 / v0.16.1+).

Band 1 (v0.16.1) graduates `react` out of shadow mode. Reply / new_topic /
engage_persona stay shadow / unimplemented until later bands; we test that
those paths still log a stub action without raising.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agents.persona_loader import (
    PersonaConfig,
    PersonaIdentity,
    ProactiveConfig,
)
from proactive.actor import ProactiveActor
from proactive.config import GlobalProactiveConfig
from proactive.decider import ValidatedDecision
from proactive.observer import DeciderInput, FormattedMsg
from proactive.store import BudgetSummary


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

def _persona() -> PersonaConfig:
    return PersonaConfig(
        schema_version=2,
        name="slashai",
        display_name="slashAI",
        identity=PersonaIdentity(personality="dry wit"),
        proactive=ProactiveConfig(enabled=True),
    )


def _global_config(shadow_mode: bool = False) -> GlobalProactiveConfig:
    return GlobalProactiveConfig(
        enabled=True,
        shadow_mode=shadow_mode,
        heartbeat_interval_seconds=3600,
        cross_persona_lockout_seconds=5,
        decider_model_default="claude-haiku-4-5-20251001",
        actor_model_default="claude-sonnet-4-6",
    )


def _ctx() -> DeciderInput:
    msg = FormattedMsg(
        message_id=999,
        author_id=1,
        author_name="alice",
        is_bot=False,
        content="this is a great point",
        created_at=datetime.now(timezone.utc),
    )
    return DeciderInput(
        persona=_persona(),
        channel_id=42,
        guild_id=7,
        channel_name="general",
        trigger="activity",
        recent_messages=[msg],
        triggering_message=msg,
        budget_remaining=BudgetSummary(reactions=10, replies=2, new_topics=1),
    )


def _decision(action: str = "react", target_message_id: int = 999, emoji: str = "🔥") -> ValidatedDecision:
    return ValidatedDecision(
        action=action,
        target_message_id=target_message_id,
        target_persona_id=None,
        emoji=emoji if action == "react" else None,
        reasoning="seems spicy",
        confidence=0.7,
        input_tokens=10,
        output_tokens=20,
        decider_model="claude-haiku-4-5-20251001",
    )


def _store_mock():
    """ProactiveStore mock — record_action returns an int id."""
    store = MagicMock()
    store.record_action = AsyncMock(return_value=1)
    return store


def _bot_with_message(add_reaction_side_effect=None):
    """Bot mock where get_channel returns a channel that fetches a message."""
    message = MagicMock()
    message.add_reaction = AsyncMock(side_effect=add_reaction_side_effect)

    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)

    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot.fetch_channel = AsyncMock(return_value=channel)
    return bot, channel, message


# ---------------------------------------------------------------
# react path — band 1
# ---------------------------------------------------------------

class TestReactionPath:
    @pytest.mark.asyncio
    async def test_successful_reaction(self):
        bot, channel, message = _bot_with_message()
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config(shadow_mode=False))

        result = await actor.execute(_decision(), _ctx())

        assert result.success is True
        assert result.note == "reacted"
        message.add_reaction.assert_awaited_once_with("🔥")
        store.record_action.assert_awaited_once()
        recorded = store.record_action.await_args.args[0]
        assert recorded.decision == "react"
        assert recorded.emoji == "🔥"
        assert recorded.target_message_id == 999
        assert "actor_failed" not in (recorded.reasoning or "")

    @pytest.mark.asyncio
    async def test_reaction_fires_even_in_shadow_mode(self):
        """Band 1: reactions are graduated out of the shadow_mode gate."""
        bot, channel, message = _bot_with_message()
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config(shadow_mode=True))

        result = await actor.execute(_decision(), _ctx())

        assert result.success is True
        message.add_reaction.assert_awaited_once_with("🔥")

    @pytest.mark.asyncio
    async def test_target_message_not_found(self):
        bot = MagicMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "not found")
        )
        bot.get_channel = MagicMock(return_value=channel)
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config())

        result = await actor.execute(_decision(), _ctx())

        assert result.success is False
        assert "target_message_not_found" in result.note
        recorded = store.record_action.await_args.args[0]
        assert "actor_failed: target_message_not_found" in recorded.reasoning

    @pytest.mark.asyncio
    async def test_add_reaction_http_failure(self):
        from aiohttp import ClientResponse  # for the exception ctor signature
        del ClientResponse  # unused; Mock works fine
        bot, channel, message = _bot_with_message(
            add_reaction_side_effect=discord.HTTPException(
                MagicMock(status=429), "rate limited"
            )
        )
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config())

        result = await actor.execute(_decision(), _ctx())

        assert result.success is False
        assert "add_reaction_failed" in result.note
        recorded = store.record_action.await_args.args[0]
        assert "actor_failed: add_reaction_failed" in recorded.reasoning

    @pytest.mark.asyncio
    async def test_falls_back_to_fetch_channel_when_get_returns_none(self):
        bot, channel, message = _bot_with_message()
        bot.get_channel = MagicMock(return_value=None)  # not in cache
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config())

        result = await actor.execute(_decision(), _ctx())

        assert result.success is True
        bot.fetch_channel.assert_awaited_once_with(42)
        message.add_reaction.assert_awaited_once_with("🔥")


# ---------------------------------------------------------------
# none path
# ---------------------------------------------------------------

class TestNoOp:
    @pytest.mark.asyncio
    async def test_none_logs_only(self):
        bot = MagicMock()
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config())

        result = await actor.execute(_decision(action="none"), _ctx())

        assert result.success is True
        assert result.note == "logged_noop"
        store.record_action.assert_awaited_once()
        bot.get_channel.assert_not_called()


# ---------------------------------------------------------------
# Stub paths (still shadow / unimplemented in band 1)
# ---------------------------------------------------------------

class TestStubPaths:
    @pytest.mark.asyncio
    async def test_unknown_action_logs_safely(self):
        bot = MagicMock()
        store = _store_mock()
        actor = ProactiveActor(_persona(), bot, store, _global_config())

        decision = _decision(action="something_weird")  # type: ignore
        result = await actor.execute(decision, _ctx())

        assert result.success is False
        assert "unknown:something_weird" in result.note
        store.record_action.assert_awaited_once()


# ---------------------------------------------------------------
# reply path — band 2
# ---------------------------------------------------------------

def _bot_with_reply_target(reply_text: str = "yeah, fair point"):
    """Bot mock for the reply path: get_channel returns a channel where
    fetch_message returns a Message and channel.send returns a posted message
    with id 5555."""
    target_message = MagicMock(spec=discord.Message)
    target_message.id = 999
    target_message.content = "this is a great point"
    target_message.author = MagicMock()
    target_message.author.display_name = "alice"
    target_message.author.name = "alice"

    posted_message = MagicMock(spec=discord.Message)
    posted_message.id = 5555

    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=target_message)
    channel.send = AsyncMock(return_value=posted_message)

    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot.fetch_channel = AsyncMock(return_value=channel)
    return bot, channel, target_message, posted_message


def _anthropic_with_text(text: str = "yeah, fair point"):
    """Mock AsyncAnthropic where messages.create returns a response with one text block."""
    client = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=50, output_tokens=12)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


class TestReplyPath:
    @pytest.mark.asyncio
    async def test_successful_reply(self):
        bot, channel, target, posted = _bot_with_reply_target()
        client = _anthropic_with_text("yeah, that tracks.")
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store,
            _global_config(shadow_mode=False),
            anthropic_client=client,
        )

        result = await actor.execute(_decision(action="reply"), _ctx())

        assert result.success is True
        assert result.note == "replied"
        assert result.posted_message_id == 5555
        # Reply was sent with reference= to the target message (Discord native reply)
        channel.send.assert_awaited_once()
        send_args = channel.send.await_args
        assert send_args.args[0] == "yeah, that tracks."
        assert send_args.kwargs.get("reference") is target
        # Anthropic was called with persona's actor model
        client.messages.create.assert_awaited_once()
        call_kwargs = client.messages.create.await_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-6"
        # Audit row was written with posted_message_id
        recorded = store.record_action.await_args.args[0]
        assert recorded.decision == "reply"
        assert recorded.posted_message_id == 5555
        assert recorded.target_message_id == 999

    @pytest.mark.asyncio
    async def test_reply_fires_in_shadow_mode(self):
        """Band 2: reply graduates out of shadow_mode."""
        bot, channel, target, posted = _bot_with_reply_target()
        client = _anthropic_with_text()
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store,
            _global_config(shadow_mode=True),
            anthropic_client=client,
        )

        result = await actor.execute(_decision(action="reply"), _ctx())

        assert result.success is True
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_anthropic_client_records_failure(self):
        bot, channel, _, _ = _bot_with_reply_target()
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=None,
        )

        result = await actor.execute(_decision(action="reply"), _ctx())

        assert result.success is False
        assert "no_anthropic_client" in result.note
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_target_message_not_found(self):
        bot = MagicMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "not found")
        )
        bot.get_channel = MagicMock(return_value=channel)
        client = _anthropic_with_text()
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
        )

        result = await actor.execute(_decision(action="reply"), _ctx())

        assert result.success is False
        assert "target_message_not_found" in result.note
        client.messages.create.assert_not_called()  # no LLM call wasted

    @pytest.mark.asyncio
    async def test_send_http_failure(self):
        bot, channel, target, posted = _bot_with_reply_target()
        channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        client = _anthropic_with_text()
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
        )

        result = await actor.execute(_decision(action="reply"), _ctx())

        assert result.success is False
        assert "send_reply_failed" in result.note
        recorded = store.record_action.await_args.args[0]
        assert "actor_failed: send_reply_failed" in recorded.reasoning

    @pytest.mark.asyncio
    async def test_empty_generation_records_failure(self):
        bot, channel, target, posted = _bot_with_reply_target()
        client = _anthropic_with_text(text="   ")  # whitespace-only -> empty after strip
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
        )

        result = await actor.execute(_decision(action="reply"), _ctx())

        assert result.success is False
        assert "reply_generation_empty" in result.note
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_long_reply_truncated_to_discord_limit(self):
        long_text = "a" * 2500  # over 2000-char Discord limit
        bot, channel, target, posted = _bot_with_reply_target()
        client = _anthropic_with_text(text=long_text)
        store = _store_mock()
        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
        )

        await actor.execute(_decision(action="reply"), _ctx())

        sent_text = channel.send.await_args.args[0]
        assert len(sent_text) <= 2000
        assert sent_text.endswith("...")
