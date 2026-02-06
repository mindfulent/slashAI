# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Reaction-based memory signals module.

Captures emoji reactions as memory metadata, enabling Claude to understand
what users like, dislike, find funny, or find controversial.

Part of v0.12.0 - Reaction-Based Memory Signals.
"""

from .dimensions import (
    EMOJI_DIMENSIONS,
    DEFAULT_EMOJI_DIMENSIONS,
    get_emoji_dimensions,
    INTENT_CATEGORIES,
    RELEVANCE_TYPES,
)
from .store import ReactionStore
from .aggregator import ReactionAggregator

__all__ = [
    "EMOJI_DIMENSIONS",
    "DEFAULT_EMOJI_DIMENSIONS",
    "get_emoji_dimensions",
    "INTENT_CATEGORIES",
    "RELEVANCE_TYPES",
    "ReactionStore",
    "ReactionAggregator",
]
