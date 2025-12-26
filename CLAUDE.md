# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

slashAI is a Discord chatbot and MCP server powered by Claude Sonnet 4.5 with privacy-aware persistent memory.

**Two modes:**
1. **MCP Server**: Claude Code uses tools to control Discord (send/edit/read messages)
2. **Chatbot**: Discord users chat with Claude by mentioning the bot or DMing it, with cross-session memory

## Commands

```bash
# Setup
python -m venv venv && venv\Scripts\activate  # Windows
pip install -r requirements.txt

# Run standalone chatbot
python src/discord_bot.py

# MCP server (started automatically by Claude Code when configured)
python src/mcp_server.py
```

## Architecture

The system has four components that work together:

1. **`mcp_server.py`** - FastMCP server exposing Discord tools via stdio transport
   - Uses `@mcp.tool()` decorators for tool definitions
   - Starts Discord bot via async lifespan context manager
   - Channel IDs passed as strings (converted to int internally)

2. **`discord_bot.py`** - discord.py Bot with dual responsibilities
   - Handles message events for chatbot (mentions and DMs)
   - Exposes async methods (`send_message`, `read_messages`, etc.) for MCP tools
   - Initializes database pool and memory manager on startup
   - Uses `_ready_event` for startup synchronization

3. **`claude_client.py`** - Anthropic API wrapper
   - Conversation history keyed by `(user_id, channel_id)` tuple
   - History capped at 20 messages per conversation
   - Integrates with MemoryManager for retrieval and tracking
   - Tracks cumulative token usage for cost monitoring

4. **`src/memory/`** - Privacy-aware persistent memory system
   - `config.py`: Configuration (top_k, thresholds, embedding model)
   - `privacy.py`: Privacy level classification based on channel
   - `extractor.py`: LLM-based topic extraction from conversations
   - `retriever.py`: Vector search with Voyage AI + pgvector
   - `updater.py`: ADD/MERGE logic (same privacy level only)
   - `manager.py`: Facade orchestrating all operations

**Data flow:**
```
Claude Code → stdio → mcp_server.py → discord_bot.py → Discord API

Discord User → discord_bot.py → claude_client.py → Anthropic API
                                       ↓
                               memory/ → PostgreSQL + pgvector
                                       ↓
                               Voyage AI (embeddings)
```

## MCP Tools

| Tool | Parameters | Returns |
|------|------------|---------|
| `send_message` | `channel_id`, `content` | Message ID |
| `edit_message` | `channel_id`, `message_id`, `content` | Confirmation |
| `read_messages` | `channel_id`, `limit` (max 100) | Formatted message list |
| `list_channels` | `guild_id` (optional) | Channel list with IDs |
| `get_channel_info` | `channel_id` | Channel metadata dict |

## Key Constants

- `MODEL_ID`: `claude-sonnet-4-5-20250929` (in `claude_client.py:14`)
- `MAX_HISTORY_LENGTH`: 20 messages per conversation
- Discord message limit: 2000 characters (enforced in system prompt)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token |
| `ANTHROPIC_API_KEY` | For chatbot | Anthropic API key |
| `DATABASE_URL` | For memory | PostgreSQL connection string |
| `VOYAGE_API_KEY` | For memory | Voyage AI API key for embeddings |
| `MEMORY_ENABLED` | No | Set to "true" to enable memory system |

## Development Notes

- **Restart requirements:**
  - `mcp_server.py` changes: Restart Claude Code
  - `discord_bot.py` changes: Restart bot process
  - `claude_client.py` changes: Apply on next message
  - `src/memory/` changes: Restart bot process

- **MCP server lifespan:** Bot starts on server init, has 30s connection timeout

- **Bot personality:** Configured in `DEFAULT_SYSTEM_PROMPT` in `claude_client.py`. Tuned for direct, technical communication with minimal emoji.

- **Memory system rollback:** Set `MEMORY_ENABLED=false` to fall back to v0.9.0 behavior

## Database Migrations

Run migrations in order to set up the memory system:

```bash
# Connect to your PostgreSQL database and run:
psql $DATABASE_URL -f migrations/001_enable_pgvector.sql
psql $DATABASE_URL -f migrations/002_create_memories.sql
psql $DATABASE_URL -f migrations/003_create_sessions.sql
psql $DATABASE_URL -f migrations/004_add_indexes.sql
```

## Memory Privacy Model

Memories are classified by privacy level based on their source channel:

| Level | Source | Retrievable In |
|-------|--------|----------------|
| `dm` | DM conversation | DMs only |
| `channel_restricted` | Role-gated channel | Same channel only |
| `guild_public` | Public channel | Any channel in same guild |
| `global` | Explicit facts (IGN, timezone) | Anywhere |

See `docs/MEMORY_TECHSPEC.md` and `docs/MEMORY_PRIVACY.md` for full documentation.
