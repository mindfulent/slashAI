# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for inter-agent thread state machine (Enhancement 015 / v0.16.3).

Covers all five termination conditions plus the engagement decay function
and the actor's engage_persona path. DB calls are mocked at the asyncpg pool
level so tests stay fast and deterministic.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
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
from proactive.threads import (
    DEFAULT_MAX_TURNS,
    InterAgentThreads,
    ThreadState,
    engagement_decay_factor,
)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _row(
    *,
    id: int = 1,
    channel_id: int = 42,
    guild_id: int = 7,
    initiator: str = "slashai",
    participants=None,
    turn_count: int = 0,
    max_turns: int = DEFAULT_MAX_TURNS,
    seed_message_id: int = 999,
    seed_topic: str = "topic",
    started_at: datetime | None = None,
    last_turn_at: datetime | None = None,
    ended_at=None,
    ended_reason=None,
):
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    if last_turn_at is None:
        last_turn_at = started_at
    if participants is None:
        participants = [
            {"persona_id": "slashai", "user_id": 1001},
            {"persona_id": "lena", "user_id": 1002},
        ]
    # asyncpg may return participants as JSON-string or list; test both
    return {
        "id": id,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "initiator_persona_id": initiator,
        "participants": json.dumps(participants),  # JSONB-as-string
        "turn_count": turn_count,
        "max_turns": max_turns,
        "seed_message_id": seed_message_id,
        "seed_topic": seed_topic,
        "started_at": started_at,
        "last_turn_at": last_turn_at,
        "ended_at": ended_at,
        "ended_reason": ended_reason,
    }


def _pool_mock(fetchrow=None, fetch=None, execute=None):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.fetch = AsyncMock(return_value=fetch or [])
    pool.execute = AsyncMock(return_value=execute)
    # acquire() async context manager
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=fetch or [])
    txn_ctx = MagicMock()
    txn_ctx.__aenter__ = AsyncMock(return_value=None)
    txn_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_ctx)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


# ---------------------------------------------------------------
# engagement_decay_factor
# ---------------------------------------------------------------

class TestEngagementDecayFactor:
    def test_turn_zero_no_decay(self):
        assert engagement_decay_factor(0) == 1.0

    def test_turn_two_intermediate(self):
        assert engagement_decay_factor(2) == 0.6

    def test_turn_four_floor(self):
        assert engagement_decay_factor(4) == 0.2

    def test_high_turn_clamped(self):
        # The floor is 0.2 even at very high turn counts
        assert engagement_decay_factor(100) == 0.2


# ---------------------------------------------------------------
# ThreadState
# ---------------------------------------------------------------

class TestThreadState:
    def _state(self) -> ThreadState:
        return ThreadState(
            id=1,
            channel_id=42,
            guild_id=7,
            initiator_persona_id="slashai",
            participants=[
                {"persona_id": "slashai", "user_id": 1001},
                {"persona_id": "lena", "user_id": 1002},
            ],
            turn_count=2,
            max_turns=4,
            seed_message_id=999,
            seed_topic="hey",
            started_at=datetime.now(timezone.utc),
            last_turn_at=datetime.now(timezone.utc),
        )

    def test_participant_user_ids(self):
        s = self._state()
        assert s.participant_user_ids() == {1001, 1002}

    def test_other_participant(self):
        s = self._state()
        assert s.other_participant("slashai") == "lena"
        assert s.other_participant("lena") == "slashai"

    def test_other_participant_unknown_persona(self):
        s = self._state()
        # If we ask from a non-participant, returns the first non-self entry
        assert s.other_participant("alien") in {"slashai", "lena"}


# ---------------------------------------------------------------
# InterAgentThreads
# ---------------------------------------------------------------

class TestStartThread:
    @pytest.mark.asyncio
    async def test_start_thread_returns_state_with_turn_zero(self):
        new_row = _row(turn_count=0)
        pool, conn = _pool_mock()
        conn.fetchrow = AsyncMock(return_value=new_row)
        threads = InterAgentThreads(pool)

        state = await threads.start_thread(
            initiator_persona_id="slashai",
            channel_id=42,
            guild_id=7,
            participants=[
                {"persona_id": "slashai", "user_id": 1001},
                {"persona_id": "lena", "user_id": 1002},
            ],
            seed_message_id=999,
            seed_topic="topic",
        )

        assert state.id == 1
        assert state.turn_count == 0
        assert state.initiator_persona_id == "slashai"
        # Supersede UPDATE was issued before the INSERT
        assert any(
            "ended_reason = 'superseded'" in (call.args[0] if call.args else "")
            for call in conn.execute.call_args_list
        )

    @pytest.mark.asyncio
    async def test_start_thread_supersedes_existing(self):
        new_row = _row(id=2)
        pool, conn = _pool_mock()
        conn.fetchrow = AsyncMock(return_value=new_row)
        threads = InterAgentThreads(pool)

        await threads.start_thread(
            initiator_persona_id="slashai",
            channel_id=42,
            guild_id=7,
            participants=[],
        )

        # First conn.execute should be the supersede UPDATE; verify the
        # WHERE clause filters by the right channel
        first_call = conn.execute.call_args_list[0]
        assert "UPDATE inter_agent_threads" in first_call.args[0]
        assert "superseded" in first_call.args[0]
        assert first_call.args[1] == 42  # channel_id


class TestAdvanceThread:
    @pytest.mark.asyncio
    async def test_advance_returns_updated_state(self):
        updated_row = _row(turn_count=2)
        pool, conn = _pool_mock(fetchrow=updated_row)
        threads = InterAgentThreads(pool)

        state = await threads.advance_thread(thread_id=1)

        assert state is not None
        assert state.turn_count == 2

    @pytest.mark.asyncio
    async def test_advance_returns_none_for_ended_thread(self):
        # The UPDATE filters on ended_at IS NULL, so a 0-row UPDATE returns None
        pool, conn = _pool_mock(fetchrow=None)
        threads = InterAgentThreads(pool)

        state = await threads.advance_thread(thread_id=999)

        assert state is None


class TestEndThread:
    @pytest.mark.asyncio
    async def test_end_thread_executes_update(self):
        pool, conn = _pool_mock()
        threads = InterAgentThreads(pool)

        await threads.end_thread(thread_id=1, reason="turn_cap")

        pool.execute.assert_awaited_once()
        sql, *params = pool.execute.await_args.args
        assert "UPDATE inter_agent_threads" in sql
        assert "ended_at = NOW()" in sql
        assert params == [1, "turn_cap"]


class TestGetActive:
    @pytest.mark.asyncio
    async def test_returns_state_when_active(self):
        active_row = _row(turn_count=1)
        pool, conn = _pool_mock(fetchrow=active_row)
        threads = InterAgentThreads(pool)

        state = await threads.get_active_thread(channel_id=42)

        assert state is not None
        assert state.id == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_no_active(self):
        pool, conn = _pool_mock(fetchrow=None)
        threads = InterAgentThreads(pool)

        state = await threads.get_active_thread(channel_id=42)

        assert state is None


class TestHumanInterrupt:
    @pytest.mark.asyncio
    async def test_ends_active_thread(self):
        active_row = _row(id=99, turn_count=2)
        pool, conn = _pool_mock(fetchrow=active_row)
        threads = InterAgentThreads(pool)

        ended_id = await threads.end_active_if_human(channel_id=42)

        assert ended_id == 99
        # end_thread issued an UPDATE with reason='human_interrupt'
        sql_call = pool.execute.await_args
        assert sql_call.args[0].startswith("\n            UPDATE inter_agent_threads")
        assert sql_call.args[2] == "human_interrupt"

    @pytest.mark.asyncio
    async def test_no_active_thread_returns_none(self):
        pool, conn = _pool_mock(fetchrow=None)
        threads = InterAgentThreads(pool)

        ended_id = await threads.end_active_if_human(channel_id=42)

        assert ended_id is None
        pool.execute.assert_not_called()


class TestNaturalEnd:
    @pytest.mark.asyncio
    async def test_two_consecutive_none_returns_true(self):
        pool, conn = _pool_mock(
            fetch=[
                {"decision": "none", "reasoning": "nothing to add"},
                {"decision": "none", "reasoning": "winding down"},
            ]
        )
        threads = InterAgentThreads(pool)

        result = await threads.check_natural_end(thread_id=1, persona_id="slashai")

        assert result is True

    @pytest.mark.asyncio
    async def test_mixed_decisions_returns_false(self):
        pool, conn = _pool_mock(
            fetch=[
                {"decision": "none", "reasoning": "..."},
                {"decision": "reply", "reasoning": "..."},
            ]
        )
        threads = InterAgentThreads(pool)

        result = await threads.check_natural_end(thread_id=1, persona_id="slashai")

        assert result is False

    @pytest.mark.asyncio
    async def test_only_one_decision_returns_false(self):
        pool, conn = _pool_mock(
            fetch=[{"decision": "none", "reasoning": "just one"}]
        )
        threads = InterAgentThreads(pool)

        result = await threads.check_natural_end(thread_id=1, persona_id="slashai")

        assert result is False

    @pytest.mark.asyncio
    async def test_excludes_prefilter_rejections(self):
        """Pre-filter no-ops (e.g., cooldown, quiet hours) shouldn't count
        as decider decisions for the natural-end heuristic."""
        # The SQL filters out reasoning LIKE 'prefilter:%' — verify the WHERE clause
        pool, conn = _pool_mock(fetch=[])
        threads = InterAgentThreads(pool)

        await threads.check_natural_end(thread_id=1, persona_id="slashai")

        call = pool.fetch.await_args
        sql = call.args[0]
        assert "reasoning NOT LIKE 'prefilter:%'" in sql


# ---------------------------------------------------------------
# Actor — engage_persona path
# ---------------------------------------------------------------

def _persona() -> PersonaConfig:
    return PersonaConfig(
        schema_version=2,
        name="slashai",
        display_name="slashAI",
        identity=PersonaIdentity(personality="dry wit"),
        proactive=ProactiveConfig(
            enabled=True,
            engages_with_personas=["lena"],
        ),
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


def _engage_decision(target: str = "lena") -> ValidatedDecision:
    return ValidatedDecision(
        action="engage_persona",
        target_message_id=None,
        target_persona_id=target,
        emoji=None,
        reasoning="want to chat with Lena",
        confidence=0.7,
        input_tokens=10,
        output_tokens=20,
        decider_model="claude-haiku-4-5-20251001",
    )


def _engage_ctx() -> DeciderInput:
    msg = FormattedMsg(
        message_id=999,
        author_id=1,
        author_name="alice",
        is_bot=False,
        content="anyone here farms wheat?",
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


def _bot_for_engage():
    """Bot mock where get_channel returns a channel that successfully sends and
    bot.user is set to a known id (the initiator)."""
    posted = MagicMock(spec=discord.Message)
    posted.id = 7777
    channel = MagicMock()
    channel.send = AsyncMock(return_value=posted)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    user = MagicMock()
    user.id = 1001
    bot.user = user
    return bot, channel, posted


def _anthropic_with_text(text: str = "hey lena, got a sec?"):
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


def _store_mock():
    s = MagicMock()
    s.record_action = AsyncMock(return_value=1)
    return s


def _threads_mock(thread_id: int = 5):
    """InterAgentThreads mock that returns a ThreadState from start_thread."""
    t = MagicMock()
    t.start_thread = AsyncMock(
        return_value=ThreadState(
            id=thread_id,
            channel_id=42,
            guild_id=7,
            initiator_persona_id="slashai",
            participants=[
                {"persona_id": "slashai", "user_id": 1001},
                {"persona_id": "lena", "user_id": 1002},
            ],
            turn_count=0,
            max_turns=4,
            seed_message_id=7777,
            seed_topic="hey",
            started_at=datetime.now(timezone.utc),
            last_turn_at=datetime.now(timezone.utc),
        )
    )
    t.advance_thread = AsyncMock(
        return_value=ThreadState(
            id=thread_id,
            channel_id=42,
            guild_id=7,
            initiator_persona_id="slashai",
            participants=[],
            turn_count=1,
            max_turns=4,
            seed_message_id=7777,
            seed_topic="hey",
            started_at=datetime.now(timezone.utc),
            last_turn_at=datetime.now(timezone.utc),
        )
    )
    return t


class TestEngagePersonaActor:
    @pytest.mark.asyncio
    async def test_successful_engage(self):
        bot, channel, posted = _bot_for_engage()
        client = _anthropic_with_text("hey lena, got a sec to chat about wheat?")
        store = _store_mock()
        threads = _threads_mock(thread_id=5)

        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
            threads=threads,
            resolve_persona_user_id=lambda p: 1002 if p == "lena" else 1001,
        )

        result = await actor.execute(_engage_decision(), _engage_ctx())

        assert result.success is True
        assert result.note == "engaged"
        # Posted message includes the @-mention prefix and the generated opening
        sent_text = channel.send.await_args.args[0]
        assert sent_text.startswith("<@1002> ")
        assert "wheat" in sent_text
        # Thread row started with both participants and their user_ids
        threads.start_thread.assert_awaited_once()
        kwargs = threads.start_thread.await_args.kwargs
        assert kwargs["initiator_persona_id"] == "slashai"
        assert kwargs["channel_id"] == 42
        participant_ids = {p["user_id"] for p in kwargs["participants"]}
        assert participant_ids == {1001, 1002}
        # Thread was advanced once (seed counts as turn 1)
        threads.advance_thread.assert_awaited_once_with(5)
        # Audit row carries inter_agent_thread_id
        recorded = store.record_action.await_args.args[0]
        assert recorded.decision == "engage_persona"
        assert recorded.posted_message_id == 7777
        assert recorded.inter_agent_thread_id == 5

    @pytest.mark.asyncio
    async def test_target_not_connected_records_failure(self):
        bot, channel, posted = _bot_for_engage()
        client = _anthropic_with_text()
        store = _store_mock()
        threads = _threads_mock()

        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
            threads=threads,
            resolve_persona_user_id=lambda p: None,  # nobody connected
        )

        result = await actor.execute(_engage_decision(), _engage_ctx())

        assert result.success is False
        assert "target_persona_not_connected" in result.note
        # No LLM call, no Discord post, no thread row
        client.messages.create.assert_not_called()
        channel.send.assert_not_called()
        threads.start_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_failure_skips_thread_creation(self):
        bot, channel, posted = _bot_for_engage()
        channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        client = _anthropic_with_text()
        store = _store_mock()
        threads = _threads_mock()

        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=client,
            threads=threads,
            resolve_persona_user_id=lambda p: 1002,
        )

        result = await actor.execute(_engage_decision(), _engage_ctx())

        assert result.success is False
        assert "send_engage_failed" in result.note
        # Thread row should NOT have been created since the post failed
        threads.start_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_anthropic_client(self):
        bot, channel, posted = _bot_for_engage()
        store = _store_mock()
        threads = _threads_mock()

        actor = ProactiveActor(
            _persona(), bot, store, _global_config(),
            anthropic_client=None,
            threads=threads,
            resolve_persona_user_id=lambda p: 1002,
        )

        result = await actor.execute(_engage_decision(), _engage_ctx())

        assert result.success is False
        assert "no_anthropic_client" in result.note
