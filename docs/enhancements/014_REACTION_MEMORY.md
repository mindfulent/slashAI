# Enhancement 014: Reaction-Based Memory Signals

**Version**: 0.12.0 - 0.12.7
**Status**: Implemented
**Author**: Slash + Claude
**Created**: 2026-02-06
**Implemented**: 2026-02-06

## Version History

| Version | Feature | Description |
|---------|---------|-------------|
| 0.12.0 | Core Infrastructure | Reaction capture, storage, aggregation job |
| 0.12.1 | Reaction Visibility | Claude sees reaction data in memory context |
| 0.12.2 | Popular Memories Tool | `get_popular_memories` agentic tool |
| 0.12.3 | Community Filter | Exclude self-reactions from popularity queries |
| 0.12.4 | Community Observations | Passive memory creation from reacted messages |
| 0.12.5 | Reactor Inference | Infer reactor preferences from positive reactions |
| 0.12.6 | Memory Promotion | Auto-promote episodic→semantic based on reactions |
| 0.12.7 | Extraction Enhancement | Include reaction context in memory extraction |

## Overview

Emoji reactions on Discord messages are rich signals about user preferences, content quality, and community sentiment. Currently, slashAI ignores all reaction data. This enhancement captures reactions as memory metadata, enabling Claude to understand what users like, dislike, find funny, or find controversial.

## Key Insight: Bidirectional Memory Signals

A reaction creates TWO memory signals:

1. **Content Signal** - What does this reaction say about the MESSAGE?
   - "This joke was funny" (received 😂)
   - "This information was valuable" (received 🔥)
   - "This take was controversial" (received both 👍 and 👎)

2. **Reactor Signal** - What does this reaction say about the REACTOR?
   - User B reacts 👍 to "I love building with copper" → User B probably also likes copper
   - User C reacts 🤔 to a technical explanation → User C may not fully understand
   - User D reacts ❤️ to a build screenshot → User D appreciates that build style

This bidirectional model means a single reaction can inform memories about BOTH the message author AND the reactor.

---

## Part 1: Multi-Dimensional Emoji Classification

### Dimensions

Each emoji is classified across four dimensions:

| Dimension | Values | Purpose |
|-----------|--------|---------|
| **Sentiment** | -1 to +1 | Emotional valence (negative/neutral/positive) |
| **Intensity** | 0.3 to 1.0 | Strength of the signal (mild/strong) |
| **Intent** | category string | What the reactor is communicating |
| **Relevance** | content/delivery/person/meta | What aspect is being reacted to |

### Intent Categories

| Intent | Description | Indicates |
|--------|-------------|-----------|
| `agreement` | Endorsing the content | Shared opinion/preference |
| `disagreement` | Objecting to content | Opposing view |
| `appreciation` | Gratitude, thanks, love | Positive relationship signal |
| `amusement` | Found it funny/entertaining | Humor preference |
| `excitement` | Hyped, energized about content | Strong interest |
| `surprise` | Unexpected, mind-blown | Novel information |
| `sadness` | Empathy, sympathy | Emotional support |
| `thinking` | Contemplating, considering | Uncertainty/interest |
| `confusion` | Doesn't understand | Clarity needed |
| `attention` | Noticed, watching | Passive interest |
| `support` | Solidarity, encouragement | Relationship building |
| `celebration` | Marking achievement | Milestone recognition |

### Emoji Mapping Table

```python
EMOJI_DIMENSIONS = {
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
    "💀": {"sentiment": 0.0, "intensity": 0.9, "intent": "amusement", "relevance": "delivery", "context_dependent": True},
    "☠️": {"sentiment": 0.0, "intensity": 0.8, "intent": "amusement", "relevance": "delivery", "context_dependent": True},
    "🙃": {"sentiment": 0.0, "intensity": 0.5, "intent": "amusement", "relevance": "delivery", "context_dependent": True},

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
    "🤯": {"sentiment": 0.5, "intensity": 1.0, "intent": "surprise", "relevance": "content"},  # Usually positive
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
    "🔖": {"sentiment": 0.3, "intensity": 0.5, "intent": "attention", "relevance": "content"},  # Bookmarking
    "📌": {"sentiment": 0.3, "intensity": 0.5, "intent": "attention", "relevance": "content"},  # Pinning

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
    "🏠": {"sentiment": 0.5, "intensity": 0.5, "intent": "appreciation", "relevance": "content"},  # Build appreciation
    "🏰": {"sentiment": 0.7, "intensity": 0.6, "intent": "appreciation", "relevance": "content"},  # Epic build
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
DEFAULT_EMOJI_DIMENSIONS = {
    "sentiment": 0.0,
    "intensity": 0.3,
    "intent": "attention",
    "relevance": "content",
    "context_dependent": True  # Claude should interpret
}
```

### Handling Unknown Emoji

For emoji not in the mapping (rare unicode, new emoji):
1. Apply `DEFAULT_EMOJI_DIMENSIONS`
2. Flag as `context_dependent: True`
3. Claude interprets meaning during memory processing

### Custom Server Emoji

**Decision**: Ignore custom server emoji (Option A)
- Custom emoji like `:pepethink:` are server-specific
- Meaning varies wildly between communities
- Would require per-server configuration
- Focus on universal unicode emoji for v0.12.0

---

## Part 2: Data Model

### New Tables

#### `message_reactions` Table

Stores every reaction event:

```sql
-- Migration 014a: Create message_reactions table
CREATE TABLE message_reactions (
    id SERIAL PRIMARY KEY,

    -- Message context
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,
    message_author_id BIGINT NOT NULL,

    -- Reaction details
    reactor_id BIGINT NOT NULL,
    emoji TEXT NOT NULL,              -- Unicode emoji or custom emoji string
    emoji_is_custom BOOLEAN DEFAULT FALSE,

    -- Computed dimensions (for unicode emoji)
    sentiment FLOAT,                  -- -1 to +1
    intensity FLOAT,                  -- 0 to 1
    intent TEXT,                      -- agreement, amusement, etc.
    relevance TEXT,                   -- content, delivery, person, meta
    context_dependent BOOLEAN DEFAULT FALSE,

    -- Timestamps
    reacted_at TIMESTAMPTZ DEFAULT NOW(),
    removed_at TIMESTAMPTZ,           -- NULL if still active

    -- Indexes
    CONSTRAINT unique_reaction UNIQUE (message_id, reactor_id, emoji)
);

CREATE INDEX idx_reactions_message ON message_reactions(message_id);
CREATE INDEX idx_reactions_reactor ON message_reactions(reactor_id);
CREATE INDEX idx_reactions_channel ON message_reactions(channel_id);
CREATE INDEX idx_reactions_guild ON message_reactions(guild_id);
CREATE INDEX idx_reactions_author ON message_reactions(message_author_id);
CREATE INDEX idx_reactions_emoji ON message_reactions(emoji);
CREATE INDEX idx_reactions_active ON message_reactions(message_id) WHERE removed_at IS NULL;
```

#### `memory_message_links` Table

Links memories to the messages they were extracted from:

```sql
-- Migration 014b: Create memory_message_links table
CREATE TABLE memory_message_links (
    id SERIAL PRIMARY KEY,

    memory_id INT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,

    -- How this message contributed
    contribution_type TEXT DEFAULT 'source',  -- 'source', 'context', 'trigger'

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT unique_memory_message UNIQUE (memory_id, message_id)
);

CREATE INDEX idx_memory_links_memory ON memory_message_links(memory_id);
CREATE INDEX idx_memory_links_message ON memory_message_links(message_id);
```

#### Memories Table Updates

```sql
-- Migration 014c: Add reaction metadata to memories
ALTER TABLE memories ADD COLUMN reaction_summary JSONB;
-- Structure:
-- {
--   "total_reactions": 15,
--   "sentiment_score": 0.72,       -- Weighted average
--   "intensity_score": 0.65,       -- Weighted average
--   "controversy_score": 0.3,      -- Presence of opposing reactions
--   "intent_distribution": {
--     "agreement": 8,
--     "amusement": 4,
--     "excitement": 3
--   },
--   "top_reactors": [123456, 789012],  -- User IDs who reacted most
--   "last_reaction_at": "2026-02-06T..."
-- }

ALTER TABLE memories ADD COLUMN reaction_confidence_boost FLOAT DEFAULT 0.0;
-- Additional confidence from reactions (0.0 to 0.2)
```

---

## Part 3: Event Handling

### Discord Event Listeners

```python
# In discord_bot.py

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """
    Handle reaction additions.

    Uses raw event to capture reactions on uncached messages.
    """
    # Skip bot reactions
    if payload.user_id == bot.user.id:
        return

    # Skip custom emoji (for v0.12.0)
    if payload.emoji.is_custom_emoji():
        return

    emoji_str = str(payload.emoji)
    dimensions = EMOJI_DIMENSIONS.get(emoji_str, DEFAULT_EMOJI_DIMENSIONS)

    # Store reaction
    await store_reaction(
        message_id=payload.message_id,
        channel_id=payload.channel_id,
        guild_id=payload.guild_id,
        message_author_id=await get_message_author(payload),
        reactor_id=payload.user_id,
        emoji=emoji_str,
        dimensions=dimensions
    )

    # Track analytics
    track("reaction_added", "memory", user_id=payload.user_id, properties={
        "emoji": emoji_str,
        "intent": dimensions.get("intent"),
        "sentiment": dimensions.get("sentiment"),
        "channel_id": payload.channel_id,
        "guild_id": payload.guild_id
    })


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """Handle reaction removals."""
    if payload.user_id == bot.user.id:
        return

    if payload.emoji.is_custom_emoji():
        return

    await mark_reaction_removed(
        message_id=payload.message_id,
        reactor_id=payload.user_id,
        emoji=str(payload.emoji)
    )

    track("reaction_removed", "memory", user_id=payload.user_id, properties={
        "emoji": str(payload.emoji),
        "channel_id": payload.channel_id
    })
```

### Why `on_raw_reaction_*`?

Standard `on_reaction_add` only fires for cached messages. Using raw events ensures we capture reactions on older messages that may have been extracted into memories.

---

## Part 4: Memory Integration

### A. Confidence Adjustment

Reactions boost or reduce memory confidence:

```python
def calculate_reaction_confidence_boost(reaction_summary: dict) -> float:
    """
    Calculate confidence boost from reactions.

    Returns value between -0.1 and +0.2
    """
    if not reaction_summary or reaction_summary.get("total_reactions", 0) == 0:
        return 0.0

    sentiment = reaction_summary.get("sentiment_score", 0)
    intensity = reaction_summary.get("intensity_score", 0.5)
    controversy = reaction_summary.get("controversy_score", 0)
    count = reaction_summary.get("total_reactions", 0)

    # Base boost from sentiment (-0.1 to +0.1)
    base_boost = sentiment * 0.1

    # Intensity amplifier (0.5x to 1.5x)
    intensity_multiplier = 0.5 + intensity

    # Count bonus (logarithmic, caps at 10 reactions)
    count_bonus = min(0.1, math.log10(count + 1) * 0.05)

    # Controversy penalty (reduces confidence for divisive content)
    controversy_penalty = controversy * 0.05

    boost = (base_boost * intensity_multiplier) + count_bonus - controversy_penalty

    return max(-0.1, min(0.2, boost))
```

### B. Decay Resistance

Reactions count toward decay resistance (like retrieval_count):

```python
def calculate_decay_resistance(memory: Memory) -> float:
    """
    Calculate decay resistance including reaction signal.
    """
    retrieval_count = memory.retrieval_count or 0
    reaction_count = memory.reaction_summary.get("total_reactions", 0) if memory.reaction_summary else 0

    # Reactions count as half a retrieval each
    effective_retrievals = retrieval_count + (reaction_count * 0.5)

    return min(1.0, effective_retrievals / 10)
```

### C. Retrieval Ranking Boost

Highly-reacted memories surface more often:

```python
def apply_reaction_boost(similarity: float, reaction_summary: dict) -> float:
    """
    Boost retrieval score based on reaction engagement.
    """
    if not reaction_summary:
        return similarity

    sentiment = reaction_summary.get("sentiment_score", 0)
    total = reaction_summary.get("total_reactions", 0)

    # Only boost for positive sentiment
    if sentiment <= 0:
        return similarity

    # Logarithmic boost based on reaction count
    reaction_boost = min(0.15, math.log10(total + 1) * 0.05 * sentiment)

    return similarity * (1 + reaction_boost)
```

### D. Memory Type Promotion

Strong, consistent positive reactions could promote episodic → semantic:

```python
async def check_for_promotion(memory: Memory) -> bool:
    """
    Check if an episodic memory should be promoted to semantic.

    Criteria:
    - At least 10 positive reactions
    - Sentiment score > 0.8
    - No significant controversy
    - Memory is at least 7 days old
    """
    if memory.memory_type != "episodic":
        return False

    rs = memory.reaction_summary
    if not rs:
        return False

    if (rs.get("total_reactions", 0) >= 10 and
        rs.get("sentiment_score", 0) > 0.8 and
        rs.get("controversy_score", 0) < 0.2 and
        memory.created_at < datetime.now() - timedelta(days=7)):

        await promote_to_semantic(memory)
        return True

    return False
```

### E. Extraction Context

When extracting new memories, include reaction context:

```python
# Updated MEMORY_EXTRACTION_PROMPT section:

REACTION_CONTEXT_SECTION = """
## Reaction Context

The following messages received notable emoji reactions from other users:

{reaction_summaries}

Consider these reactions when evaluating:
- Agreement reactions (👍, ✅, 💯) suggest shared opinions
- Excitement reactions (🔥, 🚀) suggest high-value content
- Amusement reactions (😂, 💀) suggest humor preference
- Confusion reactions (🤔, ❓) suggest unclear communication

Reactions can inform confidence levels:
- Heavily agreed-upon statements → higher confidence
- Controversial (mixed 👍/👎) statements → note the controversy
- Content that received 🔥 from multiple users → likely important
"""
```

### F. Bidirectional Memory Creation

When processing reactions, create memories about BOTH parties:

```python
async def process_reaction_for_memories(reaction: MessageReaction):
    """
    A reaction can create/update memories for both:
    1. The message author (their content received feedback)
    2. The reactor (they expressed a preference)
    """

    # Skip if reactor is the author (self-reactions less meaningful)
    if reaction.reactor_id == reaction.message_author_id:
        return

    message = await fetch_message(reaction.message_id)
    dimensions = get_emoji_dimensions(reaction.emoji)

    # === Memory about the AUTHOR ===
    # "User A's statement about X received agreement from the community"
    await update_author_memory(
        user_id=reaction.message_author_id,
        message=message,
        reaction=reaction,
        dimensions=dimensions
    )

    # === Memory about the REACTOR ===
    # Only for strong agreement/appreciation signals
    if dimensions["intent"] in ("agreement", "appreciation", "excitement") and dimensions["sentiment"] > 0.5:
        # "User B also appears to [like/agree with] X (reacted to User A's statement)"
        await create_reactor_inference(
            reactor_id=reaction.reactor_id,
            message=message,
            reaction=reaction,
            dimensions=dimensions
        )
```

---

## Part 5: Background Processing

### Reaction Aggregation Job

Runs periodically to aggregate reactions into memory summaries:

```python
class ReactionAggregationJob:
    """
    Background job to aggregate reactions into memory metadata.

    Runs every 15 minutes.
    """

    async def run(self):
        # Find memories with linked messages that have new reactions
        memories_to_update = await self.find_memories_with_new_reactions()

        for memory in memories_to_update:
            # Get all reactions on linked messages
            reactions = await self.get_reactions_for_memory(memory.id)

            # Calculate aggregates
            summary = self.calculate_reaction_summary(reactions)

            # Update memory
            await self.update_memory_reaction_data(memory.id, summary)

            # Check for promotion
            await check_for_promotion(memory)
```

### Reaction Summary Calculation

```python
def calculate_reaction_summary(reactions: list[MessageReaction]) -> dict:
    """
    Aggregate reactions into a summary structure.
    """
    if not reactions:
        return None

    active_reactions = [r for r in reactions if r.removed_at is None]

    if not active_reactions:
        return None

    # Calculate weighted averages
    total_weight = sum(r.intensity for r in active_reactions)

    sentiment_score = sum(r.sentiment * r.intensity for r in active_reactions) / total_weight
    intensity_score = sum(r.intensity for r in active_reactions) / len(active_reactions)

    # Intent distribution
    intent_counts = {}
    for r in active_reactions:
        intent_counts[r.intent] = intent_counts.get(r.intent, 0) + 1

    # Controversy: presence of both positive and negative sentiment
    positive_count = sum(1 for r in active_reactions if r.sentiment > 0.3)
    negative_count = sum(1 for r in active_reactions if r.sentiment < -0.3)
    controversy_score = min(positive_count, negative_count) / max(len(active_reactions), 1)

    # Top reactors
    reactor_counts = {}
    for r in active_reactions:
        reactor_counts[r.reactor_id] = reactor_counts.get(r.reactor_id, 0) + 1
    top_reactors = sorted(reactor_counts.keys(), key=lambda x: reactor_counts[x], reverse=True)[:5]

    return {
        "total_reactions": len(active_reactions),
        "sentiment_score": round(sentiment_score, 3),
        "intensity_score": round(intensity_score, 3),
        "controversy_score": round(controversy_score, 3),
        "intent_distribution": intent_counts,
        "top_reactors": top_reactors,
        "last_reaction_at": max(r.reacted_at for r in active_reactions).isoformat()
    }
```

---

## Part 6: Historical Backfill

### Backfill Strategy

**Phase 1**: slashAI's own messages (highest priority)
- Reactions on slashAI's responses indicate what users found helpful/funny/valuable

**Phase 2**: Messages in threads where slashAI participated
- Context around slashAI's conversations

**Phase 3**: All public channel messages with reactions
- Full community signal capture

### Backfill Script

```python
# scripts/backfill_reactions.py

async def backfill_reactions(
    guild_id: int,
    phase: int = 1,
    after_date: datetime = None,
    dry_run: bool = True
):
    """
    Backfill historical reactions from Discord.

    Args:
        guild_id: Guild to backfill
        phase: 1 = slashAI messages, 2 = threads, 3 = all public
        after_date: Only process messages after this date
        dry_run: If True, log but don't store
    """
    guild = await bot.fetch_guild(guild_id)
    stats = {"messages": 0, "reactions": 0, "channels": 0}

    for channel in guild.text_channels:
        # Check permissions
        if not channel.permissions_for(guild.me).read_message_history:
            continue

        stats["channels"] += 1

        async for message in channel.history(limit=None, after=after_date):
            # Phase filtering
            if phase == 1 and message.author.id != bot.user.id:
                continue
            # Phase 2/3 logic...

            stats["messages"] += 1

            for reaction in message.reactions:
                # Skip custom emoji
                if reaction.custom_emoji:
                    continue

                async for user in reaction.users():
                    if user.bot:
                        continue

                    stats["reactions"] += 1

                    if not dry_run:
                        await store_reaction(
                            message_id=message.id,
                            channel_id=channel.id,
                            guild_id=guild_id,
                            message_author_id=message.author.id,
                            reactor_id=user.id,
                            emoji=str(reaction.emoji),
                            dimensions=get_emoji_dimensions(str(reaction.emoji)),
                            reacted_at=None  # Unknown historical timestamp
                        )

            # Rate limiting
            await asyncio.sleep(0.1)

    return stats
```

### Backfill CLI

```bash
# Dry run - see what would be captured
python scripts/backfill_reactions.py --guild 123456789 --phase 1 --dry-run

# Phase 1: slashAI's messages only
python scripts/backfill_reactions.py --guild 123456789 --phase 1

# Phase 2: Add threads where slashAI participated
python scripts/backfill_reactions.py --guild 123456789 --phase 2

# Phase 3: All public channels (can be slow)
python scripts/backfill_reactions.py --guild 123456789 --phase 3 --after 2025-01-01
```

### Rate Limiting Considerations

Discord API limits:
- 50 requests per second (global)
- `reaction.users()` is paginated (100 per request)

For a server with 10,000 messages and average 2 reactions each:
- ~10,000 message fetches
- ~20,000 reaction user fetches
- At 50 req/sec = ~10 minutes minimum

The backfill script includes:
- Configurable delays between requests
- Progress logging
- Resume capability (tracks last processed message)
- Graceful rate limit handling

---

## Part 7: Privacy Considerations

### Reaction Visibility

Reactions inherit the privacy level of the message they're on:
- Reactions on DM messages → `dm` privacy (only visible to those in the DM)
- Reactions on restricted channels → `channel_restricted`
- Reactions on public channels → `guild_public`

### Reactor Consent

By reacting on Discord, users implicitly consent to that reaction being visible to others in the same context. slashAI's use of reactions for memory is consistent with Discord's existing visibility model.

### Data Retention

Reactions are stored with `removed_at` timestamps:
- When a user removes a reaction, we mark it removed but don't delete
- Background job can purge old removed reactions (>90 days)
- User data export includes their reaction history

---

## Part 8: Implementation Phases

### Phase 1: Foundation (Week 1) ✅
- [x] Migration 014a: `message_reactions` table
- [x] Migration 014b: `memory_message_links` table
- [x] Add `on_raw_reaction_add` and `on_raw_reaction_remove` event listeners
- [x] Implement emoji dimension mapping
- [x] Analytics tracking for reaction events
- [x] Unit tests for emoji classification

### Phase 2: Storage & Linking (Week 2) ✅
- [x] Store reactions in database
- [x] Link memories to source messages during extraction
- [x] Migration 014c: Add `reaction_summary` to memories
- [x] Backfill script (Phase 1 - slashAI messages)

### Phase 3: Memory Integration (Week 3) ✅
- [x] Reaction aggregation background job
- [x] Confidence boost calculation
- [x] Decay resistance integration
- [x] Retrieval ranking boost

### Phase 4: Advanced Features (Week 4) ✅
- [x] Bidirectional memory creation (reactor preferences) - v0.12.5
- [x] Extraction prompt enhancement with reaction context - v0.12.7
- [x] Memory type promotion logic - v0.12.6
- [x] Context-dependent emoji interpretation (Claude)
- [x] Backfill script (Phases 2-3)

### Phase 5: Polish & Release
- [x] CLI tools for reaction inspection
- [x] Documentation updates
- [x] CHANGELOG entry
- [x] v0.12.0 release

---

## Part 9: Post-Release Enhancements (v0.12.1 - v0.12.4)

### v0.12.1 - Reaction Visibility

Made reaction data visible to Claude in memory context:

- Added `reaction_summary` field to `RetrievedMemory` dataclass
- Memory metadata displays reaction count and sentiment (e.g., "[3 positive reactions]")
- Fixed JSONB encoding bug in aggregator (`json.dumps()` for asyncpg)

### v0.12.2 - Popular Memories Tool

New agentic tool for querying reaction-engaged content:

```python
# Tool definition
{
    "name": "get_popular_memories",
    "description": "Find memories that received community reactions",
    "parameters": {
        "min_reactions": "Minimum reaction count (default: 1)",
        "sentiment": "Filter by sentiment: positive, negative, any (default: any)",
        "limit": "Max results (default: 10)"
    }
}
```

Enables Claude to answer "What topics are popular?" or "What content got positive reactions?"

### v0.12.3 - Community Engagement Filter

Added filtering to distinguish community engagement from self-validation:

```python
# New parameters for get_popular_memories
{
    "scope": "community (default) or all - community excludes self-reactions",
    "min_unique_reactors": "Minimum distinct users who reacted (default: 1)"
}
```

**Rationale**: A user reacting to their own message isn't community feedback. The `scope: "community"` filter excludes `reactor_id = memory.user_id`.

### v0.12.4 - Community Observations (Passive Memory)

**Problem**: slashAI only creates memories from conversations where it's @mentioned. Rich community content in channels goes unremembered unless someone explicitly asks about it.

**Solution**: When a message receives a reaction, check if it has any memory links. If not, create a lightweight "community_observation" memory.

```python
async def _maybe_create_community_observation(self, payload, message_author_id):
    """Create observation for reacted message without memory link."""
    # Skip DMs, bots, short messages
    if not payload.guild_id:
        return

    # Check for existing link
    has_link = await self.reaction_store.has_memory_link(payload.message_id)
    if has_link:
        return

    # Fetch message and create observation
    message = await channel.fetch_message(payload.message_id)
    await self.memory.create_community_observation(
        message_id=payload.message_id,
        channel_id=payload.channel_id,
        guild_id=payload.guild_id,
        author_id=message.author.id,
        content=message.content,
    )
```

**Memory characteristics**:
- `memory_type = "community_observation"`
- `confidence = 0.5` (moderate, since not LLM-extracted)
- `privacy_level = "guild_public"`
- Embedding generated for semantic search

**Migration 014e** adds `community_observation` to memory type constraint and makes embedding nullable.

**Backfill script** (`scripts/backfill_community_observations.py`) creates observations for existing reacted messages.

### v0.12.5 - Reactor Preference Inference (Bidirectional Memory)

**Problem**: Reactions tell us about both the message AND the reactor. If User B reacts 👍 to "I love building with copper", we can infer User B also likes copper—but v0.12.4 only created memories about the message author.

**Solution**: Create inferred preference memories for reactors when they react with strong positive signals.

```python
from memory.reactions import should_create_reactor_inference

# Check if reaction qualifies
if should_create_reactor_inference(dimensions, reactor_id, message_author_id):
    await memory.create_reactor_inference(
        reactor_id=reactor_id,
        message_content=message.content,
        intent=dimensions["intent"],  # agreement, appreciation, excitement
        channel_id=channel_id,
        guild_id=guild_id,
        message_id=message_id,
        message_author_id=message_author_id,
    )
```

**Memory characteristics**:
- `memory_type = "inferred_preference"`
- `confidence = 0.4` (lower since inferred, not directly stated)
- `privacy_level = "guild_public"`
- Topic format: "Agrees with: ...", "Appreciates: ...", "Excited about: ..."
- Deduplication: One inference per (reactor, message) pair

**Qualifying reactions** (from `inference.py`):
- Intent must be: `agreement`, `appreciation`, or `excitement`
- Sentiment must be > 0.5
- Reactor must not be the message author

**Migration 014f** adds `inferred_preference` to memory type constraint.

### v0.12.6 - Memory Type Promotion

**Problem**: Good content discovered through reactions stays as episodic memory and eventually decays. We want highly-validated content to become permanent.

**Solution**: Auto-promote `episodic` and `community_observation` memories to `semantic` when they meet reaction criteria.

**Promotion criteria** (configurable via `MemoryConfig`):

| Criterion | Default | Env Var |
|-----------|---------|---------|
| memory_type | episodic or community_observation | - |
| total_reactions | ≥ 4 | `MEMORY_PROMOTION_MIN_REACTIONS` |
| unique_reactors | ≥ 3 | `MEMORY_PROMOTION_MIN_REACTORS` |
| sentiment_score | > 0.6 | `MEMORY_PROMOTION_MIN_SENTIMENT` |
| controversy_score | < 0.3 | `MEMORY_PROMOTION_MAX_CONTROVERSY` |
| age | > 3 days | `MEMORY_PROMOTION_MIN_AGE_DAYS` |

**Implementation**: `_check_for_promotion()` in aggregator.py runs after each memory's reaction summary is updated. Promoted memories get:
- `memory_type = 'semantic'`
- `confidence = max(current, 0.8)`

Semantic memories don't decay, so promoted content becomes permanent community knowledge.

### v0.12.7 - Extraction Prompt Enhancement

**Problem**: When extracting memories from conversations, Claude doesn't know which statements the community validated through reactions. A message with 5 🔥 reactions should probably be remembered with higher confidence than an unreacted message.

**Solution**: Include reaction context in the extraction prompt so Claude can use community signals.

```python
# In manager.py _trigger_extraction:
reaction_context = await self._get_reaction_context_for_messages(messages)
extracted = await self.extractor.extract_with_privacy(
    messages, channel, reaction_context=reaction_context
)
```

**Prompt addition** (`REACTION_CONTEXT_SECTION`):
```
## Reaction Context

The following messages received emoji reactions:

- "I love copper builds..." received: 👍×3 🔥×2

**How to use this information:**
- Agreement reactions (👍, ✅) → shared community opinions
- Excitement reactions (🔥, 🚀) → high-value content
- Amusement reactions (😂) → humor that resonated

**Confidence adjustment:**
- Multiple positive reactions → consider higher confidence
- Content with 🔥 from multiple users → prioritize remembering
```

This completes Phase 4 of the reaction memory feature.

---

## Part 10: Success Metrics

### Quantitative
- Reaction capture rate: % of reactions successfully stored
- Memory-reaction link rate: % of memories with linked reactions
- Confidence boost distribution: How reactions affect confidence scores
- Retrieval improvement: Do reacted memories surface more appropriately?

### Qualitative
- Does slashAI reference reaction signals appropriately in conversation?
- Are reactor preferences being inferred correctly?
- Does the bidirectional model create useful memories?

---

## Part 11: Future Enhancements

### Custom Emoji Support
- Per-server emoji mapping configuration
- LLM-based emoji meaning inference

### Reaction Patterns
- Detect "reaction conversations" (back-and-forth reactions)
- Identify power reactors (highly engaged community members)

### Sentiment Trends
- Track sentiment over time per user
- Detect mood shifts in the community

### Super Reactions
- Discord's Super Reactions have additional properties
- Could indicate even stronger signals

---

## Appendix: Full File List

### New Files
- `src/memory/reactions/__init__.py`
- `src/memory/reactions/dimensions.py` - Emoji mapping
- `src/memory/reactions/store.py` - Database operations
- `src/memory/reactions/aggregator.py` - Summary calculation
- `src/memory/reactions/inference.py` - Bidirectional memory logic (v0.12.5)
- `migrations/014a_create_message_reactions.sql`
- `migrations/014b_create_memory_message_links.sql`
- `migrations/014c_add_reaction_metadata.sql`
- `migrations/014d_update_hybrid_search_for_reactions.sql`
- `migrations/014e_add_community_observation_type.sql` (v0.12.4)
- `migrations/014f_add_inferred_preference_type.sql` (v0.12.5)
- `scripts/backfill_reactions.py`
- `scripts/backfill_community_observations.py` (v0.12.4)
- `tests/test_reaction_dimensions.py`
- `tests/test_reaction_aggregation.py`

### Modified Files
- `src/discord_bot.py` - Add reaction event listeners, community observation trigger
- `src/memory/extractor.py` - Create memory-message links, add reaction context
- `src/memory/retriever.py` - Apply reaction boost to ranking, add reaction_summary to RetrievedMemory
- `src/memory/decay.py` - Include reaction count in decay resistance
- `src/memory/manager.py` - Orchestrate reaction processing, get_popular_memories, create_community_observation
- `src/claude_client.py` - Add get_popular_memories tool, reaction label formatting
- `src/analytics.py` - Add reaction event types
- `CLAUDE.md` - Document reaction system and migrations
