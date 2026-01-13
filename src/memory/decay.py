# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing: [slashdaemon@protonmail.com]

"""
Memory Confidence Decay

Background job that applies relevance-weighted decay to episodic memories
and identifies consolidation candidates.

Decay policy:
- Episodic memories decay based on retrieval frequency AND time since access
- High retrieval_count (10+) = slow decay (1% per period)
- Low retrieval_count (0) = fast decay (5% per period)
- Semantic memories do not decay
- Frequently-accessed memories are reinforced on each retrieval
- Very low confidence memories are flagged for potential cleanup

Decay formula:
  decay_resistance = min(1.0, retrieval_count / 10)
  effective_decay_rate = base_rate + ((max_rate - base_rate) * decay_resistance)
  new_confidence = confidence * (effective_decay_rate ^ periods_since_access)
"""

import logging
from dataclasses import dataclass
from typing import Optional

import asyncpg
from discord.ext import tasks

from .config import MemoryConfig

logger = logging.getLogger("slashAI.memory.decay")


@dataclass
class DecayStats:
    """Statistics from a decay job run."""

    decayed_count: int = 0
    cleanup_flagged: int = 0
    consolidation_candidates: int = 0


class MemoryDecayJob:
    """Background job for memory confidence decay."""

    def __init__(self, db_pool: asyncpg.Pool, config: Optional[MemoryConfig] = None):
        self.db = db_pool
        self.config = config or MemoryConfig.from_env()
        self._started = False
        self._decay_available: bool | None = None  # Cache for schema check

    def start(self) -> None:
        """Start the decay job loop."""
        if not self.config.decay_enabled:
            logger.info("Memory decay disabled via config")
            return

        if not self._started:
            self._decay_loop.start()
            self._started = True
            logger.info("Memory decay job started (runs every 6 hours)")

    def stop(self) -> None:
        """Stop the decay job loop."""
        if self._started:
            self._decay_loop.cancel()
            self._started = False
            logger.info("Memory decay job stopped")

    @tasks.loop(hours=6)
    async def _decay_loop(self) -> None:
        """Run decay operations every 6 hours."""
        try:
            stats = await self.run_decay()
            logger.info(
                f"Decay job complete: decayed={stats.decayed_count}, "
                f"cleanup_flagged={stats.cleanup_flagged}, "
                f"consolidation_candidates={stats.consolidation_candidates}"
            )
        except Exception as e:
            logger.error(f"Error in decay job: {e}", exc_info=True)

    async def _is_decay_available(self) -> bool:
        """Check if decay schema is available (columns exist)."""
        if self._decay_available is not None:
            return self._decay_available

        try:
            result = await self.db.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'memories' AND column_name = 'decay_policy'
                )
            """)
            self._decay_available = result
            if not result:
                logger.warning(
                    "Decay unavailable: decay_policy column not found. "
                    "Run migration 013."
                )
            return result
        except Exception as e:
            logger.warning(f"Decay schema check failed: {e}")
            self._decay_available = False
            return False

    async def run_decay(self) -> DecayStats:
        """
        Execute the decay job.

        Returns:
            Statistics about the decay run
        """
        if not await self._is_decay_available():
            return DecayStats()

        stats = DecayStats()

        # Step 1: Apply exponential decay to episodic memories
        stats.decayed_count = await self._apply_decay()

        # Step 2: Flag very low confidence memories for cleanup
        stats.cleanup_flagged = await self._flag_for_cleanup()

        # Step 3: Identify consolidation candidates
        stats.consolidation_candidates = await self._find_consolidation_candidates()

        return stats

    async def _apply_decay(self) -> int:
        """
        Apply relevance-weighted decay to eligible memories.

        Formula: new_confidence = confidence * (effective_rate ^ periods_elapsed)
        Where:
          effective_rate = base_rate + ((max_rate - base_rate) * min(1.0, retrieval_count / 10))
          periods_elapsed = floor(days_since_access / decay_period_days)

        Memories with higher retrieval_count decay slower (0.99 vs 0.95).
        """
        base_rate = self.config.base_decay_rate
        max_rate = self.config.max_decay_rate
        period_days = self.config.decay_period_days
        min_conf = self.config.min_confidence

        # Use parameterized query with interval computed separately
        result = await self.db.execute(
            f"""
            UPDATE memories
            SET confidence = GREATEST(
                $1,
                confidence * POWER(
                    -- Relevance-weighted decay rate: base to max based on retrieval_count
                    $2 + (($3 - $2) * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10)),
                    -- Periods elapsed since last access
                    FLOOR(EXTRACT(EPOCH FROM (NOW() - last_accessed_at)) / 86400 / $4)
                )
            ),
            updated_at = NOW()
            WHERE decay_policy = 'standard'
              AND is_protected = FALSE
              AND last_accessed_at IS NOT NULL
              AND last_accessed_at < NOW() - INTERVAL '{period_days} days'
              AND confidence > $1
        """,
            min_conf,
            base_rate,
            max_rate,
            period_days,
        )

        # Parse affected row count from result (format: "UPDATE N")
        count = int(result.split()[-1]) if result else 0
        return count

    async def _flag_for_cleanup(self) -> int:
        """Flag very low confidence old memories for potential cleanup."""
        cleanup_threshold = self.config.cleanup_threshold
        cleanup_age = self.config.cleanup_age_days

        result = await self.db.execute(
            f"""
            UPDATE memories
            SET decay_policy = 'pending_deletion'
            WHERE decay_policy = 'standard'
              AND is_protected = FALSE
              AND confidence < $1
              AND created_at < NOW() - INTERVAL '{cleanup_age} days'
        """,
            cleanup_threshold,
        )

        count = int(result.split()[-1]) if result else 0
        return count

    async def _find_consolidation_candidates(self) -> int:
        """
        Find episodic memories that may be worth promoting to semantic.

        These are frequently-accessed memories that have proven useful.
        Only logs candidates - actual promotion is a future enhancement.
        """
        threshold = self.config.consolidation_threshold

        candidates = await self.db.fetch(
            """
            SELECT id, user_id, topic_summary, retrieval_count, confidence
            FROM memories
            WHERE memory_type = 'episodic'
              AND retrieval_count >= $1
              AND confidence > 0.6
              AND decay_policy != 'none'
            ORDER BY retrieval_count DESC
            LIMIT 10
        """,
            threshold,
        )

        for c in candidates:
            logger.info(
                f"Consolidation candidate: memory_id={c['id']}, "
                f"user={c['user_id']}, retrievals={c['retrieval_count']}, "
                f"confidence={c['confidence']:.2f}, "
                f"summary='{c['topic_summary'][:50]}...'"
            )

        return len(candidates)


async def run_decay_job(db_pool: asyncpg.Pool, config: Optional[MemoryConfig] = None) -> DecayStats:
    """Run decay job once (for testing or manual triggers)."""
    job = MemoryDecayJob(db_pool, config)
    return await job.run_decay()
