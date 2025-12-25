# Changelog

All notable changes to slashAI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Slash command support (`/ask`, `/summarize`, `/clear`)
- Persistent conversation memory (database-backed)
- Rate limiting and token budget management
- Multi-guild configuration support
- Webhook notifications for mentions

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
| 0.9.0 | 2025-12-25 | Initial release with Discord bot and MCP server |

---

## Upgrade Notes

### Migrating from Previous Versions

This is the initial release. No migration required.

### Breaking Changes

None (initial release).

---

[Unreleased]: https://github.com/mindfulent/slashAI/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/mindfulent/slashAI/releases/tag/v0.9.0
