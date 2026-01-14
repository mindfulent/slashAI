# slashAI - Feedback Generator
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Feedback Generator for Build Analysis

Transforms structured analysis into player-facing messages:
- DM delivery format
- Discord announcement format
- Build details embed format
"""

from dataclasses import dataclass
from typing import Optional

from .analyzer import BuildAnalysis
from .api import Submission


@dataclass
class FeedbackMessage:
    """Formatted feedback message"""

    dm_content: str  # Full feedback for DM to player
    announcement_content: Optional[str]  # Short version for #server-releases
    embed_title: Optional[str]
    embed_description: Optional[str]


class FeedbackGenerator:
    """Generates player-facing feedback from analysis"""

    def generate(
        self,
        submission: Submission,
        analysis: BuildAnalysis,
        player_name: str,
    ) -> FeedbackMessage:
        """
        Generate feedback messages from analysis results.

        Args:
            submission: The original submission
            analysis: The completed analysis
            player_name: Player's Minecraft username

        Returns:
            FeedbackMessage with DM and announcement content
        """
        # Build DM content
        dm_content = self._format_dm(submission, analysis, player_name)

        # Build announcement (only if recognized)
        announcement = None
        embed_title = None
        embed_description = None

        if analysis.recognized:
            announcement = self._format_announcement(submission, analysis, player_name)
            embed_title = f"{player_name}'s Build Recognized"
            embed_description = analysis.overall_impression

        return FeedbackMessage(
            dm_content=dm_content,
            announcement_content=announcement,
            embed_title=embed_title,
            embed_description=embed_description,
        )

    def _format_dm(
        self,
        submission: Submission,
        analysis: BuildAnalysis,
        player_name: str,
    ) -> str:
        """Format the full feedback for DM delivery"""
        # Header
        if analysis.recognized:
            header = f"## Your build has been recognized!\n\n"
            status_emoji = ":star2:"
        else:
            header = f"## Thanks for sharing your build!\n\n"
            status_emoji = ":hammer_pick:"

        lines = [header]

        # Build info
        lines.append(f"**{submission.build_name}**\n")

        # Overall impression
        lines.append(f"{status_emoji} {analysis.overall_impression}\n")

        # Strengths
        if analysis.strengths:
            lines.append("\n**What's working well:**")
            for strength in analysis.strengths:
                lines.append(f"- {strength}")

        # Areas for growth
        if analysis.areas_for_growth:
            lines.append("\n**Ideas to explore:**")
            for area in analysis.areas_for_growth:
                lines.append(f"- {area}")

        # Style notes
        if analysis.style_notes:
            lines.append(f"\n**Style notes:** {analysis.style_notes}")

        # Technical summary (brief)
        tech_summary = self._format_technical_summary(analysis)
        if tech_summary:
            lines.append(f"\n{tech_summary}")

        # Title progression
        if analysis.title_recommendation:
            title_msg = self._format_title_message(analysis.title_recommendation)
            lines.append(f"\n{title_msg}")

        # Footer
        lines.append(
            "\n---\n*Keep building! Every project is a step in your creative journey.*"
        )

        return "\n".join(lines)

    def _format_announcement(
        self,
        submission: Submission,
        analysis: BuildAnalysis,
        player_name: str,
    ) -> str:
        """Format short announcement for public channel"""
        # Title earned?
        title_part = ""
        if analysis.title_recommendation:
            title_display = self._get_title_display(analysis.title_recommendation)
            if title_display:
                title_part = f" and earned **{title_display}**"

        # Main announcement
        announcement = (
            f":star2: **{player_name}**'s build **{submission.build_name}** "
            f"has been recognized{title_part}!\n\n"
            f"> {analysis.overall_impression}"
        )

        return announcement

    def _format_technical_summary(self, analysis: BuildAnalysis) -> str:
        """Format brief technical summary"""
        if analysis.technical_score < 0.3:
            return ""

        parts = []
        if analysis.palette_quality:
            parts.append(f"Palette: {analysis.palette_quality}")
        if analysis.depth_usage:
            parts.append(f"Depth: {analysis.depth_usage}")

        if parts:
            return "**Technical notes:** " + " | ".join(parts)
        return ""

    def _format_title_message(self, title_slug: str) -> str:
        """Format title progression message"""
        title_display = self._get_title_display(title_slug)
        if not title_display:
            return ""

        messages = {
            "first-build": (
                f":tada: **Congratulations!** You've earned your first title: "
                f"**{title_display}**"
            ),
            "apprentice-builder": (
                f":hammer: **Level up!** Your consistent work has earned you "
                f"**{title_display}**"
            ),
            "journeyman-builder": (
                f":star: **Your style is shining through!** You've earned "
                f"**{title_display}**"
            ),
            "featured-artist": (
                f":sparkles: **This build is exceptional!** It has been selected "
                f"for the community showcase. You've earned **{title_display}**"
            ),
        }

        return messages.get(
            title_slug, f":trophy: You've earned the title **{title_display}**!"
        )

    def _get_title_display(self, title_slug: str) -> Optional[str]:
        """Convert title slug to display name"""
        titles = {
            "first-build": "First Build",
            "apprentice-builder": "Apprentice Builder",
            "journeyman-builder": "Journeyman Builder",
            "master-builder": "Master Builder",
            "featured-artist": "Featured Artist",
            "campus-builder": "Campus Builder",
        }
        return titles.get(title_slug)


# Convenience function
def generate_feedback(
    submission: Submission,
    analysis: BuildAnalysis,
    player_name: str,
) -> FeedbackMessage:
    """Generate feedback messages from analysis"""
    generator = FeedbackGenerator()
    return generator.generate(submission, analysis, player_name)
