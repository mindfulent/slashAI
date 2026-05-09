# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
The actor: executes a ValidatedDecision.

Action paths graduate out of shadow mode band-by-band. v0.16.0 (band 0)
shadow-modes everything. v0.16.1 (band 1) graduates `react` — reactions are
cheap, can't crowd humans, and have no body to misjudge. `reply`,
`new_topic`, and `engage_persona` stay shadow / unimplemented until later
bands so the operator can tune the decider on real reaction traces first.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import discord

from agents.persona_loader import PersonaConfig

from .config import GlobalProactiveConfig
from .decider import ValidatedDecision
from .observer import DeciderInput, FormattedMsg
from .store import ActionRecord, ProactiveStore
from .threads import InterAgentThreads

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger("slashAI.proactive.actor")

DISCORD_MAX_LENGTH = 2000
REPLY_MAX_TOKENS = 400
ENGAGE_MAX_TOKENS = 300
NEW_TOPIC_MAX_TOKENS = 250
NEW_TOPIC_HISTORY_HOURS = 72  # last 3 days for wider topical context


@dataclass
class ActionResult:
    success: bool
    note: str = ""
    posted_message_id: Optional[int] = None


class ProactiveActor:
    """Executes the decider's validated decision. Always logs to proactive_actions."""

    def __init__(
        self,
        persona: PersonaConfig,
        bot,
        store: ProactiveStore,
        global_config: GlobalProactiveConfig,
        anthropic_client: Optional["anthropic.AsyncAnthropic"] = None,
        threads: Optional[InterAgentThreads] = None,
        resolve_persona_user_id: Optional[Callable[[str], Optional[int]]] = None,
    ):
        self.persona = persona
        self.bot = bot
        self.store = store
        self.global_config = global_config
        # Anthropic client is required for reply / new_topic / engage_persona
        # generation. Optional in v0.16.0 because those paths were stubbed.
        self.anthropic_client = anthropic_client
        # InterAgentThreads is required for engage_persona (band 3+). Falls
        # back to no-op behavior when missing.
        self.threads = threads
        # Persona-name -> Discord bot user.id resolver. Set up by AgentManager
        # so any persona's actor can mention any other persona's bot.
        self.resolve_persona_user_id = resolve_persona_user_id or (lambda _: None)

    async def execute(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
    ) -> ActionResult:
        if decision.action == "none":
            await self._record(decision, ctx, posted_message_id=None)
            return ActionResult(success=True, note="logged_noop")

        # Per-action dispatch. Each handler decides whether shadow_mode applies.
        handler = {
            "react": self._do_reaction,
            "reply": self._do_reply,
            "new_topic": self._do_new_topic,
            "engage_persona": self._do_engage_persona,
        }.get(decision.action)

        if handler is None:
            logger.warning(f"[actor] unknown action {decision.action!r}; logging as noop")
            await self._record(decision, ctx, posted_message_id=None)
            return ActionResult(success=False, note=f"unknown:{decision.action}")

        return await handler(decision, ctx)

    # ------------------------------------------------------------------
    # Reactions (band 1 / v0.16.1) — graduated out of shadow mode
    # ------------------------------------------------------------------

    async def _do_reaction(
        self, decision: ValidatedDecision, ctx: DeciderInput
    ) -> ActionResult:
        channel = self.bot.get_channel(ctx.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ctx.channel_id)
            except discord.HTTPException as e:
                return await self._record_failure(
                    decision, ctx, f"fetch_channel_failed: {type(e).__name__}"
                )

        try:
            message = await channel.fetch_message(decision.target_message_id)
        except discord.NotFound:
            return await self._record_failure(
                decision, ctx, "target_message_not_found"
            )
        except discord.HTTPException as e:
            return await self._record_failure(
                decision, ctx, f"fetch_message_failed: {type(e).__name__}"
            )

        try:
            await message.add_reaction(decision.emoji)
        except discord.HTTPException as e:
            return await self._record_failure(
                decision, ctx, f"add_reaction_failed: {type(e).__name__}: {e!s:.80}"
            )

        # Success
        await self._record(decision, ctx, posted_message_id=None)
        logger.info(
            f"[{self.persona.name}] reacted with {decision.emoji} to "
            f"message {decision.target_message_id} in channel {ctx.channel_id}"
        )
        return ActionResult(success=True, note="reacted")

    # ------------------------------------------------------------------
    # Replies (band 2 / v0.16.2) — graduated out of shadow mode
    # ------------------------------------------------------------------

    async def _do_reply(
        self, decision: ValidatedDecision, ctx: DeciderInput
    ) -> ActionResult:
        if self.anthropic_client is None:
            return await self._record_failure(
                decision, ctx, "no_anthropic_client_configured"
            )

        # Resolve channel + target message
        channel = self.bot.get_channel(ctx.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ctx.channel_id)
            except discord.HTTPException as e:
                return await self._record_failure(
                    decision, ctx, f"fetch_channel_failed: {type(e).__name__}"
                )

        try:
            target = await channel.fetch_message(decision.target_message_id)
        except discord.NotFound:
            return await self._record_failure(
                decision, ctx, "target_message_not_found"
            )
        except discord.HTTPException as e:
            return await self._record_failure(
                decision, ctx, f"fetch_message_failed: {type(e).__name__}"
            )

        # Generate the reply text
        try:
            reply_text = await self._generate_reply_text(decision, ctx, target)
        except Exception as e:
            return await self._record_failure(
                decision, ctx, f"reply_generation_failed: {type(e).__name__}"
            )

        if not reply_text:
            return await self._record_failure(
                decision, ctx, "reply_generation_empty"
            )

        # Truncate to Discord limit (the directive asks for 1-3 sentences but
        # belt-and-suspenders so a runaway gen can't 400 the API)
        if len(reply_text) > DISCORD_MAX_LENGTH:
            reply_text = reply_text[: DISCORD_MAX_LENGTH - 3] + "..."

        # Post as a reply (Discord's native reply UI links the messages)
        try:
            posted = await channel.send(reply_text, reference=target)
        except discord.HTTPException as e:
            return await self._record_failure(
                decision, ctx, f"send_reply_failed: {type(e).__name__}"
            )

        await self._record(decision, ctx, posted_message_id=posted.id)
        # TODO(v0.16.4): feed proactive replies into the reflection job so
        # personas accumulate beliefs from their own contributions, not just
        # from inbound mentions.
        logger.info(
            f"[{self.persona.name}] replied to message {decision.target_message_id} "
            f"in channel {ctx.channel_id} (reply id={posted.id}, "
            f"chars={len(reply_text)})"
        )
        return ActionResult(success=True, note="replied", posted_message_id=posted.id)

    async def _generate_reply_text(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
        target_message: discord.Message,
    ) -> str:
        """Produce the reply body via the actor model.

        System prompt = persona identity (cacheable). User message = recent
        channel context + the reply directive (per spec Part 7).
        """
        system_prompt = self.persona.build_system_prompt()

        recent = "\n".join(m.render() for m in ctx.recent_messages) or "(no recent messages)"
        target_content = (target_message.content or "")[:500]
        target_author = target_message.author.display_name or target_message.author.name

        user_message = (
            f"Recent channel messages (oldest first):\n"
            f"{recent}\n\n"
            f"You decided to reply to message id={decision.target_message_id} "
            f"by {target_author}: \"{target_content}\"\n\n"
            f"Write the reply now. Keep it short — 1-3 sentences max. Match the "
            f"channel's tone. Don't @-mention the author. No trailing questions."
        )

        resp = await self.anthropic_client.messages.create(
            model=self.persona.proactive.actor_model,
            max_tokens=REPLY_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        text_parts: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        return "".join(text_parts).strip()

    # ------------------------------------------------------------------
    # New topic (band 4 / v0.16.4) — silence-breaker, graduated out of shadow
    # ------------------------------------------------------------------

    async def _do_new_topic(
        self, decision: ValidatedDecision, ctx: DeciderInput
    ) -> ActionResult:
        if self.anthropic_client is None:
            return await self._record_failure(
                decision, ctx, "no_anthropic_client_configured"
            )

        channel = self.bot.get_channel(ctx.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ctx.channel_id)
            except discord.HTTPException as e:
                return await self._record_failure(
                    decision, ctx, f"fetch_channel_failed: {type(e).__name__}"
                )

        try:
            topic_text = await self._generate_new_topic(decision, ctx, channel)
        except Exception as e:
            return await self._record_failure(
                decision, ctx, f"new_topic_generation_failed: {type(e).__name__}"
            )

        if not topic_text:
            return await self._record_failure(
                decision, ctx, "new_topic_generation_empty"
            )

        if len(topic_text) > DISCORD_MAX_LENGTH:
            topic_text = topic_text[: DISCORD_MAX_LENGTH - 3] + "..."

        # Plain channel send — not a reply, no @-mention. The point of
        # new_topic is breaking silence, not addressing a specific person.
        try:
            posted = await channel.send(topic_text)
        except discord.HTTPException as e:
            return await self._record_failure(
                decision, ctx, f"send_new_topic_failed: {type(e).__name__}"
            )

        await self._record(decision, ctx, posted_message_id=posted.id)
        logger.info(
            f"[{self.persona.name}] new_topic posted in channel {ctx.channel_id} "
            f"(message id={posted.id}, chars={len(topic_text)})"
        )
        return ActionResult(success=True, note="new_topic", posted_message_id=posted.id)

    async def _generate_new_topic(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
        channel: discord.abc.Messageable,
    ) -> str:
        """Generate a silence-breaker for a quiet channel.

        Pulls a wider history window (last ~3 days) than the decider's tick
        used, so the new topic can be a callback to a stale conversation
        rather than something out-of-the-blue.
        """
        from datetime import datetime, timedelta, timezone

        wide_history: list[str] = []
        try:
            after = datetime.now(timezone.utc) - timedelta(hours=NEW_TOPIC_HISTORY_HOURS)
            async for m in channel.history(limit=50, after=after):
                if m.author.bot:
                    continue
                content = (m.content or "").strip()
                if not content:
                    continue
                # Keep one-line snippets; trim long messages
                content = content[:200] + ("…" if len(content) > 200 else "")
                wide_history.append(f"- {m.author.display_name or m.author.name}: {content}")
        except discord.HTTPException as e:
            logger.debug(f"new_topic wide history fetch failed: {e}")

        wide_history_text = "\n".join(wide_history[-30:]) or "(no recent activity in the last 3 days)"
        memories_text = "\n".join(f"- {m}" for m in ctx.relevant_memories) or "(none)"
        reflections_text = "\n".join(f"- {r}" for r in ctx.reflections_about_others) or "(none)"

        # Estimate silence in hours from the most recent recent_message
        silence_hint = ""
        if ctx.recent_messages:
            latest = ctx.recent_messages[-1].created_at
            now = datetime.now(timezone.utc)
            hours = (now - latest).total_seconds() / 3600
            silence_hint = f"The channel has been quiet for ~{hours:.1f} hours."

        system_prompt = self.persona.build_system_prompt()

        user_message = (
            f"You're considering breaking silence in #{ctx.channel_name}. {silence_hint}\n\n"
            f"Recent channel activity (last 3 days, humans only):\n"
            f"{wide_history_text}\n\n"
            f"Relevant memories:\n{memories_text}\n\n"
            f"Reflections about people in this channel:\n{reflections_text}\n\n"
            f"Write a short message that sparks conversation — could be a question, "
            f"an observation, or a callback to a recent topic. 1-2 sentences. "
            f"No 'Hey everyone!' preamble. No @-mentions. Match the channel's tone."
        )

        resp = await self.anthropic_client.messages.create(
            model=self.persona.proactive.actor_model,
            max_tokens=NEW_TOPIC_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        text_parts: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        return "".join(text_parts).strip()

    # ------------------------------------------------------------------
    # Engage persona (band 3 / v0.16.3) — graduated; bot-to-bot threads
    # ------------------------------------------------------------------

    async def _do_engage_persona(
        self, decision: ValidatedDecision, ctx: DeciderInput
    ) -> ActionResult:
        if self.anthropic_client is None:
            return await self._record_failure(
                decision, ctx, "no_anthropic_client_configured"
            )
        if self.threads is None:
            return await self._record_failure(
                decision, ctx, "threads_subsystem_unavailable"
            )

        target_persona_id = decision.target_persona_id
        if not target_persona_id:
            return await self._record_failure(
                decision, ctx, "engage_persona_missing_target"
            )

        # Resolve the target persona's bot user.id so we can @-mention it.
        # If the target bot isn't connected, we degrade gracefully — log
        # the failure rather than crashing the scheduler.
        target_user_id = self.resolve_persona_user_id(target_persona_id)
        if target_user_id is None:
            return await self._record_failure(
                decision, ctx,
                f"target_persona_not_connected: {target_persona_id}",
            )

        # Resolve channel
        channel = self.bot.get_channel(ctx.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ctx.channel_id)
            except discord.HTTPException as e:
                return await self._record_failure(
                    decision, ctx, f"fetch_channel_failed: {type(e).__name__}"
                )

        # Generate the opening
        try:
            opening = await self._generate_engage_opening(
                decision, ctx, target_persona_id
            )
        except Exception as e:
            return await self._record_failure(
                decision, ctx, f"engage_generation_failed: {type(e).__name__}"
            )

        if not opening:
            return await self._record_failure(
                decision, ctx, "engage_generation_empty"
            )

        # Prepend an @-mention of the target bot so its mention-chat handler fires
        full_message = f"<@{target_user_id}> {opening}"
        if len(full_message) > DISCORD_MAX_LENGTH:
            full_message = full_message[: DISCORD_MAX_LENGTH - 3] + "..."

        # Post first; if that succeeds, start the thread row. If we started the
        # row first and the post failed, we'd leave a phantom thread.
        try:
            posted = await channel.send(full_message)
        except discord.HTTPException as e:
            return await self._record_failure(
                decision, ctx, f"send_engage_failed: {type(e).__name__}"
            )

        # Resolve ourselves too (we're the initiator)
        self_user_id = self.resolve_persona_user_id(self.persona.name)
        # If somehow our own user.id isn't resolvable (shouldn't happen since
        # we just sent a message), fall back to the bot.user attribute.
        if self_user_id is None:
            user = getattr(self.bot, "user", None)
            self_user_id = int(user.id) if user is not None else 0

        thread_state = await self.threads.start_thread(
            initiator_persona_id=self.persona.name,
            channel_id=ctx.channel_id,
            guild_id=ctx.guild_id,
            participants=[
                {"persona_id": self.persona.name, "user_id": self_user_id},
                {"persona_id": target_persona_id, "user_id": target_user_id},
            ],
            seed_message_id=posted.id,
            seed_topic=opening[:200],
        )

        # The seed message itself counts as turn 1. Advance now so the next
        # observed cross-bot message lands at turn 2.
        await self.threads.advance_thread(thread_state.id)

        await self._record(
            decision, ctx,
            posted_message_id=posted.id,
            inter_agent_thread_id=thread_state.id,
        )
        logger.info(
            f"[{self.persona.name}] engage_persona started thread {thread_state.id} "
            f"with {target_persona_id} in channel {ctx.channel_id} (seed={posted.id})"
        )
        return ActionResult(success=True, note="engaged", posted_message_id=posted.id)

    async def _generate_engage_opening(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
        target_persona_id: str,
    ) -> str:
        """Generate the opening message for engage_persona via the actor model."""
        system_prompt = self.persona.build_system_prompt()

        recent = "\n".join(m.render() for m in ctx.recent_messages) or "(channel is quiet)"
        seed_hint = ""
        if ctx.triggering_message:
            seed_hint = (
                f"\nA recent message that prompted this engagement:\n"
                f"{ctx.triggering_message.render()}\n"
            )

        user_message = (
            f"Recent channel context:\n"
            f"{recent}\n{seed_hint}\n"
            f"You decided to engage @{target_persona_id} in conversation here. "
            f"Write the opening message — short, in your voice, addressed to them. "
            f"1-2 sentences. No preamble. The message will be posted with an "
            f"@-mention of {target_persona_id} prefixed automatically; do NOT write "
            f"'@{target_persona_id}' in your reply. No trailing questions unless the "
            f"question is the whole point of engaging."
        )

        resp = await self.anthropic_client.messages.create(
            model=self.persona.proactive.actor_model,
            max_tokens=ENGAGE_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        text_parts: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        return "".join(text_parts).strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _stub_or_shadow(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
        action_name: str,
        lands_in: str,
    ) -> ActionResult:
        """Unimplemented action. Logs the *intended* decision so operators can
        see the decider's behavior on these paths before we implement them."""
        if self.global_config.shadow_mode:
            decision.reasoning = f"[SHADOW] {decision.reasoning}"
            note = "shadow_mode_skipped"
        else:
            decision.reasoning = f"[STUB:{lands_in}] {decision.reasoning}"
            note = f"unimplemented:{action_name}"
        await self._record(decision, ctx, posted_message_id=None)
        return ActionResult(success=False, note=note)

    async def _record_failure(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
        why: str,
    ) -> ActionResult:
        decision.reasoning = f"actor_failed: {why} | {decision.reasoning}"
        await self._record(decision, ctx, posted_message_id=None)
        logger.warning(f"[{self.persona.name}] actor_failed: {why}")
        return ActionResult(success=False, note=why)

    async def _record(
        self,
        decision: ValidatedDecision,
        ctx: DeciderInput,
        posted_message_id: Optional[int],
        inter_agent_thread_id: Optional[int] = None,
    ) -> int:
        # If the caller didn't supply a thread id but we're inside an active
        # thread for this channel, attach it so /proactive history shows the
        # action under the thread.
        if inter_agent_thread_id is None and ctx.active_inter_agent_thread:
            tid = ctx.active_inter_agent_thread.get("id")
            if tid is not None:
                inter_agent_thread_id = int(tid)
        return await self.store.record_action(
            ActionRecord(
                persona_id=self.persona.name,
                channel_id=ctx.channel_id,
                guild_id=ctx.guild_id,
                decision=decision.action,
                trigger=ctx.trigger,
                target_message_id=decision.target_message_id,
                target_persona_id=decision.target_persona_id,
                emoji=decision.emoji,
                posted_message_id=posted_message_id,
                inter_agent_thread_id=inter_agent_thread_id,
                reasoning=decision.reasoning,
                confidence=decision.confidence,
                decider_model=decision.decider_model,
                input_tokens=decision.input_tokens,
                output_tokens=decision.output_tokens,
            )
        )
