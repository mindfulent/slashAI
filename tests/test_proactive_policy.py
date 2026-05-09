# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Tests for the proactive pre-filter (Enhancement 015 / v0.14.0).

The pre-filter is pure-Python: no LLM, no DB. Tests exercise the full
short-circuit chain — quiet hours boundary, cross-persona lockout,
per-persona cooldown, silence threshold, budget exhaustion.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agents.persona_loader import (
    ProactiveBudgets,
    ProactiveConfig,
    ProactiveCooldowns,
    ProactiveQuietHours,
)
from proactive.policy import (
    PreFilterContext,
    _in_quiet_hours,
    _parse_hhmm,
    can_consider_acting,
    remaining_budget,
)
from proactive.store import BudgetSummary


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _policy(
    *,
    enabled: bool = True,
    channel_allowlist: list[int] | None = None,
    cooldown_reaction: int = 600,
    silence_hours: int = 4,
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
    quiet_tz: str = "UTC",
    reactions_per_day: int = 15,
    replies_per_day: int = 3,
    new_topics_per_day: int = 1,
) -> ProactiveConfig:
    return ProactiveConfig(
        enabled=enabled,
        channel_allowlist=channel_allowlist if channel_allowlist is not None else [123],
        budgets=ProactiveBudgets(
            reactions_per_day=reactions_per_day,
            replies_per_day=replies_per_day,
            new_topics_per_day=new_topics_per_day,
        ),
        cooldowns=ProactiveCooldowns(reaction_seconds=cooldown_reaction),
        quiet_hours=ProactiveQuietHours(
            timezone=quiet_tz, start=quiet_start, end=quiet_end
        ),
        silence_threshold_hours=silence_hours,
    )


def _ctx(
    *,
    persona_id: str = "lena",
    channel_id: int = 123,
    trigger: str = "activity",
    now: datetime | None = None,
    last_human: datetime | None = None,
    last_persona: datetime | None = None,
    last_other_persona: datetime | None = None,
    budget: BudgetSummary | None = None,
) -> PreFilterContext:
    return PreFilterContext(
        persona_id=persona_id,
        channel_id=channel_id,
        trigger=trigger,
        now=now or _utc(2026, 5, 8, 12, 0),
        last_human_message_at=last_human,
        last_persona_action_at=last_persona,
        last_other_persona_action_at=last_other_persona,
        budget=budget or BudgetSummary(reactions=10, replies=2, new_topics=1),
    )


# ---------------------------------------------------------------
# _parse_hhmm
# ---------------------------------------------------------------

class TestParseHHMM:
    def test_valid(self):
        assert _parse_hhmm("22:00").hour == 22
        assert _parse_hhmm("07:30").minute == 30

    def test_invalid_falls_back_to_midnight(self):
        assert _parse_hhmm("garbage") == _parse_hhmm("00:00")

    def test_none_falls_back(self):
        assert _parse_hhmm(None) == _parse_hhmm("00:00")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------

class TestQuietHours:
    def test_inside_window_wrapping_midnight(self):
        qh = ProactiveQuietHours(timezone="UTC", start="22:00", end="07:00")
        assert _in_quiet_hours(_utc(2026, 5, 8, 23, 0), qh) is True
        assert _in_quiet_hours(_utc(2026, 5, 8, 3, 30), qh) is True

    def test_outside_window_wrapping_midnight(self):
        qh = ProactiveQuietHours(timezone="UTC", start="22:00", end="07:00")
        assert _in_quiet_hours(_utc(2026, 5, 8, 12, 0), qh) is False

    def test_non_wrapping_window(self):
        qh = ProactiveQuietHours(timezone="UTC", start="01:00", end="05:00")
        assert _in_quiet_hours(_utc(2026, 5, 8, 3, 0), qh) is True
        assert _in_quiet_hours(_utc(2026, 5, 8, 6, 0), qh) is False

    def test_zero_width_window_is_off(self):
        qh = ProactiveQuietHours(timezone="UTC", start="22:00", end="22:00")
        assert _in_quiet_hours(_utc(2026, 5, 8, 22, 0), qh) is False

    def test_unknown_timezone_falls_back_to_utc(self):
        qh = ProactiveQuietHours(timezone="Not/A/Zone", start="22:00", end="07:00")
        # Should not raise; behavior == UTC
        result = _in_quiet_hours(_utc(2026, 5, 8, 23, 0), qh)
        assert result is True

    def test_boundary_inclusive_at_start_exclusive_at_end(self):
        qh = ProactiveQuietHours(timezone="UTC", start="22:00", end="07:00")
        assert _in_quiet_hours(_utc(2026, 5, 8, 22, 0), qh) is True   # at start
        assert _in_quiet_hours(_utc(2026, 5, 8, 7, 0), qh) is False   # at end


# ---------------------------------------------------------------
# can_consider_acting
# ---------------------------------------------------------------

class TestCanConsiderActing:
    def test_persona_disabled(self):
        policy = _policy(enabled=False)
        ok, reason = can_consider_acting(_ctx(), policy, cross_persona_lockout_seconds=5)
        assert ok is False
        assert reason == "persona_disabled"

    def test_channel_not_allowlisted(self):
        policy = _policy(channel_allowlist=[999])
        ok, reason = can_consider_acting(_ctx(channel_id=123), policy, 5)
        assert ok is False
        assert reason == "channel_not_allowlisted"

    def test_quiet_hours_blocks(self):
        policy = _policy(quiet_start="22:00", quiet_end="07:00", quiet_tz="UTC")
        ok, reason = can_consider_acting(
            _ctx(now=_utc(2026, 5, 8, 23, 0)), policy, 5
        )
        assert ok is False
        assert reason == "quiet_hours"

    def test_cross_persona_lockout(self):
        policy = _policy()
        now = _utc(2026, 5, 8, 12, 0)
        # Other persona acted 2s ago — within the 5s lockout
        last_other = now - timedelta(seconds=2)
        ok, reason = can_consider_acting(
            _ctx(now=now, last_other_persona=last_other),
            policy,
            cross_persona_lockout_seconds=5,
        )
        assert ok is False
        assert reason.startswith("cross_persona_lockout")

    def test_cross_persona_lockout_expired(self):
        policy = _policy()
        now = _utc(2026, 5, 8, 12, 0)
        last_other = now - timedelta(seconds=10)  # > 5s lockout
        ok, reason = can_consider_acting(
            _ctx(now=now, last_other_persona=last_other),
            policy,
            cross_persona_lockout_seconds=5,
        )
        assert ok is True

    def test_persona_cooldown(self):
        policy = _policy(cooldown_reaction=600)
        now = _utc(2026, 5, 8, 12, 0)
        last_persona = now - timedelta(seconds=300)  # half the 600s cooldown
        ok, reason = can_consider_acting(
            _ctx(now=now, last_persona=last_persona), policy, 5
        )
        assert ok is False
        assert reason.startswith("persona_cooldown")

    def test_persona_cooldown_expired(self):
        policy = _policy(cooldown_reaction=60)
        now = _utc(2026, 5, 8, 12, 0)
        last_persona = now - timedelta(seconds=120)
        ok, reason = can_consider_acting(
            _ctx(now=now, last_persona=last_persona), policy, 5
        )
        assert ok is True

    def test_heartbeat_silence_threshold_blocks(self):
        policy = _policy(silence_hours=4)
        now = _utc(2026, 5, 8, 12, 0)
        last_human = now - timedelta(hours=2)  # only 2h of silence, threshold is 4h
        ok, reason = can_consider_acting(
            _ctx(trigger="heartbeat", now=now, last_human=last_human), policy, 5
        )
        assert ok is False
        assert reason.startswith("channel_not_silent_enough")

    def test_heartbeat_no_recent_activity(self):
        policy = _policy(silence_hours=4)
        ok, reason = can_consider_acting(
            _ctx(trigger="heartbeat", last_human=None), policy, 5
        )
        assert ok is False
        assert reason == "no_recent_activity_to_evaluate"

    def test_heartbeat_silence_threshold_met(self):
        policy = _policy(silence_hours=4)
        now = _utc(2026, 5, 8, 12, 0)
        last_human = now - timedelta(hours=6)
        ok, reason = can_consider_acting(
            _ctx(trigger="heartbeat", now=now, last_human=last_human), policy, 5
        )
        assert ok is True

    def test_activity_does_not_check_silence_threshold(self):
        """Activity-trigger should fire on every message regardless of recency."""
        policy = _policy(silence_hours=4)
        # last_human is None — for heartbeat that means "no_recent_activity",
        # but for activity that's fine.
        ok, reason = can_consider_acting(
            _ctx(trigger="activity", last_human=None), policy, 5
        )
        assert ok is True

    def test_all_budgets_exhausted(self):
        policy = _policy()
        ok, reason = can_consider_acting(
            _ctx(budget=BudgetSummary(0, 0, 0)), policy, 5
        )
        assert ok is False
        assert reason == "all_budgets_exhausted"

    def test_partial_budget_remaining_is_ok(self):
        policy = _policy()
        ok, reason = can_consider_acting(
            _ctx(budget=BudgetSummary(0, 0, 1)), policy, 5
        )
        assert ok is True


# ---------------------------------------------------------------
# remaining_budget
# ---------------------------------------------------------------

class TestRemainingBudget:
    def test_no_actions_used(self):
        policy = _policy()
        b = remaining_budget({}, policy)
        assert b.reactions == 15
        assert b.replies == 3
        assert b.new_topics == 1

    def test_subtracts_each_decision_type(self):
        policy = _policy(reactions_per_day=15, replies_per_day=3, new_topics_per_day=1)
        b = remaining_budget(
            {"react": 5, "reply": 2, "new_topic": 0},
            policy,
        )
        assert b.reactions == 10
        assert b.replies == 1
        assert b.new_topics == 1

    def test_engage_persona_counts_as_reply(self):
        """Per spec Part 11: engage_persona is a message creating an opening
        for another persona, so it consumes a reply slot."""
        policy = _policy(replies_per_day=3)
        b = remaining_budget(
            {"reply": 1, "engage_persona": 1},
            policy,
        )
        assert b.replies == 1  # 3 - 1 - 1

    def test_never_negative(self):
        policy = _policy(reactions_per_day=2)
        b = remaining_budget({"react": 100}, policy)
        assert b.reactions == 0
