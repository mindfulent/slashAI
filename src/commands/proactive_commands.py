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
    async def reflect(self, interaction: discord.Interaction, persona: str):
        """Force-trigger reflection job (lands in v0.14.4)."""
        await interaction.response.send_message(
            "`/proactive reflect` lands in v0.14.4 with the reflection feature.",
            ephemeral=True,
        )

    @proactive_group.command(name="simulate")
    @owner_only()
    async def simulate(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        """Run the decider against current state without acting (lands in v0.14.5)."""
        await interaction.response.send_message(
            "`/proactive simulate` lands in v0.14.5 (decider dry-run).",
            ephemeral=True,
        )
