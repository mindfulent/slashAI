# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

slashAI is a Discord chatbot and MCP server powered by Claude Sonnet 4.5 with privacy-aware persistent memory.

**Two modes:**
1. **MCP Server**: Claude Code uses tools to control Discord (send/edit/read messages)
2. **Chatbot**: Discord users chat with Claude by mentioning the bot or DMing it, with cross-session memory

## Commands

```bash
# Setup (Windows)
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt

# Run standalone chatbot
python src/discord_bot.py

# MCP server (started automatically by Claude Code when configured)
python src/mcp_server.py
```

## Architecture

```
Claude Code → stdio → mcp_server.py → discord_bot.py → Discord API

Discord User → discord_bot.py → claude_client.py → Anthropic API
                                       ↓
                               memory/ → PostgreSQL + pgvector
                                       ↓
                               Voyage AI (embeddings)
```

**Core components:**

1. **`mcp_server.py`** - FastMCP server with stdio transport
   - `@mcp.tool()` decorators define tools
   - Lifespan context manager starts/stops Discord bot
   - Channel IDs passed as strings (converted to int internally)

2. **`discord_bot.py`** - discord.py Bot
   - Chatbot: responds to mentions and DMs
   - Reads text attachments (.md, .txt, .py, etc.) up to 100KB
   - Auto-chunks responses >2000 chars (semantic splitting on headers)
   - Exposes async methods for MCP tools
   - `_ready_event` for startup synchronization

3. **`claude_client.py`** - Anthropic API wrapper
   - Conversation history keyed by `(user_id, channel_id)` tuple
   - History capped at 20 messages
   - System prompt in `DEFAULT_SYSTEM_PROMPT` (personality config)
   - Tracks cumulative token usage

4. **`src/memory/`** - Text memory system
   - `extractor.py`: LLM topic extraction (triggers after 5 exchanges)
   - `retriever.py`: Hybrid search (lexical + semantic) with Reciprocal Rank Fusion
   - `updater.py`: ADD/MERGE logic for memory updates
   - `privacy.py`: dm/channel_restricted/guild_public/global levels
   - `manager.py`: Facade orchestrating all operations
   - `config.py`: Configurable thresholds (env-overridable)
   - `decay.py`: Confidence decay job (episodic memories decay over time)

5. **`src/memory/images/`** - Image memory system
   - `observer.py`: Pipeline entry point (moderation → analysis → storage → clustering)
   - `analyzer.py`: Claude Vision + Voyage multimodal embeddings
   - `clusterer.py`: Groups images into build clusters by similarity
   - `narrator.py`: Generates progression narratives
   - `storage.py`: DigitalOcean Spaces (S3-compatible)

6. **`src/reminders/`** - Scheduled reminders system
   - `time_parser.py`: Natural language + CRON parsing
   - `manager.py`: Database operations for reminders
   - `scheduler.py`: Background task loop for delivery (60s interval)

7. **`src/recognition/`** - Core Curriculum integration for AI-assisted build reviews
   - `analyzer.py`: Vision-based analysis of Minecraft screenshots
   - `feedback.py`: Constructive feedback generation for craft development
   - `progression.py`: Title progression evaluation
   - `nominations.py`: Anti-gaming checks for peer nominations
   - `api.py`: Recognition API client for theblockacademy backend
   - `scheduler.py`: Background polling for pending submissions

8. **`src/commands/`** - Discord slash commands
   - `memory_commands.py`: `/memories` command group for user memory management
   - `reminder_commands.py`: `/remind` command group
   - `analytics_commands.py`: `/analytics` owner-only commands
   - `link_commands.py`: `/verify` for Minecraft-Discord account linking
   - `streamcraft_commands.py`: `/streamcraft` owner-only license/usage queries
   - `views.py`: Pagination and confirmation UI components

9. **`src/tools/`** - Agentic tools for the chatbot
   - `github_docs.py`: Read-only access to slashAI documentation via GitHub API

## MCP Tools

These tools are exposed via `mcp_server.py` for Claude Code to control Discord:

| Tool | Parameters | Returns |
|------|------------|---------|
| `send_message` | `channel_id`, `content` | Message ID |
| `edit_message` | `channel_id`, `message_id`, `content` | Confirmation |
| `delete_message` | `channel_id`, `message_id` | Confirmation |
| `read_messages` | `channel_id`, `limit` (default 10, max 100) | Formatted message list |
| `search_messages` | `query`, `channel` (optional, ID or name), `author` (optional), `limit` (default 10, max 50) | Matching messages with IDs |
| `list_channels` | `guild_id` (optional) | Channel list with IDs |
| `get_channel_info` | `channel_id` | Channel metadata dict |

**Channel name resolution:** `search_messages` supports channel names (e.g., "server-general") in addition to IDs. Handles emoji prefixes and partial matching.

**Agentic Tools (chatbot-only, owner via `OWNER_ID`):** `send_message`, `edit_message`, `delete_message`, `read_messages`, `list_channels`, `get_channel_info`, `describe_message_image`, `set_reminder`, `list_reminders`, `cancel_reminder`, `set_user_timezone`, `search_memories`, `read_github_file`, `list_github_docs` - defined in `claude_client.py:DISCORD_TOOLS`, only available when chatting with the bot as the owner.

**GitHub Documentation Tools:**
- `read_github_file`: Read a documentation file from the slashAI repo (path must start with "docs/")
- `list_github_docs`: List files in a docs subdirectory (e.g., "enhancements")
- Both tools support an optional `ref` parameter for branch/commit SHA (default: "main")
- Caches responses for 5 minutes to reduce API calls
- Uses `GITHUB_TOKEN` env var for higher rate limits (5,000 vs 60 req/hr)

## Key Constants

| Constant | Value | Location |
|----------|-------|----------|
| `MODEL_ID` | `claude-sonnet-4-5-20250929` | `claude_client.py` |
| `MAX_HISTORY_LENGTH` | 20 messages | `claude_client.py` |
| `extraction_message_threshold` | 5 exchanges | `memory/config.py` |
| `similarity_threshold` (text) | 0.50 | `memory/config.py` |
| `embedding_model` | `voyage-3.5-lite` | `memory/config.py` |
| `cluster_assignment_threshold` | 0.35 | `memory/config.py` |
| `DISCORD_MAX_LENGTH` | 2000 chars | `discord_bot.py` |

**Note:** Thresholds were recalibrated in v0.9.21 to account for different embedding model distributions. Text embeddings (voyage-3.5-lite) have high baseline similarity (~0.63 mean), while image embeddings (voyage-multimodal) have low baseline (~0.19 mean). See `docs/enhancements/007_IMAGE_MEMORY_FIXES.md` for calibration data.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token |
| `ANTHROPIC_API_KEY` | For chatbot | Anthropic API key |
| `DATABASE_URL` | For memory | PostgreSQL connection string |
| `VOYAGE_API_KEY` | For memory | Voyage AI API key for embeddings |
| `MEMORY_ENABLED` | No | Set to "true" to enable memory system |
| `IMAGE_MEMORY_ENABLED` | No | Set to "true" to enable image memory |
| `DO_SPACES_KEY` | For images | DigitalOcean Spaces access key |
| `DO_SPACES_SECRET` | For images | DigitalOcean Spaces secret key |
| `DO_SPACES_BUCKET` | For images | Spaces bucket name (default: slashai-images) |
| `DO_SPACES_REGION` | For images | Spaces region (default: nyc3) |
| `IMAGE_MODERATION_ENABLED` | No | Set to "false" to disable content moderation |
| `OWNER_ID` | For tools | Discord user ID allowed to trigger agentic actions |
| `ANALYTICS_ENABLED` | No | Set to "false" to disable analytics tracking (default: true) |
| `GITHUB_TOKEN` | Recommended | GitHub personal access token for higher API rate limits |
| `MEMORY_HYBRID_SEARCH` | No | Set to "false" to disable hybrid search (default: true) |
| `MEMORY_DECAY_ENABLED` | No | Set to "false" to disable confidence decay (default: true) |
| `RECOGNITION_API_URL` | For recognition | theblockacademy Recognition API URL |
| `RECOGNITION_API_KEY` | For recognition | API key for recognition webhooks |

## Development Notes

**Restart requirements:**
- `mcp_server.py` changes: Restart Claude Code
- `discord_bot.py` changes: Restart bot process
- `claude_client.py` changes: Apply on next message
- `src/memory/` changes: Restart bot process

**MCP server lifespan:** Bot starts on server init, has 30s connection timeout

**Memory system rollback:** Set `MEMORY_ENABLED=false` to fall back to v0.9.0 behavior (no memory)

**Prompt caching:** System prompt (~1,100 tokens) is cached via Anthropic's ephemeral cache. Cache expires after 5 minutes of inactivity. Memory context is dynamic and not cached.

**Release workflow:** When cutting a new release, always update:
1. `CHANGELOG.md` - Add version entry with date and changes
2. `README.md` - Update "Current Version" at top
3. `docs/MEMORY_*.md` - Update version numbers if memory-related
4. Create GitHub release with `gh release create vX.Y.Z`

**License:** AGPL-3.0 with commercial licensing option. All source files have AGPL headers.

## Database Migrations

Run migrations in order to set up the memory system. Use `psql` or a PostgreSQL client:

```sql
-- Text memory (v0.9.1)
\i migrations/001_enable_pgvector.sql
\i migrations/002_create_memories.sql
\i migrations/003_create_sessions.sql
\i migrations/004_add_indexes.sql

-- Image memory (v0.9.2)
\i migrations/005_create_build_clusters.sql
\i migrations/006_create_image_observations.sql
\i migrations/007_create_image_moderation_and_indexes.sql

-- Memory management (v0.9.11)
\i migrations/008_add_deletion_log.sql

-- Analytics (v0.9.16)
\i migrations/009_create_analytics.sql

-- Scheduled Reminders (v0.9.17)
\i migrations/010_create_scheduled_reminders.sql
\i migrations/011_create_user_settings.sql

-- Hybrid Search (v0.10.0)
\i migrations/012_add_hybrid_search.sql

-- Confidence Decay (v0.10.1)
\i migrations/013_add_confidence_decay.sql
```

## Memory Privacy Model

Memories are classified by privacy level based on their source channel:

| Level | Source | Retrievable In |
|-------|--------|----------------|
| `dm` | DM conversation | DMs only |
| `channel_restricted` | Role-gated channel | Same channel only |
| `guild_public` | Public channel | Any channel in same guild |
| `global` | Explicit facts (IGN, timezone) | Anywhere |

See `docs/MEMORY_TECHSPEC.md` and `docs/MEMORY_PRIVACY.md` for details.

## Image Memory Pipeline

When a user posts an image:
1. **Moderation** - Claude Vision checks for policy violations (confidence ≥0.7 = delete, 0.5-0.7 = flag for review)
2. **Analysis** - Claude Vision generates description/tags, Voyage creates multimodal embedding
3. **Storage** - Image uploaded to DO Spaces with hash-based deduplication
4. **Clustering** - Observation assigned to or creates a build cluster based on embedding similarity
5. **Narration** - On demand, generates progression narratives for build clusters

## CLI Tools

The `scripts/` directory contains debugging and maintenance tools:

```bash
# Memory Inspector - Debug and query the memory system
python scripts/memory_inspector.py list --user-id 123456789          # List user's memories
python scripts/memory_inspector.py list --user-id 123456789 -v       # Verbose output
python scripts/memory_inspector.py stats                              # System statistics
python scripts/memory_inspector.py inspect --memory-id 42             # Inspect specific memory
python scripts/memory_inspector.py search -q "creeper farm"           # Search by content
python scripts/memory_inspector.py export --user-id 123 -o out.json   # Export to JSON
python scripts/memory_inspector.py export --all -o backup.json        # Backup ALL memories

# Memory Migration - Convert old "User's X" format to pronoun-neutral format
# IMPORTANT: Always backup first with `export --all` before running with --apply
python scripts/migrate_memory_format.py                               # Dry run (preview)
python scripts/migrate_memory_format.py --apply                       # Apply changes

# Analytics Query - Query analytics data from command line
python scripts/analytics_query.py summary                             # 24-hour overview
python scripts/analytics_query.py dau                                 # Daily active users
python scripts/analytics_query.py tokens                              # Token usage by day
python scripts/analytics_query.py commands                            # Command usage
python scripts/analytics_query.py errors                              # Recent errors
python scripts/analytics_query.py latency                             # Response latency
python scripts/analytics_query.py memory                              # Memory system stats
python scripts/analytics_query.py tools                               # Tool execution stats

# Database Backup - Trigger and manage database backups
python scripts/backup_db.py backup --type pre-migration               # Pre-migration backup (before schema changes)
python scripts/backup_db.py backup --type manual                      # Manual backup
python scripts/backup_db.py backup --type manual -q                   # Skip Discord notification
python scripts/backup_db.py list                                       # List all backups in DO Spaces

# Memory Decay CLI (v0.10.1) - Manage confidence decay
python scripts/memory_decay_cli.py run --dry-run                      # Preview decay without applying
python scripts/memory_decay_cli.py run                                # Run decay job manually
python scripts/memory_decay_cli.py stats                              # Show decay statistics
python scripts/memory_decay_cli.py candidates                         # Show consolidation candidates
python scripts/memory_decay_cli.py protect 42                         # Protect memory from decay
python scripts/memory_decay_cli.py unprotect 42                       # Remove protection
python scripts/memory_decay_cli.py pending                            # Show memories pending deletion
```

## Memory Slash Commands

Users can manage their memories directly through Discord slash commands:

| Command | Description |
|---------|-------------|
| `/memories list [page] [privacy]` | List your memories with pagination |
| `/memories search <query> [page]` | Search your memories by text |
| `/memories mentions [page]` | View others' public memories that mention you |
| `/memories view <memory_id>` | View full details of a memory |
| `/memories delete <memory_id>` | Delete one of your memories (with confirmation) |
| `/memories stats` | View your memory statistics |

**Privacy features:**
- All responses are ephemeral (only visible to you)
- You can only delete your own memories
- Mentions shows read-only view of others' guild_public memories

## Analytics Commands (Owner-Only)

Slash commands for viewing bot analytics (requires `OWNER_ID` env var):

| Command | Description |
|---------|-------------|
| `/analytics summary [hours]` | Quick overview (messages, users, tokens, cost, errors) |
| `/analytics dau [days]` | Daily active users over time |
| `/analytics tokens [days]` | Token usage and estimated costs |
| `/analytics commands [days]` | Command usage breakdown |
| `/analytics errors [limit]` | Recent errors with details |
| `/analytics users [days]` | Top users by message count |
| `/analytics memory [days]` | Memory system stats (extractions, retrievals, success rate) |

**Event tracking:** Analytics events are tracked automatically for messages, API calls, memory operations, commands, and errors. Set `ANALYTICS_ENABLED=false` to disable.

## StreamCraft Commands (Owner-Only)

Owner-only slash commands for viewing StreamCraft license, usage, and streaming data:

| Command | Description |
|---------|-------------|
| `/streamcraft licenses` | List all licenses |
| `/streamcraft player <name_or_uuid>` | Player usage lookup |
| `/streamcraft servers [server_id]` | Per-server usage summary |
| `/streamcraft active` | Currently active rooms and participants |

## Account Linking

| Command | Description |
|---------|-------------|
| `/verify <code>` | Link Discord to Minecraft using a code from `/discord link` in-game |

Used by CoreCurriculum recognition system for DM notifications when builds are reviewed.

## Reminder Commands

Users can schedule reminders via slash commands or natural language:

| Command | Description |
|---------|-------------|
| `/remind set <message> <time> [channel]` | Create a reminder (channel delivery admin-only) |
| `/remind list [include_completed] [page]` | List your reminders |
| `/remind cancel <reminder_id>` | Cancel a reminder |
| `/remind pause <reminder_id>` | Pause a recurring reminder |
| `/remind resume <reminder_id>` | Resume a paused reminder |
| `/remind timezone <timezone>` | Set your timezone (e.g., `America/Los_Angeles`) |

**Time formats supported:**
- Natural language: `in 2 hours`, `tomorrow at 10am`, `next Monday 3pm`
- Recurring: `every day at 9am`, `every weekday at 3pm`, `every 2 hours`
- CRON: `0 10 * * *` (daily at 10am), `0 9 * * 1-5` (weekdays at 9am)
- Presets: `hourly`, `daily`, `weekly`, `weekdays`, `monthly`

**Delivery:**
- Regular users: Reminders delivered via DM
- Admin (OWNER_ID): Can specify a channel for delivery

**Natural language (chatbot):** Users can also say "@slashAI remind me at 10am to check the server" and Claude will use the `set_reminder` tool.

## Troubleshooting

**Bot doesn't respond to mentions:**
- Check `ANTHROPIC_API_KEY` is set (chatbot mode requires it)
- Verify bot has `message_content` intent enabled in Discord Developer Portal
- Check logs: `logger.setLevel(logging.DEBUG)` for verbose output
- Bot ignores `@everyone` and `@here` mentions (only responds to direct `@slashAI`)

**Memory not being stored:**
- Ensure `MEMORY_ENABLED=true` and `DATABASE_URL` + `VOYAGE_API_KEY` are set
- Check migration status: all 13 migrations must be applied
- Verify pgvector extension is enabled: `SELECT * FROM pg_extension WHERE extname = 'vector';`

**MCP tools return "Discord bot not initialized":**
- Bot has 30s to connect on startup
- Check `DISCORD_BOT_TOKEN` is valid
- Restart Claude Code to reinitialize the MCP server

**Images not being processed:**
- Requires `IMAGE_MEMORY_ENABLED=true` plus DO Spaces credentials
- Check image size (max 10MB) and format (png, jpg, jpeg, gif, webp)
- Images are normalized (CMYK→RGB, EXIF stripped) before API calls

**Slash commands not appearing:**
- Commands sync on bot startup (check logs for "Synced X slash command(s)")
- May take up to 1 hour for Discord to propagate globally
- Requires `MEMORY_ENABLED=true` (commands only load with memory system)
- MCP-only mode (`enable_chat=False`) skips command sync to avoid wiping production commands

**Reminders not being delivered:**
- Scheduler runs every 60 seconds (check logs for "Reminder scheduler started")
- Verify migrations 010 and 011 are applied
- Check user hasn't blocked DMs (falls back to retry up to 5 times)
- Use `/remind list include_completed:true` to see failed reminders with error messages

## Claude Code MCP Configuration

Add to `~/.claude.json` (or Claude Code settings):

```json
{
  "mcpServers": {
    "slashAI": {
      "type": "stdio",
      "command": "python",
      "args": ["C:/Users/slash/Projects/slashAI/src/mcp_server.py"],
      "env": {
        "DISCORD_BOT_TOKEN": "your_token_here"
      }
    }
  }
}
```

## Files to Ignore

- `src/claude_client_new.py` - Experimental file, not used in production

## Documentation Structure

```
docs/
├── ARCHITECTURE.md          # High-level system design
├── MEMORY_TECHSPEC.md       # Text memory specification
├── MEMORY_PRIVACY.md        # Privacy model
├── MEMORY_IMAGES.md         # Image memory specification
├── MULTI_AGENT.md           # Plan for multi-bot architecture (Fabricord replacement)
├── PRD.md                   # Product requirements
├── enhancements/            # Feature specifications
│   ├── README.md            # Enhancement index and roadmap
│   ├── 001-007_*.md         # Implemented (memory, tools, analytics, reminders)
│   ├── 008_DATABASE_BACKUP.md     # Manual backup system
│   ├── 009_GITHUB_DOC_READER.md   # GitHub docs tool
│   ├── 010_HYBRID_SEARCH.md       # v0.10.0
│   ├── 011_CONFIDENCE_DECAY.md    # v0.10.1
│   └── 012-013_*.md               # Planned features
└── research/                # Background research
    ├── MEMVID_COMPARISON.md       # Memory system comparison
    └── MEMVID_LESSONS_ANALYSIS.md # Lessons learned
```

**Enhancement specs** contain implementation details, database migrations, and acceptance criteria for each feature. See `docs/enhancements/README.md` for the full index and roadmap.
