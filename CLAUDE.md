# CLAUDE.md

## Project Overview

slashAI is a Discord chatbot and MCP server powered by Claude Sonnet 4.5.

**Two modes:**
1. **MCP Server**: Claude Code uses tools to control Discord (send/edit/read messages)
2. **Chatbot**: Discord users chat with Claude by mentioning the bot or DMing it

## Quick Start

```bash
cd slashAI

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your tokens
```

## Running

### As MCP Server (for Claude Code)
The MCP server is started automatically by Claude Code when configured.

### As Standalone Chatbot
```bash
python src/discord_bot.py
```

## Architecture

See `docs/ARCHITECTURE.md` for full documentation.

```
src/
├── mcp_server.py    # MCP tools (send_message, edit_message, read_messages, etc.)
├── discord_bot.py   # Discord client with chatbot functionality
└── claude_client.py # Anthropic API wrapper with conversation history
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `send_message` | Send a message to a Discord channel |
| `edit_message` | Edit an existing message |
| `read_messages` | Fetch recent messages from a channel |
| `list_channels` | List all accessible text channels |
| `get_channel_info` | Get details about a channel |

## Claude Code Configuration

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "slashAI": {
      "command": "python",
      "args": ["C:/Users/slash/Projects/slashAI/src/mcp_server.py"],
      "env": {
        "DISCORD_BOT_TOKEN": "your_token"
      }
    }
  }
}
```

## Key Patterns

- **Async everywhere**: Discord.py and MCP are both async
- **Conversation history**: Stored per (user_id, channel_id) pair
- **Token tracking**: ClaudeClient tracks usage for cost monitoring
- **Discord limits**: Responses capped at 2000 chars

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token |
| `ANTHROPIC_API_KEY` | For chatbot | Anthropic API key |

## Development

When modifying:
- `mcp_server.py`: Restart Claude Code to reload MCP server
- `discord_bot.py`: Restart the bot process
- `claude_client.py`: Changes apply on next message (no restart needed if imported fresh)
