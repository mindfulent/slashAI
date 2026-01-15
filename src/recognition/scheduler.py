# slashAI - Recognition Scheduler
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Recognition Scheduler Module

Background task loop for processing build submissions from Core Curriculum.
Polls the Recognition API for pending submissions, analyzes them using Claude Vision,
and sends results back via webhook.
"""

import io
import logging
import os
from typing import TYPE_CHECKING, Optional

import discord
import httpx
from discord.ext import tasks

if TYPE_CHECKING:
    from discord_bot import DiscordBot

from .api import RecognitionAPIClient, Submission
from .analyzer import BuildAnalyzer, BuildAnalysis
from .feedback import generate_feedback

logger = logging.getLogger("slashAI.recognition.scheduler")

# Channel for public announcements (optional)
ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RECOGNITION_ANNOUNCEMENTS_CHANNEL")
if ANNOUNCEMENTS_CHANNEL_ID:
    logger.info(f"Announcements channel configured: {ANNOUNCEMENTS_CHANNEL_ID}")
else:
    logger.info("No announcements channel configured (RECOGNITION_ANNOUNCEMENTS_CHANNEL not set)")

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
            # Use minecraft_username if available, otherwise fall back to UUID prefix
            player_name = (
                (player_profile.minecraft_username if player_profile else None)
                or submission.player_uuid[:8]
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

        Posts a conversational message with all screenshots attached as files
        in a single post, like how a community member would share a build.

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

            # Build conversational message
            message_parts = []

            # Header with build name and builder
            message_parts.append(f"**{submission.build_name}** by {player_name} ✨")
            message_parts.append("")

            # Claude's overall impression (the heart of the message)
            message_parts.append(analysis.overall_impression)

            # Title earned (if any)
            if analysis.title_recommendation:
                title_display = self._get_title_display(analysis.title_recommendation)
                if title_display:
                    message_parts.append("")
                    message_parts.append(f"🏆 Earned **{title_display}**")

            # Location
            coords = submission.coordinates
            coord_str = f"{coords.get('x', '?')}, {coords.get('y', '?')}, {coords.get('z', '?')}"
            dimension = coords.get('dimension', 'Overworld')
            message_parts.append("")
            message_parts.append(f"📍 {coord_str} ({dimension})")

            message_content = "\n".join(message_parts)

            # Download all screenshots and attach as files
            files = []
            async with httpx.AsyncClient() as client:
                for i, url in enumerate(submission.screenshot_urls[:10]):  # Discord max 10 files
                    try:
                        response = await client.get(url)
                        if response.status_code == 200:
                            # Extract filename from URL or use index
                            filename = url.split("/")[-1] or f"screenshot_{i+1}.jpg"
                            file_data = io.BytesIO(response.content)
                            files.append(discord.File(file_data, filename=filename))
                    except Exception as e:
                        logger.warning(f"Failed to download screenshot {i+1}: {e}")

            # Send single message with all attachments
            if files:
                await channel.send(content=message_content, files=files)
            else:
                # Fallback: send message without images if downloads failed
                await channel.send(content=message_content)

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
