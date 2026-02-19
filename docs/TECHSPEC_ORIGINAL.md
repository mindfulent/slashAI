# Technical Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.9.0 |
| Last Updated | 2025-12-25 |
| Status | Implemented |

---

## 1. System Overview

slashAI is a dual-mode application that functions as both a Discord chatbot and an MCP (Model Context Protocol) server. It enables AI-powered conversations in Discord and allows Claude Code to programmatically interact with Discord channels.

### 1.1 Design Principles

1. **Async-first**: All I/O operations use Python's asyncio for non-blocking execution
2. **Separation of concerns**: Discord client, Claude API, and MCP server are loosely coupled
3. **Stateless deployment**: Conversation state is ephemeral (in-memory only)
4. **Fail gracefully**: Errors in one component don't crash the entire system

### 1.2 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           slashAI Application                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐      │
│  │   MCP Server    │    │  Discord Bot    │    │  Claude Client  │      │
│  │  (mcp_server)   │───▶│ (discord_bot)   │───▶│ (claude_client) │      │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘      │
│           │                      │                      │                │
│           │ FastMCP              │ discord.py           │ anthropic      │
│           │ stdio transport      │ WebSocket            │ HTTPS          │
│           ▼                      ▼                      ▼                │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐      │
│  │  Claude Code    │    │  Discord API    │    │  Anthropic API  │      │
│  │  (MCP Client)   │    │  Gateway        │    │  Messages       │      │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘      │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Specifications

### 2.1 Discord Bot (`src/discord_bot.py`)

#### 2.1.1 Class: `DiscordBot`

Extends `discord.ext.commands.Bot` to provide both command handling and chatbot functionality.

```python
class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)
```

#### 2.1.2 Discord Intents

| Intent | Purpose | Required |
|--------|---------|----------|
| `message_content` | Read message text content | Yes |
| `guilds` | Access guild/server information | Yes |
| `messages` | Receive message events | Yes |
| `members` | Access member information | No (optional) |

#### 2.1.3 Event Handlers

| Event | Handler | Description |
|-------|---------|-------------|
| `on_ready` | `async def on_ready()` | Logs connection status, sets ready flag |
| `on_message` | `async def on_message(message)` | Routes messages to chatbot or command handler |

#### 2.1.4 Chatbot Trigger Conditions

The bot responds when:
1. The bot is @mentioned in a message, OR
2. The message is in a DM channel (`discord.DMChannel`)

Messages from the bot itself are ignored to prevent loops.

#### 2.1.5 MCP Tool Methods

These methods are invoked by the MCP server to perform Discord operations:

| Method | Signature | Returns |
|--------|-----------|---------|
| `send_message` | `async def send_message(channel_id: int, content: str)` | `discord.Message` |
| `edit_message` | `async def edit_message(channel_id: int, message_id: int, content: str)` | `discord.Message` |
| `read_messages` | `async def read_messages(channel_id: int, limit: int)` | `list[discord.Message]` |
| `list_channels` | `async def list_channels(guild_id: Optional[int])` | `list[discord.TextChannel]` |
| `get_channel_info` | `async def get_channel_info(channel_id: int)` | `dict` |

---

### 2.2 Claude Client (`src/claude_client.py`)

#### 2.2.1 Class: `ClaudeClient`

Async wrapper for the Anthropic Messages API with conversation management.

```python
class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = MODEL_ID,
    )
```

#### 2.2.2 Model Configuration

| Parameter | Value | Notes |
|-----------|-------|-------|
| Model ID | `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| Max Tokens | 1024 | Per response |
| Context Window | 200K tokens | Model limit |

#### 2.2.3 Conversation Management

Conversations are keyed by `(user_id, channel_id)` tuple:

```python
ConversationHistory:
    messages: list[dict]  # {"role": "user"|"assistant", "content": str}

    def add_message(role: str, content: str) -> None
    def get_messages() -> list[dict]
    def clear() -> None
```

**History Limit**: 20 messages per conversation (older messages are trimmed)

#### 2.2.4 Token Tracking

```python
def get_usage_stats() -> dict:
    return {
        "total_input_tokens": int,
        "total_output_tokens": int,
        "estimated_cost_usd": float,  # ($3/M input + $15/M output)
    }
```

#### 2.2.5 System Prompt Structure

The system prompt defines the bot's personality and behavior:

```
## Personality
- Thoughtful pragmatist with dry wit
- Engineer's directness, professional when needed

## Interests & Knowledge
- Minecraft technical (automation, modpacks, AI systems)
- AI/ML (practical and philosophical)
- Building/making things

## Communication Style
- Direct, solutions-oriented
- Detailed when deserved, punchy when not
- Minimal emojis, technical precision

## What You're Not
- Not a cheerleader (no excessive enthusiasm)
- Not condescending
- Not evasive
- Not generic
```

---

### 2.3 MCP Server (`src/mcp_server.py`)

#### 2.3.1 Server Initialization

Uses FastMCP with async lifespan for bot initialization:

```python
@asynccontextmanager
async def lifespan(server: FastMCP):
    global bot
    bot = DiscordBot()
    bot_task = asyncio.create_task(bot.start(token))
    await asyncio.wait_for(bot._ready_event.wait(), timeout=30.0)
    yield
    await bot.close()
    bot_task.cancel()

mcp = FastMCP(name="slashAI", lifespan=lifespan)
```

#### 2.3.2 Transport

| Transport | Protocol | Use Case |
|-----------|----------|----------|
| stdio | JSON-RPC 2.0 | Claude Code integration |

#### 2.3.3 Tool Definitions

Tools are defined using FastMCP's `@mcp.tool()` decorator:

```python
@mcp.tool()
async def send_message(channel_id: str, content: str) -> str:
    """
    Send a message to a Discord channel.

    Args:
        channel_id: The Discord channel ID to send the message to
        content: The message content to send

    Returns:
        Confirmation with the sent message ID
    """
```

#### 2.3.4 Tool Specifications

| Tool | Parameters | Return Type | Error Handling |
|------|------------|-------------|----------------|
| `send_message` | `channel_id: str`, `content: str` | Success message with ID | Returns error string |
| `edit_message` | `channel_id: str`, `message_id: str`, `content: str` | Confirmation | Returns error string |
| `read_messages` | `channel_id: str`, `limit: int = 10` | Formatted message list | Returns error string |
| `list_channels` | `guild_id: Optional[str] = None` | Channel list with IDs | Returns error string |
| `get_channel_info` | `channel_id: str` | Key-value channel details | Returns error string |

---

## 3. Data Flow

### 3.1 Chatbot Message Flow

```
User @mentions bot in Discord
         │
         ▼
┌─────────────────────┐
│  on_message event   │
│  (discord_bot.py)   │
└──────────┬──────────┘
           │ Check: is bot mentioned OR is DM?
           ▼
┌─────────────────────┐
│  _handle_chat()     │
│  Extract content    │
│  Remove @mention    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  ClaudeClient.chat()│
│  - Add to history   │
│  - Call Claude API  │
│  - Track tokens     │
│  - Add response     │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  message.reply()    │
│  Send response      │
└─────────────────────┘
```

### 3.2 MCP Tool Invocation Flow

```
Claude Code sends tool request
         │
         ▼
┌─────────────────────┐
│  MCP Server (stdio) │
│  Receives JSON-RPC  │
└──────────┬──────────┘
           │ Route to tool handler
           ▼
┌─────────────────────┐
│  @mcp.tool() handler│
│  e.g., send_message │
└──────────┬──────────┘
           │ Call Discord bot method
           ▼
┌─────────────────────┐
│  DiscordBot method  │
│  e.g., send_message │
└──────────┬──────────┘
           │ Discord API call
           ▼
┌─────────────────────┐
│  Discord Gateway    │
│  Execute operation  │
└──────────┬──────────┘
           │ Return result
           ▼
┌─────────────────────┐
│  MCP Server         │
│  Return JSON-RPC    │
└─────────────────────┘
```

---

## 4. Error Handling

### 4.1 Error Categories

| Category | Handling Strategy | Example |
|----------|-------------------|---------|
| Discord API errors | Return error message to user/tool | Channel not found |
| Anthropic API errors | Return error message, log details | Rate limit, auth failure |
| MCP protocol errors | Return JSON-RPC error response | Invalid parameters |
| Bot initialization | Raise exception, prevent startup | Invalid token |

### 4.2 Timeout Configuration

| Operation | Timeout | Behavior on Timeout |
|-----------|---------|---------------------|
| Bot ready wait | 30 seconds | Raise RuntimeError |
| Claude API call | Default (60s) | Raise TimeoutError |
| Discord operations | discord.py default | Raise discord.HTTPException |

---

## 5. Deployment Architecture

### 5.1 DigitalOcean App Platform

```yaml
workers:
  - name: discord-bot
    github:
      repo: mindfulent/slashAI
      branch: main
      deploy_on_push: true
    source_dir: /
    environment_slug: python
    instance_size_slug: apps-s-1vcpu-0.5gb
    instance_count: 1
    run_command: python src/discord_bot.py
```

### 5.2 Resource Requirements

| Resource | Specification |
|----------|---------------|
| CPU | 1 vCPU (shared) |
| Memory | 512 MB |
| Storage | Ephemeral (no persistence) |
| Network | Outbound only (Discord WebSocket, Anthropic HTTPS) |

### 5.3 Environment Variables

| Variable | Scope | Type | Required |
|----------|-------|------|----------|
| `DISCORD_BOT_TOKEN` | RUN_TIME | SECRET | Yes |
| `ANTHROPIC_API_KEY` | RUN_TIME | SECRET | Yes |

---

## 6. Security Considerations

### 6.1 Credential Management

- All credentials stored as environment variables
- Never committed to version control (`.env` in `.gitignore`)
- Marked as SECRET in deployment config (encrypted at rest)

### 6.2 Discord Permissions

The bot requests minimal permissions:
- Send Messages
- Read Message History
- View Channels

### 6.3 Rate Limiting

| Service | Limit | Handling |
|---------|-------|----------|
| Discord | 5 messages/5 seconds per channel | discord.py handles automatically |
| Anthropic | Varies by plan | SDK raises exception |

---

## 7. Monitoring & Observability

### 7.1 Logging

Current logging:
- Bot ready status (stdout)
- Connection status

### 7.2 Metrics (Future)

Planned metrics:
- Messages processed per hour
- Token usage over time
- Response latency
- Error rates by type

---

## 8. Testing Strategy

### 8.1 Manual Testing

| Test Case | Steps | Expected Result |
|-----------|-------|-----------------|
| Bot connection | Start bot | "Logged in as slashAI#XXXX" |
| @mention response | @mention bot with question | Bot replies with Claude response |
| DM response | DM the bot | Bot replies in DM |
| MCP tool | Use Claude Code to send message | Message appears in Discord |

### 8.2 Automated Testing (Future)

- Unit tests for ClaudeClient
- Integration tests with mock Discord client
- MCP protocol conformance tests

---

## 9. Dependencies

### 9.1 Direct Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| discord.py | ≥2.3.0 | Discord API client |
| mcp[cli] | ≥1.25.0 | MCP server framework |
| anthropic | ≥0.40.0 | Claude API client |
| python-dotenv | ≥1.0.0 | Environment variable loading |

### 9.2 Transitive Dependencies (Key)

| Package | Purpose |
|---------|---------|
| aiohttp | Async HTTP for Discord |
| pydantic | Data validation for MCP |
| httpx | HTTP client for Anthropic |

---

## 10. Future Considerations

### 10.1 Scalability

Current architecture supports single-instance deployment. For horizontal scaling:
- Implement message deduplication
- Use external state store (Redis/PostgreSQL)
- Consider Discord sharding for large guild counts

### 10.2 Planned Enhancements

1. **Persistent memory**: Store conversations in database
2. **Slash commands**: Native Discord slash command support
3. **Rate limiting**: Per-user token budgets
4. **Webhooks**: Outbound notifications for events
5. **Multi-model**: Support for different Claude models per context
