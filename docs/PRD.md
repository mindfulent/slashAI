# Product Requirements Document (PRD)

## Document Information

| Field | Value |
|-------|-------|
| Product | slashAI |
| Version | 0.9.0 |
| Last Updated | 2025-12-25 |
| Status | Implemented |
| Owner | Minecraft College |

---

## 1. Executive Summary

slashAI is an AI-powered Discord bot that brings Claude Sonnet 4.5's intelligence to the Minecraft College community. It enables natural conversations through Discord mentions and provides programmatic Discord control via the Model Context Protocol (MCP) for integration with Claude Code.

### 1.1 Vision

Create an AI assistant that feels like a knowledgeable community member—one who understands Minecraft's technical depth, can engage in meaningful conversations, and serves as a bridge between Discord and development workflows.

### 1.2 Goals

1. **Enhance community engagement** through intelligent, personality-driven conversations
2. **Bridge Discord and development tools** via MCP integration with Claude Code
3. **Maintain low friction** for users—no commands to learn, just @mention and chat

---

## 2. User Personas

### 2.1 Community Member (Discord User)

**Profile**: Active Minecraft College Discord member who plays on the server

**Needs**:
- Quick answers to Minecraft questions
- Casual conversation with an AI that "gets" the community
- Technical help with mods, automation, redstone

**Pain Points**:
- Generic AI assistants don't understand Minecraft context
- Waiting for human experts to answer questions

### 2.2 Developer (Claude Code User)

**Profile**: Developer using Claude Code who wants to interact with Discord programmatically

**Needs**:
- Send messages to Discord channels without leaving the IDE
- Read Discord conversations for context
- Automate Discord notifications from development workflows

**Pain Points**:
- Context switching between Discord and development environment
- No easy way to programmatically post to Discord from Claude Code

### 2.3 Server Administrator

**Profile**: Manages the Minecraft College Discord server and community

**Needs**:
- Reliable bot that doesn't require constant maintenance
- Clear usage patterns and cost visibility
- Easy deployment and configuration

**Pain Points**:
- Bots that go offline unexpectedly
- Unexpected API costs from chatty bots

---

## 3. User Stories & Acceptance Criteria

### 3.1 Discord Chatbot Features

---

#### US-001: Mention-Based Conversation

**As a** Discord community member
**I want to** @mention the bot to start a conversation
**So that** I can get AI-powered help without leaving Discord

**Acceptance Criteria**:
- [ ] When I type `@slashAI [question]` in any channel the bot can see, the bot responds
- [ ] The response appears as a reply to my message (threaded)
- [ ] The bot shows a typing indicator while generating the response
- [ ] The response is under 2000 characters (Discord's limit)
- [ ] If the response would exceed 2000 characters, it is truncated gracefully

**Technical Notes**:
- Trigger: `message.mentions` contains bot user OR `@slashAI` in content
- Handler: `discord_bot.py:on_message()` → `_handle_chat()`

---

#### US-002: Direct Message Conversation

**As a** Discord user
**I want to** DM the bot directly
**So that** I can have private conversations without @mentioning

**Acceptance Criteria**:
- [ ] When I send a DM to slashAI, the bot responds
- [ ] No @mention required in DMs
- [ ] Conversation history is maintained separately from channel conversations
- [ ] The bot ignores its own messages (no infinite loops)

**Technical Notes**:
- Trigger: `isinstance(message.channel, discord.DMChannel)`
- History key: `(user_id, dm_channel_id)`

---

#### US-003: Conversation Context

**As a** Discord user having a multi-turn conversation
**I want to** have my previous messages remembered
**So that** I can have natural back-and-forth discussions

**Acceptance Criteria**:
- [ ] The bot remembers up to 20 previous messages in our conversation
- [ ] Context is maintained per user per channel (different channels = different contexts)
- [ ] The bot can reference things I said earlier in the conversation
- [ ] Old messages are automatically trimmed when limit is exceeded (FIFO)

**Technical Notes**:
- Storage: `ClaudeClient._conversations[user_id, channel_id]`
- Limit: `MAX_HISTORY_LENGTH = 20`

---

#### US-004: Custom Personality

**As a** Minecraft College community member
**I want to** interact with a bot that has a distinct, community-appropriate personality
**So that** conversations feel natural and aligned with our community vibe

**Acceptance Criteria**:
- [ ] The bot uses dry wit rather than excessive enthusiasm
- [ ] The bot demonstrates knowledge of Minecraft technical topics
- [ ] The bot gives direct answers without unnecessary fluff
- [ ] The bot uses minimal emojis (occasional for emphasis only)
- [ ] The bot admits when it doesn't know something rather than making things up

**Technical Notes**:
- Configured via `DEFAULT_SYSTEM_PROMPT` in `claude_client.py`
- Personality traits defined in system prompt sections

---

#### US-005: Error Handling in Chat

**As a** Discord user
**I want to** receive clear error messages when something goes wrong
**So that** I understand what happened and what to do next

**Acceptance Criteria**:
- [ ] If the Anthropic API fails, the bot replies with an error message
- [ ] If the bot isn't properly configured, it notifies the user
- [ ] Error messages are user-friendly (not stack traces)
- [ ] The bot continues functioning after errors (doesn't crash)

**Technical Notes**:
- Try/catch in `_handle_chat()` method
- Graceful error messages via `message.reply()`

---

### 3.2 MCP Server Features

---

#### US-010: Send Message via MCP

**As a** Claude Code user
**I want to** send messages to Discord channels using natural language
**So that** I can post updates without switching to Discord

**Acceptance Criteria**:
- [ ] Claude Code can invoke `send_message(channel_id, content)` tool
- [ ] The message appears in the specified Discord channel
- [ ] The tool returns confirmation with the message ID
- [ ] Invalid channel IDs return a clear error message

**Example Usage**:
```
"Use slashAI to send 'Build complete!' to channel 123456789"
```

**Technical Notes**:
- Tool: `mcp_server.py:send_message()`
- Returns: `"Message sent successfully. Message ID: {id}"`

---

#### US-011: Edit Message via MCP

**As a** Claude Code user
**I want to** edit previously sent bot messages
**So that** I can correct mistakes or update status messages

**Acceptance Criteria**:
- [ ] Claude Code can invoke `edit_message(channel_id, message_id, content)` tool
- [ ] The specified message is updated with new content
- [ ] Only messages sent by the bot can be edited
- [ ] Invalid message IDs return a clear error message

**Example Usage**:
```
"Edit message 987654321 in channel 123456789 to say 'Build failed'"
```

**Technical Notes**:
- Tool: `mcp_server.py:edit_message()`
- Requires both channel_id and message_id

---

#### US-012: Read Messages via MCP

**As a** Claude Code user
**I want to** read recent messages from a Discord channel
**So that** I can understand conversation context without switching apps

**Acceptance Criteria**:
- [ ] Claude Code can invoke `read_messages(channel_id, limit)` tool
- [ ] Returns up to `limit` recent messages (default 10, max 100)
- [ ] Each message includes timestamp, author name, and content
- [ ] Messages are formatted in a readable text format
- [ ] Empty channels return "No messages found" message

**Example Usage**:
```
"Read the last 5 messages from channel 123456789"
```

**Technical Notes**:
- Tool: `mcp_server.py:read_messages()`
- Format: `[{timestamp}] {author}: {content}`

---

#### US-013: List Channels via MCP

**As a** Claude Code user
**I want to** see what Discord channels the bot can access
**So that** I know valid channel IDs for other operations

**Acceptance Criteria**:
- [ ] Claude Code can invoke `list_channels(guild_id?)` tool
- [ ] Returns list of text channels with IDs and names
- [ ] Optionally filter by guild_id
- [ ] If no guild_id provided, lists channels from all guilds
- [ ] Each entry shows: `[{id}] #{name} (in {guild_name})`

**Example Usage**:
```
"List all Discord channels slashAI can access"
```

**Technical Notes**:
- Tool: `mcp_server.py:list_channels()`
- Filters to `discord.TextChannel` only

---

#### US-014: Get Channel Info via MCP

**As a** Claude Code user
**I want to** get detailed information about a specific channel
**So that** I can understand the channel context before posting

**Acceptance Criteria**:
- [ ] Claude Code can invoke `get_channel_info(channel_id)` tool
- [ ] Returns channel metadata: name, topic, guild, category, position, NSFW status
- [ ] Invalid channel IDs return a clear error message

**Example Usage**:
```
"Get info about channel 123456789"
```

**Technical Notes**:
- Tool: `mcp_server.py:get_channel_info()`
- Returns dict formatted as key-value lines

---

### 3.3 Infrastructure & Operations

---

#### US-020: Persistent Bot Operation

**As a** server administrator
**I want to** have the bot run reliably without intervention
**So that** community members can depend on it being available

**Acceptance Criteria**:
- [ ] Bot automatically reconnects if Discord connection drops
- [ ] Bot survives temporary API outages without crashing
- [ ] Deployment is automatic on git push to main branch
- [ ] Bot starts successfully after deployment (no manual intervention)

**Technical Notes**:
- discord.py handles reconnection automatically
- DigitalOcean App Platform with `deploy_on_push: true`

---

#### US-021: Secure Credential Management

**As a** server administrator
**I want to** keep API keys and tokens secure
**So that** credentials aren't exposed in code or logs

**Acceptance Criteria**:
- [ ] Credentials are stored as environment variables, not in code
- [ ] `.env` file is in `.gitignore` and never committed
- [ ] Deployment config marks credentials as SECRET (encrypted)
- [ ] Credentials don't appear in deployment logs

**Technical Notes**:
- Local: `.env` file loaded by python-dotenv
- Production: DO App Platform encrypted environment variables

---

#### US-022: Cost Visibility

**As a** server administrator
**I want to** understand and monitor API costs
**So that** I can budget appropriately and catch runaway usage

**Acceptance Criteria**:
- [ ] Token usage is tracked per session
- [ ] Cost estimation is available programmatically
- [ ] Pricing formula: $3/M input tokens + $15/M output tokens

**Technical Notes**:
- Tracked in `ClaudeClient.get_usage_stats()`
- Currently in-memory only (resets on restart)

---

### 3.4 Local Development

---

#### US-030: Local Bot Development

**As a** developer
**I want to** run the bot locally for testing
**So that** I can develop and debug without affecting production

**Acceptance Criteria**:
- [ ] Bot runs locally with `python src/discord_bot.py`
- [ ] Local `.env` file is used for configuration
- [ ] Can test both chatbot and MCP functionality locally
- [ ] Only one instance (local OR production) should run at a time to avoid duplicate responses

**Technical Notes**:
- Virtual environment: `venv/Scripts/activate`
- Ensure production is stopped when testing locally

---

#### US-031: MCP Development with Claude Code

**As a** developer
**I want to** test MCP tools with Claude Code locally
**So that** I can verify tool functionality before deployment

**Acceptance Criteria**:
- [ ] MCP server can be added to local Claude Code config
- [ ] All 5 tools appear in Claude Code's tool list
- [ ] Tools work correctly when invoked by Claude Code
- [ ] Bot initializes within 30 seconds of MCP server start

**Technical Notes**:
- Config: `~/.claude.json` → `mcpServers.slashAI`
- Lifespan timeout: 30 seconds for bot ready

---

## 4. Non-Functional Requirements

### 4.1 Performance

| Metric | Target | Notes |
|--------|--------|-------|
| Response latency | < 10 seconds | For typical messages |
| Bot startup time | < 30 seconds | Until ready for messages |
| Memory usage | < 512 MB | Fits in smallest DO instance |

### 4.2 Reliability

| Metric | Target | Notes |
|--------|--------|-------|
| Uptime | 99% monthly | Standard DO App Platform SLA |
| Auto-recovery | Automatic | discord.py handles reconnection |

### 4.3 Scalability

| Metric | Current | Notes |
|--------|---------|-------|
| Concurrent guilds | ~10 | Single instance, no sharding |
| Conversations | In-memory | Resets on restart |

---

## 5. Out of Scope (v0.9.0)

The following are explicitly NOT included in v0.9.0:

1. **Slash commands** - Native Discord slash commands (`/ask`, `/clear`)
2. **Persistent memory** - Database-backed conversation storage
3. **Rate limiting** - Per-user token budgets or cooldowns
4. **Multi-model support** - Only Claude Sonnet 4.5 supported
5. **Voice channel support** - Text channels only
6. **Image generation** - Text responses only
7. **Webhook notifications** - No outbound webhooks
8. **Admin commands** - No `!clear`, `!stats`, etc.

---

## 6. Success Metrics

### 6.1 Adoption

- [ ] Bot is active in Minecraft College Discord
- [ ] At least 10 unique users interact with the bot in first week

### 6.2 Engagement

- [ ] Average conversation length > 2 messages
- [ ] Users return for multiple conversations

### 6.3 Reliability

- [ ] No unplanned downtime in first month
- [ ] Zero duplicate responses (no multi-instance issues)

---

## 7. Release Criteria

### 7.1 MVP Checklist (v0.9.0)

- [x] Bot connects to Discord successfully
- [x] Bot responds to @mentions
- [x] Bot responds to DMs
- [x] Conversation history works
- [x] Custom personality is implemented
- [x] MCP server exposes all 5 tools
- [x] Tools work when invoked from Claude Code
- [x] Deployed to DigitalOcean successfully
- [x] Documentation complete (README, TECHSPEC, PRD)

---

## 8. Future Roadmap

### v0.9.1 (Current)
- Privacy-aware persistent memory with PostgreSQL + pgvector
- Voyage AI embeddings for semantic search
- Four privacy levels: dm, channel_restricted, guild_public, global

### v1.0.0 (Planned)
- Slash command support (`/ask`, `/summarize`)
- User memory commands (`/memories`, `/forget`)
- Basic usage analytics

### v1.1.0 (Planned)
- Rate limiting per user
- Admin commands for moderation
- Webhook notifications

### v2.0.0 (Future)
- Multi-model support (Haiku for simple queries, Opus for complex)
- Voice channel transcription
- Integration with Minecraft server events
