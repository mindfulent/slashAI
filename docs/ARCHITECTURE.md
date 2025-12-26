# slashAI Architecture

Discord chatbot + MCP server powered by Claude Sonnet 4.5 with privacy-aware persistent memory.

## Overview

slashAI serves two purposes:
1. **MCP Server**: Exposes Discord operations as tools for Claude Code
2. **Chatbot**: Responds to Discord messages using Claude Sonnet 4.5 with persistent memory

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              slashAI v0.9.1                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐  │
│  │  Discord Bot    │───▶│  Claude Client  │───▶│  Memory Manager         │  │
│  │                 │    │                 │    │                         │  │
│  │  on_message()   │    │  chat()         │    │  - extract_topics()     │  │
│  │  _handle_chat() │    │  inject_memory()│    │  - retrieve_memories()  │  │
│  │                 │    │                 │    │  - update_memory()      │  │
│  │  [Passes channel│    │  [Passes channel│    │  - classify_privacy()   │  │
│  │   for privacy]  │    │   context]      │    │                         │  │
│  └─────────────────┘    └─────────────────┘    └───────────┬─────────────┘  │
│                                                            │                │
│  ┌─────────────────────────────────────────────────────────┼──────────────┐ │
│  │                         Data Layer                      │              │ │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌───────▼───────────┐  │ │
│  │  │  PostgreSQL     │    │  pgvector       │    │  Voyage AI        │  │ │
│  │  │  (memories w/   │◀──▶│  (semantic      │◀───│  (embeddings)     │  │ │
│  │  │  privacy_level) │    │   search)       │    │                   │  │ │
│  │  └─────────────────┘    └─────────────────┘    └───────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

MCP Server Flow:
Claude Code ──stdio──▶ mcp_server.py ──▶ discord_bot.py ──▶ Discord API

Chatbot Flow:
Discord User ──▶ discord_bot.py ──▶ claude_client.py ──▶ Anthropic API
                                           │
                                           ▼
                                   memory/ (retrieval + tracking)
```

## Components

### 1. MCP Server (`src/mcp_server.py`)

Uses the official MCP Python SDK with FastMCP for decorator-based tool definitions.

**Transport:** stdio (for Claude Code integration)

**Tools Exposed:**
| Tool | Description | Parameters |
|------|-------------|------------|
| `send_message` | Send a message to a channel | `channel_id`, `content` |
| `edit_message` | Edit an existing message | `channel_id`, `message_id`, `content` |
| `read_messages` | Fetch recent messages from a channel | `channel_id`, `limit` (default 10) |
| `list_channels` | List all channels in a guild | `guild_id` (optional) |
| `get_channel_info` | Get details about a channel | `channel_id` |

### 2. Discord Bot (`src/discord_bot.py`)

Persistent Discord connection using discord.py.

**Features:**
- Message event handling for chatbot functionality
- Exposes methods for MCP tools to invoke
- Maintains channel/guild cache for quick lookups

**Required Intents:**
- `message_content` - Read message content
- `guilds` - Access guild information
- `messages` - Receive message events

### 3. Claude Client (`src/claude_client.py`)

Anthropic API wrapper for chatbot responses.

**Model:** `claude-sonnet-4-5-20250929`
**Pricing:** $3/M input, $15/M output tokens

**Features:**
- Conversation history management (per channel or per user)
- Memory retrieval and injection into system prompt
- System prompt configuration
- Token usage tracking

### 4. Memory System (`src/memory/`)

Privacy-aware persistent memory using PostgreSQL + pgvector + Voyage AI.

**Components:**

| Module | Responsibility |
|--------|----------------|
| `config.py` | Configuration dataclass with defaults |
| `privacy.py` | Privacy level classification based on channel |
| `extractor.py` | LLM-based topic extraction from conversations |
| `retriever.py` | Vector search with privacy filtering |
| `updater.py` | ADD/MERGE memory operations |
| `manager.py` | Facade orchestrating all operations |

**Privacy Levels:**

| Level | Assigned When | Retrievable In |
|-------|---------------|----------------|
| `dm` | Conversation in DM | DMs only |
| `channel_restricted` | Role-gated channel | Same channel only |
| `guild_public` | Public channel | Any channel in same guild |
| `global` | Explicit, non-sensitive facts | Anywhere |

See `docs/MEMORY_TECHSPEC.md` and `docs/MEMORY_PRIVACY.md` for full documentation.

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| MCP Server | mcp (FastMCP) | >=1.25.0 |
| Discord | discord.py | >=2.3.0 |
| Claude API | anthropic | >=0.40.0 |
| Database | asyncpg + pgvector | >=0.29.0 |
| Embeddings | voyageai | >=0.3.0 |
| Python | Python | >=3.10 |

## Configuration

### Environment Variables (`.env`)

```env
# Required
DISCORD_BOT_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key

# Memory System (v0.9.1)
DATABASE_URL=postgresql://user:pass@host:5432/slashai
VOYAGE_API_KEY=pa-your_voyage_api_key
MEMORY_ENABLED=true
```

### Claude Code MCP Configuration

Add to `~/.claude.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "slashAI": {
      "command": "python",
      "args": ["C:/Users/slash/Projects/slashAI/src/mcp_server.py"],
      "env": {
        "DISCORD_BOT_TOKEN": "your_token_here"
      }
    }
  }
}
```

## Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Navigate to "Bot" section
4. Create a bot and copy the token
5. Enable these Privileged Gateway Intents:
   - Message Content Intent
   - Server Members Intent (optional)
6. Generate invite URL with scopes: `bot`, `applications.commands`
7. Required bot permissions:
   - Read Messages/View Channels
   - Send Messages
   - Read Message History
   - Embed Links (optional, for rich embeds)

## Running the Project

### As MCP Server (for Claude Code)

```bash
cd slashAI
python src/mcp_server.py
```

Claude Code will spawn this process automatically when configured.

### As Standalone Chatbot

```bash
cd slashAI
python src/discord_bot.py
```

## Research Sources

- [MCP Specification 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [discord.py Documentation](https://discordpy.readthedocs.io/en/stable/api.html)
- [Claude Sonnet 4.5 Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
- [Claude Code MCP Setup](https://code.claude.com/docs/en/mcp)

## Future Enhancements

- [ ] Slash command support (`/ask`, `/summarize`)
- [x] Per-user conversation memory with persistence (v0.9.1)
- [ ] Rate limiting and token budget management
- [ ] Multi-guild support with per-guild configuration
- [ ] Agent Skills integration for extended capabilities
- [ ] Webhook support for notifications
- [ ] User memory commands (`/memories`, `/forget`)
- [ ] Memory decay (Ebbinghaus-inspired)
