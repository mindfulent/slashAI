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
Memory System Configuration

Configurable parameters for memory extraction, retrieval, and merging.
Values can be overridden via environment variables.
"""

import os
from dataclasses import dataclass


@dataclass
class MemoryConfig:
    """Configuration for the memory system.

    Text memory thresholds are calibrated for voyage-3.5-lite embeddings which
    produce high baseline similarity (mean ~0.63, range 0.44-0.88).
    See docs/enhancements/007_IMAGE_MEMORY_FIXES.md for calibration data.
    """

    # Retrieval settings
    top_k: int = 5
    # Text similarity threshold - calibrated for voyage-3.5-lite
    # Mean similarity is ~0.63, so 0.50 captures top ~50% of matches
    similarity_threshold: float = 0.50  # Was 0.30, passed 100% of texts

    # Text relevance labeling thresholds
    text_high_relevance: float = 0.70  # Top ~10% of similarities
    text_moderate_relevance: float = 0.55  # Top ~50%

    # Extraction settings
    extraction_message_threshold: int = 5
    extraction_inactivity_minutes: int = 30

    # Merge settings
    merge_similarity_threshold: float = 0.85

    # Embedding settings (Voyage AI)
    embedding_model: str = "voyage-3.5-lite"
    embedding_dimensions: int = 1024

    # Token budget for injected context
    max_memory_tokens: int = 2000

    # Hybrid search settings
    hybrid_search_enabled: bool = True
    hybrid_candidate_limit: int = 20  # Candidates per search type for RRF
    rrf_k: int = 60  # Smoothing constant for RRF

    # Decay settings (v0.10.1)
    # Relevance-weighted decay: memories decay slower if frequently retrieved
    decay_enabled: bool = True
    base_decay_rate: float = 0.95  # Rate for 0 retrievals (5% loss per period)
    max_decay_rate: float = 0.99  # Rate for 10+ retrievals (1% loss per period)
    decay_period_days: int = 30  # Days between decay periods
    min_confidence: float = 0.10  # Floor - memories never drop below this
    cleanup_threshold: float = 0.10  # Flag for cleanup below this
    cleanup_age_days: int = 90  # Only cleanup memories older than this
    consolidation_threshold: int = 5  # Retrievals needed for consolidation candidate

    # Reinforcement boosts on access (per memory type)
    reinforcement_boost_semantic: float = 0.05
    reinforcement_boost_episodic: float = 0.03
    reinforcement_boost_procedural: float = 0.04
    reinforcement_cap_semantic: float = 0.99
    reinforcement_cap_episodic: float = 0.95
    reinforcement_cap_procedural: float = 0.97

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        """Create config from environment variables with defaults."""
        return cls(
            top_k=int(os.getenv("MEMORY_TOP_K", "5")),
            similarity_threshold=float(os.getenv("MEMORY_SIMILARITY_THRESHOLD", "0.50")),
            text_high_relevance=float(os.getenv("MEMORY_TEXT_HIGH_RELEVANCE", "0.70")),
            text_moderate_relevance=float(os.getenv("MEMORY_TEXT_MODERATE_RELEVANCE", "0.55")),
            extraction_message_threshold=int(
                os.getenv("MEMORY_EXTRACTION_THRESHOLD", "5")
            ),
            extraction_inactivity_minutes=int(
                os.getenv("MEMORY_INACTIVITY_MINUTES", "30")
            ),
            merge_similarity_threshold=float(
                os.getenv("MEMORY_MERGE_THRESHOLD", "0.85")
            ),
            embedding_model=os.getenv("MEMORY_EMBEDDING_MODEL", "voyage-3.5-lite"),
            max_memory_tokens=int(os.getenv("MEMORY_MAX_TOKENS", "2000")),
            hybrid_search_enabled=os.getenv("MEMORY_HYBRID_SEARCH", "true").lower() == "true",
            hybrid_candidate_limit=int(os.getenv("MEMORY_HYBRID_CANDIDATES", "20")),
            rrf_k=int(os.getenv("MEMORY_RRF_K", "60")),
            # Decay settings
            decay_enabled=os.getenv("MEMORY_DECAY_ENABLED", "true").lower() == "true",
            base_decay_rate=float(os.getenv("MEMORY_BASE_DECAY_RATE", "0.95")),
            max_decay_rate=float(os.getenv("MEMORY_MAX_DECAY_RATE", "0.99")),
            decay_period_days=int(os.getenv("MEMORY_DECAY_PERIOD_DAYS", "30")),
            min_confidence=float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.10")),
            cleanup_threshold=float(os.getenv("MEMORY_CLEANUP_THRESHOLD", "0.10")),
            cleanup_age_days=int(os.getenv("MEMORY_CLEANUP_AGE_DAYS", "90")),
            consolidation_threshold=int(os.getenv("MEMORY_CONSOLIDATION_THRESHOLD", "5")),
            reinforcement_boost_semantic=float(os.getenv("MEMORY_REINFORCE_SEMANTIC", "0.05")),
            reinforcement_boost_episodic=float(os.getenv("MEMORY_REINFORCE_EPISODIC", "0.03")),
            reinforcement_boost_procedural=float(os.getenv("MEMORY_REINFORCE_PROCEDURAL", "0.04")),
            reinforcement_cap_semantic=float(os.getenv("MEMORY_REINFORCE_CAP_SEMANTIC", "0.99")),
            reinforcement_cap_episodic=float(os.getenv("MEMORY_REINFORCE_CAP_EPISODIC", "0.95")),
            reinforcement_cap_procedural=float(os.getenv("MEMORY_REINFORCE_CAP_PROCEDURAL", "0.97")),
        )


@dataclass
class ImageMemoryConfig:
    """Configuration for the image memory system.

    Image thresholds are calibrated for Voyage multimodal embeddings which
    produce low baseline similarity (mean ~0.19, range -0.04 to 1.0).
    See docs/enhancements/007_IMAGE_MEMORY_FIXES.md for calibration data.
    """

    # Image analysis settings
    vision_model: str = "claude-sonnet-4-5-20250929"
    image_embedding_model: str = "voyage-multimodal-3"
    image_embedding_dimensions: int = 1024

    # File settings
    max_image_size_mb: int = 10
    supported_formats: tuple = ("png", "jpg", "jpeg", "gif", "webp")

    # Moderation thresholds
    moderation_enabled: bool = True
    nsfw_threshold: float = 0.7
    violence_threshold: float = 0.8
    require_human_review_threshold: float = 0.5

    # Clustering settings - calibrated for Voyage multimodal
    # 0.35 is ~90th percentile (was 0.72, nearly unreachable)
    cluster_assignment_threshold: float = 0.35
    cluster_active_window_days: int = 30
    cluster_stale_window_days: int = 90
    max_clusters_per_user: int = 50

    # Image relevance labeling thresholds
    image_high_relevance: float = 0.40  # Top ~6% of similarities
    image_moderate_relevance: float = 0.25  # Top ~25%
    image_minimum_relevance: float = 0.15  # Top ~50%

    # Context injection
    max_build_context_clusters: int = 3

    @classmethod
    def from_env(cls) -> "ImageMemoryConfig":
        """Create config from environment variables with defaults."""
        return cls(
            vision_model=os.getenv(
                "IMAGE_VISION_MODEL", "claude-sonnet-4-5-20250929"
            ),
            image_embedding_model=os.getenv(
                "IMAGE_EMBEDDING_MODEL", "voyage-multimodal-3"
            ),
            max_image_size_mb=int(os.getenv("IMAGE_MAX_SIZE_MB", "10")),
            moderation_enabled=os.getenv("IMAGE_MODERATION_ENABLED", "true").lower()
            == "true",
            nsfw_threshold=float(os.getenv("IMAGE_NSFW_THRESHOLD", "0.7")),
            violence_threshold=float(os.getenv("IMAGE_VIOLENCE_THRESHOLD", "0.8")),
            cluster_assignment_threshold=float(
                os.getenv("IMAGE_CLUSTER_THRESHOLD", "0.35")
            ),
            cluster_active_window_days=int(
                os.getenv("IMAGE_CLUSTER_ACTIVE_DAYS", "30")
            ),
            max_clusters_per_user=int(os.getenv("IMAGE_MAX_CLUSTERS", "50")),
            image_high_relevance=float(os.getenv("IMAGE_HIGH_RELEVANCE", "0.40")),
            image_moderate_relevance=float(os.getenv("IMAGE_MODERATE_RELEVANCE", "0.25")),
            image_minimum_relevance=float(os.getenv("IMAGE_MINIMUM_RELEVANCE", "0.15")),
            max_build_context_clusters=int(
                os.getenv("IMAGE_MAX_CONTEXT_CLUSTERS", "3")
            ),
        )
