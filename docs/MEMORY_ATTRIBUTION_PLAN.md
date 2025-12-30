# Memory Attribution Improvements Plan

**Version:** 0.9.10
**Date:** 2025-12-30
**Status:** Implemented

## Problem Statement

### The Incident

On Discord, a user named Rain asked slashAI: *"Hey @slashAI what do you remember about me? We've never actually spoken directly to each other"*

slashAI responded with information about **SlashDaemon** (a different user), incorrectly attributing those memories to Rain:

> "Pretty much nothing, honestly. I've got fragments—you go by SlashDaemon (which is wild because that's my creator's name, so that's confusing), something about being a 'techno-smartypants' good at idea conceptualization..."

This led to confusion, with Rain joking about "accidentally gaslighting an AI."

### Root Cause Analysis

The memory system correctly retrieved relevant memories, but **failed to indicate WHO each memory belongs to**. The issue spans multiple components:

1. **Retrieval Design (Working as Intended)**
   - `guild_public` memories are intentionally shared across the guild
   - When Rain asked about memories, the system retrieved both Rain's memories AND SlashDaemon's public memories (same guild)
   - This cross-user retrieval is a feature, not a bug

2. **Missing Attribution (The Bug)**
   - `RetrievedMemory` dataclass doesn't include `user_id`
   - `_format_memories()` formats all memories identically with no indication of ownership
   - Claude sees "Memory 1", "Memory 2" with no way to know whose memory is whose

3. **Ambiguous Summary Text**
   - Summaries are stored as `"User's IGN is slashdaemon"`
   - The word "User" is generic—it doesn't indicate which user
   - When multiple users' memories are retrieved, "User" becomes meaningless

## Decisions Made

### Decision 1: Discord ID-Based Name Resolution

**Chosen approach:** Look up display names at retrieval/formatting time using Discord user IDs.

**Alternatives considered:**
- Store username in memories table → Rejected (goes stale when users change names)
- Store both ID and username → Rejected (unnecessary duplication)

**How it works:**
```python
# At format time, resolve user_id to current display name
member = guild.get_member(user_id)
display_name = member.display_name if member else f"Unknown User ({user_id})"
```

**Benefits:**
- Always shows current display name (Rain_Plays → Rain handled automatically)
- No schema migration needed
- No stale data problem
- Already have `user_id` in the database

**Edge cases:**
- User left the server → Fall back to "a former member" or raw ID
- DM context (no guild) → All memories are user's own, use "You"

---

### Decision 2: Pronoun-Neutral Summary Format

**Chosen approach:** Store facts in a pronoun-neutral format to avoid baking pronouns into data.

**Old format:**
```
"User's IGN is slashdaemon"
"User built an ilmango creeper farm"
"User prefers they/them pronouns"
```

**New format:**
```
"IGN: slashdaemon"
"Built ilmango creeper farm, debugged light leak issues"
"Pronouns: they/them"
```

**Why this matters:**
- Avoids assuming pronouns for any user
- When someone shares pronouns, they're captured as a fact
- Claude (the chatbot) naturally uses correct pronouns when it has that fact as context
- Cleaner, more structured data

**Alternatives considered:**
- Default to "They/Their" → Rejected (could feel impersonal for users who've shared different pronouns)
- Store pronouns in user_preferences table → Rejected (over-engineering; memory system already extracts facts)
- Placeholder substitution (`{POSS} IGN is...`) → Rejected (complex, error-prone)

---

### Decision 3: Reformat Existing Data (Not Re-extract)

**Chosen approach:** Run a migration script that reformats existing summaries using Claude, without re-extracting from raw dialogue.

**Why reformat instead of re-extract:**
- Re-extraction might produce different facts (risk of losing memories)
- Re-extraction is expensive and unpredictable
- Reformatting preserves the extracted information, just changes presentation

**Migration approach:**
```python
# For each memory, call Claude with a reformatting prompt
prompt = """Convert this memory summary to pronoun-neutral format.

Examples:
- "User's IGN is slashdaemon" → "IGN: slashdaemon"
- "User built an ilmango creeper farm" → "Built ilmango creeper farm"
- "User prefers they/them pronouns" → "Pronouns: they/them"
- "User is interested in technical Minecraft" → "Interested in technical Minecraft"

Convert: "{summary}"
"""
```

**Benefits:**
- Predictable transformation
- Preserves all existing facts
- One-time migration cost
- Can be reviewed before committing

---

### Decision 4: Separate Memory Sections in Formatting

**Chosen approach:** Format memories into distinct sections based on ownership.

**New format for Claude's context:**
```markdown
## Relevant Context From Past Conversations

### Your History
- IGN: slashdaemon
- Built ilmango creeper farm, debugged light leak issues

### Public Knowledge From This Server

#### Rain's shared context
- Built a guardian farm in the end
- Pronouns: they/them

#### AnotherUser's shared context
- Runs the community gold farm
```

**Key principles:**
- Clear separation between "your memories" and "others' public memories"
- Each user's public memories grouped under their display name
- Claude can clearly distinguish who information is about

---

## Implementation Plan

### Phase 1: Core Attribution (No Schema Changes)

#### 1.1 Update `RetrievedMemory` dataclass
**File:** `src/memory/retriever.py`

Add `user_id` field:
```python
@dataclass
class RetrievedMemory:
    id: int
    user_id: int  # NEW: Discord user ID who owns this memory
    summary: str
    raw_dialogue: str
    memory_type: str
    privacy_level: PrivacyLevel
    similarity: float
    updated_at: datetime
```

#### 1.2 Update retriever SQL queries
**File:** `src/memory/retriever.py`

Select `user_id` in all queries:
```sql
SELECT
    id, user_id, topic_summary, raw_dialogue, memory_type, privacy_level,
    1 - (embedding <=> $1::vector) as similarity, updated_at
FROM memories
WHERE ...
```

#### 1.3 Update `_format_memories()` method
**File:** `src/claude_client.py`

- Accept `guild` parameter for name resolution
- Group memories by ownership (current user vs others)
- Resolve `user_id` to display names via `guild.get_member()`

```python
def _format_memories(
    self,
    memories: list["RetrievedMemory"],
    current_user_id: int,
    guild: Optional[discord.Guild] = None
) -> str:
    # Separate own memories from others' public memories
    own_memories = [m for m in memories if m.user_id == current_user_id]
    others_memories = [m for m in memories if m.user_id != current_user_id]

    # Group others' memories by user
    by_user = defaultdict(list)
    for m in others_memories:
        by_user[m.user_id].append(m)

    # Format with clear attribution
    # ... (see implementation)
```

#### 1.4 Update `chat()` method
**File:** `src/claude_client.py`

Pass necessary context to formatter:
```python
memory_context = self._format_memories(
    memories,
    current_user_id=int(user_id),
    guild=getattr(channel, 'guild', None)
)
```

#### 1.5 Add retrieval debug logging
**File:** `src/memory/retriever.py`

Log raw retrieval results before formatting for debugging:
```python
logger.debug(
    f"Retrieved {len(memories)} memories for query '{query[:50]}...':\n" +
    "\n".join(
        f"  - Memory {m.id} (user_id={m.user_id}, similarity={m.similarity:.3f}): "
        f"{m.summary[:60]}..."
        for m in memories
    )
)
```

**Why this matters:**
- When attribution issues occur, we need to see exactly what was retrieved
- Shows similarity scores to understand why memories were included
- Shows user_id to verify attribution before formatting
- Invaluable for debugging without running the full inspector CLI

### Phase 2: Pronoun-Neutral Extraction

#### 2.1 Update extraction prompt
**File:** `src/memory/extractor.py`

Change the `MEMORY_EXTRACTION_PROMPT` to produce pronoun-neutral facts:

```python
MEMORY_EXTRACTION_PROMPT = """Extract memorable facts from this conversation.

Format each fact in a pronoun-neutral way:
- Use "IGN: value" not "User's IGN is value"
- Use "Built X" not "User built X"
- Use "Pronouns: they/them" not "User prefers they/them"
- Use "Interested in X" not "User is interested in X"

Focus on:
- Identifiers (IGN, timezone, pronouns, location)
- Skills and expertise
- Projects and builds
- Preferences and opinions
...
"""
```

### Phase 3: Data Migration

#### 3.1 Create migration script
**File:** `scripts/migrate_memory_format.py`

```python
"""
One-time migration to convert existing memory summaries
from "User's X" format to pronoun-neutral format.
"""

async def reformat_summary(client: AsyncAnthropic, summary: str) -> str:
    """Use Claude to reformat a single summary."""
    response = await client.messages.create(
        model="claude-haiku-3-5-20241022",  # Fast and cheap for simple reformatting
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"""Convert this memory summary to pronoun-neutral format.

Examples:
- "User's IGN is slashdaemon" → "IGN: slashdaemon"
- "User built an ilmango creeper farm" → "Built ilmango creeper farm"
- "User prefers they/them pronouns" → "Pronouns: they/them"

Convert (respond with ONLY the converted text, nothing else):
"{summary}"
"""
        }]
    )
    return response.content[0].text.strip().strip('"')

async def migrate_all_memories(db_url: str, dry_run: bool = True):
    """Migrate all memories to new format."""
    # Connect to database
    # Fetch all memories
    # Reformat each one
    # Update in database (or print if dry_run)
    # Log results
```

**Safety features:**
- Dry-run mode by default (prints changes without applying)
- Batch processing with rate limiting (respect Anthropic API limits)
- Progress logging with ETA
- **Mandatory backup before apply** (export all memories to JSON first)

**Backup procedure (required before `--apply`):**
```bash
# Export all memories to JSON backup
python scripts/memory_inspector.py export --all -o backups/memories_pre_migration_$(date +%Y%m%d).json

# Verify backup is complete
python scripts/memory_inspector.py stats  # Note total count
wc -l backups/memories_pre_migration_*.json  # Should match

# Then run migration
python scripts/migrate_memory_format.py --apply
```

### Phase 4: Debugging Tools

#### 4.1 Memory inspection CLI
**File:** `scripts/memory_inspector.py`

A CLI tool for debugging memory issues:

```bash
# List all memories for a user
python scripts/memory_inspector.py list --user-id 123456789

# Show what would be retrieved for a query
python scripts/memory_inspector.py query --user-id 123 --channel-id 456 --query "what's my IGN"

# Show full memory details including embedding similarity
python scripts/memory_inspector.py inspect --memory-id 42

# Show retrieval debug log for a simulated conversation
python scripts/memory_inspector.py simulate --user-id 123 --channel-id 456 --query "tell me about Rain"
```

**Capabilities:**
- Query memories by user, channel, guild, privacy level
- Simulate retrieval with full debug output (eligible memories, similarity scores, privacy filtering)
- Inspect individual memory records (embedding preview, metadata, access history)
- Export memories to JSON for analysis

---

## Testing Plan

### Unit Tests
- `test_format_memories_own_vs_others`: Verify correct grouping
- `test_format_memories_name_resolution`: Verify Discord ID → name lookup
- `test_format_memories_user_left_server`: Verify fallback behavior
- `test_pronoun_neutral_extraction`: Verify new extraction format

### Integration Tests
- Simulate the Rain/SlashDaemon scenario with test data
- Verify memories are correctly attributed in formatted output
- Test DM vs guild channel formatting differences

### Regression Test: The Rain Scenario

**This is the critical test.** Reproduce the exact bug that prompted this work:

```python
def test_rain_scenario_regression():
    """
    Regression test: Two users in same guild, one asks about themselves,
    should NOT get the other's memories attributed to them.
    """
    # Setup: Two users in same guild with memories
    rain_user_id = 111
    slashdaemon_user_id = 222
    guild_id = 999

    # SlashDaemon has memories
    create_memory(user_id=slashdaemon_user_id, guild_id=guild_id,
                  summary="IGN: slashdaemon", privacy_level="guild_public")
    create_memory(user_id=slashdaemon_user_id, guild_id=guild_id,
                  summary="Techno-smartypants, good at idea conceptualization",
                  privacy_level="guild_public")

    # Rain asks about themselves
    memories = retrieve_memories(
        query="what do you remember about me?",
        user_id=rain_user_id,
        guild_id=guild_id
    )

    # Format for Claude
    formatted = format_memories(memories, current_user_id=rain_user_id, guild=mock_guild)

    # CRITICAL ASSERTIONS:
    # 1. SlashDaemon's memories should NOT appear under "Your History"
    assert "Your History" not in formatted or "slashdaemon" not in get_your_history_section(formatted)

    # 2. If SlashDaemon's memories appear at all, they should be clearly attributed
    if "slashdaemon" in formatted:
        assert "SlashDaemon's shared context" in formatted or "SlashDaemon" in formatted
```

**This test must pass before shipping Phase 1.**

### Manual Testing
- Have two users chat in same guild channel
- Verify each user's memories are correctly attributed when retrieved
- Test name change scenario (user changes display name mid-conversation)

---

## Rollout Plan

1. **Deploy Phase 1 + Phase 2 together** (attribution + pronoun-neutral extraction)
   - Phase 1: Low risk, no schema changes, fixes the core bug
   - Phase 2: Only affects new memories, low risk, no schema changes
   - Ship together since both are safe and Phase 2 starts producing cleaner data immediately
   - **Run regression test before deploying**

2. **Deploy Phase 4** (debugging tools)
   - Deploy alongside or shortly after Phase 1+2
   - Essential for monitoring the fix in production
   - Useful for investigating any edge cases that arise

3. **Run Phase 3 migration** (reformat existing)
   - Run in dry-run mode first, review output thoroughly
   - **Create backup before applying** (see backup procedure above)
   - Apply to production after verification
   - Can be done days/weeks after Phase 1+2 ships—existing data still works, just in old format

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/memory/retriever.py` | Add `user_id` to dataclass and queries |
| `src/claude_client.py` | Update `_format_memories()` with attribution logic |
| `src/memory/extractor.py` | Update extraction prompt for pronoun-neutral format |
| `scripts/migrate_memory_format.py` | NEW: Migration script |
| `scripts/memory_inspector.py` | NEW: Debugging CLI tool |

---

## Success Criteria

After implementation:

1. **Attribution is clear**: When slashAI retrieves memories, it's obvious whose memory is whose
2. **Name changes handled**: If Rain_Plays becomes Rain, memories still show correctly
3. **Pronouns respected**: When users share pronouns, they're captured and Claude uses them naturally
4. **No data loss**: Migration preserves all existing memory facts
5. **Debuggable**: We can inspect exactly what was retrieved and why for any conversation

---

## Open Questions

1. **Memory decay**: Should old memories in the legacy format eventually be purged, or keep forever?
2. **Confidence adjustment**: Should reformatted memories have their confidence score adjusted?
3. ~~**Audit log**: Should we keep a log of the original summaries before migration?~~ **Resolved: Yes.** Mandatory JSON backup before migration (see Phase 3 backup procedure).

### Future Enhancement: Attribution-Based Search

**The edge case:** When someone asks "what do you remember about Rain?" or "tell me about SlashDaemon", the current system retrieves memories by *semantic similarity* to the query—it doesn't have a dedicated path for "retrieve all memories attributed to user X."

**Current behavior:** If Rain asks "what do you know about SlashDaemon?", the system will:
1. Embed the query "what do you know about SlashDaemon?"
2. Find semantically similar memories (ones that mention "SlashDaemon" in their text)
3. Format them with correct attribution (Phase 1 fix)

**This works okay** because memories mentioning a user often contain their name/IGN. But it's not a *guaranteed* way to find all memories about a specific person.

**Potential future improvement:**
```python
# Dedicated retrieval by attributed user
async def retrieve_memories_by_user(
    target_user_id: int,
    requester_user_id: int,
    guild_id: int
) -> list[RetrievedMemory]:
    """Get all memories attributed to a specific user, respecting privacy."""
    # Direct query by user_id, not semantic search
    ...
```

**Not blocking for v0.9.10.** The semantic search + proper attribution should handle most cases. Add to backlog if users report issues like "slashAI didn't tell me everything about [user]."

---

## Appendix: The Original Conversation

For reference, here's the conversation that prompted this work:

```
Rain — Yesterday at 3:50 AM
Hey @slashAI what do you remember about me? We've never actually spoken directly to each other

slashAI — Yesterday at 3:51 AM
Pretty much nothing, honestly. I've got fragments—you go by SlashDaemon (which is wild because that's my creator's name, so that's confusing), something about being a "techno-smartypants" good at idea conceptualization, and mentions of helping with Jeannie Bell's SMP.

But no actual conversations between us. The memory system has picked up things you've said to other people or bots, but we haven't talked directly until now.

What's the deal with the name overlap? Are you the actual Slash, or is this some kind of meta situation I'm not getting?

Rain — Yesterday at 3:52 AM
Lmao, where did you hear about the name overlap? @slashAI

slashAI — Yesterday at 3:52 AM
Wait, you're right—I just created that confusion myself. Let me back up.

I'm modeled after Slash Daemon (the person). You're SlashDaemon (apparently also a person). My system information tells me about my creator, and my memory has info about you from the SMP Discord.

I assumed we were different people and called it an "overlap." But now I'm second-guessing—are you actually the same person? The Slash I'm modeled after?

Because if so, this is a weird moment where I'm basically asking my creator to introduce himself.

Rain — Yesterday at 3:54 AM
Mouahahahaha, I've confused the a.i with my existence, nothing better than accidentally gaslighting an a.i lol

[... conversation continues with Rain asking about training, expressing AI skepticism ...]

Rain — Yesterday at 5:34 AM
Oh and also, I'm Rain, not SlashDeamon lol

slashAI — Yesterday at 5:34 AM
Oh damn, that's hilarious. I just spent several messages confused about a name overlap that didn't exist.

So you're Rain_Plays—Discord ninja, problem-solver, Bastion main. Got it now.

Sorry for the identity crisis. Clearly my memory retrieval needs better "who am I talking to" logic.
```
