# Voice Architecture

## Overview

slashAI has two separate Discord bot processes sharing the same codebase:

| | slashAI (Text Bot) | Voice Agent (Lena) |
|---|---|---|
| **Entry point** | `src/discord_bot.py` | `src/voice_agent.py` → `src/agents/agent_client.py` |
| **Platform** | DigitalOcean App Platform | DigitalOcean Droplet (`umami-stats`) |
| **Token** | `DISCORD_BOT_TOKEN` | `AGENT_LENA_TOKEN` |
| **Networking** | HTTP/TCP only (no UDP) | Full UDP support |
| **Voice capable** | No | Yes |
| **Memory agent_id** | `None` | `"lena"` |

The split exists because App Platform blocks UDP, which Discord voice requires (Opus codec over UDP).

## Shared Components

Both bots use the same backend:
- `src/claude_client.py` — LLM conversation (text uses `chat()`, voice uses `chat_streaming()`)
- `src/memory/` — PostgreSQL + pgvector memory system with Voyage embeddings
- Anthropic API (Claude Sonnet 4.6)
- Same database (`DATABASE_URL`)

## Voice Pipeline

```
Discord UDP → AudioReceiver → Per-user VAD → Cartesia STT → Echo Guard
    → Name Filter (multi-user) → Claude LLM (streaming) → Cartesia TTS → Discord Playback
```

Key components in `src/voice/`:
- `receiver.py` — Decrypts/decodes Discord voice packets (AEAD XChaCha20 + Opus)
- `vad.py` — RMS-based voice activity detection with silence timeout + background flush timer
- `cartesia_stt.py` — Speech-to-text via Cartesia REST API (ink-whisper model)
- `cartesia_tts.py` — Text-to-speech via Cartesia WebSocket (sonic-3 model, auto-reconnect)
- `session.py` — Orchestrates the full pipeline per voice channel
- `name_filter.py` — In multi-user channels (2+ humans), only responds when addressed by name
- `echo_guard.py` — Prevents responding to its own TTS output
- `resampler.py` — 48kHz stereo (Discord) ↔ 16kHz mono (STT) ↔ 24kHz mono (TTS)
- `audio_source.py` — Streaming audio source for discord.py playback
- `text_processor.py` — Cleans transcripts, infers emotion for TTS

## Persona System

Voice agents are configured via JSON files in `personas/`. Each persona defines:
- Identity (personality, background, speech style)
- Voice config (Cartesia voice ID, model, emotion, speed)
- Name aliases for multi-user filtering (common STT mishearings)
- Memory scoping (`agent_id`)

`voice_agent.py` auto-discovers personas and starts agents for any that have a matching `AGENT_{NAME}_TOKEN` env var.

## Deployment

- **Text bot**: Auto-deploys via App Platform on push to main (`.do/app.yaml`)
- **Voice agent**: Auto-deploys via GitHub Actions on push to main when voice-related files change (`.github/workflows/deploy-voice.yml`). SSHes into the droplet, rebuilds Docker image, restarts container with health check and rollback.

## Adding a New Voice Persona

To add a new voice-capable bot (e.g., slashAI's personality in voice):

1. **Create persona JSON** — `personas/slashai.json` with identity, voice config, and memory settings. Set `agent_id` to match the text bot's scope if you want shared memories, or use a new ID for separate memory.

2. **Create a Discord bot application** — In the Discord Developer Portal, create a new bot and get its token. You cannot reuse a token that's already connected on another process (two gateway connections = one gets kicked).

3. **Add token to droplet** — Set `AGENT_SLASHAI_TOKEN=<token>` in the container env vars. `voice_agent.py` will auto-detect it on next restart.

4. **Configure Cartesia voice** — Choose a voice ID from Cartesia's library and set it in the persona's `voice.cartesia.voice_id`.

5. **Redeploy** — Push to main or trigger the deploy workflow manually.

No code changes needed — the multi-persona infrastructure already supports this.

## Key Constraints

- **One token per process** — Discord's gateway only allows one connection per bot token. Can't run the same token on App Platform and the droplet simultaneously.
- **UDP required** — Discord voice is Opus over UDP. App Platform, Cloud Run, and similar serverless platforms block UDP.
- **Memory scoping** — `agent_id` in the memories table separates what each persona knows. Set to the same value for shared memories, different values for isolation.
- **Cartesia API** — Required for both STT and TTS. Set `CARTESIA_API_KEY` in the droplet env.
