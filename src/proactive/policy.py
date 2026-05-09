# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Pure-Python pre-filter for proactive decisions.

Runs before any LLM call. Short-circuits the common case (silence) so the
heartbeat and activity paths are cheap when nothing should happen.

Order matters: cheapest checks first.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from agents.persona_loader import ProactiveConfig, ProactiveQuietHours

from .store import BudgetSummary

logger = logging.getLogger("slashAI.proactive.policy")


@dataclass
class PreFilterContext:
    persona_id: str
    channel_id: int
    trigger: str                                  # 'activity' | 'heartbeat'
    now: datetime                                 # tz-aware UTC
    last_human_message_at: Optional[datetime]    # in this channel, recent
    last_persona_action_at: Optional[datetime]   # this persona, this channel
    last_other_persona_action_at: Optional[datetime]  # any OTHER persona, this channel
    budget: BudgetSummary


def _parse_hhmm(s: str) -> time:
    """Parse 'HH:MM' into a time. Defensive against bad input."""
    try:
        hour, minute = s.split(":", 1)
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return time(0, 0)


def _in_quiet_hours(now: datetime, qh: ProactiveQuietHours) -> bool:
    """True if `now` falls inside the persona's quiet-hours window.

    Window may wrap midnight (e.g., 22:00 -> 07:00). Comparison is done in
    the persona's configured timezone.
    """
    try:
        tz = ZoneInfo(qh.timezone)
    except Exception:
        logger.warning(f"Unknown timezone {qh.timezone!r}; falling back to UTC")
        tz = ZoneInfo("UTC")

    local_now = now.astimezone(tz).time()
    start = _parse_hhmm(qh.start)
    end = _parse_hhmm(qh.end)

    if start == end:
        return False  # zero-width window
    if start < end:
        return start <= local_now < end
    # Wraps midnight
    return local_now >= start or local_now < end


def can_consider_acting(
    ctx: PreFilterContext,
    policy: ProactiveConfig,
    cross_persona_lockout_seconds: int,
) -> tuple[bool, str]:
    """
    Decide whether the decider should be invoked at all.

    Returns (allowed, reason). Reason is for logging/debugging when
    allowed=False — written into `proactive_actions.reasoning` so the
    operator can see what's silencing the system.
    """
    if not policy.enabled:
        return False, "persona_disabled"

    if ctx.channel_id not in policy.channel_allowlist:
        return False, "channel_not_allowlisted"

    if _in_quiet_hours(ctx.now, policy.quiet_hours):
        return False, "quiet_hours"

    # Cross-persona lockout: any other persona acted recently → wait
    if ctx.last_other_persona_action_at:
        elapsed = (ctx.now - ctx.last_other_persona_action_at).total_seconds()
        if elapsed < cross_persona_lockout_seconds:
            return False, f"cross_persona_lockout ({elapsed:.0f}s < {cross_persona_lockout_seconds}s)"

    # This persona's own cooldown — uses tightest (reaction) cooldown as the floor
    if ctx.last_persona_action_at:
        elapsed = (ctx.now - ctx.last_persona_action_at).total_seconds()
        if elapsed < policy.cooldowns.reaction_seconds:
            return False, f"persona_cooldown ({elapsed:.0f}s < {policy.cooldowns.reaction_seconds}s)"

    # Heartbeat-only: silence threshold (no point breaking silence in an active channel)
    if ctx.trigger == "heartbeat":
        if ctx.last_human_message_at is None:
            return False, "no_recent_activity_to_evaluate"
        silence_hours = (ctx.now - ctx.last_human_message_at).total_seconds() / 3600
        if silence_hours < policy.silence_threshold_hours:
            return False, f"channel_not_silent_enough ({silence_hours:.1f}h < {policy.silence_threshold_hours}h)"

    # Daily budget exhausted?
    if (
        ctx.budget.reactions == 0
        and ctx.budget.replies == 0
        and ctx.budget.new_topics == 0
    ):
        return False, "all_budgets_exhausted"

    return True, "ok"


def remaining_budget(used: dict[str, int], policy: ProactiveConfig) -> BudgetSummary:
    """Compute remaining daily budget from a `used` dict (decision → count).

    `engage_persona` consumes a reply slot (it's a message creating an opening).
    """
    return BudgetSummary(
        reactions=max(0, policy.budgets.reactions_per_day - used.get("react", 0)),
        replies=max(
            0,
            policy.budgets.replies_per_day
            - used.get("reply", 0)
            - used.get("engage_persona", 0),
        ),
        new_topics=max(0, policy.budgets.new_topics_per_day - used.get("new_topic", 0)),
    )
