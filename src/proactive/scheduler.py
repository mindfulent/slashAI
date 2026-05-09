# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
ProactiveScheduler — the entry point that ties everything together.

One instance per persona. Owns:
  - the heartbeat tasks.loop (silence-breaker check across allowlisted channels)
  - the on_message_hook (activity path)
  - construction of pre-filter context, decider, actor

The actor is a no-op in shadow mode; the decider still runs so the operator
can read decisions out of `proactive_actions`.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Optional

import discord
from discord.ext import tasks

from agents.persona_loader import PersonaConfig
from analytics import track

from .actor import ProactiveActor
from .config import GlobalProactiveConfig
from .decider import ProactiveDecider
from .observer import ProactiveObserver
from .policy import PreFilterContext, can_consider_acting, remaining_budget
from .reflection import ReflectionEngine
from .store import ActionRecord, ProactiveStore
from .threads import InterAgentThreads, ThreadState

if TYPE_CHECKING:
    import anthropic
    import asyncpg

logger = logging.getLogger("slashAI.proactive.scheduler")

HUMAN_LAST_MESSAGE_LOOKBACK = 50  # max messages to scan for "last human message"


class ProactiveScheduler:
    """Per-persona scheduler bound to a single Discord client (primary or agent)."""

    def __init__(
        self,
        persona: PersonaConfig,
        bot: discord.Client,
        anthropic_client: "anthropic.AsyncAnthropic",
        memory_manager,
        db_pool: "asyncpg.Pool",
        global_config: GlobalProactiveConfig,
        all_persona_names: Optional[list[str]] = None,
        resolve_persona_user_id: Optional[Callable[[str], Optional[int]]] = None,
    ):
        self.persona = persona
        self.bot = bot
        self.global_config = global_config
        self.all_persona_names = all_persona_names or []
        self.resolve_persona_user_id = resolve_persona_user_id or (lambda _: None)

        self.store = ProactiveStore(db_pool)
        self.threads = InterAgentThreads(db_pool)
        self.reflection = ReflectionEngine(db_pool)
        self.observer = ProactiveObserver(
            persona, bot, memory_manager, self.store,
            threads=self.threads,
            reflection=self.reflection,
        )
        self.decider = ProactiveDecider(anthropic_client)
        self.actor = ProactiveActor(
            persona, bot, self.store, global_config,
            anthropic_client=anthropic_client,
            threads=self.threads,
            resolve_persona_user_id=self.resolve_persona_user_id,
        )
        # Stash the anthropic client for the reflection job
        self._anthropic_client = anthropic_client

        self._started = False
        # Runtime-mutable schedule period: discord.ext.tasks.loop is decorated
        # at class level so we read interval out at start time.
        self._heartbeat.change_interval(seconds=global_config.heartbeat_interval_seconds)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        if not self.global_config.enabled:
            logger.info(
                f"[{self.persona.name}] proactive globally disabled (PROACTIVE_ENABLED=false); "
                "scheduler will not start"
            )
            return
        if not self.persona.proactive.enabled:
            logger.info(
                f"[{self.persona.name}] proactive disabled in persona config; "
                "scheduler will not start"
            )
            return
        self._heartbeat.start()
        self._started = True
        logger.info(
            f"[{self.persona.name}] proactive scheduler started "
            f"(shadow_mode={self.global_config.shadow_mode}, "
            f"heartbeat_s={self.global_config.heartbeat_interval_seconds}, "
            f"channels={len(self.persona.proactive.channel_allowlist)})"
        )

    def stop(self) -> None:
        if self._started:
            self._heartbeat.cancel()
            self._started = False
            logger.info(f"[{self.persona.name}] proactive scheduler stopped")

    # ------------------------------------------------------------------
    # Activity path
    # ------------------------------------------------------------------

    async def on_message_hook(self, message: discord.Message) -> None:
        """Entry point from on_message for messages that aren't @-mentions/DMs to this bot.

        Handles three concerns:
          1. Human-interrupt termination of an active inter-agent thread
          2. Advancing the active thread on participant-bot messages
          3. Activity-path decider tick (for human messages, OR for participant
             bot messages while a thread is alive)
        """
        if not self._started:
            return
        # Don't fire on our own messages
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        if message.channel.id not in self.persona.proactive.channel_allowlist:
            return

        # 1+2. Thread observation
        active: Optional[ThreadState] = await self.threads.get_active_thread(
            message.channel.id
        )

        thread_alive_after_observation = active is not None
        if active is not None:
            if not message.author.bot:
                # Human-interrupt: any human message in the channel ends the thread
                await self.threads.end_thread(active.id, "human_interrupt")
                logger.info(
                    f"[{self.persona.name}] human_interrupt: ended thread "
                    f"{active.id} in channel {message.channel.id}"
                )
                thread_alive_after_observation = False
            elif message.author.id in active.participant_user_ids():
                # Participant bot posted; advance the thread
                updated = await self.threads.advance_thread(active.id)
                if updated and updated.turn_count >= updated.max_turns:
                    await self.threads.end_thread(updated.id, "turn_cap")
                    logger.info(
                        f"[{self.persona.name}] turn_cap reached at "
                        f"{updated.turn_count}/{updated.max_turns}; "
                        f"ended thread {updated.id}"
                    )
                    thread_alive_after_observation = False

        # 3. Activity-path decider tick
        # Bot messages: only tick if a thread is still alive (we may want to continue)
        if message.author.bot and not thread_alive_after_observation:
            return

        try:
            await self._tick(
                channel=message.channel,
                trigger="activity",
                triggering_message=message,
            )
        except Exception as e:
            logger.error(
                f"[{self.persona.name}] activity tick failed: {e}", exc_info=True
            )

    # ------------------------------------------------------------------
    # Heartbeat path
    # ------------------------------------------------------------------

    @tasks.loop(seconds=3600)  # placeholder; actual interval set in __init__
    async def _heartbeat(self) -> None:
        for cid in self.persona.proactive.channel_allowlist:
            channel = self.bot.get_channel(cid)
            if channel is None:
                continue
            try:
                await self._tick(channel=channel, trigger="heartbeat", triggering_message=None)
            except Exception as e:
                logger.error(
                    f"[{self.persona.name}] heartbeat tick on {cid} failed: {e}",
                    exc_info=True,
                )

        # End-of-tick reflection check (Enhancement 015 / v0.16.4).
        # Wrapped in try/except so a reflection failure can't kill the loop.
        try:
            stats = await self.reflection.maybe_reflect(
                self.persona.name, self._anthropic_client
            )
            if stats.reflections_stored > 0:
                logger.info(
                    f"[{self.persona.name}] reflection stored "
                    f"{stats.reflections_stored} insights "
                    f"(scored {stats.scored_count} actions, "
                    f"accumulated {stats.accumulated})"
                )
            elif stats.scored_count > 0:
                logger.debug(
                    f"[{self.persona.name}] reflection scored {stats.scored_count} "
                    f"actions; accumulated={stats.accumulated} (threshold={stats.threshold})"
                )
        except Exception as e:
            logger.warning(f"[{self.persona.name}] heartbeat reflection failed: {e}")

    @_heartbeat.before_loop
    async def _before_heartbeat(self) -> None:
        await self.bot.wait_until_ready()
        logger.info(f"[{self.persona.name}] heartbeat ready, entering loop")

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    async def _tick(
        self,
        channel: discord.abc.Messageable,
        trigger: str,
        triggering_message: Optional[discord.Message],
    ) -> None:
        now = datetime.now(timezone.utc)

        # 1. Pre-filter context
        last_persona_action = await self.store.last_persona_action_in_channel(
            self.persona.name, channel.id
        )
        last_other_action = await self.store.last_action_in_channel(
            channel.id, exclude_persona=self.persona.name
        )
        used_today = await self.store.daily_budget_used(
            self.persona.name, since=now.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        budget = remaining_budget(used_today, self.persona.proactive)
        last_human = await self._last_human_message_at(channel)

        prefilter = PreFilterContext(
            persona_id=self.persona.name,
            channel_id=channel.id,
            trigger=trigger,
            now=now,
            last_human_message_at=last_human,
            last_persona_action_at=last_persona_action,
            last_other_persona_action_at=last_other_action,
            budget=budget,
        )

        allowed, reason = can_consider_acting(
            prefilter,
            self.persona.proactive,
            self.global_config.cross_persona_lockout_seconds,
        )

        if not allowed:
            # Log the no-op with the rejection reason; no LLM call.
            await self._record_prefilter_noop(prefilter, reason, channel)
            # If our budget is exhausted and we initiated an active thread,
            # end it with budget_exhausted (per spec Part 8).
            if reason == "all_budgets_exhausted":
                active_thread = await self.threads.get_active_thread(channel.id)
                if (
                    active_thread is not None
                    and active_thread.initiator_persona_id == self.persona.name
                ):
                    await self.threads.end_thread(active_thread.id, "budget_exhausted")
                    logger.info(
                        f"[{self.persona.name}] budget_exhausted: ended thread "
                        f"{active_thread.id}"
                    )
            return

        # 2. Build decider input
        try:
            ctx = await self.observer.build(
                channel=channel,
                trigger=trigger,
                triggering_message=triggering_message,
                prefilter=prefilter,
                other_personas_present=[
                    p for p in self.all_persona_names if p != self.persona.name
                ],
            )
        except Exception as e:
            logger.warning(f"[{self.persona.name}] observer.build failed: {e}", exc_info=True)
            return

        # 3. Decide + sanitize
        decision = await self.decider.decide(ctx)

        # 4. Execute (no-op in shadow mode for non-graduated paths; record either way)
        await self.actor.execute(decision, ctx)

        # 5. Natural-end check: if we're in a thread and just decided 'none',
        # check if our last 2 non-prefilter decisions in this thread are both
        # 'none' (winding down).
        if (
            decision.action == "none"
            and ctx.active_inter_agent_thread is not None
        ):
            tid = ctx.active_inter_agent_thread.get("id")
            if tid is not None:
                try:
                    if await self.threads.check_natural_end(int(tid), self.persona.name):
                        await self.threads.end_thread(int(tid), "natural_end")
                        logger.info(
                            f"[{self.persona.name}] natural_end: ended thread {tid}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[{self.persona.name}] natural_end check failed: {e}"
                    )

        # 6. Analytics
        track(
            "proactive_decision",
            "system",
            channel_id=channel.id,
            guild_id=ctx.guild_id,
            properties={
                "persona_id": self.persona.name,
                "trigger": trigger,
                "action": decision.action,
                "confidence": decision.confidence,
                "decider_model": decision.decider_model,
                "input_tokens": decision.input_tokens,
                "output_tokens": decision.output_tokens,
                "reasoning_excerpt": (decision.reasoning or "")[:120],
                "shadow_mode": self.global_config.shadow_mode,
                "in_thread": ctx.active_inter_agent_thread is not None,
            },
        )

        # 7. Inter-agent turn analytics (separate event for clearer dashboards)
        if (
            ctx.active_inter_agent_thread is not None
            and decision.action in {"reply", "engage_persona"}
        ):
            track(
                "inter_agent_turn",
                "system",
                channel_id=channel.id,
                guild_id=ctx.guild_id,
                properties={
                    "thread_id": ctx.active_inter_agent_thread.get("id"),
                    "persona_id": self.persona.name,
                    "target_persona_id": ctx.active_inter_agent_thread.get(
                        "other_participant"
                    ),
                    "turn_count": ctx.active_inter_agent_thread.get("turn_count"),
                    "action": decision.action,
                },
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _record_prefilter_noop(
        self,
        prefilter: PreFilterContext,
        reason: str,
        channel: discord.abc.Messageable,
    ) -> None:
        """Pre-filter rejected the tick. Log a no-op so /proactive history shows everything."""
        try:
            await self.store.record_action(
                ActionRecord(
                    persona_id=self.persona.name,
                    channel_id=prefilter.channel_id,
                    guild_id=getattr(channel, "guild", None) and channel.guild.id,
                    decision="none",
                    trigger=prefilter.trigger,
                    reasoning=f"prefilter:{reason}",
                    confidence=0.0,
                    decider_model=None,
                    input_tokens=0,
                    output_tokens=0,
                )
            )
        except Exception as e:
            logger.debug(f"prefilter no-op log failed (non-fatal): {e}")

    async def _last_human_message_at(self, channel: discord.abc.Messageable) -> Optional[datetime]:
        """Scan recent history for the most recent non-bot message timestamp."""
        try:
            async for m in channel.history(limit=HUMAN_LAST_MESSAGE_LOOKBACK):
                if not m.author.bot:
                    return (
                        m.created_at if m.created_at.tzinfo
                        else m.created_at.replace(tzinfo=timezone.utc)
                    )
        except discord.HTTPException as e:
            logger.debug(f"Could not scan channel history: {e}")
        return None
