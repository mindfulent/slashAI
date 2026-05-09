# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the sycophancy heuristic detector (Enhancement 015 / v0.16.5).
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from proactive.sycophancy import SycophancyDetector, count_agreement_cues


# ---------------------------------------------------------------
# count_agreement_cues
# ---------------------------------------------------------------

class TestCountAgreementCues:
    def test_zero_for_neutral_text(self):
        assert count_agreement_cues("decided to ignore the message") == 0

    def test_simple_agree(self):
        assert count_agreement_cues("decided to agree with Lena") == 1

    def test_word_boundary(self):
        # "agreement" matches the \bagreement\b cue, "agreeable" does not
        assert count_agreement_cues("the agreement was clear") == 1
        assert count_agreement_cues("she's agreeable today") == 0

    def test_multi_hit(self):
        text = "yeah, totally right — fair point, +1"
        assert count_agreement_cues(text) >= 3

    def test_case_insensitive(self):
        assert count_agreement_cues("YEAH, EXACTLY") == 2
        assert count_agreement_cues("Spot on") == 1

    def test_none_input(self):
        assert count_agreement_cues(None) == 0

    def test_empty_input(self):
        assert count_agreement_cues("") == 0

    def test_validate_family(self):
        # "validating", "validation", "validate" all hit \bvalidat
        assert count_agreement_cues("just validating Lena's take") == 1
        assert count_agreement_cues("validation feels good") == 1


# ---------------------------------------------------------------
# SycophancyDetector.per_persona
# ---------------------------------------------------------------

def _pool_mock(fetch_rows=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    return pool


class TestPerPersona:
    @pytest.mark.asyncio
    async def test_aggregates_replies_and_hits(self):
        rows = [
            {"persona_id": "slashai", "reasoning": "agreeing with Lena's take", "inter_agent_thread_id": 1},
            {"persona_id": "slashai", "reasoning": "yeah, fair point", "inter_agent_thread_id": 1},
            {"persona_id": "slashai", "reasoning": "challenging the assumption", "inter_agent_thread_id": 2},
            {"persona_id": "lena", "reasoning": "echoing slashai", "inter_agent_thread_id": 1},
        ]
        pool = _pool_mock(rows)
        detector = SycophancyDetector(pool)

        stats = await detector.per_persona(days=7)
        by_id = {s.persona_id: s for s in stats}

        assert by_id["slashai"].reply_count == 3
        # "agreeing" + "yeah" + "fair point" = 3 hits across 3 replies
        assert by_id["slashai"].agreement_hits == 3
        assert by_id["slashai"].threads_seen == 2
        assert by_id["slashai"].agreement_rate == 1.0  # 3/3

        assert by_id["lena"].reply_count == 1
        assert by_id["lena"].agreement_hits == 1  # "echoing"
        assert by_id["lena"].threads_seen == 1
        assert by_id["lena"].agreement_rate == 1.0

    @pytest.mark.asyncio
    async def test_no_replies_returns_empty(self):
        pool = _pool_mock([])
        detector = SycophancyDetector(pool)
        assert await detector.per_persona(days=7) == []

    @pytest.mark.asyncio
    async def test_zero_division_safe(self):
        # If count_agreement_cues returns 0 against 0 replies, rate = 0
        rows = [{"persona_id": "slashai", "reasoning": "neutral text",
                 "inter_agent_thread_id": 1}]
        pool = _pool_mock(rows)
        detector = SycophancyDetector(pool)
        stats = await detector.per_persona(days=7)
        assert stats[0].reply_count == 1
        assert stats[0].agreement_hits == 0
        assert stats[0].agreement_rate == 0.0


class TestPerThread:
    @pytest.mark.asyncio
    async def test_parses_jsonb_string(self):
        rows = [{
            "thread_id": 1,
            "initiator_persona_id": "slashai",
            "participants": json.dumps([
                {"persona_id": "slashai", "user_id": 1001},
                {"persona_id": "lena", "user_id": 1002},
            ]),
            "turn_count": 4,
            "ended_reason": "turn_cap",
            "reply_count": 3,
            "combined_reasoning": "agreeing | echoing | spot on",
        }]
        pool = _pool_mock(rows)
        detector = SycophancyDetector(pool)

        stats = await detector.per_thread(days=7)

        assert len(stats) == 1
        assert stats[0].participants == "slashai ↔ lena"
        assert stats[0].agreement_hits == 3  # agreeing, echoing, spot on
        assert stats[0].total_replies_in_thread == 3
        assert stats[0].ended_reason == "turn_cap"

    @pytest.mark.asyncio
    async def test_handles_list_jsonb(self):
        # asyncpg with jsonb codec returns native list, not str
        rows = [{
            "thread_id": 2,
            "initiator_persona_id": "lena",
            "participants": [
                {"persona_id": "slashai", "user_id": 1001},
                {"persona_id": "lena", "user_id": 1002},
            ],
            "turn_count": 0,
            "ended_reason": None,
            "reply_count": 0,
            "combined_reasoning": None,
        }]
        pool = _pool_mock(rows)
        detector = SycophancyDetector(pool)

        stats = await detector.per_thread(days=7)

        assert stats[0].participants == "slashai ↔ lena"
        assert stats[0].agreement_hits == 0  # None reasoning
        assert stats[0].ended_reason is None

    @pytest.mark.asyncio
    async def test_malformed_participants_doesnt_crash(self):
        rows = [{
            "thread_id": 3,
            "initiator_persona_id": "slashai",
            "participants": "not valid json",
            "turn_count": 0,
            "ended_reason": None,
            "reply_count": 0,
            "combined_reasoning": "",
        }]
        pool = _pool_mock(rows)
        detector = SycophancyDetector(pool)

        stats = await detector.per_thread(days=7)
        # Doesn't crash; participants string falls back to '?'
        assert stats[0].participants == "?"
