# slashAI - Recognition Scheduler
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Recognition Scheduler Module

Background task loop for processing build submissions from Core Curriculum.
Polls the Recognition API for pending submissions, analyzes them using Claude Vision,
and sends results back via webhook.
"""

import logging
import os
from typing import TYPE_CHECKING, Optional

import discord
from discord.ext import tasks

if TYPE_CHECKING:
    from discord_bot import DiscordBot

from .api import RecognitionAPIClient, Submission
from .analyzer import BuildAnalyzer, BuildAnalysis
from .feedback import generate_feedback

logger = logging.getLogger("slashAI.recognition.scheduler")

# Channel for public announcements (optional)
ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RECOGNITION_ANNOUNCEMENTS_CHANNEL")

# Polling interval in seconds
POLL_INTERVAL = int(os.getenv("RECOGNITION_POLL_INTERVAL", "60"))


class RecognitionScheduler:
    """
    Background scheduler for processing build submissions.

    Runs a loop every 60 seconds (configurable) to check for pending submissions,
    analyze them with Claude Vision, and send results back to the Recognition API.
    """

    def __init__(self, bot: "DiscordBot"):
        """
        Initialize the recognition scheduler.

        Args:
            bot: Discord bot instance
        """
        self.bot = bot
        self._started = False

        # Initialize API client
        self.api_client = RecognitionAPIClient()

        # Initialize analyzer (requires ANTHROPIC_API_KEY)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            self.analyzer = BuildAnalyzer(api_key)
        else:
            self.analyzer = None
            logger.warning("ANTHROPIC_API_KEY not set - recognition analysis disabled")

    def start(self) -> None:
        """Start the scheduler loop."""
        if not self._started and self.analyzer:
            self._process_submissions.start()
            self._started = True
            logger.info(f"Recognition scheduler started (interval: {POLL_INTERVAL}s)")

    def stop(self) -> None:
        """Stop the scheduler loop."""
        if self._started:
            self._process_submissions.cancel()
            self._started = False
            logger.info("Recognition scheduler stopped")

    async def close(self) -> None:
        """Clean up resources."""
        self.stop()
        await self.api_client.close()

    @tasks.loop(seconds=POLL_INTERVAL)
    async def _process_submissions(self) -> None:
        """Check for pending submissions and process them."""
        try:
            # Fetch pending submissions
            pending = await self.api_client.get_pending_submissions(limit=5)

            if pending:
                logger.info(f"Processing {len(pending)} pending submission(s)")

            for submission in pending:
                await self._process_single_submission(submission)

        except Exception as e:
            logger.error(f"Error in recognition scheduler loop: {e}", exc_info=True)

    @_process_submissions.before_loop
    async def _before_process(self) -> None:
        """Wait for the bot to be ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("Recognition scheduler ready, starting loop")

    async def _process_single_submission(self, submission: Submission) -> None:
        """
        Process a single submission.

        Args:
            submission: The submission to process
        """
        submission_id = submission.id
        logger.info(f"Processing submission {submission_id}: {submission.build_name}")

        try:
            # Get player profile for context
            player_profile = await self.api_client.get_player_profile(
                submission.player_uuid
            )

            # Analyze the build
            analysis = await self.analyzer.analyze(submission, player_profile)

            logger.info(
                f"Analysis complete for {submission_id}: "
                f"recognized={analysis.recognized}, confidence={analysis.confidence:.2f}"
            )

            # Generate feedback messages
            player_name = (
                player_profile.minecraft_username
                if player_profile
                else submission.player_uuid[:8]
            )
            feedback = generate_feedback(submission, analysis, player_name)

            # Send results back to API
            success = await self.api_client.submit_analysis_result(
                submission_id=submission_id,
                recognized=analysis.recognized,
                assessment=feedback.dm_content,
                title_recommendation=analysis.title_recommendation,
                confidence=analysis.confidence,
                share_publicly=analysis.recognized,  # Only share if recognized
            )

            if success:
                logger.info(f"Successfully submitted analysis for {submission_id}")

                # Announce if recognized and channel is configured
                if analysis.recognized and feedback.announcement_content:
                    await self._announce_recognition(submission, analysis, feedback, player_name)

                # DM the player
                await self._dm_player(submission, feedback)

            else:
                logger.error(f"Failed to submit analysis result for {submission_id}")

        except Exception as e:
            logger.error(
                f"Error processing submission {submission_id}: {e}", exc_info=True
            )

    async def _announce_recognition(
        self, submission: Submission, analysis: BuildAnalysis, feedback, player_name: str
    ) -> None:
        """
        Announce a recognized build in the announcements channel with screenshots.

        Args:
            submission: The submission
            analysis: The build analysis results
            feedback: Generated feedback with announcement content
            player_name: Player's Minecraft username
        """
        if not ANNOUNCEMENTS_CHANNEL_ID:
            logger.debug("No announcements channel configured, skipping announcement")
            return

        try:
            channel_id = int(ANNOUNCEMENTS_CHANNEL_ID)
            channel = self.bot.get_channel(channel_id)

            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)

            if not channel:
                logger.warning(f"Could not find announcements channel {channel_id}")
                return

            # Create embed with first screenshot as main image
            embed = discord.Embed(
                title=f"✨ {submission.build_name}",
                description=analysis.overall_impression,
                color=discord.Color.gold(),
            )
            embed.set_author(name=f"Build by {player_name}")

            # Add first screenshot as main embed image
            if submission.screenshot_urls:
                embed.set_image(url=submission.screenshot_urls[0])

            # Add strengths as a field
            if analysis.strengths:
                strengths_text = "\n".join(f"• {s}" for s in analysis.strengths[:3])
                embed.add_field(name="Highlights", value=strengths_text, inline=False)

            # Add title earned if any
            if analysis.title_recommendation:
                title_display = self._get_title_display(analysis.title_recommendation)
                if title_display:
                    embed.add_field(name="🏆 Title Earned", value=title_display, inline=True)

            # Add coordinates
            coords = submission.coordinates
            coord_str = f"{coords.get('x', '?')}, {coords.get('y', '?')}, {coords.get('z', '?')}"
            dimension = coords.get('dimension', 'Overworld')
            embed.set_footer(text=f"📍 {coord_str} ({dimension})")

            # Send embed
            await channel.send(embed=embed)

            # Send additional screenshots as follow-up if there are more than 1
            if len(submission.screenshot_urls) > 1:
                additional_urls = submission.screenshot_urls[1:]
                # Send as simple message with image URLs (Discord will embed them)
                for url in additional_urls:
                    additional_embed = discord.Embed(color=discord.Color.gold())
                    additional_embed.set_image(url=url)
                    await channel.send(embed=additional_embed)

            logger.info(f"Announced recognition for {submission.build_name} in #{channel.name}")

        except Exception as e:
            logger.warning(f"Failed to announce recognition: {e}", exc_info=True)

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

    async def _dm_player(self, submission: Submission, feedback) -> None:
        """
        Send feedback DM to the player.

        This requires mapping Minecraft UUID to Discord ID, which is done
        via the Recognition API's player profile.

        Args:
            submission: The submission
            feedback: Generated feedback with DM content
        """
        try:
            # Get player's Discord ID from profile
            player_profile = await self.api_client.get_player_profile(
                submission.player_uuid
            )

            if not player_profile:
                logger.debug(f"No profile for player {submission.player_uuid}, skipping DM")
                return

            # The profile should include discord_id if linked
            # For now, we'll skip this step - it requires the Recognition API
            # to store Discord ID linkage, which we can add later
            #
            # discord_id = getattr(player_profile, 'discord_id', None)
            # if discord_id:
            #     user = await self.bot.fetch_user(int(discord_id))
            #     await user.send(feedback.dm_content)

            logger.debug(
                f"DM delivery not implemented yet for {submission.player_uuid}"
            )

        except Exception as e:
            logger.warning(f"Failed to DM player: {e}")
