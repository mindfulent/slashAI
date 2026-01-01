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
   - `retriever.py`: Voyage AI + pgvector similarity search
   - `updater.py`: ADD/MERGE logic for memory updates
   - `privacy.py`: dm/channel_restricted/guild_public/global levels
   - `manager.py`: Facade orchestrating all operations
   - `config.py`: Configurable thresholds (env-overridable)

5. **`src/memory/images/`** - Image memory system
   - `observer.py`: Pipeline entry point (moderation → analysis → storage → clustering)
   - `analyzer.py`: Claude Vision + Voyage multimodal embeddings
   - `clusterer.py`: Groups images into build clusters by similarity
   - `narrator.py`: Generates progression narratives
   - `storage.py`: DigitalOcean Spaces (S3-compatible)

## MCP Tools

| Tool | Parameters | Returns |
|------|------------|---------|
| `send_message` | `channel_id`, `content` | Message ID |
| `edit_message` | `channel_id`, `message_id`, `content` | Confirmation |
| `delete_message` | `channel_id`, `message_id` | Confirmation |
| `read_messages` | `channel_id`, `limit` (default 10, max 100) | Formatted message list |
| `search_messages` | `query`, `channel_id` (optional), `author` (optional), `limit` (default 10, max 50) | Matching messages with IDs |
| `list_channels` | `guild_id` (optional) | Channel list with IDs |
| `get_channel_info` | `channel_id` | Channel metadata dict |
| `describe_message_image` | `channel_id`, `message_id`, `prompt` (optional) | Vision analysis of image |

## Key Constants

| Constant | Value | Location |
|----------|-------|----------|
| `MODEL_ID` | `claude-sonnet-4-5-20250929` | `claude_client.py:40` |
| `MAX_HISTORY_LENGTH` | 20 messages | `claude_client.py:288` |
| `extraction_message_threshold` | 5 exchanges | `memory/config.py:38` |
| `similarity_threshold` | 0.3 | `memory/config.py:35` |
| `embedding_model` | `voyage-3.5-lite` | `memory/config.py:45` |
| `cluster_assignment_threshold` | 0.72 | `memory/config.py:91` |

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

## Development Notes

**Restart requirements:**
- `mcp_server.py` changes: Restart Claude Code
- `discord_bot.py` changes: Restart bot process
- `claude_client.py` changes: Apply on next message
- `src/memory/` changes: Restart bot process

**MCP server lifespan:** Bot starts on server init, has 30s connection timeout

**Memory system rollback:** Set `MEMORY_ENABLED=false` to fall back to v0.9.0 behavior (no memory)

**Release workflow:** When cutting a new release, always update:
1. `CHANGELOG.md` - Add version entry with date and changes
2. `README.md` - Update "Current Version" at top
3. `docs/MEMORY_*.md` - Update version numbers if memory-related
4. Create GitHub release with `gh release create vX.Y.Z`

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

## CLI Tools (v0.9.10+)

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
```

## Discord Slash Commands (v0.9.11+)

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

## Troubleshooting

**Bot doesn't respond to mentions:**
- Check `ANTHROPIC_API_KEY` is set (chatbot mode requires it)
- Verify bot has `message_content` intent enabled in Discord Developer Portal
- Check logs: `logger.setLevel(logging.DEBUG)` for verbose output

**Memory not being stored:**
- Ensure `MEMORY_ENABLED=true` and `DATABASE_URL` + `VOYAGE_API_KEY` are set
- Check migration status: all 8 migrations must be applied
- Verify pgvector extension is enabled: `SELECT * FROM pg_extension WHERE extname = 'vector';`

**MCP tools return "Discord bot not initialized":**
- Bot has 30s to connect on startup
- Check `DISCORD_BOT_TOKEN` is valid
- Restart Claude Code to reinitialize the MCP server

**Images not being processed:**
- Requires `IMAGE_MEMORY_ENABLED=true` plus DO Spaces credentials
- Check image size (max 10MB) and format (png, jpg, jpeg, gif, webp)

**Slash commands not appearing:**
- Commands sync on bot startup (check logs for "Synced X slash command(s)")
- May take up to 1 hour for Discord to propagate globally
- Requires `MEMORY_ENABLED=true` (commands only load with memory system)
- Try `/memories` in Discord to see if the command group is registered

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
