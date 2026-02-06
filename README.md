<p align="center">
  <img src="docs/slashAI_icon.png" alt="slashAI" width="200">
</p>

# slashAI

AI-powered Discord bot and MCP server. Powered by Claude Sonnet 4.5 with privacy-aware persistent memory.

**Current Version:** 0.12.0

## Overview

slashAI operates in two complementary modes:

1. **Discord Chatbot** - Mention `@slashAI` or DM the bot to have natural conversations powered by Claude Sonnet 4.5
2. **MCP Server** - Expose Discord operations as tools that Claude Code can invoke directly

The core features (AI chat, memory, MCP tools, reminders) are **general-purpose** and work for any Discord community. The codebase also includes **optional extensions** built for [The Block Academy](https://theblock.academy) Minecraft community, which serve as examples of community-specific integrations.

## Core Features

These features are general-purpose and work for any Discord community.

### Chatbot Mode
- Natural conversation with Claude Sonnet 4.5's intelligence
- Per-user, per-channel conversation history (maintains context across messages)
- Customizable personality via system prompt
- Direct message support for private conversations
- Discord-native formatting (markdown, code blocks)
- Automatic message chunking for responses exceeding Discord's 2000 character limit
- File attachment reading (.md, .txt, .py, etc.)

### Persistent Memory
- **Cross-session memory** - Bot remembers facts, preferences, and context between conversations
- **Privacy-aware retrieval** - Four privacy levels (dm, channel_restricted, guild_public, global)
- **Hybrid search** - Combines lexical (full-text) and semantic (vector) search
- **LLM-based extraction** - Automatic topic extraction after 5 message exchanges
- **ADD/MERGE logic** - Intelligently updates existing memories vs creating new ones
- **Memory attribution** - Clear indication of WHO each memory belongs to
- **Confidence decay** - Old episodic memories decay over time; frequently-accessed ones persist

### Memory Management
Users can view and manage their memories via Discord slash commands:
- `/memories list` - Browse stored memories with pagination and privacy filters
- `/memories search <query>` - Search memories by text
- `/memories mentions` - See public memories from others that mention you
- `/memories view <id>` - View full memory details
- `/memories delete <id>` - Remove a memory (with confirmation)
- `/memories stats` - View memory statistics

All command responses are private (ephemeral). Users can only delete their own memories.

### Scheduled Reminders
Set reminders that get delivered via DM at a specified time:
- **Natural language** - "remind me in 2 hours", "tomorrow at 10am", "next Monday 3pm"
- **Recurring** - "every weekday at 9am", "every 2 hours", full CRON expressions
- **Slash commands** - `/remind set`, `/remind list`, `/remind cancel`, `/remind timezone`
- **Timezone support** - Each user can set their preferred timezone
- **Channel delivery** - Bot owner can set reminders that post to specific channels

### Agentic Tools (Owner-Only)
The bot owner can trigger Discord actions directly through chat:
- Ask the bot to "post in #announcements" via DM
- Request message edits or deletions
- Read messages from other channels for context
- Describe images from past messages (fetches and analyzes via Vision API)
- Search memories explicitly ("what do you remember about X?")
- All actions require explicit owner request (never automatic)

Set `OWNER_ID` environment variable to your Discord user ID to enable.

### MCP Server Mode
Expose Discord operations as tools for Claude Code:
- **`send_message`** - Post messages to any channel the bot can access
- **`edit_message`** - Modify existing bot messages
- **`delete_message`** - Delete bot messages
- **`read_messages`** - Fetch recent message history from channels
- **`search_messages`** - Search messages with optional channel/author filters
- **`list_channels`** - Enumerate available text channels
- **`get_channel_info`** - Retrieve channel metadata (topic, category, etc.)

### Analytics (Owner-Only)
Track bot usage, performance, and errors:
- `/analytics summary` - 24-hour overview (messages, users, tokens, errors)
- `/analytics dau` - Daily active users over time
- `/analytics tokens` - Token usage and cost estimates
- CLI tool: `scripts/analytics_query.py` for command-line queries

---

## The Block Academy Extensions

These modules are specific to [The Block Academy](https://theblock.academy) Minecraft community. They demonstrate how to extend slashAI for community-specific use cases and can be removed or replaced for other deployments.

### Image Memory (Minecraft Builds)
- **Build tracking** - Recognizes and tracks Minecraft build projects over time
- **Visual analysis** - Claude Vision for structured image description and tagging
- **Multimodal embeddings** - Voyage multimodal-3 for semantic image similarity
- **Build clustering** - Automatically groups related images into project clusters
- **Progression narratives** - Generates stories about a user's build journey
- **Content moderation** - Active moderation for policy violations

### Core Curriculum Recognition System
AI-assisted build reviews for the recognition program:
- **Vision-based analysis** - Claude evaluates builds for technical skill and style
- **Feedback generation** - Constructive, encouraging feedback for each submission
- **DM approval flow** - Players approve before public announcement
- **Discord announcements** - Multi-image embeds with BlueMap coordinate links
- **Nomination processing** - Peer nominations with anti-gaming checks
- **Server event webhooks** - Gamemode changes, title grants, attendance credits

### Account Linking
- `/verify <code>` - Link Discord to Minecraft using a code from in-game `/discord link`
- Enables DM notifications when builds are reviewed

### StreamCraft Commands (Owner-Only)
View StreamCraft video conferencing license and usage data:
- `/streamcraft licenses` - List all licenses
- `/streamcraft player <name>` - Player usage lookup
- `/streamcraft servers` - Per-server usage summary
- `/streamcraft active` - Currently active streams

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

**Core (required for basic operation):**

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token from Developer Portal |
| `ANTHROPIC_API_KEY` | For chatbot | Anthropic API key for Claude access |
| `DATABASE_URL` | For memory | PostgreSQL connection string with pgvector |
| `VOYAGE_API_KEY` | For memory | Voyage AI API key for embeddings |
| `MEMORY_ENABLED` | No | Set to "true" to enable text memory |
| `OWNER_ID` | No | Discord user ID for owner-only features |
| `ANALYTICS_ENABLED` | No | Set to "true" to enable usage analytics |

**TBA Extensions (optional, for The Block Academy features):**

| Variable | Required | Description |
|----------|----------|-------------|
| `IMAGE_MEMORY_ENABLED` | No | Set to "true" to enable image memory |
| `DO_SPACES_KEY` | For images | DigitalOcean Spaces access key |
| `DO_SPACES_SECRET` | For images | DigitalOcean Spaces secret key |
| `DO_SPACES_BUCKET` | For images | Spaces bucket name |
| `RECOGNITION_API_URL` | For recognition | Core Curriculum API URL |
| `RECOGNITION_API_KEY` | For recognition | API key for recognition webhooks |
| `RECOGNITION_ANNOUNCEMENTS_CHANNEL` | No | Channel for build announcements |

### Customizing the Personality

Edit the `DEFAULT_SYSTEM_PROMPT` in `src/claude_client.py` to customize how the bot responds. You can tune it for your community's tone, interests, and communication style.

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
│   ├── discord_bot.py          # Discord client, event handlers, chatbot logic
│   ├── mcp_server.py           # MCP server with tool definitions
│   ├── claude_client.py        # Anthropic API wrapper, conversation management
│   ├── analytics.py            # Usage tracking and metrics
│   │
│   ├── commands/               # Discord slash commands
│   │   ├── memory_commands.py      # /memories command group
│   │   ├── reminder_commands.py    # /remind command group
│   │   ├── analytics_commands.py   # /analytics (owner-only)
│   │   ├── link_commands.py        # /verify (TBA-specific)
│   │   ├── streamcraft_commands.py # /streamcraft (TBA-specific)
│   │   └── views.py                # Pagination and confirmation UIs
│   │
│   ├── memory/                 # Core memory system
│   │   ├── extractor.py            # LLM topic extraction
│   │   ├── retriever.py            # Hybrid search (lexical + semantic)
│   │   ├── updater.py              # ADD/MERGE memory logic
│   │   ├── decay.py                # Confidence decay background job
│   │   └── images/                 # Image memory (TBA-specific)
│   │       ├── observer.py             # Pipeline entry point
│   │       ├── analyzer.py             # Claude Vision + Voyage embeddings
│   │       ├── clusterer.py            # Build project grouping
│   │       └── storage.py              # DO Spaces integration
│   │
│   ├── reminders/              # Scheduled reminders
│   │   ├── time_parser.py          # Natural language + CRON parsing
│   │   ├── manager.py              # Database operations
│   │   └── scheduler.py            # Background delivery loop
│   │
│   ├── recognition/            # Core Curriculum integration (TBA-specific)
│   │   ├── analyzer.py             # Vision-based build analysis
│   │   ├── feedback.py             # Feedback generation
│   │   ├── nominations.py          # Nomination review
│   │   ├── api.py                  # Recognition API client
│   │   └── scheduler.py            # Background processing
│   │
│   └── tools/                  # Agentic tools
│       └── github_docs.py          # Read slashAI docs from GitHub
│
├── migrations/                 # Database migrations (001-013)
├── scripts/                    # CLI tools
│   ├── memory_inspector.py         # Debug memory system
│   ├── analytics_query.py          # Query analytics
│   ├── backup_db.py                # Trigger database backups
│   └── memory_decay_cli.py         # Manage confidence decay
│
├── docs/                       # Documentation
│   ├── ARCHITECTURE.md
│   ├── MEMORY_TECHSPEC.md
│   ├── MEMORY_PRIVACY.md
│   └── enhancements/               # Feature specifications
│
├── .do/app.yaml                # DigitalOcean App Platform config
├── CHANGELOG.md                # Version history
├── CLAUDE.md                   # Claude Code project instructions
└── requirements.txt            # Python dependencies
```

**Note:** Modules marked `(TBA-specific)` can be removed for deployments outside The Block Academy.

## Deployment

slashAI is designed to run as a Worker process (no HTTP endpoint needed for core features—just a persistent connection to Discord).

### DigitalOcean App Platform

```bash
# Using doctl CLI
doctl apps create --spec .do/app.yaml

# Or add to existing app as a worker component
doctl apps update <app-id> --spec .do/app.yaml
```

### Other Platforms

Any platform that can run a persistent Python process works:
- **Railway**, **Render**, **Fly.io** - Use `python src/discord_bot.py` as the start command
- **VPS/Docker** - Run directly or containerize with the included dependencies
- **Local** - Great for development; just run `python src/discord_bot.py`

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

### Prompt Caching (v0.9.13+)
The base system prompt (~1,100 tokens) is cached using Anthropic's prompt caching:
- Cache write: 25% of input price ($0.75/M tokens)
- Cache read: 10% of input price ($0.30/M tokens)
- **Effective savings**: 15-20% reduction in input token costs on cache hits
- Cache expires after 5 minutes of inactivity per conversation

### DigitalOcean App Platform
- Worker (apps-s-1vcpu-0.5gb): ~$5/month

## Documentation

### Core Documentation
- [Architecture Overview](docs/ARCHITECTURE.md) - System design and component overview
- [Memory Technical Spec](docs/MEMORY_TECHSPEC.md) - Text memory system design
- [Memory Privacy Model](docs/MEMORY_PRIVACY.md) - Privacy level classification
- [Image Memory Spec](docs/MEMORY_IMAGES.md) - Image memory system design
- [Product Requirements](docs/PRD.md) - User stories and acceptance criteria
- [Changelog](CHANGELOG.md) - Version history and release notes

### Enhancement Specs
- [Enhancement Index](docs/enhancements/README.md) - Feature roadmap and status
- Individual specs in `docs/enhancements/` (001-013) cover implemented and planned features

### Research
- [Memvid Comparison](docs/research/MEMVID_COMPARISON.md) - Memory system comparison analysis

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

## 📄 License

slashAI is dual-licensed:

| License | Use Case | Obligations |
|---------|----------|-------------|
| **AGPL-3.0** | Open source, personal, internal | Source disclosure for network-accessible deployments |
| **Commercial** | Proprietary, SaaS, closed-source | No copyleft requirements |

### Open Source Use

Free under [AGPL-3.0](LICENSE). You can use, modify, and distribute slashAI, but:
- Modifications must be AGPL-3.0 licensed
- Network users must be offered source code access
- Copyright notices must be preserved

### Commercial Licensing

Need to keep your modifications private or avoid AGPL obligations?

📧 **Contact**: Slash Daemon
🔗 **Email**: slashdaemon@protonmail.com

### Contributing

Contributions are welcome! By submitting a PR, you agree to our [Contributor License Agreement](CLA.md).

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude and the MCP protocol
- [discord.py](https://discordpy.readthedocs.io/) for the excellent Discord library
- The community for being the inspiration and first users
