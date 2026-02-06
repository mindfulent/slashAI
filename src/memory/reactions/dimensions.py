# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Multi-dimensional emoji classification for reaction-based memory signals.

Each emoji is classified across four dimensions:
- Sentiment: -1.0 (negative) to +1.0 (positive)
- Intensity: 0.0 (mild) to 1.0 (strong)
- Intent: What the reactor is communicating
- Relevance: What aspect of the message is being reacted to

Part of v0.12.0 - Reaction-Based Memory Signals.
"""

from typing import TypedDict


class EmojiDimensions(TypedDict, total=False):
    """Type definition for emoji dimension mappings."""

    sentiment: float  # -1.0 to +1.0
    intensity: float  # 0.0 to 1.0
    intent: str  # Intent category
    relevance: str  # What's being reacted to
    context_dependent: bool  # Requires Claude interpretation


# Intent categories for reaction classification
INTENT_CATEGORIES = {
    "agreement": "Endorsing the content - shared opinion/preference",
    "disagreement": "Objecting to content - opposing view",
    "appreciation": "Gratitude, thanks, love - positive relationship signal",
    "amusement": "Found it funny/entertaining - humor preference",
    "excitement": "Hyped, energized about content - strong interest",
    "surprise": "Unexpected, mind-blown - novel information",
    "sadness": "Empathy, sympathy - emotional support",
    "thinking": "Contemplating, considering - uncertainty/interest",
    "confusion": "Doesn't understand - clarity needed",
    "attention": "Noticed, watching - passive interest",
    "support": "Solidarity, encouragement - relationship building",
    "celebration": "Marking achievement - milestone recognition",
}

# Relevance types for what aspect is being reacted to
RELEVANCE_TYPES = {
    "content": "The information or idea in the message",
    "delivery": "How it was expressed (humor, tone, style)",
    "person": "The person who sent the message",
    "meta": "Something about the conversation itself",
}

# ===== EMOJI DIMENSION MAPPINGS =====

EMOJI_DIMENSIONS: dict[str, EmojiDimensions] = {
    # ===== AGREEMENT / APPROVAL =====
    "👍": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "👍🏻": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "👍🏼": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "👍🏽": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "👍🏾": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "👍🏿": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "✅": {"sentiment": 1.0, "intensity": 0.7, "intent": "agreement", "relevance": "content"},
    "☑️": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    "💯": {"sentiment": 1.0, "intensity": 1.0, "intent": "agreement", "relevance": "content"},
    "🙌": {"sentiment": 1.0, "intensity": 0.8, "intent": "agreement", "relevance": "content"},
    "👏": {"sentiment": 1.0, "intensity": 0.7, "intent": "agreement", "relevance": "content"},
    "🤙": {"sentiment": 1.0, "intensity": 0.5, "intent": "agreement", "relevance": "content"},
    "👌": {"sentiment": 1.0, "intensity": 0.5, "intent": "agreement", "relevance": "content"},
    "✔️": {"sentiment": 1.0, "intensity": 0.6, "intent": "agreement", "relevance": "content"},
    # ===== DISAGREEMENT / DISAPPROVAL =====
    "👎": {"sentiment": -1.0, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "👎🏻": {"sentiment": -1.0, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "👎🏼": {"sentiment": -1.0, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "👎🏽": {"sentiment": -1.0, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "👎🏾": {"sentiment": -1.0, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "👎🏿": {"sentiment": -1.0, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "❌": {"sentiment": -1.0, "intensity": 0.8, "intent": "disagreement", "relevance": "content"},
    "🚫": {"sentiment": -1.0, "intensity": 0.7, "intent": "disagreement", "relevance": "content"},
    "⛔": {"sentiment": -1.0, "intensity": 0.8, "intent": "disagreement", "relevance": "content"},
    # ===== APPRECIATION / LOVE =====
    "❤️": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "🧡": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "💛": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "💚": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "💙": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "💜": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "🖤": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "🤍": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "🤎": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "💕": {"sentiment": 1.0, "intensity": 0.7, "intent": "appreciation", "relevance": "person"},
    "💖": {"sentiment": 1.0, "intensity": 0.9, "intent": "appreciation", "relevance": "person"},
    "💗": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "💓": {"sentiment": 1.0, "intensity": 0.7, "intent": "appreciation", "relevance": "person"},
    "💞": {"sentiment": 1.0, "intensity": 0.7, "intent": "appreciation", "relevance": "person"},
    "💘": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    "🥰": {"sentiment": 1.0, "intensity": 0.9, "intent": "appreciation", "relevance": "person"},
    "😍": {"sentiment": 1.0, "intensity": 0.9, "intent": "appreciation", "relevance": "person"},
    "🙏": {"sentiment": 1.0, "intensity": 0.7, "intent": "appreciation", "relevance": "content"},
    "🫶": {"sentiment": 1.0, "intensity": 0.8, "intent": "appreciation", "relevance": "person"},
    # ===== AMUSEMENT / HUMOR =====
    "😂": {"sentiment": 1.0, "intensity": 0.8, "intent": "amusement", "relevance": "delivery"},
    "🤣": {"sentiment": 1.0, "intensity": 1.0, "intent": "amusement", "relevance": "delivery"},
    "😆": {"sentiment": 1.0, "intensity": 0.6, "intent": "amusement", "relevance": "delivery"},
    "😄": {"sentiment": 1.0, "intensity": 0.5, "intent": "amusement", "relevance": "delivery"},
    "😁": {"sentiment": 1.0, "intensity": 0.5, "intent": "amusement", "relevance": "delivery"},
    "😹": {"sentiment": 1.0, "intensity": 0.8, "intent": "amusement", "relevance": "delivery"},
    "🙈": {"sentiment": 0.5, "intensity": 0.5, "intent": "amusement", "relevance": "delivery"},
    "😏": {"sentiment": 0.5, "intensity": 0.4, "intent": "amusement", "relevance": "delivery"},
    "😜": {"sentiment": 0.5, "intensity": 0.5, "intent": "amusement", "relevance": "delivery"},
    "😝": {"sentiment": 0.5, "intensity": 0.5, "intent": "amusement", "relevance": "delivery"},
    "🤪": {"sentiment": 0.5, "intensity": 0.6, "intent": "amusement", "relevance": "delivery"},
    # Context-dependent: Claude should interpret
    "💀": {
        "sentiment": 0.0,
        "intensity": 0.9,
        "intent": "amusement",
        "relevance": "delivery",
        "context_dependent": True,
    },
    "☠️": {
        "sentiment": 0.0,
        "intensity": 0.8,
        "intent": "amusement",
        "relevance": "delivery",
        "context_dependent": True,
    },
    "🙃": {
        "sentiment": 0.0,
        "intensity": 0.5,
        "intent": "amusement",
        "relevance": "delivery",
        "context_dependent": True,
    },
    # ===== EXCITEMENT / HYPE =====
    "🔥": {"sentiment": 1.0, "intensity": 1.0, "intent": "excitement", "relevance": "content"},
    "🚀": {"sentiment": 1.0, "intensity": 0.9, "intent": "excitement", "relevance": "content"},
    "⭐": {"sentiment": 1.0, "intensity": 0.7, "intent": "excitement", "relevance": "content"},
    "🌟": {"sentiment": 1.0, "intensity": 0.8, "intent": "excitement", "relevance": "content"},
    "✨": {"sentiment": 1.0, "intensity": 0.6, "intent": "excitement", "relevance": "content"},
    "💫": {"sentiment": 1.0, "intensity": 0.7, "intent": "excitement", "relevance": "content"},
    "⚡": {"sentiment": 1.0, "intensity": 0.8, "intent": "excitement", "relevance": "content"},
    "🎯": {"sentiment": 1.0, "intensity": 0.8, "intent": "excitement", "relevance": "content"},
    "💥": {"sentiment": 1.0, "intensity": 0.9, "intent": "excitement", "relevance": "content"},
    "🤩": {"sentiment": 1.0, "intensity": 0.9, "intent": "excitement", "relevance": "content"},
    "😎": {"sentiment": 1.0, "intensity": 0.6, "intent": "excitement", "relevance": "content"},
    "🥇": {"sentiment": 1.0, "intensity": 0.9, "intent": "excitement", "relevance": "content"},
    "🏅": {"sentiment": 1.0, "intensity": 0.8, "intent": "excitement", "relevance": "content"},
    "💪": {"sentiment": 1.0, "intensity": 0.7, "intent": "excitement", "relevance": "content"},
    # ===== SURPRISE / AMAZEMENT =====
    "😮": {"sentiment": 0.0, "intensity": 0.6, "intent": "surprise", "relevance": "content"},
    "😲": {"sentiment": 0.0, "intensity": 0.7, "intent": "surprise", "relevance": "content"},
    "😯": {"sentiment": 0.0, "intensity": 0.5, "intent": "surprise", "relevance": "content"},
    "🫢": {"sentiment": 0.0, "intensity": 0.6, "intent": "surprise", "relevance": "content"},
    "😱": {"sentiment": 0.0, "intensity": 0.9, "intent": "surprise", "relevance": "content"},
    "🤯": {
        "sentiment": 0.5,
        "intensity": 1.0,
        "intent": "surprise",
        "relevance": "content",
    },  # Usually positive
    "😳": {"sentiment": 0.0, "intensity": 0.6, "intent": "surprise", "relevance": "content"},
    "👁️": {"sentiment": 0.0, "intensity": 0.5, "intent": "surprise", "relevance": "content"},
    "🫣": {"sentiment": 0.0, "intensity": 0.5, "intent": "surprise", "relevance": "content"},
    # ===== SADNESS / EMPATHY =====
    "😢": {"sentiment": -0.5, "intensity": 0.6, "intent": "sadness", "relevance": "person"},
    "😭": {"sentiment": -0.5, "intensity": 0.8, "intent": "sadness", "relevance": "person"},
    "🥺": {"sentiment": -0.3, "intensity": 0.5, "intent": "sadness", "relevance": "person"},
    "😿": {"sentiment": -0.5, "intensity": 0.6, "intent": "sadness", "relevance": "person"},
    "💔": {"sentiment": -0.5, "intensity": 0.7, "intent": "sadness", "relevance": "person"},
    "😞": {"sentiment": -0.5, "intensity": 0.5, "intent": "sadness", "relevance": "person"},
    "😔": {"sentiment": -0.5, "intensity": 0.5, "intent": "sadness", "relevance": "person"},
    "🫂": {"sentiment": 0.5, "intensity": 0.6, "intent": "sadness", "relevance": "person"},  # Supportive
    # ===== THINKING / CONTEMPLATION =====
    "🤔": {"sentiment": 0.0, "intensity": 0.5, "intent": "thinking", "relevance": "content"},
    "🧐": {"sentiment": 0.0, "intensity": 0.6, "intent": "thinking", "relevance": "content"},
    "🤨": {"sentiment": -0.2, "intensity": 0.5, "intent": "thinking", "relevance": "content"},
    "🫤": {"sentiment": -0.2, "intensity": 0.4, "intent": "thinking", "relevance": "content"},
    "💭": {"sentiment": 0.0, "intensity": 0.4, "intent": "thinking", "relevance": "content"},
    # ===== CONFUSION =====
    "😕": {"sentiment": -0.3, "intensity": 0.4, "intent": "confusion", "relevance": "content"},
    "😟": {"sentiment": -0.3, "intensity": 0.5, "intent": "confusion", "relevance": "content"},
    "❓": {"sentiment": 0.0, "intensity": 0.5, "intent": "confusion", "relevance": "content"},
    "❔": {"sentiment": 0.0, "intensity": 0.4, "intent": "confusion", "relevance": "content"},
    "🤷": {"sentiment": 0.0, "intensity": 0.4, "intent": "confusion", "relevance": "content"},
    "🤷‍♂️": {"sentiment": 0.0, "intensity": 0.4, "intent": "confusion", "relevance": "content"},
    "🤷‍♀️": {"sentiment": 0.0, "intensity": 0.4, "intent": "confusion", "relevance": "content"},
    # ===== ATTENTION / ACKNOWLEDGMENT =====
    "👀": {"sentiment": 0.0, "intensity": 0.4, "intent": "attention", "relevance": "content"},
    "👁️‍🗨️": {"sentiment": 0.0, "intensity": 0.5, "intent": "attention", "relevance": "content"},
    "📍": {"sentiment": 0.0, "intensity": 0.4, "intent": "attention", "relevance": "content"},
    "🔖": {
        "sentiment": 0.3,
        "intensity": 0.5,
        "intent": "attention",
        "relevance": "content",
    },  # Bookmarking
    "📌": {
        "sentiment": 0.3,
        "intensity": 0.5,
        "intent": "attention",
        "relevance": "content",
    },  # Pinning
    # ===== SUPPORT / SOLIDARITY =====
    "🤝": {"sentiment": 1.0, "intensity": 0.6, "intent": "support", "relevance": "person"},
    "🫡": {"sentiment": 1.0, "intensity": 0.6, "intent": "support", "relevance": "person"},
    "✊": {"sentiment": 1.0, "intensity": 0.7, "intent": "support", "relevance": "person"},
    "🤗": {"sentiment": 1.0, "intensity": 0.7, "intent": "support", "relevance": "person"},
    "💐": {"sentiment": 1.0, "intensity": 0.6, "intent": "support", "relevance": "person"},
    # ===== CELEBRATION =====
    "🎉": {"sentiment": 1.0, "intensity": 0.9, "intent": "celebration", "relevance": "content"},
    "🥳": {"sentiment": 1.0, "intensity": 0.9, "intent": "celebration", "relevance": "content"},
    "🎊": {"sentiment": 1.0, "intensity": 0.8, "intent": "celebration", "relevance": "content"},
    "🏆": {"sentiment": 1.0, "intensity": 0.9, "intent": "celebration", "relevance": "content"},
    "🎂": {"sentiment": 1.0, "intensity": 0.7, "intent": "celebration", "relevance": "content"},
    "🍾": {"sentiment": 1.0, "intensity": 0.8, "intent": "celebration", "relevance": "content"},
    "🥂": {"sentiment": 1.0, "intensity": 0.7, "intent": "celebration", "relevance": "content"},
    "🎁": {"sentiment": 1.0, "intensity": 0.6, "intent": "celebration", "relevance": "content"},
    # ===== GAMING / MINECRAFT SPECIFIC =====
    "⛏️": {"sentiment": 0.5, "intensity": 0.5, "intent": "attention", "relevance": "content"},  # Mining
    "🧱": {"sentiment": 0.3, "intensity": 0.4, "intent": "attention", "relevance": "content"},  # Building
    "🏠": {
        "sentiment": 0.5,
        "intensity": 0.5,
        "intent": "appreciation",
        "relevance": "content",
    },  # Build appreciation
    "🏰": {
        "sentiment": 0.7,
        "intensity": 0.6,
        "intent": "appreciation",
        "relevance": "content",
    },  # Epic build
    "🎮": {"sentiment": 0.3, "intensity": 0.4, "intent": "attention", "relevance": "content"},  # Gaming
    "🕹️": {"sentiment": 0.3, "intensity": 0.4, "intent": "attention", "relevance": "content"},  # Gaming
    # ===== NEGATIVE EMOTIONS =====
    "😠": {"sentiment": -0.8, "intensity": 0.7, "intent": "disagreement", "relevance": "content"},
    "😤": {"sentiment": -0.6, "intensity": 0.6, "intent": "disagreement", "relevance": "content"},
    "😡": {"sentiment": -1.0, "intensity": 0.9, "intent": "disagreement", "relevance": "content"},
    "🤬": {"sentiment": -1.0, "intensity": 1.0, "intent": "disagreement", "relevance": "content"},
    "💢": {"sentiment": -0.7, "intensity": 0.7, "intent": "disagreement", "relevance": "content"},
    "🙄": {"sentiment": -0.5, "intensity": 0.5, "intent": "disagreement", "relevance": "delivery"},
    "😒": {"sentiment": -0.4, "intensity": 0.4, "intent": "disagreement", "relevance": "delivery"},
}

# Default for unknown unicode emoji
DEFAULT_EMOJI_DIMENSIONS: EmojiDimensions = {
    "sentiment": 0.0,
    "intensity": 0.3,
    "intent": "attention",
    "relevance": "content",
    "context_dependent": True,  # Claude should interpret
}


def get_emoji_dimensions(emoji: str) -> EmojiDimensions:
    """
    Get the dimension mapping for an emoji.

    Args:
        emoji: The emoji string (unicode character)

    Returns:
        Dictionary with sentiment, intensity, intent, relevance, and context_dependent
    """
    if emoji in EMOJI_DIMENSIONS:
        # Return a copy with context_dependent defaulting to False
        dims = EMOJI_DIMENSIONS[emoji].copy()
        if "context_dependent" not in dims:
            dims["context_dependent"] = False
        return dims

    # Unknown emoji - use defaults
    return DEFAULT_EMOJI_DIMENSIONS.copy()


def is_known_emoji(emoji: str) -> bool:
    """Check if an emoji has a predefined dimension mapping."""
    return emoji in EMOJI_DIMENSIONS


def get_positive_emoji() -> list[str]:
    """Get list of emoji with positive sentiment (> 0.5)."""
    return [e for e, d in EMOJI_DIMENSIONS.items() if d.get("sentiment", 0) > 0.5]


def get_negative_emoji() -> list[str]:
    """Get list of emoji with negative sentiment (< -0.5)."""
    return [e for e, d in EMOJI_DIMENSIONS.items() if d.get("sentiment", 0) < -0.5]


def get_emoji_by_intent(intent: str) -> list[str]:
    """Get list of emoji with a specific intent category."""
    return [e for e, d in EMOJI_DIMENSIONS.items() if d.get("intent") == intent]
