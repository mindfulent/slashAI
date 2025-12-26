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

    # Retrieval settings (per RMM paper recommendations)
    top_k: int = 5
    similarity_threshold: float = 0.7

    # Extraction settings
    extraction_message_threshold: int = 10
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
            similarity_threshold=float(os.getenv("MEMORY_SIMILARITY_THRESHOLD", "0.7")),
            extraction_message_threshold=int(
                os.getenv("MEMORY_EXTRACTION_THRESHOLD", "10")
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
