# slashAI - Recognition Scheduler
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Recognition Scheduler Module

Background task loop for processing build submissions from Core Curriculum.
Polls the Recognition API for pending submissions, analyzes them using Claude Vision,
and sends results back via webhook.

Includes DM approval flow - players receive a DM asking if they want to share
their recognized build publicly before it's posted to #server-showcase.
"""

import io
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import discord
import httpx
from discord.ext import tasks

if TYPE_CHECKING:
    from discord_bot import DiscordBot

from .api import RecognitionAPIClient, Submission, PlayerProfile, Nomination
from .analyzer import BuildAnalyzer, BuildAnalysis
from .approval import ApprovalView, format_dm_message, AdminNominationReviewView, format_admin_review_message
from .feedback import generate_feedback, FeedbackMessage
from .nominations import NominationReviewer, NominationReview

logger = logging.getLogger("slashAI.recognition.scheduler")

# Channel for public announcements (optional)
ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RECOGNITION_ANNOUNCEMENTS_CHANNEL")
if ANNOUNCEMENTS_CHANNEL_ID:
    logger.info(f"Announcements channel configured: {ANNOUNCEMENTS_CHANNEL_ID}")
else:
    logger.info("No announcements channel configured (RECOGNITION_ANNOUNCEMENTS_CHANNEL not set)")

# Channel for nomination announcements
NOMINATIONS_CHANNEL_ID = os.getenv("NOMINATIONS_CHANNEL_ID", "1461411967901372487")
logger.info(f"Nominations channel configured: {NOMINATIONS_CHANNEL_ID}")

# Admin user ID for flagged nomination review
OWNER_ID = os.getenv("OWNER_ID")
if OWNER_ID:
    logger.info(f"Owner ID configured for admin review: {OWNER_ID}")
else:
    logger.warning("OWNER_ID not set - flagged nominations won't be sent for admin review")

# Polling interval in seconds
POLL_INTERVAL = int(os.getenv("RECOGNITION_POLL_INTERVAL", "60"))


@dataclass
class PendingApproval:
    """Data stored for a submission pending user approval."""

    submission: Submission
    analysis: BuildAnalysis
    feedback: FeedbackMessage
    player_name: str
    player_profile: Optional[PlayerProfile]


class RecognitionScheduler:
    """
    Background scheduler for processing build submissions.

    Runs a loop every 60 seconds (configurable) to check for pending submissions,
    analyze them with Claude Vision, and send results back to the Recognition API.
    Also processes ended events for teaching/attendance credits.
    """

    def __init__(self, bot: "DiscordBot"):
        """
        Initialize the recognition scheduler.

        Args:
            bot: Discord bot instance
        """
        self.bot = bot
        self._started = False
        self._loop_count = 0  # Track iterations for event processing

        # Initialize API client
        self.api_client = RecognitionAPIClient()

        # Initialize analyzer (requires ANTHROPIC_API_KEY)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            self.analyzer = BuildAnalyzer(api_key)
            self.nomination_reviewer = NominationReviewer(api_key)
        else:
            self.analyzer = None
            self.nomination_reviewer = None
            logger.warning("ANTHROPIC_API_KEY not set - recognition analysis disabled")

        # Storage for submissions pending user approval
        # Maps submission_id -> PendingApproval data
        self._pending_approvals: dict[str, PendingApproval] = {}

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
        """Check for pending submissions, nominations, and events to process."""
        try:
            self._loop_count += 1

            # Fetch and process pending submissions
            pending = await self.api_client.get_pending_submissions(limit=5)

            if pending:
                logger.info(f"Processing {len(pending)} pending submission(s)")

            for submission in pending:
                await self._process_single_submission(submission)

            # Fetch and process pending nominations
            if self.nomination_reviewer:
                pending_nominations = await self.api_client.get_pending_nominations(limit=5)

                if pending_nominations:
                    logger.info(f"Processing {len(pending_nominations)} pending nomination(s)")

                for nomination in pending_nominations:
                    await self._process_single_nomination(nomination)

            # Process ended events every 5th iteration (~5 minutes)
            if self._loop_count % 5 == 0:
                await self._process_ended_events()

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

        Flow:
        1. Analyze with Claude Vision
        2. Send results to Recognition API
        3. If recognized AND player has Discord linked:
           - DM player with assessment and approval buttons
           - Wait for approval before announcing
        4. If recognized but no Discord:
           - Announce directly to channel
        5. If not recognized:
           - Just send DM feedback (no public announcement)

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

            # Defensive: handle None confidence in case of parsing issues
            confidence_str = f"{analysis.confidence:.2f}" if analysis.confidence is not None else "N/A"
            logger.info(
                f"Analysis complete for {submission_id}: "
                f"recognized={analysis.recognized}, confidence={confidence_str}"
            )

            # Generate feedback messages
            # Use minecraft_username if available, otherwise fall back to UUID prefix
            player_name = (
                (player_profile.minecraft_username if player_profile else None)
                or submission.player_uuid[:8]
            )
            feedback = generate_feedback(submission, analysis, player_name)

            # Send results back to API (don't share publicly yet - wait for approval)
            success = await self.api_client.submit_analysis_result(
                submission_id=submission_id,
                recognized=analysis.recognized,
                assessment=feedback.dm_content,
                title_recommendation=analysis.title_recommendation,
                confidence=analysis.confidence,
                share_publicly=False,  # Don't auto-share, wait for approval
                announcement_text=analysis.overall_impression,  # Clean text for feed/Discord
                screenshot_urls=submission.screenshot_urls,  # All screenshots
            )

            if not success:
                logger.error(f"Failed to submit analysis result for {submission_id}")
                return

            logger.info(f"Successfully submitted analysis for {submission_id}")

            # Handle announcement based on recognition and Discord linkage
            if analysis.recognized:
                discord_id = player_profile.discord_id if player_profile else None

                if discord_id:
                    # Player has Discord linked - send DM with approval buttons
                    await self._send_approval_dm(
                        submission, analysis, feedback, player_name, player_profile
                    )
                else:
                    # No Discord linked - announce directly
                    logger.info(
                        f"No Discord ID for {player_name}, announcing directly"
                    )
                    await self._announce_recognition(
                        submission, analysis, feedback, player_name
                    )
            else:
                # Not recognized - just log, no announcement needed
                # Could send feedback DM here if Discord is linked
                discord_id = player_profile.discord_id if player_profile else None
                if discord_id:
                    await self._send_feedback_dm(
                        submission, analysis, feedback, player_name, player_profile
                    )

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

            # Location with BlueMap link
            coords = submission.coordinates
            x = coords.get('x', 0)
            y = coords.get('y', 64)
            z = coords.get('z', 0)
            dimension = coords.get('dimension', 'Overworld')

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

            # BlueMap URL format: #world:x:y:z:zoom:rotation:tilt:0:0:perspective
            bluemap_url = f"http://66.59.211.148:8100/#{bluemap_world}:{x}:{y}:{z}:100:0:0.5:0:0:perspective"
            coord_str = f"{x}, {y}, {z}"

            message_parts.append("")
            message_parts.append(f"📍 [{coord_str}]({bluemap_url}) ({dimension})")

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

    async def _send_approval_dm(
        self,
        submission: Submission,
        analysis: BuildAnalysis,
        feedback: FeedbackMessage,
        player_name: str,
        player_profile: PlayerProfile,
    ) -> None:
        """
        Send DM to player with approval buttons for sharing.

        Args:
            submission: The submission
            analysis: The build analysis
            feedback: Generated feedback messages
            player_name: Player's Minecraft username
            player_profile: Player's profile with discord_id
        """
        if not player_profile.discord_id:
            logger.debug(f"No Discord ID for {player_name}, cannot send approval DM")
            return

        try:
            user = await self.bot.fetch_user(int(player_profile.discord_id))

            # Store pending approval data
            self._pending_approvals[submission.id] = PendingApproval(
                submission=submission,
                analysis=analysis,
                feedback=feedback,
                player_name=player_name,
                player_profile=player_profile,
            )

            # Format the DM message
            title_display = None
            if analysis.title_recommendation:
                title_display = self._get_title_display(analysis.title_recommendation)

            dm_content = format_dm_message(
                build_name=submission.build_name,
                player_name=player_name,
                assessment=analysis.overall_impression,
                recognized=True,
                title_earned=title_display,
                coordinates=submission.coordinates,
            )

            # Create approval view with buttons
            view = ApprovalView(
                submission_id=submission.id,
                on_approve=self._handle_approval,
                on_decline=self._handle_decline,
            )

            await user.send(content=dm_content, view=view)
            logger.info(f"Sent approval DM to {player_name} for {submission.build_name}")

        except discord.NotFound:
            logger.warning(
                f"Discord user {player_profile.discord_id} not found, announcing directly"
            )
            await self._announce_recognition(submission, analysis, feedback, player_name)
        except discord.Forbidden:
            logger.warning(
                f"Cannot DM user {player_profile.discord_id} (DMs disabled), announcing directly"
            )
            await self._announce_recognition(submission, analysis, feedback, player_name)
        except Exception as e:
            logger.error(f"Failed to send approval DM: {e}", exc_info=True)

    async def _send_feedback_dm(
        self,
        submission: Submission,
        analysis: BuildAnalysis,
        feedback: FeedbackMessage,
        player_name: str,
        player_profile: PlayerProfile,
    ) -> None:
        """
        Send feedback DM for non-recognized builds (no approval needed).

        Args:
            submission: The submission
            analysis: The build analysis
            feedback: Generated feedback messages
            player_name: Player's Minecraft username
            player_profile: Player's profile with discord_id
        """
        if not player_profile.discord_id:
            return

        try:
            user = await self.bot.fetch_user(int(player_profile.discord_id))

            # Format simple feedback message (no buttons needed)
            dm_content = format_dm_message(
                build_name=submission.build_name,
                player_name=player_name,
                assessment=analysis.overall_impression,
                recognized=False,
                coordinates=submission.coordinates,
            )

            await user.send(content=dm_content)
            logger.info(f"Sent feedback DM to {player_name} for {submission.build_name}")

        except discord.NotFound:
            logger.debug(f"Discord user {player_profile.discord_id} not found")
        except discord.Forbidden:
            logger.debug(f"Cannot DM user {player_profile.discord_id} (DMs disabled)")
        except Exception as e:
            logger.warning(f"Failed to send feedback DM: {e}")

    async def _handle_approval(
        self, submission_id: str, interaction: discord.Interaction
    ) -> None:
        """
        Handle user clicking "Share to Server" button.

        Args:
            submission_id: The submission being approved
            interaction: The Discord interaction
        """
        pending = self._pending_approvals.pop(submission_id, None)
        if not pending:
            logger.warning(f"No pending approval found for {submission_id}")
            await interaction.followup.send(
                "This submission has already been processed or expired.",
                ephemeral=True,
            )
            return

        logger.info(
            f"Approval received for {pending.submission.build_name} from {pending.player_name}"
        )

        # Announce to public channel
        await self._announce_recognition(
            pending.submission,
            pending.analysis,
            pending.feedback,
            pending.player_name,
        )

        # Confirm to user
        await interaction.followup.send(
            f"Your build **{pending.submission.build_name}** has been shared to #server-showcase!",
            ephemeral=True,
        )

    async def _handle_decline(
        self, submission_id: str, interaction: discord.Interaction
    ) -> None:
        """
        Handle user clicking "Keep Private" button.

        Args:
            submission_id: The submission being declined
            interaction: The Discord interaction
        """
        pending = self._pending_approvals.pop(submission_id, None)
        if not pending:
            logger.warning(f"No pending approval found for {submission_id}")
            await interaction.followup.send(
                "This submission has already been processed or expired.",
                ephemeral=True,
            )
            return

        logger.info(
            f"Sharing declined for {pending.submission.build_name} by {pending.player_name}"
        )

        # Confirm to user
        await interaction.followup.send(
            f"No problem! Your build **{pending.submission.build_name}** will remain private. "
            "Your recognition still counts toward title progression.",
            ephemeral=True,
        )

    # =========================================================================
    # NOMINATION PROCESSING
    # =========================================================================

    async def _process_single_nomination(self, nomination: Nomination) -> None:
        """
        Process a single peer nomination.

        Flow:
        1. Review with Claude for anti-gaming patterns
        2. Send decision back to Recognition API
        3. If approved, announce to #nominations channel

        Args:
            nomination: The nomination to process
        """
        nomination_id = nomination.id
        logger.info(
            f"Processing nomination {nomination_id}: "
            f"{nomination.category} nomination"
        )

        try:
            # Get context for anti-gaming checks
            # TODO: Add actual counts from API
            nominator_recent_count = 0
            nominee_total_count = 0

            # Check for reciprocal nomination pattern
            is_reciprocal = await self._check_reciprocal(
                nomination.nominator_uuid, nomination.nominee_uuid
            )

            # Review the nomination
            review = await self.nomination_reviewer.review(
                nomination,
                nominator_recent_count=nominator_recent_count,
                nominee_total_count=nominee_total_count,
                is_reciprocal=is_reciprocal,
            )

            logger.info(
                f"Nomination review complete for {nomination_id}: "
                f"decision={review.decision}, confidence={review.confidence:.2f}"
            )

            # Send results back to API
            success = await self.api_client.submit_nomination_review(
                nomination_id=nomination_id,
                decision=review.decision,
                notes=review.notes,
                confidence=review.confidence,
            )

            if not success:
                logger.error(f"Failed to submit nomination review for {nomination_id}")
                return

            logger.info(f"Successfully submitted nomination review for {nomination_id}")

            # If approved, announce to #nominations channel
            if review.decision == "approved":
                await self._announce_nomination(nomination)
            # If flagged, prompt admin for review
            elif review.decision == "flagged":
                await self._prompt_admin_review(nomination, review)

        except Exception as e:
            logger.error(
                f"Error processing nomination {nomination_id}: {e}", exc_info=True
            )

    async def _check_reciprocal(
        self, nominator_uuid: str, nominee_uuid: str
    ) -> bool:
        """
        Check if nominee has recently nominated the nominator (reciprocal pattern).

        This is a simple anti-gaming check. Returns True if suspicious.
        """
        # TODO: Implement actual reciprocal check via API
        # For now, return False to avoid false positives
        return False

    async def _prompt_admin_review(
        self, nomination: Nomination, review: NominationReview
    ) -> None:
        """
        Send a DM to the admin (OWNER_ID) with approve/reject buttons for a flagged nomination.

        Args:
            nomination: The flagged nomination
            review: The review result from slashAI
        """
        if not OWNER_ID:
            logger.warning(
                f"Nomination {nomination.id} flagged but OWNER_ID not set - cannot prompt admin"
            )
            return

        try:
            owner_id = int(OWNER_ID)
            owner = self.bot.get_user(owner_id)
            if owner is None:
                owner = await self.bot.fetch_user(owner_id)

            if not owner:
                logger.error(f"Could not find owner user {owner_id}")
                return

            # Get player names
            nominator_profile = await self.api_client.get_player_profile(
                nomination.nominator_uuid
            )
            nominee_profile = await self.api_client.get_player_profile(
                nomination.nominee_uuid
            )

            nominator_name = (
                nominator_profile.minecraft_username if nominator_profile else None
            ) or nomination.nominator_uuid[:8]
            nominee_name = (
                nominee_profile.minecraft_username if nominee_profile else None
            ) or nomination.nominee_uuid[:8]

            # Format the review message
            message = format_admin_review_message(
                nomination_category=nomination.category,
                nominator_name=nominator_name,
                nominee_name=nominee_name,
                reason=nomination.reason,
                slashai_notes=review.notes,
                confidence=review.confidence,
            )

            # Create the view with callbacks
            view = AdminNominationReviewView(
                nomination_id=nomination.id,
                on_approve=self._handle_admin_approve,
                on_reject=self._handle_admin_reject,
            )

            # Send DM to owner
            await owner.send(message, view=view)
            logger.info(f"Sent admin review prompt for nomination {nomination.id} to owner")

        except discord.Forbidden:
            logger.error(f"Cannot DM owner {OWNER_ID} - DMs may be disabled")
        except Exception as e:
            logger.error(f"Error sending admin review prompt: {e}", exc_info=True)

    async def _handle_admin_approve(
        self, nomination_id: str, reason: str, interaction: discord.Interaction
    ) -> None:
        """
        Handle admin approval of a flagged nomination.

        Args:
            nomination_id: The nomination ID
            reason: Reason for approval
            interaction: The Discord interaction
        """
        admin_id = str(interaction.user.id)

        success = await self.api_client.apply_admin_nomination_action(
            nomination_id=nomination_id,
            action="approve",
            reason=reason,
            admin_id=admin_id,
        )

        if success:
            await interaction.followup.send(
                "✅ Nomination approved and added to the Honor Roll feed!",
                ephemeral=True,
            )
            logger.info(f"Admin approved nomination {nomination_id}")

            # Fetch the nomination details and announce it
            # Note: We don't have the full nomination object here, so we'll just log success
            # The announcement will be handled by the API which adds to the feed
        else:
            await interaction.followup.send(
                "❌ Failed to approve nomination. Please try again or check logs.",
                ephemeral=True,
            )
            logger.error(f"Failed to approve nomination {nomination_id} via admin action")

    async def _handle_admin_reject(
        self, nomination_id: str, reason: str, interaction: discord.Interaction
    ) -> None:
        """
        Handle admin rejection of a flagged nomination.

        Args:
            nomination_id: The nomination ID
            reason: Reason for rejection
            interaction: The Discord interaction
        """
        admin_id = str(interaction.user.id)

        success = await self.api_client.apply_admin_nomination_action(
            nomination_id=nomination_id,
            action="reject",
            reason=reason,
            admin_id=admin_id,
        )

        if success:
            await interaction.followup.send(
                "❌ Nomination rejected.",
                ephemeral=True,
            )
            logger.info(f"Admin rejected nomination {nomination_id}")
        else:
            await interaction.followup.send(
                "❌ Failed to reject nomination. Please try again or check logs.",
                ephemeral=True,
            )
            logger.error(f"Failed to reject nomination {nomination_id} via admin action")

    async def _announce_nomination(self, nomination: Nomination) -> None:
        """
        Announce an approved nomination to the #nominations channel.

        Args:
            nomination: The approved nomination
        """
        if not NOMINATIONS_CHANNEL_ID:
            logger.debug("No nominations channel configured, skipping announcement")
            return

        try:
            channel_id = int(NOMINATIONS_CHANNEL_ID)
            channel = self.bot.get_channel(channel_id)

            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)

            if not channel:
                logger.warning(f"Could not find nominations channel {channel_id}")
                return

            # Get player names from API
            nominator_profile = await self.api_client.get_player_profile(
                nomination.nominator_uuid
            )
            nominee_profile = await self.api_client.get_player_profile(
                nomination.nominee_uuid
            )

            nominator_name = (
                nominator_profile.minecraft_username if nominator_profile else None
            ) or nomination.nominator_uuid[:8]
            nominee_name = (
                nominee_profile.minecraft_username if nominee_profile else None
            ) or nomination.nominee_uuid[:8]

            # Build announcement message
            category_display = self._get_category_display(nomination.category)
            category_emoji = self._get_category_emoji(nomination.category)

            message_parts = []

            # Header
            message_parts.append(f"{category_emoji} **{category_display} Recognition**")
            message_parts.append("")

            # Content
            if nomination.anonymous:
                message_parts.append(
                    f"**{nominee_name}** was nominated by a fellow community member:"
                )
            else:
                message_parts.append(
                    f"**{nominee_name}** was nominated by **{nominator_name}**:"
                )

            message_parts.append("")
            message_parts.append(f"> {nomination.reason}")

            message_content = "\n".join(message_parts)

            await channel.send(content=message_content)
            logger.info(
                f"Announced {nomination.category} nomination for {nominee_name} "
                f"in #{channel.name}"
            )

        except Exception as e:
            logger.warning(f"Failed to announce nomination: {e}", exc_info=True)

    def _get_category_display(self, category: str) -> str:
        """Convert category slug to display name."""
        categories = {
            "mentor": "Mentor",
            "collaborator": "Collaborator",
            "helper": "Helper",
            "spirit": "Community Spirit",
        }
        return categories.get(category, category.title())

    def _get_category_emoji(self, category: str) -> str:
        """Get emoji for nomination category."""
        emojis = {
            "mentor": "\U0001F393",  # Graduation cap
            "collaborator": "\U0001F91D",  # Handshake
            "helper": "\U0001F4AC",  # Speech bubble
            "spirit": "\U00002728",  # Sparkles
        }
        return emojis.get(category, "\U0001F3C6")  # Trophy default

    # =========================================================================
    # EVENT PROCESSING
    # =========================================================================

    async def _process_ended_events(self) -> None:
        """
        Trigger processing of ended events for teaching/attendance credits.
        Called every 5th scheduler iteration (~5 minutes).
        """
        try:
            events_processed = await self.api_client.trigger_event_processing()

            if events_processed > 0:
                logger.info(f"Processed {events_processed} ended event(s)")

        except Exception as e:
            logger.error(f"Error processing ended events: {e}", exc_info=True)
