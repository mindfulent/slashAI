# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Reaction aggregation for memory enhancement.

Background job that aggregates reactions on messages linked to memories,
updating memory metadata with reaction summaries and confidence boosts.

Part of v0.12.0 - Reaction-Based Memory Signals.
"""

import json
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from discord.ext import tasks

logger = logging.getLogger(__name__)


class ReactionAggregator:
    """Background job to aggregate reactions into memory metadata."""

    def __init__(self, bot, db_pool: asyncpg.Pool):
        """
        Initialize the reaction aggregator.

        Args:
            bot: Discord bot instance (for wait_until_ready)
            db_pool: AsyncPG connection pool
        """
        self.bot = bot
        self.db = db_pool
        self._started = False

    def start(self):
        """Start the background aggregation loop."""
        if not self._started:
            self._aggregate_loop.start()
            self._started = True
            logger.info("Reaction aggregation job started")

    def stop(self):
        """Stop the background aggregation loop."""
        if self._started:
            self._aggregate_loop.cancel()
            self._started = False
            logger.info("Reaction aggregation job stopped")

    @tasks.loop(minutes=15)
    async def _aggregate_loop(self):
        """Aggregate reactions for memories with new reactions."""
        try:
            await self.run_aggregation()
        except Exception as e:
            logger.error(f"Error in reaction aggregation: {e}", exc_info=True)

    @_aggregate_loop.before_loop
    async def _before_aggregate(self):
        """Wait for bot to be ready before starting."""
        await self.bot.wait_until_ready()

    async def run_aggregation(self) -> dict:
        """
        Run aggregation for all memories with linked reactions.

        This finds memories that have:
        1. Linked messages via memory_message_links
        2. Active reactions on those messages
        3. Either no reaction_summary or new reactions since last aggregation

        Returns:
            Dictionary with aggregation statistics
        """
        stats = {"memories_processed": 0, "memories_updated": 0, "errors": 0}

        try:
            # Find memory IDs that need aggregation:
            # - Have linked messages with active reactions
            # - Either no summary yet, or new reactions since last aggregation
            memory_ids = await self._get_memories_needing_aggregation()
            logger.info(f"Found {len(memory_ids)} memories needing reaction aggregation")

            for memory_id in memory_ids:
                try:
                    updated = await self.aggregate_memory(memory_id)
                    stats["memories_processed"] += 1
                    if updated:
                        stats["memories_updated"] += 1
                except Exception as e:
                    logger.error(f"Error aggregating memory {memory_id}: {e}", exc_info=True)
                    stats["errors"] += 1

            logger.info(
                f"Reaction aggregation complete: "
                f"{stats['memories_updated']}/{stats['memories_processed']} updated, "
                f"{stats['errors']} errors"
            )

        except Exception as e:
            logger.error(f"Error in run_aggregation: {e}", exc_info=True)
            stats["errors"] += 1

        return stats

    async def _get_memories_needing_aggregation(self) -> list[int]:
        """Get IDs of memories that need reaction aggregation."""
        try:
            # Find memories with linked messages that have reactions,
            # where either no summary exists or reactions are newer than summary
            rows = await self.db.fetch(
                """
                SELECT DISTINCT m.id
                FROM memories m
                JOIN memory_message_links l ON m.id = l.memory_id
                JOIN message_reactions r ON l.message_id = r.message_id
                WHERE r.removed_at IS NULL
                AND (
                    m.reaction_summary IS NULL
                    OR r.reacted_at > (m.reaction_summary->>'last_aggregated_at')::timestamptz
                )
                ORDER BY m.id
                LIMIT 500
                """
            )
            return [row["id"] for row in rows]

        except Exception as e:
            logger.error(f"Error getting memories needing aggregation: {e}", exc_info=True)
            return []

    async def aggregate_memory(self, memory_id: int) -> bool:
        """
        Aggregate reactions for a single memory.

        Args:
            memory_id: Memory ID to aggregate

        Returns:
            True if memory was updated
        """
        try:
            # Get all active reactions on linked messages
            reactions = await self.db.fetch(
                """
                SELECT r.emoji, r.sentiment, r.intensity, r.intent, r.relevance,
                       r.reactor_id, r.reacted_at
                FROM message_reactions r
                JOIN memory_message_links l ON r.message_id = l.message_id
                WHERE l.memory_id = $1 AND r.removed_at IS NULL
                """,
                memory_id,
            )

            if not reactions:
                # No reactions - clear summary if exists
                await self.db.execute(
                    """
                    UPDATE memories
                    SET reaction_summary = NULL, reaction_confidence_boost = 0.0
                    WHERE id = $1 AND reaction_summary IS NOT NULL
                    """,
                    memory_id,
                )
                return False

            # Calculate aggregated summary
            summary = self._calculate_reaction_summary(reactions)

            # Calculate confidence boost
            confidence_boost = self._calculate_confidence_boost(summary)

            # Update memory
            await self.db.execute(
                """
                UPDATE memories
                SET reaction_summary = $2::jsonb, reaction_confidence_boost = $3
                WHERE id = $1
                """,
                memory_id,
                json.dumps(summary),
                confidence_boost,
            )

            return True

        except Exception as e:
            logger.error(f"Error aggregating memory {memory_id}: {e}", exc_info=True)
            return False

    def _calculate_reaction_summary(self, reactions: list) -> dict:
        """
        Calculate aggregated summary from reactions.

        Args:
            reactions: List of reaction records

        Returns:
            Summary dictionary for storage as JSONB
        """
        total_reactions = len(reactions)
        unique_reactors = len(set(r["reactor_id"] for r in reactions))

        # Sentiment stats (weighted by intensity)
        sentiments = [r["sentiment"] for r in reactions if r["sentiment"] is not None]
        intensities = [r["intensity"] for r in reactions if r["intensity"] is not None]

        if sentiments and intensities:
            # Weighted average sentiment
            weights = intensities
            weighted_sentiment = sum(s * w for s, w in zip(sentiments, weights)) / sum(weights)
            avg_intensity = sum(intensities) / len(intensities)

            # Controversy: high if reactions are mixed (both positive and negative)
            positive_count = sum(1 for s in sentiments if s > 0.3)
            negative_count = sum(1 for s in sentiments if s < -0.3)
            if positive_count > 0 and negative_count > 0:
                # Controversy based on balance of positive/negative
                minority = min(positive_count, negative_count)
                majority = max(positive_count, negative_count)
                controversy = (2 * minority) / (minority + majority)  # 0 to 1
            else:
                controversy = 0.0
        else:
            weighted_sentiment = 0.0
            avg_intensity = 0.5
            controversy = 0.0

        # Intent distribution
        intents = [r["intent"] for r in reactions if r["intent"]]
        intent_distribution = dict(Counter(intents))

        # Top emoji
        emoji_counts = Counter(r["emoji"] for r in reactions)
        top_emoji = [{"emoji": e, "count": c} for e, c in emoji_counts.most_common(5)]

        return {
            "total_reactions": total_reactions,
            "unique_reactors": unique_reactors,
            "sentiment_score": round(weighted_sentiment, 3),
            "intensity_score": round(avg_intensity, 3),
            "controversy_score": round(controversy, 3),
            "intent_distribution": intent_distribution,
            "top_emoji": top_emoji,
            "last_aggregated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _calculate_confidence_boost(self, summary: dict) -> float:
        """
        Calculate confidence boost from reaction summary.

        Formula produces a value from -0.1 to +0.2:
        - Positive sentiment increases confidence
        - Higher intensity amplifies the effect
        - More reactions add a logarithmic bonus
        - Controversy reduces confidence

        Args:
            summary: Aggregated reaction summary

        Returns:
            Confidence boost value (-0.1 to +0.2)
        """
        sentiment = summary.get("sentiment_score", 0)
        intensity = summary.get("intensity_score", 0.5)
        count = summary.get("total_reactions", 0)
        controversy = summary.get("controversy_score", 0)

        # Base boost from sentiment (-0.1 to +0.1)
        base_boost = sentiment * 0.1

        # Intensity multiplier (0.5 to 1.5)
        intensity_multiplier = 0.5 + intensity

        # Count bonus (logarithmic, up to +0.1)
        # log10(1) = 0, log10(10) = 1, log10(100) = 2
        count_bonus = min(0.1, math.log10(count + 1) * 0.05)

        # Controversy penalty (up to -0.05)
        controversy_penalty = controversy * 0.05

        # Final calculation
        boost = (base_boost * intensity_multiplier) + count_bonus - controversy_penalty

        # Clamp to range
        return max(-0.1, min(0.2, round(boost, 4)))


def calculate_reaction_confidence_boost(reaction_summary: Optional[dict]) -> float:
    """
    Standalone function to calculate confidence boost from reaction summary.

    This can be used outside the aggregator class when needed.

    Args:
        reaction_summary: Summary dict from memories.reaction_summary

    Returns:
        Confidence boost value (-0.1 to +0.2)
    """
    if not reaction_summary:
        return 0.0

    sentiment = reaction_summary.get("sentiment_score", 0)
    intensity = reaction_summary.get("intensity_score", 0.5)
    count = reaction_summary.get("total_reactions", 0)
    controversy = reaction_summary.get("controversy_score", 0)

    base_boost = sentiment * 0.1
    intensity_multiplier = 0.5 + intensity
    count_bonus = min(0.1, math.log10(count + 1) * 0.05)
    controversy_penalty = controversy * 0.05

    boost = (base_boost * intensity_multiplier) + count_bonus - controversy_penalty

    return max(-0.1, min(0.2, round(boost, 4)))
