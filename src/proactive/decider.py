# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
The decider: a Haiku JSON call that decides whether to act, and what.

The actor validates everything the decider returns. Anything malformed
falls back to action='none' with a sanitization-rejected reason — this
addresses MAST FM-2.6 (Reasoning-Action Mismatch) and FM-1.2 (Disobey
Role Specification).
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from agents.persona_loader import PersonaConfig

from .observer import DeciderInput
from .store import BudgetSummary

logger = logging.getLogger("slashAI.proactive.decider")

ALLOWED_ACTIONS = {"none", "react", "reply", "new_topic", "engage_persona"}

# Single emoji or compound (ZWJ-joined / variation-selector). We reject
# anything containing ASCII letters/digits or punctuation that suggests text.
_FORBIDDEN_EMOJI_CHARS = re.compile(r"[A-Za-z0-9<>:_\-]")
_MAX_EMOJI_LEN = 16  # generous; family-emoji can be 7-8 codepoints


@dataclass
class ValidatedDecision:
    """Decider output after sanitization. Always trustworthy to act on."""
    action: str                              # ALLOWED_ACTIONS
    target_message_id: Optional[int]
    target_persona_id: Optional[str]
    emoji: Optional[str]
    reasoning: str
    confidence: float
    input_tokens: int
    output_tokens: int
    decider_model: str
    raw: Optional[dict[str, Any]] = None     # original JSON for audit


def _build_prompt(ctx: DeciderInput) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    persona = ctx.persona

    persona_summary = (
        f"{persona.display_name}: {persona.identity.personality}"
    )
    if persona.identity.background:
        persona_summary += f"\nBackground: {persona.identity.background}"

    formatted_recent = "\n".join(m.render() for m in ctx.recent_messages) or "(channel is silent)"
    triggering_block = (
        f"\nThe triggering message:\n{ctx.triggering_message.render()}"
        if ctx.triggering_message else ""
    )
    memories = "\n".join(f"- {m}" for m in ctx.relevant_memories) or "(none)"
    reflections = "\n".join(f"- {r}" for r in ctx.reflections_about_others) or "(none)"
    other_personas = ", ".join(ctx.other_personas_present) if ctx.other_personas_present else "none"
    recent_acting = ", ".join(ctx.other_personas_recent) if ctx.other_personas_recent else "none"
    if ctx.active_inter_agent_thread:
        t = ctx.active_inter_agent_thread
        decay = float(t.get("decay_factor", 1.0))
        active_thread = (
            f"\n\nActive bot-to-bot thread with @{t.get('other_participant')}: "
            f"turn {t.get('turn_count')}/{t.get('max_turns')}. "
            f"Engagement strength is decaying — only continue if there's a clear "
            f"reason to. Probability hint: ~{int(decay * 100)}%. "
            f"Prefer 'none' as turns climb. Don't repeat yourself; humans can "
            f"interrupt at any time and the thread will end."
        )
    else:
        active_thread = ""

    trigger_descr = (
        "new message arrived" if ctx.trigger == "activity" else "scheduled silence check"
    )

    system = (
        "You decide whether an AI persona should proactively act in a Discord channel. "
        "You return strict JSON. You bias HARD toward 'none' — most ticks should be no-ops. "
        "Reactions are the sweet spot: low-stakes, charming. Replies are sparing — only when "
        "there's a clear opening. New topics are rare — only in genuinely quiet channels with "
        "a real reason to engage. If a human conversation is active, prefer 'none' or a single "
        "reaction. If another persona just acted, prefer 'none' — don't pile on."
    )

    user = f"""# Persona deciding
{persona_summary}

# Channel state
Channel: #{ctx.channel_name}
Time: {ctx.now_local}
Trigger: {ctx.trigger} ({trigger_descr})

Recent messages (oldest first):
{formatted_recent}
{triggering_block}

# What {persona.display_name} knows
Relevant memories:
{memories}

What {persona.display_name} has previously concluded about people in this channel:
{reflections}

# State
Budget remaining today: {ctx.budget_remaining.reactions} reactions, {ctx.budget_remaining.replies} replies, {ctx.budget_remaining.new_topics} new topics
{ctx.last_action_summary}
Other AI personas in this server: {other_personas}
Personas that have acted in this channel within the last hour: {recent_acting}{active_thread}
Human conversation active right now: {"yes" if ctx.is_human_conversation_active else "no"}

# Decide
Respond with strict JSON only. No prose outside the JSON.

Schema:
{{
  "action": "none" | "react" | "reply" | "new_topic" | "engage_persona",
  "target_message_id": <int or null>,
  "target_persona_id": <string or null>,
  "emoji": <string or null>,
  "reasoning": <string, 1-2 sentences>,
  "confidence": <float 0.0-1.0>
}}
"""
    return system, user


def _extract_json_block(text: str) -> Optional[str]:
    """Pull the first JSON object out of free text. Decider should return clean
    JSON, but Haiku occasionally wraps it in ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        # Strip first fence line and trailing fence
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Locate the outermost {...}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def _looks_like_emoji(s: Optional[str]) -> bool:
    if not s or not isinstance(s, str):
        return False
    if len(s) > _MAX_EMOJI_LEN:
        return False
    if _FORBIDDEN_EMOJI_CHARS.search(s):
        return False
    # At least one codepoint must have a Unicode category that's symbolic
    # (So, Sk) or be in the supplementary planes typical for emoji.
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("So", "Sk", "Cs"):
            return True
        if ord(ch) >= 0x1F000:  # emoji blocks
            return True
        if ord(ch) in (0x200D, 0xFE0F):  # ZWJ, variation selector — only valid as joiners
            continue
    return False


class ProactiveDecider:
    def __init__(
        self,
        anthropic_client: anthropic.AsyncAnthropic,
        max_tokens: int = 300,
    ):
        self.client = anthropic_client
        self.max_tokens = max_tokens

    async def decide(self, ctx: DeciderInput) -> ValidatedDecision:
        """Call the decider model, parse, sanitize. Always returns a ValidatedDecision."""
        model = ctx.persona.proactive.decider_model
        system, user = _build_prompt(ctx)

        try:
            resp = await self.client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            logger.warning(f"Decider call failed for {ctx.persona.name}: {e}")
            return ValidatedDecision(
                action="none",
                target_message_id=None,
                target_persona_id=None,
                emoji=None,
                reasoning=f"decider_call_failed: {type(e).__name__}",
                confidence=0.0,
                input_tokens=0,
                output_tokens=0,
                decider_model=model,
            )

        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0)) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0)) if usage else 0

        return self._sanitize(
            text=text,
            ctx=ctx,
            input_tokens=in_tok,
            output_tokens=out_tok,
            decider_model=model,
        )

    def _sanitize(
        self,
        text: str,
        ctx: DeciderInput,
        input_tokens: int,
        output_tokens: int,
        decider_model: str,
    ) -> ValidatedDecision:
        json_block = _extract_json_block(text)
        if not json_block:
            return self._reject(
                "no_json_in_response", ctx, decider_model, input_tokens, output_tokens
            )

        try:
            raw = json.loads(json_block)
        except json.JSONDecodeError as e:
            return self._reject(
                f"json_decode_error: {e.msg}",
                ctx,
                decider_model,
                input_tokens,
                output_tokens,
            )

        if not isinstance(raw, dict):
            return self._reject(
                "json_not_object", ctx, decider_model, input_tokens, output_tokens
            )

        action = raw.get("action")
        if action not in ALLOWED_ACTIONS:
            return self._reject(
                f"invalid_action: {action!r}",
                ctx,
                decider_model,
                input_tokens,
                output_tokens,
                raw=raw,
            )

        # Budget check (action must have remaining budget; engage_persona uses the reply budget)
        budget_required = {
            "none": True,
            "react": ctx.budget_remaining.reactions > 0,
            "reply": ctx.budget_remaining.replies > 0,
            "new_topic": ctx.budget_remaining.new_topics > 0,
            "engage_persona": ctx.budget_remaining.replies > 0,
        }
        if not budget_required.get(action, False):
            return self._reject(
                f"budget_exhausted_for: {action}",
                ctx,
                decider_model,
                input_tokens,
                output_tokens,
                raw=raw,
            )

        target_message_id = raw.get("target_message_id")
        target_persona_id = raw.get("target_persona_id")
        emoji = raw.get("emoji")
        reasoning = str(raw.get("reasoning", ""))[:1000]
        confidence_raw = raw.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0

        valid_message_ids = {fm.message_id for fm in ctx.recent_messages}

        if action == "react":
            if target_message_id is None or int(target_message_id) not in valid_message_ids:
                return self._reject(
                    "react_target_message_id_not_in_window",
                    ctx, decider_model, input_tokens, output_tokens, raw=raw,
                )
            if not _looks_like_emoji(emoji):
                return self._reject(
                    f"react_invalid_emoji: {emoji!r}",
                    ctx, decider_model, input_tokens, output_tokens, raw=raw,
                )

        if action == "reply":
            if target_message_id is None or int(target_message_id) not in valid_message_ids:
                return self._reject(
                    "reply_target_message_id_not_in_window",
                    ctx, decider_model, input_tokens, output_tokens, raw=raw,
                )

        if action == "engage_persona":
            allowlist = ctx.persona.proactive.engages_with_personas
            if not target_persona_id or target_persona_id not in allowlist:
                return self._reject(
                    f"engage_persona_not_in_allowlist: {target_persona_id!r}",
                    ctx, decider_model, input_tokens, output_tokens, raw=raw,
                )

        return ValidatedDecision(
            action=action,
            target_message_id=int(target_message_id) if target_message_id is not None else None,
            target_persona_id=target_persona_id,
            emoji=emoji if action == "react" else None,
            reasoning=reasoning or "(no reasoning provided)",
            confidence=confidence,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            decider_model=decider_model,
            raw=raw,
        )

    @staticmethod
    def _reject(
        why: str,
        ctx: DeciderInput,
        decider_model: str,
        input_tokens: int,
        output_tokens: int,
        raw: Optional[dict[str, Any]] = None,
    ) -> ValidatedDecision:
        logger.info(f"[decider] sanitization rejected: {why}")
        return ValidatedDecision(
            action="none",
            target_message_id=None,
            target_persona_id=None,
            emoji=None,
            reasoning=f"actor_sanitization_rejected: {why}",
            confidence=0.0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            decider_model=decider_model,
            raw=raw,
        )
