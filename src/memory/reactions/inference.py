# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Bidirectional memory inference from reactions.

When a user reacts to a message with a strong positive signal (agreement,
appreciation, excitement), we can infer something about the REACTOR's
preferences, not just the message author's content quality.

Example:
- User A posts: "I love building with copper blocks"
- User B reacts with 👍 (agreement, sentiment=1.0)
- We infer: User B probably also likes copper blocks

Part of v0.12.5 - Bidirectional Reactor Preference Inference.
"""

import logging
from typing import Optional

from .dimensions import EmojiDimensions

logger = logging.getLogger(__name__)

# Intents that indicate the reactor shares the author's opinion/preference
PREFERENCE_INTENTS = {"agreement", "appreciation", "excitement"}

# Minimum sentiment to trigger preference inference
MIN_SENTIMENT_FOR_INFERENCE = 0.5

# Maximum message length to use for inference (longer = too complex to infer)
MAX_MESSAGE_LENGTH_FOR_INFERENCE = 300


def should_create_reactor_inference(
    dimensions: EmojiDimensions,
    reactor_id: int,
    message_author_id: int,
) -> bool:
    """
    Determine if a reaction should trigger a reactor preference inference.

    Args:
        dimensions: Emoji dimension mapping for the reaction
        reactor_id: Discord user ID of the person who reacted
        message_author_id: Discord user ID of the message author

    Returns:
        True if we should create an inferred preference memory for the reactor
    """
    # Skip self-reactions (doesn't indicate new preference)
    if reactor_id == message_author_id:
        return False

    # Check intent is preference-indicating
    intent = dimensions.get("intent", "")
    if intent not in PREFERENCE_INTENTS:
        return False

    # Check sentiment is strongly positive
    sentiment = dimensions.get("sentiment", 0)
    if sentiment < MIN_SENTIMENT_FOR_INFERENCE:
        return False

    return True


def format_inferred_topic(
    message_content: str,
    intent: str,
) -> str:
    """
    Format the topic summary for an inferred preference memory.

    Args:
        message_content: The original message content that was reacted to
        intent: The reaction intent (agreement, appreciation, excitement)

    Returns:
        Formatted topic summary for the inference
    """
    # Truncate long messages
    if len(message_content) > MAX_MESSAGE_LENGTH_FOR_INFERENCE:
        content = message_content[:MAX_MESSAGE_LENGTH_FOR_INFERENCE].rsplit(" ", 1)[0] + "..."
    else:
        content = message_content

    # Format based on intent
    if intent == "agreement":
        return f"Agrees with: {content}"
    elif intent == "appreciation":
        return f"Appreciates: {content}"
    elif intent == "excitement":
        return f"Excited about: {content}"
    else:
        return f"Reacted positively to: {content}"
