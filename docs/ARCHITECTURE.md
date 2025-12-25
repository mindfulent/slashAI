# slashAI Architecture

Discord chatbot + MCP server powered by Claude Sonnet 4.5.

## Overview

slashAI serves two purposes:
1. **MCP Server**: Exposes Discord operations as tools for Claude Code
2. **Chatbot**: Responds to Discord messages using Claude Sonnet 4.5

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      Claude Code                            │
│                    (MCP Client)                             │
└─────────────────────┬───────────────────────────────────────┘
                      │ stdio (JSON-RPC 2.0)
┌─────────────────────▼───────────────────────────────────────┐
│                  slashAI MCP Server                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Tools:                                                 │ │
│  │  • send_message(channel_id, content)                   │ │
│  │  • edit_message(channel_id, message_id, content)       │ │
│  │  • read_messages(channel_id, limit)                    │ │
│  │  • list_channels(guild_id)                             │ │
│  │  • get_channel_info(channel_id)                        │ │
│  └────────────────────────────────────────────────────────┘ │
│                         │                                   │
│  ┌──────────────────────▼─────────────────────────────────┐ │
│  │            Discord Bot (discord.py)                    │ │
│  │         Maintains persistent connection                │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                      │
                      ▼
              Discord API
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
- System prompt configuration
- Token usage tracking

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| MCP Server | mcp (FastMCP) | >=1.25.0 |
| Discord | discord.py | >=2.3.0 |
| Claude API | anthropic | >=0.40.0 |
| Python | Python | >=3.10 |

## Configuration

### Environment Variables (`.env`)

```env
DISCORD_BOT_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
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
- [ ] Per-user conversation memory with persistence
- [ ] Rate limiting and token budget management
- [ ] Multi-guild support with per-guild configuration
- [ ] Agent Skills integration for extended capabilities
- [ ] Webhook support for notifications
