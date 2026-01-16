# slashAI - Recognition Approval UI
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Discord UI components for build recognition approval flow.

When a build is recognized, the player receives a DM with the assessment
and buttons to approve or decline sharing to the public channel.
"""

import logging
from typing import TYPE_CHECKING, Optional, Callable, Awaitable

import discord

if TYPE_CHECKING:
    pass

logger = logging.getLogger("slashAI.recognition.approval")


class ApprovalView(discord.ui.View):
    """
    Discord UI View for build recognition approval.

    Contains "Share" and "Keep Private" buttons. When clicked,
    the appropriate callback is invoked with the submission data.
    """

    def __init__(
        self,
        submission_id: str,
        on_approve: Callable[[str, discord.Interaction], Awaitable[None]],
        on_decline: Callable[[str, discord.Interaction], Awaitable[None]],
        timeout: float = 86400.0,  # 24 hours
    ):
        """
        Initialize the approval view.

        Args:
            submission_id: The submission being approved
            on_approve: Async callback when user clicks Share
            on_decline: Async callback when user clicks Keep Private
            timeout: How long buttons remain active (default 24h)
        """
        super().__init__(timeout=timeout)
        self.submission_id = submission_id
        self._on_approve = on_approve
        self._on_decline = on_decline

    @discord.ui.button(
        label="Share to Server",
        style=discord.ButtonStyle.success,
        emoji="✨",
        custom_id="recognition_approve",
    )
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Handle approval - share to public channel."""
        await interaction.response.defer()

        # Disable buttons after click
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        try:
            await self._on_approve(self.submission_id, interaction)
        except Exception as e:
            logger.error(f"Error in approval callback: {e}", exc_info=True)
            await interaction.followup.send(
                "Something went wrong while sharing. Please try again later.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Keep Private",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        custom_id="recognition_decline",
    )
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Handle decline - don't share publicly."""
        await interaction.response.defer()

        # Disable buttons after click
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        try:
            await self._on_decline(self.submission_id, interaction)
        except Exception as e:
            logger.error(f"Error in decline callback: {e}", exc_info=True)

    async def on_timeout(self):
        """Called when the view times out (24h default)."""
        logger.debug(f"Approval view timed out for submission {self.submission_id}")


def format_dm_message(
    build_name: str,
    player_name: str,
    assessment: str,
    recognized: bool,
    title_earned: Optional[str] = None,
    coordinates: Optional[dict] = None,
) -> str:
    """
    Format the DM message content for the approval flow.

    Args:
        build_name: Name of the build
        player_name: Player's Minecraft username
        assessment: slashAI's assessment text
        recognized: Whether the build was recognized
        title_earned: Optional title earned
        coordinates: Build coordinates dict

    Returns:
        Formatted message string
    """
    lines = []

    if recognized:
        lines.append(f"## ✨ Your build has been recognized!")
        lines.append("")
        lines.append(f"**{build_name}**")
        lines.append("")
        lines.append(assessment)

        if title_earned:
            lines.append("")
            lines.append(f"🏆 You earned the title: **{title_earned}**")

        if coordinates:
            x = coordinates.get('x', 0)
            y = coordinates.get('y', 64)
            z = coordinates.get('z', 0)
            dimension = coordinates.get('dimension', 'Overworld')

            # Map dimension to BlueMap world name
            world_map = {
                'Overworld': 'world',
                'minecraft:overworld': 'world',
                'The Nether': 'world_nether',
                'minecraft:the_nether': 'world_nether',
                'The End': 'world_the_end',
                'minecraft:the_end': 'world_the_end',
            }
            bluemap_world = world_map.get(dimension, 'world')
            bluemap_url = f"http://66.59.211.148:8100/#{bluemap_world}:{x}:{y}:{z}:100:0:0.5:0:0:perspective"

            coord_str = f"{x}, {y}, {z}"
            lines.append("")
            lines.append(f"📍 [{coord_str}]({bluemap_url}) ({dimension})")

        lines.append("")
        lines.append("Would you like to share this to **#server-showcase**?")
    else:
        lines.append(f"## Thanks for sharing your build!")
        lines.append("")
        lines.append(f"**{build_name}**")
        lines.append("")
        lines.append(assessment)
        lines.append("")
        lines.append("*Keep building! Every project is a step in your creative journey.*")

    return "\n".join(lines)


class AdminNominationReviewView(discord.ui.View):
    """
    Discord UI View for admin review of flagged nominations.

    Contains "Approve" and "Reject" buttons for the admin to decide
    on flagged nominations that require manual review.
    """

    def __init__(
        self,
        nomination_id: str,
        on_approve: Callable[[str, str, discord.Interaction], Awaitable[None]],
        on_reject: Callable[[str, str, discord.Interaction], Awaitable[None]],
        timeout: float = 86400.0,  # 24 hours
    ):
        """
        Initialize the admin review view.

        Args:
            nomination_id: The nomination being reviewed
            on_approve: Async callback(nomination_id, reason, interaction) when admin approves
            on_reject: Async callback(nomination_id, reason, interaction) when admin rejects
            timeout: How long buttons remain active (default 24h)
        """
        super().__init__(timeout=timeout)
        self.nomination_id = nomination_id
        self._on_approve = on_approve
        self._on_reject = on_reject

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="nomination_admin_approve",
    )
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Handle approval - approve the flagged nomination."""
        await interaction.response.defer()

        # Disable buttons after click
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        try:
            await self._on_approve(
                self.nomination_id,
                f"Manually approved by {interaction.user.display_name}",
                interaction,
            )
        except Exception as e:
            logger.error(f"Error in admin approval callback: {e}", exc_info=True)
            await interaction.followup.send(
                "Something went wrong while approving. Please try again later.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="nomination_admin_reject",
    )
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Handle rejection - reject the flagged nomination."""
        await interaction.response.defer()

        # Disable buttons after click
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        try:
            await self._on_reject(
                self.nomination_id,
                f"Manually rejected by {interaction.user.display_name}",
                interaction,
            )
        except Exception as e:
            logger.error(f"Error in admin rejection callback: {e}", exc_info=True)

    async def on_timeout(self):
        """Called when the view times out (24h default)."""
        logger.debug(f"Admin review view timed out for nomination {self.nomination_id}")


def format_admin_review_message(
    nomination_category: str,
    nominator_name: str,
    nominee_name: str,
    reason: str,
    slashai_notes: str,
    confidence: float,
) -> str:
    """
    Format the DM message for admin review of a flagged nomination.

    Args:
        nomination_category: Category of the nomination (mentor, collaborator, etc.)
        nominator_name: Name of the person who submitted the nomination
        nominee_name: Name of the person being nominated
        reason: The nomination reason text
        slashai_notes: slashAI's review notes explaining why it was flagged
        confidence: slashAI's confidence score

    Returns:
        Formatted message string
    """
    category_display = {
        "mentor": "🎓 Mentor",
        "collaborator": "🤝 Collaborator",
        "community_builder": "🏠 Community Builder",
        "innovator": "💡 Innovator",
    }.get(nomination_category, nomination_category.title())

    lines = [
        "## 🚩 Nomination Flagged for Review",
        "",
        f"**Category:** {category_display}",
        f"**Nominator:** {nominator_name}",
        f"**Nominee:** {nominee_name}",
        "",
        "**Nomination Reason:**",
        f"> {reason}",
        "",
        "**slashAI Assessment:**",
        f"> {slashai_notes}",
        f"> *Confidence: {confidence:.0%}*",
        "",
        "Please review and decide whether to approve or reject this nomination.",
    ]

    return "\n".join(lines)
