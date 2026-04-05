# slashAI Desktop — Screen Share Vision for Voice Personas

## Context

When someone shares their screen in Discord voice chat, Lena (and future personas) can hear the conversation but can't see what's being shared. slashAI already has image understanding for static images in text channels, but nothing for live screen shares in voice.

**Solution:** A Tauri (Rust) system tray app — branded as "slashAI Desktop" — that captures screen share content and sends frames to the voice agent backend. The voice agent analyzes frames with Claude Vision and injects visual context into the persona's conversation, letting Lena naturally comment on what she sees.

**Why Tauri:** ~5MB installer, native Windows capture APIs via Rust, minimal resource usage, proper platform feel. The TBA community already installs desktop tools (Prism Launcher, mods).

---

## Architecture

```
slashAI Desktop (Tauri, user's PC)
  │  captures window frames every 5-10s
  │  JPEG compressed, change-detected
  │
  ▼  POST /api/vision/frame (multipart)
Voice Agent Droplet (DO, existing)
  │  new: lightweight aiohttp server on port 8001
  │  Claude Vision analyzes frame → 1-2 sentence description
  │  VisionFrameStore (in-memory, ephemeral)
  │
  ▼  chat_streaming() pulls visual context
Lena's voice response
  "Oh I see you're working on a sorting system — that hopper chain looks like it might need a comparator..."
```

### Key insight
The voice agent runs on a **separate DO Droplet** (for UDP), not the main bot's App Platform. The vision endpoint must live on the voice Droplet, not the main bot.

---

## Phase 1: Backend — Vision Endpoint on Voice Agent

### 1A. New file: `src/api/vision.py`

Follows the `MemoryBridgeAPI` pattern from `src/api/memory_bridge.py`:

- **`VisionFrameStore`** class — in-memory dict keyed by `channel_id`
  - Stores: latest analysis text, timestamp, sharer's display name, user_id
  - `get_latest(channel_id) -> Optional[VisionContext]` — returns context if <60s old
  - `set_analysis(channel_id, text, user_id, display_name)` — stores new analysis
  - No database, no persistence — purely ephemeral session data

- **`VisionAPI`** class with `register_routes(app)` pattern
  - `POST /api/vision/frame` — accepts multipart: JPEG `frame` + JSON `metadata` (discord_user_id, channel_id)
  - Auth: `Bearer SLASHAI_API_KEY` (same `_check_auth` pattern)
  - Rate limit: skip Claude Vision call if last analysis for this channel was <30s ago
  - Claude Vision prompt (short, ~150 output tokens):
    ```
    Describe what's on this screen in 1-2 sentences. Focus on what someone 
    in a voice call would find relevant. Be specific about code, games, 
    documents, or whatever is visible.
    ```
  - `GET /api/vision/session?user_id=X` — returns the channel_id if this user is in an active voice session (so the desktop app can auto-discover it)
  - `GET /api/vision/health` — health check

### 1B. Modify: `src/voice_agent.py`

Add a lightweight aiohttp server alongside the voice agent (~20 lines):
```python
from aiohttp import web
from api.vision import VisionAPI, VisionFrameStore

vision_store = VisionFrameStore()
vision_api = VisionAPI(vision_store, anthropic_client)

app = web.Application()
vision_api.register_routes(app)
runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('VISION_API_PORT', '8001')))
await site.start()
```

Pass `vision_store` to each `AgentClient` constructor.

### 1C. Modify: `src/agents/agent_client.py`

Accept `vision_store` parameter, pass to `ClaudeClient`.

### 1D. Modify: `src/claude_client.py` — `chat_streaming()`

After memory context injection (~line 1078), add visual context:
```python
if self._vision_store:
    visual = self._vision_store.get_latest(channel_id)
    if visual:
        context_parts.append(
            f"**Screen share** ({visual.sharer_name} is sharing, "
            f"{visual.age_seconds:.0f}s ago): {visual.description}"
        )
```

### Files touched (Phase 1):
| File | Change |
|------|--------|
| `src/api/vision.py` | **NEW** — VisionFrameStore + VisionAPI (~150 lines) |
| `src/voice_agent.py` | Add aiohttp server, create vision_store (~20 lines) |
| `src/agents/agent_client.py` | Accept + pass vision_store (~5 lines) |
| `src/claude_client.py` | Inject visual context in chat_streaming (~15 lines) |

---

## Phase 2: Tauri Desktop App

### Project structure: `desktop/`

```
desktop/
  src-tauri/
    src/
      main.rs           — Tauri entry, tray icon, capture loop
      capture.rs         — Windows.Graphics.Capture via windows-capture crate
      api_client.rs      — reqwest HTTP client for POSTing frames
      config.rs          — Settings persistence (%APPDATA%/slashAI Desktop/)
    Cargo.toml
    tauri.conf.json
    icons/               — slashAI tray/app icons
  src/
    index.html           — Settings UI (minimal HTML)
    main.js              — Vanilla JS for settings form
  package.json           — Tauri CLI + build scripts
  README.md
```

### 2A. `capture.rs` — Window capture engine

- **`list_windows()`** — enumerate capturable windows (title + hwnd)
- **`CaptureEngine`** — wraps `windows-capture` crate
  - Captures target window at configured interval
  - Converts RGBA → JPEG via `image` crate, resizes to max 1280px wide
  - **Change detection:** perceptual hash (`img_hash` crate), only emit frame when hash differs significantly from last sent frame
  - Runs on a background thread, sends frames via `tokio::sync::mpsc` channel

### 2B. `api_client.rs` — Frame upload

- `reqwest` multipart POST to `{api_url}/api/vision/frame`
- Fields: `frame` (JPEG bytes), `metadata` (JSON: discord_user_id, channel_id)
- `Authorization: Bearer {api_key}`
- Fire-and-forget with 10s timeout — failures logged, not retried
- On startup, calls `GET /api/vision/session?user_id=X` to auto-discover channel_id

### 2C. `config.rs` — Settings

Stored in `%APPDATA%/slashAI Desktop/config.json`:
```json
{
  "api_url": "https://voice.slashai.example:8001",
  "api_key": "...",
  "discord_user_id": "123456789",
  "capture_interval_ms": 5000,
  "auto_start": false,
  "selected_window_title": null
}
```

### 2D. `main.rs` — Tray app

- System tray with slashAI icon
- Menu: "Select Window" | "Start/Stop Capture" | separator | "Settings" | "Quit"
- "Select Window" → small picker window listing capturable windows
- "Settings" → webview window with config form
- Capture loop: timer fires every `capture_interval_ms`, capture engine checks for changes, posts if changed

### 2E. Settings UI (`src/index.html`)

Minimal form: API URL, API key, Discord user ID, capture interval slider (1-30s), auto-start checkbox. "Test Connection" button. No framework — vanilla HTML/JS.

### Rust dependencies:
- `tauri` v2
- `windows-capture` — Windows.Graphics.Capture wrapper
- `image` — JPEG encoding + resize
- `img_hash` — perceptual hashing
- `reqwest` — HTTP client (multipart)
- `serde` / `serde_json` — config
- `tokio` — async runtime (bundled with Tauri)

---

## Phase 3: Polish (post-MVP)

- **Proactive commentary:** When screen changes significantly, Lena can comment unprompted (requires careful UX — opt-in, with cooldown)
- **Auto-detect Discord screen share:** Match window titles or use Discord RPC to detect Go Live state
- **Auto-start:** `tauri-plugin-autostart` (Windows Registry)
- **CI/CD:** `.github/workflows/build-desktop.yml` — build on push, attach MSI/exe to GitHub Release
- **Installer:** NSIS or WiX via Tauri bundler, Start Menu entry, auto-update via Tauri updater

---

## Cost Estimate

- Claude Vision: ~$0.005 per frame analysis (small JPEG + short prompt + 150 tokens)
- At 1 analysis per 30s during 1hr session = 120 calls = ~$0.60/hour
- With change detection on static screens, real cost significantly lower
- No new Python dependencies needed

---

## .gitignore additions

```
# Tauri / Rust
desktop/src-tauri/target/
desktop/dist/
desktop/node_modules/
*.msi
*.exe
*.nsis
```

---

## Verification

1. **Phase 1 (backend):** Start voice agent locally, POST a test JPEG to `/api/vision/frame` via curl, verify analysis stored, trigger a voice utterance and confirm visual context appears in the LLM system prompt (enable verbose logging)
2. **Phase 2 (desktop):** Build Tauri app, configure it to point at local voice agent, share a window, verify frames arrive at endpoint, join voice and confirm Lena references the screen content
3. **End-to-end:** User in Discord voice with Lena, sharing screen, Lena naturally references visible content in her responses
