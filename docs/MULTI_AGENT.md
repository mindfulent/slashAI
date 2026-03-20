# Plan: Deprecate Fabricord — Consolidate Discord Functions into slashAI

## Goal
Replace Fabricord (and optionally AutoWhitelist) with slashAI, using a multi-bot architecture where slashAI manages both the `@slashAI` persona and the `@DeanBot` persona under one codebase.

## Current State

**Fabricord (DeanBot)** provides:
1. MC → Discord chat bridge (player messages appear in #server-chat)
2. Discord → MC chat bridge (Discord messages appear in-game)
3. Player join/leave announcements
4. Advancement announcements
5. Server start/stop messages
6. Optional console bridge

**AutoWhitelist (also DeanBot token)** provides:
- `/register <username>` slash command to whitelist players

**slashAI** already has:
- Webhook endpoints (gamemode changes, title grants)
- Posts to `SERVER_CHAT_CHANNEL`
- Full Discord bot with slash commands, memory, MCP tools
- Webhook server (aiohttp)

## Architecture

### Multi-Bot Client
slashAI will run **two Discord clients**:
- **Primary client** (`@slashAI`): Existing bot token — chat, memory, MCP tools, webhooks
- **Secondary client** (`@DeanBot`): Existing DeanBot token — MC bridge messages, /register command

Both clients managed in one process. DeanBot client is lightweight (no AI, no memory) — just forwards events.

### Minecraft Server Communication
Since Fabricord runs as a Fabric mod with direct game access, we need an alternative data source:

- **Pterodactyl WebSocket API** — Stream server console output in real-time to detect:
  - Player join/leave
  - Chat messages
  - Advancements
  - Server start/stop
- **RCON** — Send commands to the MC server:
  - `/say` or `/tellraw` for Discord → MC chat relay
  - `/whitelist add/remove` for registration

The TBA server already has Pterodactyl API credentials (used by `server-config.py`).

## Implementation Phases

### Phase 1: Pterodactyl Console Listener
**Files:** `slashAI/src/minecraft/` (new module)

- `console_listener.py` — WebSocket client connecting to Pterodactyl console API
  - Parse log lines with regex for: player join/leave, chat, advancements, server start/stop
  - Emit events via callback/event system
- `rcon_client.py` — RCON client for sending commands to MC server
  - Used for Discord → MC chat relay and whitelist commands
- `events.py` — Event types (PlayerJoin, PlayerLeave, ChatMessage, Advancement, ServerStart, ServerStop)

### Phase 2: DeanBot Bridge Client
**Files:** `slashAI/src/bridge/` (new module)

- `dean_bot.py` — Lightweight discord.py client using DeanBot token
  - Listens for messages in #server-chat → relays to MC via RCON
  - Receives MC events from console listener → posts to #server-chat as DeanBot
  - Formats messages to match current Fabricord style (player avatars, join/leave embeds)
  - `/register` slash command implementation (whitelist add via RCON)

### Phase 3: Integration with slashAI Main Bot
**Files:** `slashAI/src/discord_bot.py`, `slashAI/src/mcp_server.py`

- Start DeanBot client alongside slashAI in the same async loop
- Add env vars: `DEANBOT_TOKEN`, `PTERODACTYL_WS_URL`, `RCON_HOST`, `RCON_PORT`, `RCON_PASSWORD`, `MC_BRIDGE_CHANNEL_ID`
- Add MCP tools for MC server interaction (optional, for Claude Code):
  - `mc_send_command(command)` — Execute server command via RCON
  - `mc_player_list()` — Get online players

### Phase 4: Remove Fabricord from TBA
**Files:** `TBA/mods/fabricord.pw.toml` (delete), `TBA/pack.toml`, `TBA/CHANGELOG.md`, etc.

- Remove Fabricord mod from packwiz
- Remove AutoWhitelist mod if /register is fully migrated
- Update documentation and version
- Deploy to server

## Key Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `slashAI/src/minecraft/__init__.py` | Create | New module |
| `slashAI/src/minecraft/console_listener.py` | Create | Pterodactyl WebSocket console streaming |
| `slashAI/src/minecraft/rcon_client.py` | Create | RCON client for MC commands |
| `slashAI/src/minecraft/events.py` | Create | Event types and parser |
| `slashAI/src/bridge/__init__.py` | Create | New module |
| `slashAI/src/bridge/dean_bot.py` | Create | DeanBot Discord client |
| `slashAI/src/discord_bot.py` | Modify | Start DeanBot alongside main bot |
| `slashAI/src/mcp_server.py` | Modify | Add MC server MCP tools |
| `slashAI/requirements.txt` | Modify | Add `mcrcon` or RCON library |
| `TBA/mods/fabricord.pw.toml` | Delete | Remove Fabricord |

## Environment Variables (New)

```
DEANBOT_TOKEN=           # DeanBot Discord bot token
PTERODACTYL_API_KEY=     # Already exists in TBA/.env
PTERODACTYL_SERVER_ID=   # Already exists in TBA/.env
PTERODACTYL_BASE_URL=    # Panel URL for WebSocket connection
RCON_HOST=               # MC server address
RCON_PORT=25575          # RCON port
RCON_PASSWORD=           # RCON password
MC_BRIDGE_CHANNEL_ID=    # #server-chat channel for bridge messages
```

## Verification

1. **Console listener**: Connect to Pterodactyl WS, join/leave the MC server, verify events are detected
2. **Chat bridge MC→Discord**: Send a chat message in-game, verify it appears in #server-chat posted by DeanBot
3. **Chat bridge Discord→MC**: Send a message in #server-chat, verify it appears in-game via RCON `/tellraw`
4. **Advancements**: Earn an advancement in-game, verify Discord announcement
5. **Register command**: Run `/register <name>` in Discord, verify whitelist add via RCON
6. **slashAI unaffected**: Verify @slashAI chat, memory, MCP tools still work normally
7. **Remove Fabricord**: Delete from TBA, test server starts without it

## Risks & Considerations

- **Pterodactyl WS availability**: Need to verify Bloom.host exposes WebSocket API (most Pterodactyl panels do)
- **Log parsing fragility**: Regex on console output can break with mod log format changes — keep patterns configurable
- **RCON on production**: Bloom.host may need RCON enabled; verify port accessibility from DigitalOcean (where slashAI runs)
- **Latency**: Fabricord is in-process (instant); WebSocket + RCON adds network latency (likely <1s, acceptable)
- **AutoWhitelist migration**: `/register` is more than just `/whitelist add` — it may have role-based entries, lock times, and cache. Need to replicate or simplify.
