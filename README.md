# slashAI

AI-powered Discord bot and MCP server for the Minecraft College community. Powered by Claude Sonnet 4.5.

## Overview

slashAI operates in two complementary modes:

1. **Discord Chatbot** - Mention `@slashAI` or DM the bot to have natural conversations powered by Claude Sonnet 4.5
2. **MCP Server** - Expose Discord operations as tools that Claude Code can invoke directly

## Features

### Chatbot Mode
- Natural conversation with Claude Sonnet 4.5's intelligence
- Per-user, per-channel conversation history (maintains context across messages)
- Custom personality tuned for the Minecraft College community
- Direct message support for private conversations
- Discord-native formatting (markdown, code blocks)

### MCP Server Mode
- **`send_message`** - Post messages to any channel the bot can access
- **`edit_message`** - Modify existing bot messages
- **`read_messages`** - Fetch recent message history from channels
- **`list_channels`** - Enumerate available text channels
- **`get_channel_info`** - Retrieve channel metadata (topic, category, etc.)

## Quick Start

### Prerequisites
- Python 3.10+
- Discord Bot Token ([create one here](https://discord.com/developers/applications))
- Anthropic API Key ([get one here](https://console.anthropic.com/settings/keys))

### Installation

```bash
# Clone the repository
git clone https://github.com/mindfulent/slashAI.git
cd slashAI

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your tokens
```

### Running the Bot

```bash
python src/discord_bot.py
```

The bot will connect to Discord and respond to:
- `@slashAI` mentions in any channel it can see
- Direct messages

### Using as MCP Server (Claude Code)

Add to your `~/.claude.json`:

```json
{
  "mcpServers": {
    "slashAI": {
      "type": "stdio",
      "command": "python",
      "args": ["/path/to/slashAI/src/mcp_server.py"],
      "env": {
        "DISCORD_BOT_TOKEN": "your_token_here"
      }
    }
  }
}
```

Then in Claude Code:
```
"Use slashAI to send 'Hello!' to channel 123456789"
"Read the last 10 messages from channel 123456789"
```

## Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** → name it "slashAI"
3. Navigate to **Bot** → **Reset Token** → copy the token
4. Enable **Privileged Gateway Intents**:
   - ✅ Message Content Intent
   - ✅ Server Members Intent (optional)
5. Go to **OAuth2** → **URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Permissions: Send Messages, Read Message History, View Channels
6. Use the generated URL to invite the bot to your server

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token from Developer Portal |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude access |

### Customizing the Personality

Edit the `DEFAULT_SYSTEM_PROMPT` in `src/claude_client.py` to customize how the bot responds. The current prompt is tuned for:
- Thoughtful, pragmatic responses with dry wit
- Technical depth (especially Minecraft automation, AI/ML)
- Direct communication without excessive enthusiasm
- Minimal emoji usage

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Claude Code                            │
│                    (MCP Client)                             │
└─────────────────────┬───────────────────────────────────────┘
                      │ stdio (JSON-RPC 2.0)
┌─────────────────────▼───────────────────────────────────────┐
│                  slashAI MCP Server                         │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Tools: send_message, edit_message, read_messages,  │   │
│  │         list_channels, get_channel_info             │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│  ┌────────────────────────▼────────────────────────────┐   │
│  │            Discord Bot (discord.py)                 │   │
│  │         Persistent WebSocket connection             │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│  ┌────────────────────────▼────────────────────────────┐   │
│  │           Claude Client (anthropic SDK)             │   │
│  │         Conversation history & API calls            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
                    Discord Gateway API
                            │
                            ▼
                    Anthropic Claude API
```

## Project Structure

```
slashAI/
├── src/
│   ├── __init__.py
│   ├── discord_bot.py      # Discord client, event handlers, chatbot logic
│   ├── mcp_server.py       # MCP server with tool definitions
│   └── claude_client.py    # Anthropic API wrapper, conversation management
├── docs/
│   ├── ARCHITECTURE.md     # High-level architecture overview
│   ├── TECHSPEC.md         # Detailed technical specification
│   └── PRD.md              # Product requirements document
├── .do/
│   └── app.yaml            # DigitalOcean App Platform config
├── .env.example            # Environment variable template
├── .gitignore
├── CHANGELOG.md            # Version history
├── CLAUDE.md               # Claude Code project instructions
├── Procfile                # Process definition for deployment
├── README.md               # This file
├── requirements.txt        # Python dependencies
└── runtime.txt             # Python version specification
```

## Deployment

slashAI is designed to run as a Worker on DigitalOcean App Platform (no HTTP endpoint needed—just a persistent process).

### Current Deployment

The bot runs as a worker component within the `minecraftcollege` app on DigitalOcean:
- **App**: minecraftcollege
- **Component**: discord-bot (Worker)
- **Region**: SFO

### Manual Deployment

```bash
# Using doctl CLI
doctl apps create --spec .do/app.yaml

# Or add to existing app as a worker component
doctl apps update <app-id> --spec .do/app.yaml
```

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Runtime | Python | 3.12 |
| Discord Client | discord.py | ≥2.3.0 |
| MCP Server | mcp (FastMCP) | ≥1.25.0 |
| Claude API | anthropic | ≥0.40.0 |
| Environment | python-dotenv | ≥1.0.0 |

## Cost Estimation

### Anthropic API (Claude Sonnet 4.5)
- Input: $3.00 per million tokens
- Output: $15.00 per million tokens
- Typical message: ~500 input tokens, ~200 output tokens
- **Cost per message**: ~$0.0045 (~$4.50 per 1,000 messages)

### DigitalOcean App Platform
- Worker (apps-s-1vcpu-0.5gb): ~$5/month

## Documentation

- [Architecture Overview](docs/ARCHITECTURE.md) - System design and component overview
- [Technical Specification](docs/TECHSPEC.md) - Detailed implementation documentation
- [Product Requirements](docs/PRD.md) - User stories and acceptance criteria
- [Changelog](CHANGELOG.md) - Version history and release notes

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude and the MCP protocol
- [discord.py](https://discordpy.readthedocs.io/) for the excellent Discord library
- The Minecraft College community for being the inspiration and first users
