<p align="center">
  <img src="docs/slashAI_icon.png" alt="slashAI" width="200">
</p>

# slashAI

AI-powered Discord bot and MCP server. Powered by Claude Sonnet 4.5 with privacy-aware persistent memory.

**Current Version:** 0.9.10

## Overview

slashAI operates in two complementary modes:

1. **Discord Chatbot** - Mention `@slashAI` or DM the bot to have natural conversations powered by Claude Sonnet 4.5
2. **MCP Server** - Expose Discord operations as tools that Claude Code can invoke directly

## Features

### Chatbot Mode
- Natural conversation with Claude Sonnet 4.5's intelligence
- Per-user, per-channel conversation history (maintains context across messages)
- Customizable personality via system prompt
- Direct message support for private conversations
- Discord-native formatting (markdown, code blocks)
- Automatic message chunking for responses exceeding Discord's 2000 character limit
- File attachment reading (.md, .txt, .py, etc.)

### Persistent Memory (v0.9.1+)
- **Cross-session memory** - Bot remembers facts, preferences, and context between conversations
- **Privacy-aware retrieval** - Four privacy levels (dm, channel_restricted, guild_public, global)
- **LLM-based extraction** - Automatic topic extraction after 5 message exchanges
- **Semantic search** - Voyage AI embeddings with pgvector for relevant memory retrieval
- **ADD/MERGE logic** - Intelligently updates existing memories vs creating new ones

### Image Memory (v0.9.2+)
- **Build tracking** - Recognizes and tracks Minecraft build projects over time
- **Visual analysis** - Claude Vision for structured image description and tagging
- **Multimodal embeddings** - Voyage multimodal-3 for semantic image similarity
- **Build clustering** - Automatically groups related images into project clusters
- **Progression narratives** - Generates stories about a user's build journey
- **Content moderation** - Active moderation for policy violations
- **Persistent storage** - DigitalOcean Spaces for permanent image storage

### MCP Server Mode
- **`send_message`** - Post messages to any channel the bot can access
- **`edit_message`** - Modify existing bot messages
- **`delete_message`** - Delete bot messages
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
| `ANTHROPIC_API_KEY` | For chatbot | Anthropic API key for Claude access |
| `DATABASE_URL` | For memory | PostgreSQL connection string with pgvector |
| `VOYAGE_API_KEY` | For memory | Voyage AI API key for embeddings |
| `MEMORY_ENABLED` | No | Set to "true" to enable text memory (v0.9.1+) |
| `IMAGE_MEMORY_ENABLED` | No | Set to "true" to enable image memory (v0.9.2+) |
| `DO_SPACES_KEY` | For images | DigitalOcean Spaces access key |
| `DO_SPACES_SECRET` | For images | DigitalOcean Spaces secret key |
| `DO_SPACES_BUCKET` | For images | Spaces bucket name (default: slashai-images) |
| `DO_SPACES_REGION` | For images | Spaces region (default: nyc3) |
| `IMAGE_MODERATION_ENABLED` | No | Set to "false" to disable content moderation |
| `MOD_CHANNEL_ID` | No | Discord channel ID for moderation alerts |

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
│   ├── claude_client.py    # Anthropic API wrapper, conversation management
│   └── memory/             # Memory system (v0.9.1+)
│       ├── __init__.py
│       ├── config.py       # Memory configuration
│       ├── privacy.py      # Privacy level classification
│       ├── extractor.py    # LLM topic extraction
│       ├── retriever.py    # Voyage AI + pgvector retrieval
│       ├── updater.py      # ADD/MERGE memory logic
│       ├── manager.py      # Memory system facade
│       └── images/         # Image memory (v0.9.2+)
│           ├── __init__.py
│           ├── observer.py     # Pipeline entry point
│           ├── analyzer.py     # Claude Vision + Voyage embeddings
│           ├── clusterer.py    # Build project grouping
│           ├── narrator.py     # Progression narratives
│           └── storage.py      # DO Spaces integration
├── migrations/             # Database migrations
│   ├── 001_enable_pgvector.sql
│   ├── 002_create_memories.sql
│   ├── 003_create_sessions.sql
│   ├── 004_add_indexes.sql
│   ├── 005_create_build_clusters.sql
│   ├── 006_create_image_observations.sql
│   └── 007_create_image_moderation_and_indexes.sql
├── scripts/                # CLI tools (v0.9.10+)
│   ├── migrate_memory_format.py  # Convert memories to pronoun-neutral format
│   └── memory_inspector.py       # Debug and inspect memory system
├── docs/
│   ├── ARCHITECTURE.md         # High-level architecture overview
│   ├── MEMORY_TECHSPEC.md      # Text memory specification
│   ├── MEMORY_PRIVACY.md       # Privacy model documentation
│   ├── MEMORY_IMAGES.md        # Image memory specification
│   └── PRD.md                  # Product requirements document
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
| Embeddings | voyageai | ≥0.3.0 |
| Vector Database | PostgreSQL + pgvector | ≥16 + 0.7 |
| Database Driver | asyncpg | ≥0.29.0 |
| Image Storage | boto3 (S3) | ≥1.35.0 |
| Image Processing | Pillow | ≥10.0.0 |
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
- [Memory Technical Spec](docs/MEMORY_TECHSPEC.md) - Text memory system design
- [Memory Privacy Model](docs/MEMORY_PRIVACY.md) - Privacy level classification
- [Image Memory Spec](docs/MEMORY_IMAGES.md) - Image memory system design
- [Product Requirements](docs/PRD.md) - User stories and acceptance criteria
- [Changelog](CHANGELOG.md) - Version history and release notes

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Soundtrack

Songs generated during the v0.9.1 and v0.9.2 coding sessions:

| Track | Description |
|-------|-------------|
| [01 Christmas Morning Build](docs/01%20Christmas%20Morning%20Build%20by%20Slash.mp3) | The initial v0.9.0 release, Christmas Day 2025 |
| [02 Why Can't I Remember](docs/02%20Why%20Can%27t%20I%20Remember%20by%20Slash.mp3) | Building the persistent memory system |
| [03 What You Showed Me](docs/03%20What%20You%20Showed%20Me%20by%20Slash.mp3) | Adding image understanding |

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude and the MCP protocol
- [discord.py](https://discordpy.readthedocs.io/) for the excellent Discord library
- The community for being the inspiration and first users
