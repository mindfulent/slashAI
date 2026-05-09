# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the Park-style reflection engine (Enhancement 015 / v0.16.4).

Focus on the parsing helpers, the threshold heuristic, and the maybe_reflect
orchestrator's branching. The DB-backed paths are exercised via mocked pools.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from proactive.reflection import (
    DEFAULT_REFLECTION_THRESHOLD,
    ReflectionEngine,
    infer_subject,
    observation_text,
    parse_importance,
    parse_insights,
    parse_questions,
)


# ---------------------------------------------------------------
# Importance parsing
# ---------------------------------------------------------------

class TestParseImportance:
    def test_clean_integer(self):
        assert parse_importance("7") == 7

    def test_with_prose(self):
        assert parse_importance("I'd rate this a 6 because...") == 6

    def test_clamps_to_one(self):
        assert parse_importance("0") == 5  # 0 doesn't match \b1-9|10\b -> falls back to 5

    def test_clamps_to_ten(self):
        # Numbers > 10 don't match the regex; falls back to 5
        assert parse_importance("100") == 5

    def test_empty_falls_back(self):
        assert parse_importance("") == 5

    def test_no_digit_falls_back(self):
        assert parse_importance("absolutely poignant") == 5

    def test_first_digit_wins(self):
        assert parse_importance("between 3 and 5, I'd say 4") == 3


# ---------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------

class TestParseQuestions:
    def test_three_numbered(self):
        text = "1. What does Lena think about wheat?\n2. Why does she help?\n3. When does she go quiet?"
        qs = parse_questions(text)
        assert len(qs) == 3
        assert qs[0].endswith("?")

    def test_caps_at_three(self):
        text = "1. q1?\n2. q2?\n3. q3?\n4. q4?"
        qs = parse_questions(text)
        assert len(qs) == 3

    def test_adds_question_mark_when_missing(self):
        # Questions without trailing ? but >5 chars get one appended
        text = "1. What does she care about most"
        qs = parse_questions(text)
        assert len(qs) == 1
        assert qs[0].endswith("?")

    def test_skips_garbage_lines(self):
        text = "intro line\n1. what?\nmore prose\n2. why?\n3. when?"
        qs = parse_questions(text)
        assert len(qs) == 3

    def test_empty(self):
        assert parse_questions("") == []


# ---------------------------------------------------------------
# Insight parsing
# ---------------------------------------------------------------

class TestParseInsights:
    def test_strict_format_with_citations(self):
        text = (
            "1. Lena prefers practical advice (because of 1, 3)\n"
            "2. She softens her tone with newcomers (because of 2)\n"
        )
        insights = parse_insights(text)
        assert len(insights) == 2
        assert insights[0]["text"] == "Lena prefers practical advice"
        assert insights[0]["cite_indices"] == [1, 3]
        assert insights[1]["cite_indices"] == [2]

    def test_fallback_no_citations(self):
        text = "1. Bare insight without citations\n2. Another one"
        insights = parse_insights(text)
        assert len(insights) == 2
        assert insights[0]["cite_indices"] == []

    def test_caps_at_five(self):
        text = "\n".join(f"{i+1}. insight (because of {i+1})" for i in range(8))
        insights = parse_insights(text)
        assert len(insights) == 5

    def test_handles_mixed_separators(self):
        text = "1. insight one (because of 1, 2 3)"
        insights = parse_insights(text)
        assert insights[0]["cite_indices"] == [1, 2, 3]

    def test_empty(self):
        assert parse_insights("") == []


# ---------------------------------------------------------------
# Observation rendering
# ---------------------------------------------------------------

class TestObservationText:
    def test_basic_decision(self):
        action = {"decision": "react", "reasoning": "spicy take", "emoji": "🔥"}
        text = observation_text(action)
        assert "react" in text
        assert "🔥" in text
        assert "spicy take" in text

    def test_engage_persona(self):
        action = {
            "decision": "engage_persona",
            "target_persona_id": "lena",
            "reasoning": "wanted to chat",
        }
        text = observation_text(action)
        assert "lena" in text
        assert "wanted to chat" in text

    def test_missing_reasoning(self):
        action = {"decision": "reply"}
        text = observation_text(action)
        assert "no reasoning" in text


# ---------------------------------------------------------------
# Subject inference
# ---------------------------------------------------------------

class TestInferSubject:
    def test_persona_target(self):
        assert infer_subject({"target_persona_id": "lena", "channel_id": 42}) == ("persona", "lena")

    def test_falls_back_to_channel(self):
        assert infer_subject({"channel_id": 42}) == ("channel", "42")

    def test_no_channel(self):
        assert infer_subject({}) == ("channel", "0")


# ---------------------------------------------------------------
# Threshold + maybe_reflect orchestration
# ---------------------------------------------------------------

def _pool_mock():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()
    return pool


class TestAccumulatedImportance:
    @pytest.mark.asyncio
    async def test_returns_sum_below_threshold(self):
        pool = _pool_mock()
        pool.fetchval = AsyncMock(return_value=None)  # no prior reflection
        pool.fetchrow = AsyncMock(return_value={"total": 42})
        engine = ReflectionEngine(pool)

        result = await engine.accumulated_importance_since_last_reflection("slashai")

        assert result == 42

    @pytest.mark.asyncio
    async def test_should_reflect_below_threshold(self):
        pool = _pool_mock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value={"total": 30})
        engine = ReflectionEngine(pool)

        assert await engine.should_reflect("slashai") is False

    @pytest.mark.asyncio
    async def test_should_reflect_at_threshold(self):
        pool = _pool_mock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value={"total": DEFAULT_REFLECTION_THRESHOLD})
        engine = ReflectionEngine(pool)

        assert await engine.should_reflect("slashai") is True


class TestMaybeReflectOrchestration:
    @pytest.mark.asyncio
    async def test_threshold_not_reached_returns_early(self):
        pool = _pool_mock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value={"total": 10})
        # No unscored actions
        pool.fetch = AsyncMock(return_value=[])
        engine = ReflectionEngine(pool)

        stats = await engine.maybe_reflect("slashai", anthropic_client=None)

        assert stats.skipped_reason == "threshold_not_reached"
        assert stats.reflections_stored == 0

    @pytest.mark.asyncio
    async def test_force_skips_threshold_check(self):
        pool = _pool_mock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value={"total": 5})  # well under threshold
        pool.fetch = AsyncMock(return_value=[])  # no synthesis memories either
        engine = ReflectionEngine(pool)

        stats = await engine.maybe_reflect("slashai", anthropic_client=None, force=True)

        # Should not skip on threshold — should proceed and skip later
        assert stats.skipped_reason == "no_scored_memories"

    @pytest.mark.asyncio
    async def test_no_questions_skips_synthesis(self):
        pool = _pool_mock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value={"total": 200})  # over threshold

        # First fetch (score_unscored: empty), second fetch (synthesis memories: some rows)
        pool.fetch = AsyncMock(side_effect=[
            [],  # no unscored actions
            [    # synthesis memories
                {"id": 1, "decision": "react", "target_persona_id": None,
                 "emoji": "🔥", "channel_id": 42, "reasoning": "hot take",
                 "importance": 6, "created_at": None}
            ],
        ])

        # Anthropic client returns no parseable questions
        client = MagicMock()
        bad_response = MagicMock()
        bad_block = MagicMock()
        bad_block.type = "text"
        bad_block.text = "I'd rather not say."
        bad_response.content = [bad_block]
        client.messages = MagicMock()
        client.messages.create = AsyncMock(return_value=bad_response)

        engine = ReflectionEngine(pool)

        stats = await engine.maybe_reflect("slashai", anthropic_client=client)

        assert stats.skipped_reason == "no_questions_generated"
        assert stats.reflections_stored == 0


# ---------------------------------------------------------------
# Score importance via Anthropic mock
# ---------------------------------------------------------------

class TestScoreImportance:
    @pytest.mark.asyncio
    async def test_returns_parsed_score(self):
        client = MagicMock()
        block = MagicMock()
        block.type = "text"
        block.text = "8"
        response = MagicMock()
        response.content = [block]
        client.messages = MagicMock()
        client.messages.create = AsyncMock(return_value=response)

        engine = ReflectionEngine(_pool_mock())
        score = await engine.score_importance("a poignant memory", client)

        assert score == 8

    @pytest.mark.asyncio
    async def test_no_client_returns_default(self):
        engine = ReflectionEngine(_pool_mock())
        score = await engine.score_importance("doesn't matter", anthropic_client=None)
        assert score == 5

    @pytest.mark.asyncio
    async def test_api_error_returns_default(self):
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))

        engine = ReflectionEngine(_pool_mock())
        score = await engine.score_importance("x", client)
        assert score == 5


# ---------------------------------------------------------------
# score_unscored_actions
# ---------------------------------------------------------------

class TestScoreUnscoredActions:
    @pytest.mark.asyncio
    async def test_scores_each_row(self):
        pool = _pool_mock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "decision": "react", "target_persona_id": None,
             "emoji": "🔥", "channel_id": 42, "reasoning": "hot"},
            {"id": 2, "decision": "reply", "target_persona_id": None,
             "emoji": None, "channel_id": 42, "reasoning": "agree"},
        ])
        # Anthropic returns "7" then "5"
        responses = []
        for n in [7, 5]:
            block = MagicMock()
            block.type = "text"
            block.text = str(n)
            r = MagicMock()
            r.content = [block]
            responses.append(r)
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=responses)

        engine = ReflectionEngine(pool)
        scored = await engine.score_unscored_actions("slashai", client)

        assert scored == 2
        # Two UPDATEs issued, one per scored row
        assert pool.execute.await_count == 2
        # Verify importance values passed through
        first_update = pool.execute.await_args_list[0]
        assert first_update.args[2] == 7
        second_update = pool.execute.await_args_list[1]
        assert second_update.args[2] == 5

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        pool = _pool_mock()
        pool.fetch = AsyncMock(return_value=[])
        engine = ReflectionEngine(pool)

        scored = await engine.score_unscored_actions("slashai", anthropic_client=None)

        assert scored == 0
        pool.execute.assert_not_called()
