"""
Privacy Classification for Memory System

Determines privacy levels based on Discord channel context.
See docs/MEMORY_PRIVACY.md for full documentation.
"""

from enum import Enum
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .extractor import ExtractedMemory


class PrivacyLevel(str, Enum):
    """Privacy levels for memory storage and retrieval."""

    DM = "dm"
    CHANNEL_RESTRICTED = "channel_restricted"
    GUILD_PUBLIC = "guild_public"
    GLOBAL = "global"


async def classify_channel_privacy(
    channel: discord.abc.Messageable,
) -> PrivacyLevel:
    """
    Determine privacy level based on channel type and permissions.

    Args:
        channel: Discord channel to classify

    Returns:
        PrivacyLevel based on channel accessibility
    """
    # DMs are always private
    if isinstance(channel, discord.DMChannel):
        return PrivacyLevel.DM

    if isinstance(channel, discord.GroupChannel):
        return PrivacyLevel.DM  # Group DMs treated as private

    # For guild channels, check if @everyone can view
    if isinstance(channel, discord.TextChannel):
        everyone_role = channel.guild.default_role
        permissions = channel.permissions_for(everyone_role)

        # If @everyone can't read messages, it's restricted
        if not permissions.read_messages:
            return PrivacyLevel.CHANNEL_RESTRICTED

        return PrivacyLevel.GUILD_PUBLIC

    # Default to most restrictive for unknown channel types
    return PrivacyLevel.CHANNEL_RESTRICTED


def classify_memory_privacy(
    extracted_memory: "ExtractedMemory",
    channel_privacy: PrivacyLevel,
) -> PrivacyLevel:
    """
    Determine final privacy level for a memory.

    Some semantic facts can be promoted to 'global' if they're
    clearly user-declared universal facts.

    Args:
        extracted_memory: The extracted memory to classify
        channel_privacy: The privacy level of the source channel

    Returns:
        Final privacy level for storage
    """
    # Check if this is a global-safe fact
    if extracted_memory.global_safe and _is_global_safe(extracted_memory):
        return PrivacyLevel.GLOBAL

    # Otherwise, inherit channel privacy
    return channel_privacy


def _is_global_safe(memory: "ExtractedMemory") -> bool:
    """
    Validate that a memory marked as global_safe actually qualifies.

    Defense-in-depth: Even if the LLM marks something as global_safe,
    we validate it here.
    """
    # Must be semantic (fact) not episodic (event)
    if memory.memory_type != "semantic":
        return False

    # Must be high confidence (explicitly stated)
    if memory.confidence < 0.9:
        return False

    # Check for sensitive patterns (NEVER global)
    sensitive_patterns = [
        "stressed",
        "anxious",
        "depressed",
        "struggling",
        "warning",
        "ban",
        "mute",
        "kick",
        "moderation",
        "salary",
        "income",
        "fired",
        "laid off",
        "job",
        "health",
        "sick",
        "diagnosis",
        "medication",
        "password",
        "secret",
        "private",
        "confidential",
        "divorce",
        "breakup",
        "relationship",
    ]

    summary_lower = memory.summary.lower()
    if any(pattern in summary_lower for pattern in sensitive_patterns):
        return False

    # Check for global-safe patterns
    global_safe_patterns = [
        "ign is",
        "username is",
        "minecraft name",
        "timezone",
        "time zone",
        "i'm in pst",
        "i'm in est",
        "prefers python",
        "prefers javascript",
        "prefers java",
        "codes in",
        "programs in",
        "coding language",
        "favorite mod",
        "favorite game",
        "favorite pack",
        "plays on",
        "java edition",
        "bedrock edition",
    ]

    return any(pattern in summary_lower for pattern in global_safe_patterns)
