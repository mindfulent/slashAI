# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Proactive Interaction Slash Commands (Owner-Only)

Tuning and observability surface for the proactive subsystem (Enhancement 015).
v0.16.0: /proactive history.
v0.16.1: /proactive status, /proactive enable, /proactive disable.
v0.16.3: /proactive threads.
v0.16.4: /proactive reflect.
v0.16.5: /proactive simulate.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from proactive.policy import remaining_budget
from proactive.store import ProactiveStore
from proactive.sycophancy import SycophancyDetector
from proactive.threads import InterAgentThreads

logger = logging.getLogger("slashAI.commands.proactive")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)


_DECISION_EMOJI = {
    "none": "·",
    "react": "👋",
    "reply": "💬",
    "new_topic": "🆕",
    "engage_persona": "🤝",
}


def _format_history_row(row: dict) -> str:
    """One-line summary of a single decision row."""
    ts = row["created_at"].strftime("%m-%d %H:%M:%S")
    icon = _DECISION_EMOJI.get(row["decision"], "?")
    persona = row["persona_id"]
    decision = row["decision"]
    trigger = row["trigger"]
    reason = (row.get("reasoning") or "")[:90]
    extra_bits = []
    if row.get("emoji"):
        extra_bits.append(f"emoji={row['emoji']}")
    if row.get("target_persona_id"):
        extra_bits.append(f"target={row['target_persona_id']}")
    if row.get("confidence") is not None:
        extra_bits.append(f"conf={row['confidence']:.2f}")
    if row.get("input_tokens") or row.get("output_tokens"):
        extra_bits.append(f"tok={row.get('input_tokens', 0)}/{row.get('output_tokens', 0)}")
    extras = " ".join(extra_bits)
    return f"`{ts}` {icon} **{persona}** `{decision}` ({trigger}) {extras}\n  └ {reason}"


class ProactiveCommands(commands.Cog):
    """Slash commands for the proactive subsystem (Enhancement 015)."""

    proactive_group = app_commands.Group(
        name="proactive",
        description="Tune and observe the proactive interaction subsystem (owner only)",
    )

    def __init__(
        self,
        bot: commands.Bot,
        db_pool: asyncpg.Pool,
        owner_id: Optional[str] = None,
    ):
        self.bot = bot
        self.db = db_pool
        self.store = ProactiveStore(db_pool)
        self.threads = InterAgentThreads(db_pool)
        self.sycophancy = SycophancyDetector(db_pool)
        self.owner_id = int(owner_id) if owner_id else None

    @proactive_group.command(name="history")
    @owner_only()
    @app_commands.describe(
        persona="Filter by persona id (e.g. slashai, lena)",
        channel="Filter by channel",
        limit="How many decisions to show (default 25, max 50)",
    )
    async def history(
        self,
        interaction: discord.Interaction,
        persona: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
        limit: app_commands.Range[int, 1, 50] = 25,
    ):
        """Show recent proactive decisions (incl. no-ops) with reasoning."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.store.recent_history(
            persona_id=persona,
            channel_id=channel.id if channel else None,
            limit=limit,
        )

        if not rows:
            await interaction.followup.send(
                "No proactive decisions logged yet. "
                "(Set `PROACTIVE_ENABLED=true` and configure `personas/*.json:proactive.channel_allowlist`.)",
                ephemeral=True,
            )
            return

        lines = [_format_history_row(r) for r in rows]
        body = "\n".join(lines)

        # Discord embed description limit is 4096; conservatively chunk to 3800
        if len(body) > 3800:
            body = body[:3800] + "\n\n_(truncated; reduce limit or filter by persona/channel)_"

        embed = discord.Embed(
            title=f"Proactive history ({len(rows)} decisions)",
            description=body,
            color=discord.Color.dark_teal(),
        )
        filters = []
        if persona:
            filters.append(f"persona={persona}")
        if channel:
            filters.append(f"channel=#{channel.name}")
        if filters:
            embed.set_footer(text=" · ".join(filters))

        await interaction.followup.send(embed=embed, ephemeral=True)

    @proactive_group.command(name="status")
    @owner_only()
    async def status(self, interaction: discord.Interaction):
        """Show current proactive status, allowlist, and remaining budgets."""
        await interaction.response.defer(ephemeral=True)

        scheduler = getattr(self.bot, "proactive_scheduler", None)
        global_config = getattr(self.bot, "global_proactive_config", None)

        if scheduler is None or global_config is None:
            await interaction.followup.send(
                "Proactive subsystem is not initialized. Check that "
                "`PROACTIVE_ENABLED` is set and the bot has restarted.",
                ephemeral=True,
            )
            return

        persona = scheduler.persona
        now = datetime.now(timezone.utc)
        used_today = await self.store.daily_budget_used(
            persona.name, since=now.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        budget = remaining_budget(used_today, persona.proactive)

        # Channel names for the allowlist (best-effort lookup)
        allowlist_lines = []
        for cid in persona.proactive.channel_allowlist:
            ch = self.bot.get_channel(cid)
            label = f"#{ch.name}" if ch else f"id={cid} (unknown)"
            allowlist_lines.append(f"  • {label} `{cid}`")
        allowlist_text = "\n".join(allowlist_lines) or "  _(empty)_"

        # Format used counts
        used_text = ", ".join(f"{k}={v}" for k, v in sorted(used_today.items())) or "none"

        embed = discord.Embed(
            title=f"Proactive status — {persona.display_name}",
            color=discord.Color.dark_teal(),
        )
        embed.add_field(
            name="Global",
            value=(
                f"`PROACTIVE_ENABLED`={global_config.enabled} "
                f"`PROACTIVE_SHADOW_MODE`={global_config.shadow_mode}\n"
                f"heartbeat: {global_config.heartbeat_interval_seconds}s · "
                f"cross-persona lockout: {global_config.cross_persona_lockout_seconds}s"
            ),
            inline=False,
        )
        embed.add_field(
            name="Persona",
            value=(
                f"persona.proactive.enabled = `{persona.proactive.enabled}`\n"
                f"scheduler started = `{scheduler._started}`\n"
                f"decider model = `{persona.proactive.decider_model}`\n"
                f"actor model = `{persona.proactive.actor_model}`"
            ),
            inline=False,
        )
        embed.add_field(name="Allowlist", value=allowlist_text, inline=False)
        embed.add_field(
            name="Today (UTC)",
            value=(
                f"used: {used_text}\n"
                f"remaining: reactions={budget.reactions}, "
                f"replies={budget.replies}, new_topics={budget.new_topics}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Engages with",
            value=", ".join(persona.proactive.engages_with_personas) or "_(none)_",
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @proactive_group.command(name="enable")
    @owner_only()
    @app_commands.describe(channel="Channel to add to the proactive allowlist")
    async def enable(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        """Add a channel to the proactive allowlist (in-memory; restart resets unless persisted to JSON)."""
        await interaction.response.defer(ephemeral=True)

        scheduler = getattr(self.bot, "proactive_scheduler", None)
        if scheduler is None:
            await interaction.followup.send(
                "Proactive subsystem is not initialized.", ephemeral=True
            )
            return

        persona = scheduler.persona
        if channel.id in persona.proactive.channel_allowlist:
            await interaction.followup.send(
                f"#{channel.name} (`{channel.id}`) is already on **{persona.display_name}**'s allowlist.",
                ephemeral=True,
            )
            return

        persona.proactive.channel_allowlist.append(channel.id)
        logger.info(
            f"[/proactive enable] Added channel {channel.id} (#{channel.name}) "
            f"to {persona.name} allowlist (in-memory)"
        )
        await interaction.followup.send(
            f"Added #{channel.name} (`{channel.id}`) to **{persona.display_name}**'s allowlist.\n"
            f"_Note: this is in-memory only — edit `personas/{persona.name}.json:proactive.channel_allowlist` "
            f"to make it persist across restarts._",
            ephemeral=True,
        )

    @proactive_group.command(name="disable")
    @owner_only()
    @app_commands.describe(channel="Channel to remove from the proactive allowlist")
    async def disable(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        """Remove a channel from the proactive allowlist (in-memory)."""
        await interaction.response.defer(ephemeral=True)

        scheduler = getattr(self.bot, "proactive_scheduler", None)
        if scheduler is None:
            await interaction.followup.send(
                "Proactive subsystem is not initialized.", ephemeral=True
            )
            return

        persona = scheduler.persona
        if channel.id not in persona.proactive.channel_allowlist:
            await interaction.followup.send(
                f"#{channel.name} (`{channel.id}`) is not on **{persona.display_name}**'s allowlist.",
                ephemeral=True,
            )
            return

        persona.proactive.channel_allowlist.remove(channel.id)
        logger.info(
            f"[/proactive disable] Removed channel {channel.id} (#{channel.name}) "
            f"from {persona.name} allowlist (in-memory)"
        )
        await interaction.followup.send(
            f"Removed #{channel.name} (`{channel.id}`) from **{persona.display_name}**'s allowlist.\n"
            f"_Note: in-memory only — edit `personas/{persona.name}.json:proactive.channel_allowlist` "
            f"to make removal persist._",
            ephemeral=True,
        )

    @proactive_group.command(name="threads")
    @owner_only()
    @app_commands.describe(limit="How many threads to show (default 10, max 25)")
    async def threads(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
    ):
        """Show active and recent inter-agent threads (Enhancement 015 / band 3)."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.threads.list_recent(limit=limit)
        if not rows:
            await interaction.followup.send(
                "No inter-agent threads recorded yet. They start when one persona's "
                "decider returns `engage_persona`.",
                ephemeral=True,
            )
            return

        lines = []
        for t in rows:
            participants = " ↔ ".join(t.participant_persona_ids()) or "?"
            ch = self.bot.get_channel(t.channel_id)
            ch_label = f"#{ch.name}" if ch else f"id={t.channel_id}"
            if t.ended_at is None:
                status = (
                    f"🟢 **active** turn {t.turn_count}/{t.max_turns}"
                )
            else:
                duration_s = max(0, int((t.ended_at - t.started_at).total_seconds()))
                status = (
                    f"⚫ **{t.ended_reason or 'ended'}** "
                    f"after {t.turn_count}/{t.max_turns} turns ({duration_s}s)"
                )
            started = t.started_at.strftime("%m-%d %H:%M:%S")
            seed_excerpt = (t.seed_topic or "")[:60]
            lines.append(
                f"`#{t.id:>4}` `{started}` {participants} in {ch_label}\n"
                f"  └ {status}\n"
                f"  └ seed: _{seed_excerpt}_" if seed_excerpt else
                f"`#{t.id:>4}` `{started}` {participants} in {ch_label}\n"
                f"  └ {status}"
            )

        body = "\n".join(lines)
        if len(body) > 3800:
            body = body[:3800] + "\n\n_(truncated)_"

        embed = discord.Embed(
            title=f"Inter-agent threads ({len(rows)} most recent)",
            description=body,
            color=discord.Color.dark_purple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @proactive_group.command(name="reflect")
    @owner_only()
    @app_commands.describe(
        persona="Persona to reflect for (defaults to the primary bot if omitted)",
    )
    async def reflect(
        self,
        interaction: discord.Interaction,
        persona: Optional[str] = None,
    ):
        """Force-trigger the reflection pipeline for a persona.

        Bypasses the importance threshold (force=True) so an operator can
        verify the synthesis pipeline end-to-end without waiting for ~150
        importance points to accumulate.
        """
        await interaction.response.defer(ephemeral=True)

        # Resolve which scheduler/anthropic_client to use
        scheduler = None
        if persona is None or persona == "slashai":
            scheduler = getattr(self.bot, "proactive_scheduler", None)
        else:
            agent_manager = getattr(self.bot, "agent_manager", None)
            if agent_manager is not None:
                client = agent_manager.agents.get(persona)
                if client is not None:
                    scheduler = getattr(client, "proactive_scheduler", None)

        if scheduler is None:
            await interaction.followup.send(
                f"Could not find a proactive scheduler for persona `{persona or 'slashai'}`. "
                f"Is the persona configured and connected?",
                ephemeral=True,
            )
            return

        try:
            stats = await scheduler.reflection.maybe_reflect(
                scheduler.persona.name,
                scheduler._anthropic_client,
                force=True,
            )
        except Exception as e:
            logger.error(f"/proactive reflect failed: {e}", exc_info=True)
            await interaction.followup.send(
                f"Reflection pipeline raised: `{type(e).__name__}: {e}`",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Reflection — {scheduler.persona.display_name}",
            color=discord.Color.dark_gold(),
        )
        embed.add_field(
            name="Scoring",
            value=(
                f"Newly scored actions: **{stats.scored_count}**\n"
                f"Accumulated importance since last reflection: "
                f"**{stats.accumulated}** / threshold {stats.threshold}"
            ),
            inline=False,
        )
        if stats.skipped_reason:
            embed.add_field(name="Skipped", value=f"`{stats.skipped_reason}`", inline=False)
        if stats.questions:
            embed.add_field(
                name="Salient questions",
                value="\n".join(f"• {q}" for q in stats.questions[:5]),
                inline=False,
            )
        embed.add_field(
            name="Stored",
            value=f"**{stats.reflections_stored}** new reflections written to `agent_reflections`",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @proactive_group.command(name="simulate")
    @owner_only()
    @app_commands.describe(
        channel="Channel to simulate against",
        persona="Persona to simulate (defaults to slashai)",
    )
    async def simulate(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        persona: Optional[str] = None,
    ):
        """Run the decider against the current channel state without acting.

        The single most useful command for tuning. Probes the decider's
        judgment without consequences.
        """
        await interaction.response.defer(ephemeral=True)

        scheduler = None
        if persona is None or persona == "slashai":
            scheduler = getattr(self.bot, "proactive_scheduler", None)
        else:
            agent_manager = getattr(self.bot, "agent_manager", None)
            if agent_manager is not None:
                client = agent_manager.agents.get(persona)
                if client is not None:
                    scheduler = getattr(client, "proactive_scheduler", None)

        if scheduler is None:
            await interaction.followup.send(
                f"Could not find a proactive scheduler for `{persona or 'slashai'}`.",
                ephemeral=True,
            )
            return

        try:
            result = await scheduler.simulate_decision(channel)
        except Exception as e:
            logger.error(f"/proactive simulate failed: {e}", exc_info=True)
            await interaction.followup.send(
                f"Simulate raised: `{type(e).__name__}: {e}`",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Simulate — {scheduler.persona.display_name} in #{channel.name}",
            color=discord.Color.dark_orange(),
        )
        b = result["budget_remaining"]
        embed.add_field(
            name="Pre-filter",
            value=(
                f"allowed = `{result['prefilter_allowed']}`\n"
                f"reason = `{result['prefilter_reason']}`\n"
                f"budget remaining: react={b['reactions']}, "
                f"reply={b['replies']}, new_topic={b['new_topics']}"
            ),
            inline=False,
        )
        if not result["prefilter_allowed"]:
            embed.set_footer(
                text="Pre-filter blocked the tick; decider was not called. "
                     "Adjust persona allowlist / quiet hours / cooldowns to test the decider."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        d = result["decider"]
        ctx = result.get("context", {})
        embed.add_field(
            name="Would-be decision",
            value=(
                f"action = `{d['action']}`\n"
                f"target_message_id = `{d['target_message_id']}`\n"
                f"target_persona_id = `{d['target_persona_id']}`\n"
                f"emoji = {d['emoji'] or '`null`'}\n"
                f"confidence = `{d['confidence']:.2f}`\n"
                f"model = `{d['decider_model']}` "
                f"({d['input_tokens']}/{d['output_tokens']} tok)"
            ),
            inline=False,
        )
        reasoning = (d["reasoning"] or "(no reasoning)")[:1000]
        embed.add_field(name="Reasoning", value=f"_{reasoning}_", inline=False)

        ctx_lines = [
            f"recent messages: {ctx.get('recent_message_count', 0)}",
            f"human conversation active: {ctx.get('is_human_conversation_active')}",
        ]
        memories = ctx.get("relevant_memories") or []
        reflections = ctx.get("reflections_about_others") or []
        if memories:
            ctx_lines.append(f"memories surfaced: {len(memories)}")
        if reflections:
            ctx_lines.append(f"reflections surfaced: {len(reflections)}")
        if ctx.get("active_inter_agent_thread"):
            t = ctx["active_inter_agent_thread"]
            ctx_lines.append(
                f"in active thread with @{t.get('other_participant')} "
                f"(turn {t.get('turn_count')}/{t.get('max_turns')})"
            )
        embed.add_field(name="Context", value="\n".join(ctx_lines), inline=False)
        embed.set_footer(text="Dry-run — no Discord action was taken.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @proactive_group.command(name="sycophancy")
    @owner_only()
    @app_commands.describe(
        days="Lookback window in days (default 7, max 90)",
        view="What to show: per-persona summary or per-thread detail",
    )
    @app_commands.choices(view=[
        app_commands.Choice(name="per-persona", value="persona"),
        app_commands.Choice(name="per-thread", value="thread"),
    ])
    async def sycophancy(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 90] = 7,
        view: Optional[app_commands.Choice[str]] = None,
    ):
        """Heuristic sycophancy scan — agreement language in inter-agent thread replies.

        This is a tuning surface, not a model-grade detector. High agreement
        rates flag persona pairs whose threads read as mutual-validation loops.
        See `src/proactive/sycophancy.py` for the detection model.
        """
        await interaction.response.defer(ephemeral=True)

        view_value = view.value if view else "persona"

        if view_value == "persona":
            stats = await self.sycophancy.per_persona(days=days)
            if not stats:
                await interaction.followup.send(
                    f"No proactive replies inside inter-agent threads in the last {days} day(s). "
                    "Run `/proactive threads` to see if any threads have started.",
                    ephemeral=True,
                )
                return
            lines = [
                f"**{s.persona_id}** — replies={s.reply_count}, "
                f"agreement_hits={s.agreement_hits}, "
                f"rate={s.agreement_rate:.2f}, threads={s.threads_seen}"
                for s in stats
            ]
            embed = discord.Embed(
                title=f"Sycophancy (per-persona, last {days}d)",
                description="\n".join(lines),
                color=discord.Color.dark_red(),
            )
            embed.set_footer(text="rate = agreement_hits / reply_count. >0.5 is suggestive.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # per-thread view
        stats = await self.sycophancy.per_thread(days=days, limit=20)
        if not stats:
            await interaction.followup.send(
                f"No threads in the last {days} day(s).", ephemeral=True
            )
            return
        lines = []
        for t in stats:
            ratio = (
                t.agreement_hits / t.total_replies_in_thread
                if t.total_replies_in_thread else 0.0
            )
            lines.append(
                f"`#{t.thread_id:>4}` {t.participants} (init={t.initiator_persona_id}) "
                f"— turns={t.turn_count}, replies={t.total_replies_in_thread}, "
                f"hits={t.agreement_hits} (ratio={ratio:.2f}) "
                f"end={t.ended_reason or 'active'}"
            )
        body = "\n".join(lines)
        if len(body) > 3800:
            body = body[:3800] + "\n\n_(truncated)_"
        embed = discord.Embed(
            title=f"Sycophancy (per-thread, last {days}d)",
            description=body,
            color=discord.Color.dark_red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
