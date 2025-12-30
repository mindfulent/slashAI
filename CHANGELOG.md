# Changelog

All notable changes to slashAI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Slash command support (`/ask`, `/summarize`, `/clear`)
- Rate limiting and token budget management
- Multi-guild configuration support
- User commands for build management (`/builds`, `/myprojects`)
- Automatic milestone detection with notifications

---

## [0.9.12] - 2025-12-30

### Added

#### Agentic Discord Tools (Owner Only)
The bot owner can now trigger Discord actions directly through chat conversations:

- **Tool Use in Chat** - Claude can call Discord tools when the owner requests actions
  - "Post 'Hello everyone!' in #general"
  - "Read the last 10 messages from #announcements"
  - "Delete my last message in that channel"

- **Available Tools** (same as MCP server):
  - `send_message` - Post to any accessible channel
  - `edit_message` - Edit bot's previous messages
  - `delete_message` - Delete bot's messages
  - `read_messages` - Fetch channel history
  - `list_channels` - List available channels
  - `get_channel_info` - Get channel metadata

- **Security Model**:
  - Tools are **only enabled for the owner** (configured via `OWNER_ID`)
  - Other users chat normally with no tool access
  - Tool calls require explicit user request (never automatic)
  - Agentic loop with 10-iteration safety limit

#### New Environment Variable
- `OWNER_ID` - Discord user ID allowed to trigger agentic actions
  - Leave empty to disable tool use entirely (falls back to v0.9.11 behavior)

### Technical Details

#### Files Modified
- `src/claude_client.py`:
  - Added `DISCORD_TOOLS` constant with Anthropic-format tool schemas
  - Added `bot` and `owner_id` parameters to `ClaudeClient.__init__()`
  - Implemented agentic loop in `chat()` method
  - Added `_execute_tool()` helper for tool execution
  - Updated system prompt with "Discord Actions (Owner Only)" section
- `src/discord_bot.py`:
  - Added `OWNER_ID` environment variable loading
  - Pass `bot=self` and `owner_id` to ClaudeClient

#### New Documentation
- `docs/AGENTIC_TOOLS_PLAN.md` - Design document for this feature

#### No Breaking Changes
- No database migrations required
- Feature is opt-in via `OWNER_ID` environment variable
- Without `OWNER_ID`, behavior is identical to v0.9.11

---

## [0.9.11] - 2025-12-30

### Added

#### Memory Management Slash Commands
Users can now view and manage their memories directly through Discord slash commands:

- `/memories list [page] [privacy]` - List your memories with pagination
  - Optional privacy filter: all, dm, channel_restricted, guild_public, global
  - Shows memory ID, type, privacy level, summary, and last updated date

- `/memories search <query> [page]` - Search your memories by text
  - Searches both topic summaries and source dialogue
  - Same pagination as list

- `/memories mentions [page]` - View others' public memories that mention you
  - Searches guild_public memories from other users
  - Looks for your username, display name, and IGN
  - Read-only (cannot delete others' memories)

- `/memories view <memory_id>` - View full memory details
  - Shows complete summary, source dialogue, and metadata
  - Privacy level, confidence score, timestamps
  - Delete button for your own memories

- `/memories delete <memory_id>` - Delete one of your memories
  - Confirmation dialog before deletion
  - Only works on your own memories
  - Deletion is logged for audit purposes

- `/memories stats` - View your memory statistics
  - Total memory count
  - Breakdown by privacy level
  - Breakdown by type (semantic/episodic)
  - Last updated timestamp

#### Privacy & Security Features
- All command responses are **ephemeral** (only visible to you)
- Ownership checks prevent deleting others' memories
- Button interactions verify the user matches the command invoker
- Mentions feature only shows guild_public memories from same server
- Audit table logs all deletions for debugging/recovery

#### New Database Migration
- `migrations/008_add_deletion_log.sql` - Audit table for memory deletions

### Technical Details

#### New Files
- `src/commands/__init__.py` - Commands package
- `src/commands/memory_commands.py` - Slash command implementations
- `src/commands/views.py` - Discord UI components (pagination, confirmation dialogs)
- `migrations/008_add_deletion_log.sql` - Deletion audit table

#### Modified Files
- `src/memory/manager.py` - Added query methods for commands
  - `list_user_memories()` - List with pagination and privacy filter
  - `search_user_memories()` - Text search
  - `find_mentions()` - Find others' public memories mentioning user
  - `get_memory()` - Get single memory by ID
  - `delete_memory()` - Delete with ownership check and audit logging
  - `get_user_stats()` - Statistics summary
- `src/discord_bot.py` - Load commands cog and sync command tree

#### Migration Steps
1. Deploy code changes
2. Run migration to create audit table:
   ```bash
   psql $DATABASE_URL -f migrations/008_add_deletion_log.sql
   ```
3. Restart bot to sync slash commands to Discord

---

## [0.9.10] - 2025-12-30

### Added

#### Memory Attribution System
- Memories now include clear attribution showing WHO each memory belongs to
- When retrieving memories from multiple users, each person's context is grouped separately
- Display names are resolved in real-time via Discord API (handles name changes automatically)
- Added debug logging showing retrieved memories with user_id and similarity scores (Phase 1.5)

#### Pronoun-Neutral Memory Format
- Memory summaries are now extracted in pronoun-neutral format
- Old: "User's IGN is slashdaemon", "User built a creeper farm"
- New: "IGN: slashdaemon", "Built creeper farm"
- Prevents ambiguity when memories from multiple users are retrieved together

#### New CLI Tools (`scripts/`)
- `migrate_memory_format.py` - One-time migration to convert existing memories to new format
  - Dry-run mode by default (safe to test)
  - Uses Claude Haiku for fast, accurate reformatting
  - Batch processing with rate limiting
- `memory_inspector.py` - Debug tool for the memory system
  - List memories with filters (user, privacy level, guild)
  - Show system statistics
  - Inspect individual memories
  - Search by content
  - Export to JSON with `--all` flag for complete backups

### Fixed

#### Cross-User Memory Confusion (Rain/SlashDaemon Incident)
- When Rain asked "what do you remember about me?", slashAI incorrectly attributed SlashDaemon's memories to Rain
- Root cause: `_format_memories()` didn't indicate WHO each memory belonged to
- Now memories are formatted with clear sections:
  - "Your History With This User" for the current user's memories
  - "Public Knowledge From This Server" with each person's memories grouped under their display name

### Technical Details

#### Files Modified
- `src/memory/retriever.py` - Added `user_id` to `RetrievedMemory` dataclass and SQL queries
- `src/claude_client.py` - Updated `_format_memories()` with attribution logic; added `_resolve_display_name()`
- `src/memory/extractor.py` - Updated extraction prompt for pronoun-neutral format

#### New Files
- `scripts/migrate_memory_format.py` - Migration script for existing memories
- `scripts/memory_inspector.py` - Debug CLI tool

#### No Breaking Changes
- No database schema changes required
- Existing memories continue to work (just lack attribution until migrated)
- New format only affects newly extracted memories

#### Migration Steps
1. Deploy code changes (Phase 1+2 take effect immediately)
2. **Create backup before migration** (required):
   ```bash
   DATABASE_URL=... python scripts/memory_inspector.py export --all -o backups/memories_pre_migration.json
   ```
3. Run migration script in dry-run mode to preview changes:
   ```bash
   DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/migrate_memory_format.py
   ```
4. Review output, then apply:
   ```bash
   DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/migrate_memory_format.py --apply
   ```

---

## [0.9.9] - 2025-12-28

### Fixed

#### Cross-User Guild Memory Sharing
- `guild_public` memories were incorrectly user-scoped, preventing cross-user knowledge sharing
- When User A shared information in a public channel, User B couldn't access that memory
- Now `guild_public` memories are properly shared across all users in the same guild

#### Files Updated
- `src/memory/retriever.py` - Text memory retrieval now cross-user for guild_public
- `src/memory/images/narrator.py` - Image memory context now cross-user for guild_public
- `src/memory/images/clusterer.py` - Build cluster listing now cross-user for guild_public

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required
- Privacy model unchanged: DMs and restricted channels remain user-scoped

---

## [0.9.8] - 2025-12-27

### Changed
- Strengthened "no trailing questions" rule in system prompt—now a hard ban instead of soft guidance
- Explicitly bans curious follow-ups like "what are you working on?" and "how's it going?"
- Only allowed exception: when needing info to help (e.g., "which file?")

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.7] - 2025-12-27

### Fixed
- "Could not process image" errors from Anthropic API on certain JPEG images (e.g., Google Pixel photos)
- Added image normalization to fix CMYK color space, progressive JPEG encoding, and problematic EXIF metadata

### Technical Details
- Added `normalize_image_for_api()` function in `discord_bot.py` and `analyzer.py`
- All JPEGs are now re-encoded through PIL before sending to API
- Converts unsupported color modes (CMYK, YCCK, LAB, P) to RGB/RGBA
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.6] - 2025-12-27

### Changed
- Reduced trailing question frequency in responses—bot no longer ends every message with a question
- Added guidance to Communication Style: questions are fine when genuinely curious, not as conversational filler
- Added "Not a conversation prolonger" to personality constraints

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.5] - 2025-12-27

### Added
- Self-knowledge in system prompt—bot can now accurately answer questions about its own capabilities
- Covers text memory, image memory, privacy boundaries, real-time vision, and limitations

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.4] - 2025-12-27

### Fixed
- Crash when posting image-only messages with memory enabled
- Voyage API rejects empty strings for embedding; now skip memory retrieval for empty queries

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.3] - 2025-12-27

### Fixed

#### Large Image Handling
- Images exceeding Anthropic's 5MB limit are now automatically resized
- Progressive JPEG compression (85 → 70 → 55 → 40 quality) before dimension reduction
- Proper MIME type updates when images are converted to JPEG

#### Memory Optimization for Constrained Workers
- Voyage embeddings now resize images to max 512px (reduces RAM from ~36MB to ~750KB for phone photos)
- PIL images explicitly closed in `finally` blocks to prevent memory leaks
- Explicit `gc.collect()` after image processing completes
- Fixed `UnboundLocalError` for `result_bytes` in resize function

#### Diagnostic Logging
- Added `[MSG]` logging at start of `on_message` showing attachments, embeds, mentions, and DM status
- Helps diagnose mobile upload issues and silent failures

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.2] - 2025-12-26

### Added

#### Image Memory System
- Full image memory pipeline for tracking Minecraft build projects
- **ImageObserver** - Entry point orchestrating moderation, analysis, storage, and clustering
- **ImageAnalyzer** - Claude Vision for structured image analysis:
  - Detailed descriptions, one-line summaries, and tags
  - Structured element detection (biome, time, structures, materials, style, completion stage)
  - Observation type classification (build_progress, landscape, redstone, farm, other)
  - Voyage multimodal-3 embeddings for semantic similarity
- **ImageStorage** - DigitalOcean Spaces integration:
  - Private ACL with signed URL access
  - Hash-based deduplication
  - Organized storage: `images/{user_id}/{year}/{month}/{hash}.{ext}`
- **BuildClusterer** - Groups related observations into project clusters:
  - Cosine similarity matching against cluster centroids (threshold: 0.72)
  - Privacy-compatible cluster assignment
  - Automatic cluster naming based on detected tags
  - Rolling centroid updates for efficiency
- **BuildNarrator** - Generates progression narratives:
  - Chronological timeline with milestone detection
  - Brief context injection for chat responses
  - LLM-generated summaries celebrating progress

#### Content Moderation
- Pre-storage moderation check for all images
- Multi-tier confidence handling:
  - High confidence (≥0.7): Delete message, warn user, notify moderators
  - Uncertain (0.5-0.7): Flag for review, continue processing
  - Low confidence (<0.5): Proceed normally
- Text-only moderation log (violated images never stored)
- Moderator notifications via configured channel

#### Database Schema (migrations 005-007)
- `build_clusters` table for project grouping with centroid embeddings
- `image_observations` table with full metadata, embeddings, and cluster references
- `image_moderation_log` table for violation tracking
- Privacy-aware indexes for efficient retrieval

#### Real-time Image Understanding
- Images in chat messages are now passed to Claude Vision
- Bot can see and respond to images shared in conversation

### Fixed
- pgvector embedding format for database inserts (string format `[0.1,0.2,...]`)
- pgvector centroid parsing in cluster matching
- Voyage multimodal embedding to use PIL Image objects (not base64)

### Technical Details
- New dependencies: `boto3`, `Pillow`
- Environment variables: `DO_SPACES_KEY`, `DO_SPACES_SECRET`, `DO_SPACES_BUCKET`, `DO_SPACES_REGION`, `IMAGE_MEMORY_ENABLED`, `IMAGE_MODERATION_ENABLED`, `MOD_CHANNEL_ID`

---

## [0.9.1] - 2025-12-26

### Added

#### Privacy-Aware Persistent Memory
- Cross-session memory using PostgreSQL + pgvector + Voyage AI
- Four privacy levels with channel-based classification:
  - `dm` - DM conversations, retrievable only in DMs
  - `channel_restricted` - Role-gated channels, retrievable in same channel only
  - `guild_public` - Public channels, retrievable anywhere in same guild
  - `global` - Explicit facts (IGN, timezone), retrievable everywhere
- **MemoryExtractor** - LLM-based topic extraction:
  - Triggers after 5 message exchanges (lowered from 10 for faster learning)
  - Structured JSON output with topics, sentiments, and privacy levels
  - Handles multi-topic conversations
- **MemoryRetriever** - Semantic search with privacy filtering:
  - Voyage AI embeddings (voyage-3.5-lite)
  - pgvector cosine similarity (threshold: 0.3)
  - Privacy-filtered results based on conversation context
- **MemoryUpdater** - ADD/MERGE logic for memory updates:
  - New information creates new memories
  - Related information merges with existing memories
  - Same-privacy-level constraint for merges
- **MemoryManager** - Facade orchestrating all memory operations

#### Message Handling
- Automatic message chunking for responses exceeding Discord's 2000 character limit
- Semantic splitting on markdown headers (##, ###)
- File attachment reading (.md, .txt, .py, .json, .yaml, .csv, etc.)
- Support for up to 100KB attachments

#### Database Schema (migrations 001-004)
- `memories` table with embeddings, privacy levels, and metadata
- `sessions` table for conversation tracking
- pgvector extension for vector similarity search
- Efficient indexes for privacy-filtered retrieval

### Fixed
- JSONB handling for session messages
- Memory extraction prompt escaping for Python .format()
- Duplicate message responses
- Privacy filter application to similarity debug logging

### Technical Details
- New dependencies: `asyncpg`, `voyageai`, `numpy`
- Environment variables: `DATABASE_URL`, `VOYAGE_API_KEY`, `MEMORY_ENABLED`
- Fallback: Set `MEMORY_ENABLED=false` to disable memory and return to v0.9.0 behavior

---

## [0.9.0] - 2025-12-25

### Added

#### Discord Bot
- Initial Discord bot implementation using discord.py 2.6.4
- Chatbot functionality powered by Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
- Responds to @mentions in any channel the bot can access
- Direct message (DM) support for private conversations
- Per-user, per-channel conversation history (up to 20 messages retained)
- Custom system prompt with configurable personality
- Token usage tracking with cost estimation
- Typing indicator while generating responses
- Automatic response truncation to Discord's 2000 character limit

#### MCP Server
- MCP server implementation using FastMCP (mcp 1.25.0)
- stdio transport for Claude Code integration
- Async lifespan management for Discord bot initialization
- Five Discord operation tools exposed:
  - `send_message(channel_id, content)` - Send messages to channels
  - `edit_message(channel_id, message_id, content)` - Edit existing messages
  - `read_messages(channel_id, limit)` - Fetch channel message history
  - `list_channels(guild_id?)` - List accessible text channels
  - `get_channel_info(channel_id)` - Get channel metadata

#### Infrastructure
- DigitalOcean App Platform deployment configuration
- Worker-based deployment (no HTTP health checks required)
- Procfile for buildpack compatibility
- Environment-based configuration with python-dotenv
- Consolidated deployment with minecraftcollege app

#### Documentation
- Comprehensive README with setup instructions
- Architecture documentation with system diagrams
- Technical specification (TECHSPEC.md)
- Product requirements document (PRD.md)
- Claude Code project instructions (CLAUDE.md)

### Technical Details

#### Dependencies
- `discord.py>=2.3.0` - Discord API client
- `mcp[cli]>=1.25.0` - Model Context Protocol SDK
- `anthropic>=0.40.0` - Claude API client
- `python-dotenv>=1.0.0` - Environment management

#### Model Configuration
- Model: Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
- Max tokens per response: 1024
- Conversation history limit: 20 messages per user/channel pair

#### Discord Intents
- `message_content` - Required for reading message text
- `guilds` - Required for channel/guild information
- `messages` - Required for message event handling

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 0.9.12 | 2025-12-30 | Agentic Discord tools for owner-only chat actions |
| 0.9.11 | 2025-12-30 | Memory management slash commands |
| 0.9.10 | 2025-12-30 | Memory attribution system and pronoun-neutral format |
| 0.9.9 | 2025-12-28 | Fix cross-user guild_public memory sharing |
| 0.9.8 | 2025-12-27 | Hard ban on trailing questions |
| 0.9.7 | 2025-12-27 | Fix image processing errors on Pixel photos |
| 0.9.6 | 2025-12-27 | Reduce trailing questions in responses |
| 0.9.5 | 2025-12-27 | Self-knowledge capabilities in system prompt |
| 0.9.4 | 2025-12-27 | Fix crash on image-only messages |
| 0.9.3 | 2025-12-27 | Large image handling and memory optimization fixes |
| 0.9.2 | 2025-12-26 | Image memory system with build tracking and clustering |
| 0.9.1 | 2025-12-26 | Privacy-aware persistent text memory |
| 0.9.0 | 2025-12-25 | Initial release with Discord bot and MCP server |

---

## Upgrade Notes

### Migrating to 0.9.2

1. Run database migrations 005-007:
   ```sql
   \i migrations/005_create_build_clusters.sql
   \i migrations/006_create_image_observations.sql
   \i migrations/007_create_image_moderation_and_indexes.sql
   ```

2. Configure DigitalOcean Spaces credentials (required for image storage)

3. Set `IMAGE_MEMORY_ENABLED=true` to enable image memory

### Migrating to 0.9.1

1. Set up PostgreSQL with pgvector extension

2. Run database migrations 001-004:
   ```sql
   \i migrations/001_enable_pgvector.sql
   \i migrations/002_create_memories.sql
   \i migrations/003_create_sessions.sql
   \i migrations/004_add_indexes.sql
   ```

3. Configure `DATABASE_URL` and `VOYAGE_API_KEY` environment variables

4. Set `MEMORY_ENABLED=true` to enable memory system

### Breaking Changes

None across 0.9.x releases. All features are opt-in via environment variables.

---

[Unreleased]: https://github.com/mindfulent/slashAI/compare/v0.9.12...HEAD
[0.9.12]: https://github.com/mindfulent/slashAI/compare/v0.9.11...v0.9.12
[0.9.11]: https://github.com/mindfulent/slashAI/compare/v0.9.10...v0.9.11
[0.9.10]: https://github.com/mindfulent/slashAI/compare/v0.9.9...v0.9.10
[0.9.9]: https://github.com/mindfulent/slashAI/compare/v0.9.8...v0.9.9
[0.9.8]: https://github.com/mindfulent/slashAI/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/mindfulent/slashAI/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/mindfulent/slashAI/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/mindfulent/slashAI/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/mindfulent/slashAI/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/mindfulent/slashAI/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/mindfulent/slashAI/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/mindfulent/slashAI/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/mindfulent/slashAI/releases/tag/v0.9.0
