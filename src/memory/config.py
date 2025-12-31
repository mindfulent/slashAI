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
    """Configuration for the memory system."""

    # Retrieval settings
    top_k: int = 5
    similarity_threshold: float = 0.3  # Lowered for better recall

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

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        """Create config from environment variables with defaults."""
        return cls(
            top_k=int(os.getenv("MEMORY_TOP_K", "5")),
            similarity_threshold=float(os.getenv("MEMORY_SIMILARITY_THRESHOLD", "0.3")),
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
        )


@dataclass
class ImageMemoryConfig:
    """Configuration for the image memory system."""

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

    # Clustering settings
    cluster_assignment_threshold: float = 0.72
    cluster_active_window_days: int = 30
    cluster_stale_window_days: int = 90
    max_clusters_per_user: int = 50

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
                os.getenv("IMAGE_CLUSTER_THRESHOLD", "0.72")
            ),
            cluster_active_window_days=int(
                os.getenv("IMAGE_CLUSTER_ACTIVE_DAYS", "30")
            ),
            max_clusters_per_user=int(os.getenv("IMAGE_MAX_CLUSTERS", "50")),
            max_build_context_clusters=int(
                os.getenv("IMAGE_MAX_CONTEXT_CLUSTERS", "3")
            ),
        )
