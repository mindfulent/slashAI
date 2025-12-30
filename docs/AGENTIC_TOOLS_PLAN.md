# v0.9.12: Agentic Discord Bot

Enable slashAI to call its own Discord tools during conversations, allowing the owner to request actions like posting to channels via DM.

## Requirements
- **Owner only**: Only the configured owner can trigger tool actions
- **Any channel**: Bot can post to any channel it has access to
- **Agentic loop**: Claude can decide to use tools and continue reasoning

## Architecture Change

```
BEFORE (v0.9.11):
Discord Message -> claude_client.chat() -> Anthropic API -> Text Response

AFTER (v0.9.12):
Discord Message -> claude_client.chat() -> Anthropic API (with tools)
                                              |
                                         tool_use block?
                                              |
                               Yes: Execute tool -> Send result -> Loop
                               No: Return text response
```

## Files to Modify

### 1. `src/claude_client.py`
Add tool definitions and agentic loop handling.

**Changes:**
- Add `DISCORD_TOOLS` constant with Anthropic-format tool schemas
- Add `bot` parameter to `__init__` (DiscordBot reference for tool execution)
- Add `owner_id` parameter to `__init__` (Discord user ID allowed to use tools)
- Modify `chat()` to:
  - Only pass `tools=` if caller is owner
  - Handle `tool_use` content blocks in response
  - Execute tools via `self.bot.send_message()`, etc.
  - Loop until Claude returns a final text response
- Add `_execute_tool()` helper method

**Tool Definitions (6 tools):**
```python
DISCORD_TOOLS = [
    {"name": "send_message", "description": "...", "input_schema": {...}},
    {"name": "edit_message", ...},
    {"name": "delete_message", ...},
    {"name": "read_messages", ...},
    {"name": "list_channels", ...},
    {"name": "get_channel_info", ...},
]
```

### 2. `src/discord_bot.py`
Pass bot reference to ClaudeClient and configure owner ID.

**Changes:**
- Add `OWNER_ID` from environment variable
- Pass `bot=self` and `owner_id=OWNER_ID` when constructing ClaudeClient
- No other changes needed (tool methods already exist)

### 3. Update System Prompt
Add section explaining new capabilities (owner-only actions).

**Add to `DEFAULT_SYSTEM_PROMPT`:**
```
### Discord Actions (Owner Only)
When Slash (the owner) requests it, you can take actions in Discord:
- Send messages to any channel
- Edit or delete your previous messages
- Read recent messages from channels
- List available channels

Only use these tools when explicitly asked. Never take actions without a clear request.
```

## Implementation Steps

1. **Add tool definitions to claude_client.py**
   - Define `DISCORD_TOOLS` list with all 6 tool schemas

2. **Modify ClaudeClient.__init__()**
   - Add optional `bot` and `owner_id` parameters
   - Store as instance variables

3. **Implement agentic loop in chat()**
   - Check if `user_id == self.owner_id` to enable tools
   - Pass `tools=DISCORD_TOOLS` to API call when enabled
   - After response, check for `tool_use` blocks
   - Execute each tool, collect results
   - Continue loop with tool results until text-only response

4. **Add _execute_tool() helper**
   - Switch on tool name
   - Call appropriate `self.bot.method()`
   - Return string result

5. **Update discord_bot.py**
   - Read `OWNER_ID` from env
   - Pass to ClaudeClient

6. **Update system prompt**
   - Document capabilities for Claude

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OWNER_ID` | For tools | Discord user ID allowed to trigger actions |

## Security Model

- Tools are ONLY passed to API when `user_id == owner_id`
- Other users chat normally (no tools, no capabilities change)
- Tool execution happens server-side (user can't inject tool calls)
- Rate limiting inherits from Discord's rate limits

## Testing Plan

1. **Owner DM**: Send DM asking to post in a channel -> should work
2. **Owner channel**: Mention in channel asking to post elsewhere -> should work
3. **Non-owner DM**: Other user asks for action -> should be refused (no tools available)
4. **Non-owner channel**: Other user mentions asking for action -> normal chat response

## Rollback

Set `OWNER_ID=` (empty) to disable tool use entirely. Falls back to v0.9.11 behavior.
