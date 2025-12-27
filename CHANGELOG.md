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
- Custom system prompt tailored for Minecraft College community personality
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

[Unreleased]: https://github.com/mindfulent/slashAI/compare/v0.9.3...HEAD
[0.9.3]: https://github.com/mindfulent/slashAI/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/mindfulent/slashAI/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/mindfulent/slashAI/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/mindfulent/slashAI/releases/tag/v0.9.0
