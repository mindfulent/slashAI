# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Builds the context bundle handed to the decider.

Reads recent channel messages, queries memory for relevant context, and
prepares everything as a structured object the decider can serialize into
its prompt template.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import discord

from agents.persona_loader import PersonaConfig

from .policy import PreFilterContext
from .reflection import ReflectionEngine
from .store import BudgetSummary
from .threads import InterAgentThreads, engagement_decay_factor

logger = logging.getLogger("slashAI.proactive.observer")

RECENT_MESSAGE_LIMIT = 15
HUMAN_ACTIVITY_WINDOW_SECONDS = 120  # "human conversation active" if 2+ humans in last 2 min


@dataclass
class FormattedMsg:
    message_id: int
    author_id: int
    author_name: str
    is_bot: bool
    content: str
    created_at: datetime

    def render(self) -> str:
        """One-line Discord-ish format for prompt inclusion."""
        kind = "BOT" if self.is_bot else "USER"
        # Trim very long messages so the decider window stays small
        content = self.content[:300] + ("…" if len(self.content) > 300 else "")
        ts = self.created_at.strftime("%H:%M")
        return f"[{ts} {kind} {self.author_name} id={self.message_id}] {content}"


@dataclass
class DeciderInput:
    persona: PersonaConfig
    channel_id: int
    guild_id: Optional[int]
    channel_name: str
    trigger: str                                       # 'activity' | 'heartbeat'

    recent_messages: list[FormattedMsg]
    triggering_message: Optional[FormattedMsg]         # set on activity trigger

    relevant_memories: list[str] = field(default_factory=list)
    reflections_about_others: list[str] = field(default_factory=list)

    budget_remaining: BudgetSummary = field(default_factory=lambda: BudgetSummary(0, 0, 0))
    last_action_summary: str = "No prior action today"

    other_personas_present: list[str] = field(default_factory=list)
    other_personas_recent: list[str] = field(default_factory=list)

    active_inter_agent_thread: Optional[dict[str, Any]] = None  # populated in v0.14.3

    now_local: str = ""
    is_human_conversation_active: bool = False


class ProactiveObserver:
    """
    Assembles a DeciderInput for one decision tick.

    Reads from Discord (channel.history), the memory manager, and the store.
    Holds no state of its own.
    """

    def __init__(
        self,
        persona: PersonaConfig,
        bot,
        memory_manager,
        store,
        threads: Optional[InterAgentThreads] = None,
        reflection: Optional[ReflectionEngine] = None,
    ):
        self.persona = persona
        self.bot = bot
        self.memory = memory_manager
        self.store = store
        self.threads = threads
        self.reflection = reflection

    async def build(
        self,
        channel: discord.abc.Messageable,
        trigger: str,
        triggering_message: Optional[discord.Message],
        prefilter: PreFilterContext,
        other_personas_present: list[str],
    ) -> DeciderInput:
        recent = await self._fetch_recent(channel)
        triggering = (
            self._format_message(triggering_message) if triggering_message else None
        )

        memories: list[str] = []
        if self.memory is not None:
            memories = await self._fetch_memories(channel, triggering_message, recent)

        last_action_summary = await self._last_action_summary(prefilter.persona_id, prefilter.channel_id, prefilter.now)
        is_active = self._is_human_conversation_active(recent, prefilter.now)

        recent_recent = await self._recent_acting_personas(prefilter.channel_id, prefilter.now)

        active_thread_summary = await self._active_thread_summary(prefilter.channel_id)

        reflections_text = await self._fetch_reflections(
            triggering_message=triggering_message,
            recent=recent,
            other_personas=other_personas_present,
            channel_id=prefilter.channel_id,
        )

        return DeciderInput(
            persona=self.persona,
            channel_id=prefilter.channel_id,
            guild_id=getattr(channel, "guild", None) and channel.guild.id,
            channel_name=getattr(channel, "name", "DM"),
            trigger=trigger,
            recent_messages=recent,
            triggering_message=triggering,
            relevant_memories=memories,
            reflections_about_others=reflections_text,
            budget_remaining=prefilter.budget,
            last_action_summary=last_action_summary,
            other_personas_present=other_personas_present,
            other_personas_recent=recent_recent,
            active_inter_agent_thread=active_thread_summary,
            now_local=self._format_now_local(prefilter.now),
            is_human_conversation_active=is_active,
        )

    async def _fetch_reflections(
        self,
        triggering_message: Optional[discord.Message],
        recent: list["FormattedMsg"],
        other_personas: list[str],
        channel_id: int,
    ) -> list[str]:
        """Pull top-3 reflections relevant to the current context.

        Subject filter = other personas in the server + recent author IDs +
        the channel itself, so reflections about anyone visible in this
        conversation surface to the decider.
        """
        if self.reflection is None:
            return []

        # Build query text from the most contextually anchoring source available
        if triggering_message is not None and triggering_message.content:
            query = triggering_message.content
        else:
            recent_human_content = [
                fm.content for fm in reversed(recent)
                if not fm.is_bot and fm.content
            ]
            query = recent_human_content[0] if recent_human_content else ""

        if not query:
            return []

        # Subject filter: other personas + recent human authors + channel
        subject_filter: list[str] = list(other_personas)
        for fm in recent:
            if not fm.is_bot:
                subject_filter.append(str(fm.author_id))
        subject_filter.append(str(channel_id))

        try:
            return await self.reflection.retrieve_about(
                persona_id=self.persona.name,
                query=query,
                subject_filter=subject_filter,
                limit=3,
            )
        except Exception as e:
            logger.debug(f"reflection.retrieve_about failed (non-fatal): {e}")
            return []

    async def _active_thread_summary(self, channel_id: int) -> Optional[dict[str, Any]]:
        """Compact dict for DeciderInput.active_inter_agent_thread.

        Includes the engagement decay factor as a soft hint to the decider
        (decay isn't math the LLM has to compute — it's a written cue in the
        prompt asking it to wind down).
        """
        if self.threads is None:
            return None
        active = await self.threads.get_active_thread(channel_id)
        if active is None:
            return None
        other = active.other_participant(self.persona.name)
        return {
            "id": active.id,
            "turn_count": active.turn_count,
            "max_turns": active.max_turns,
            "other_participant": other,
            "initiator_persona_id": active.initiator_persona_id,
            "decay_factor": engagement_decay_factor(active.turn_count),
            "we_are_initiator": active.initiator_persona_id == self.persona.name,
        }

    async def _fetch_recent(self, channel: discord.abc.Messageable) -> list[FormattedMsg]:
        msgs: list[FormattedMsg] = []
        try:
            async for m in channel.history(limit=RECENT_MESSAGE_LIMIT):
                msgs.append(self._format_message(m))
        except discord.HTTPException as e:
            logger.warning(f"Could not fetch channel history for {channel}: {e}")
            return []
        msgs.reverse()  # chronological order
        return msgs

    @staticmethod
    def _format_message(m: discord.Message) -> FormattedMsg:
        return FormattedMsg(
            message_id=m.id,
            author_id=m.author.id,
            author_name=m.author.display_name or m.author.name,
            is_bot=m.author.bot,
            content=m.content or "",
            created_at=m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=timezone.utc),
        )

    async def _fetch_memories(
        self,
        channel: discord.abc.Messageable,
        triggering_message: Optional[discord.Message],
        recent: list[FormattedMsg],
    ) -> list[str]:
        """Retrieve up to 3 relevant memories for the persona, scoped via agent_id."""
        if triggering_message is not None and triggering_message.content:
            query = triggering_message.content
            user_id = triggering_message.author.id
        elif recent:
            # Use the most recent human message as the topical anchor
            for fm in reversed(recent):
                if not fm.is_bot and fm.content:
                    query = fm.content
                    user_id = fm.author_id
                    break
            else:
                return []
        else:
            return []

        try:
            result = await self.memory.retrieve(
                user_id=user_id,
                query=query,
                channel=channel,
                agent_id=self.persona.memory.agent_id or self.persona.name,
            )
        except Exception as e:
            logger.debug(f"Memory retrieval failed (non-fatal): {e}")
            return []

        memories = getattr(result, "memories", None) or []
        out: list[str] = []
        for mem in memories[:3]:
            summary = getattr(mem, "topic_summary", None) or getattr(mem, "summary", None)
            if summary:
                out.append(summary)
        return out

    async def _last_action_summary(
        self, persona_id: str, channel_id: int, now: datetime
    ) -> str:
        last = await self.store.last_persona_action_in_channel(persona_id, channel_id)
        if last is None:
            return "No prior action in this channel today"
        elapsed_min = int((now - last).total_seconds() / 60)
        return f"Last action {elapsed_min} min ago"

    @staticmethod
    def _is_human_conversation_active(
        recent: list[FormattedMsg], now: datetime
    ) -> bool:
        cutoff = now - timedelta(seconds=HUMAN_ACTIVITY_WINDOW_SECONDS)
        humans = {fm.author_id for fm in recent if not fm.is_bot and fm.created_at >= cutoff}
        return len(humans) >= 2

    async def _recent_acting_personas(self, channel_id: int, now: datetime) -> list[str]:
        """Personas that have acted in this channel within the last hour."""
        cutoff = now - timedelta(hours=1)
        rows = await self.store.db.fetch(
            """
            SELECT DISTINCT persona_id FROM proactive_actions
            WHERE channel_id = $1 AND created_at >= $2 AND decision != 'none'
            """,
            channel_id,
            cutoff,
        )
        return [r["persona_id"] for r in rows]

    def _format_now_local(self, now: datetime) -> str:
        try:
            tz = ZoneInfo(self.persona.proactive.quiet_hours.timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        local = now.astimezone(tz)
        return local.strftime("%A %Y-%m-%d %H:%M %Z")
