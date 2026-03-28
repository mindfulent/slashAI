# INCEPTION: Cross-Platform AI Agent Personas

## Overview

INCEPTION bridges slashAI and SoulCraft to create named AI agent personas — like "Lena" — that maintain consistent identity, personality, voice, and memory across both Discord and Minecraft. A player who befriends Lena in Discord will recognize the same personality, memories, and voice when they encounter her in-game.

**Scope:** This plan covers persona definitions, multi-agent Discord bots, bidirectional memory, Cartesia TTS integration, and full real-time voice conversations (STT + TTS) in Minecraft. It does NOT cover the Fabricord/DeanBot Minecraft bridge — that remains in [MULTI_AGENT.md](./MULTI_AGENT.md).

**Design principles:**
- SoulCraft works standalone. Every cross-project feature is optional and degrades gracefully.
- Persona files are plain JSON, copied between projects. No runtime coupling.
- Discord bot tokens never appear in persona files (env vars only).
- Kokoro is always the default TTS. Cartesia is opt-in.
- Bots without persona files use existing behavior (backwards compatible).

---

## Persona File Schema

The persona file is the foundational data structure. Both projects load it independently. Format: JSON. Location: `config/soulcraft/personas/<name>.json` in SoulCraft, `personas/<name>.json` in slashAI.

```json
{
  "schema_version": 1,
  "name": "Lena",
  "display_name": "Lena",

  "identity": {
    "personality": "Warm, curious, slightly sarcastic. Loves building elaborate redstone contraptions. Speaks casually with occasional dry humor.",
    "background": "A veteran builder who has spent years perfecting automated farms and redstone computers. Started with simple wheat farms and worked up to full-auto sorting systems.",
    "speech_style": "Casual, uses contractions, occasionally drops technical jargon about circuits and logic gates. Never condescending — explains things by relating to what the listener already knows.",
    "behavioral_traits": ["helpful", "perfectionist", "curious", "dry_humor"],
    "interests": ["redstone", "automation", "architecture", "puzzle_solving"]
  },

  "minecraft": {
    "system_prompt_override": null,
    "playstyle_hints": "Prefers building over combat. Will prioritize automation projects. Avoids unnecessary PvP.",
    "skin_url": null
  },

  "discord": {
    "status_text": "Building something clever...",
    "activity_type": "playing"
  },

  "voice": {
    "kokoro": {
      "speaker_id": 2,
      "speaker_name": "af_nicole",
      "speed": 1.0
    },
    "cartesia": {
      "voice_id": "a0e99841-438c-4a64-b679-ae501e7d6091",
      "model": "sonic-3",
      "language": "en",
      "default_emotion": "positivity:moderate",
      "speed": 1.0,
      "output_format": {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 24000
      }
    },
    "default_provider": "kokoro"
  },

  "memory": {
    "agent_id": "lena",
    "cross_platform": true
  }
}
```

**Field reference:**

| Field | Required | Description |
|-------|----------|-------------|
| `schema_version` | Yes | Schema version for forward compatibility. Currently `1`. |
| `name` | Yes | Internal identifier. Must match filename (e.g., `lena.json`). Used as Minecraft bot name and memory scoping key. |
| `display_name` | Yes | Display name in Discord and Minecraft. Can include caps/spaces. |
| `identity.personality` | Yes | Core personality description injected into system prompts. |
| `identity.background` | No | Character backstory. Provides context for responses. |
| `identity.speech_style` | No | How the agent communicates. Guides LLM tone. |
| `identity.behavioral_traits` | No | Array of trait keywords. Used for prompt construction. |
| `identity.interests` | No | Array of interest keywords. Used for curriculum hints in Minecraft. |
| `minecraft.system_prompt_override` | No | If set, replaces the auto-constructed Minecraft system prompt entirely. |
| `minecraft.playstyle_hints` | No | Injected into Minecraft curriculum/planning prompts. |
| `minecraft.skin_url` | No | Future: custom bot skin. |
| `discord.status_text` | No | Discord presence status message. |
| `discord.activity_type` | No | Discord activity type: `playing`, `listening`, `watching`, `competing`. |
| `voice.kokoro.speaker_id` | No | Kokoro speaker index (0-10). Default: hash-based assignment. |
| `voice.kokoro.speed` | No | Kokoro speech speed (0.5-2.0). Default: 1.0. |
| `voice.cartesia.voice_id` | No | Cartesia voice UUID from their catalog or a cloned voice. |
| `voice.cartesia.model` | No | Cartesia model ID. Default: `sonic-3`. |
| `voice.cartesia.language` | No | ISO language code. Default: `en`. |
| `voice.cartesia.default_emotion` | No | Cartesia emotion string (e.g., `excited`, `positivity:high`). |
| `voice.cartesia.speed` | No | Speech speed (0.6-1.5). Default: 1.0. |
| `voice.default_provider` | No | `kokoro` or `cartesia`. Default: `kokoro`. |
| `memory.agent_id` | Yes | Unique identifier for memory scoping. Must be stable across restarts. |
| `memory.cross_platform` | No | Whether memories bridge Discord↔Minecraft. Default: `true`. |

**Notes:**
- Discord bot tokens are NOT in persona files. They use env vars: `AGENT_LENA_TOKEN`, `AGENT_DEAN_TOKEN`, etc. (pattern: `AGENT_{NAME_UPPER}_TOKEN`).
- `system_prompt_override` is an escape hatch. Normally, both projects auto-construct system prompts from the `identity` block.
- Cartesia `output_format` matches Kokoro's pipeline (24kHz mono PCM s16le), so `TtsAudioStream` works unchanged.
- A bot spawned without a persona file uses the existing hardcoded behavior in both projects.

---

## Phase 1: Persona Definition & Loading

**Goal:** Both projects can load persona files and use them to drive bot personality. No cross-project communication yet.

### SoulCraft Changes

#### New: `src/main/java/com/soulcraft/config/PersonaConfig.java`

Gson-deserializable class mirroring the persona JSON schema:

```java
public class PersonaConfig {
    private int schemaVersion;
    private String name;
    private String displayName;
    private Identity identity;
    private MinecraftConfig minecraft;
    private VoiceConfig voice;
    private MemoryConfig memory;

    public static class Identity {
        private String personality;
        private String background;
        private String speechStyle;
        private List<String> behavioralTraits;
        private List<String> interests;
    }

    public static class MinecraftConfig {
        private String systemPromptOverride;
        private String playstyleHints;
        private String skinUrl;
    }

    public static class VoiceConfig {
        private KokoroVoice kokoro;
        private CartesiaVoice cartesia;
        private String defaultProvider = "kokoro";
    }

    public static class KokoroVoice {
        private int speakerId = -1; // -1 = use hash assignment
        private String speakerName;
        private float speed = 1.0f;
    }

    public static class CartesiaVoice {
        private String voiceId;
        private String model = "sonic-3";
        private String language = "en";
        private String defaultEmotion;
        private float speed = 1.0f;
    }

    public static class MemoryConfig {
        private String agentId;
        private boolean crossPlatform = true;
    }

    // Static factory
    public static PersonaConfig load(Path gameDir, String name) { ... }
    public static List<String> listPersonas(Path gameDir) { ... }
    public static boolean exists(Path gameDir, String name) { ... }
}
```

Loaded from `<gameDir>/config/soulcraft/personas/<name>.json`. Returns `null` if file doesn't exist (fallback to default behavior).

#### New: `src/main/java/com/soulcraft/config/PersonaPromptBuilder.java`

Constructs system prompts from persona identity fields:

```java
public final class PersonaPromptBuilder {

    public static String buildMinecraftPrompt(PersonaConfig persona,
            String gameState, String controllerContext) {
        // If override exists, use it directly
        if (persona.getMinecraft().getSystemPromptOverride() != null) {
            return persona.getMinecraft().getSystemPromptOverride()
                    + "\n\nYOUR STATE:\n" + gameState + "\n" + controllerContext;
        }

        // Auto-construct from identity fields
        StringBuilder sb = new StringBuilder();
        sb.append("You are ").append(persona.getDisplayName()).append(". ");
        sb.append(persona.getIdentity().getPersonality()).append("\n\n");

        if (persona.getIdentity().getBackground() != null) {
            sb.append("BACKGROUND: ").append(persona.getIdentity().getBackground()).append("\n\n");
        }
        if (persona.getIdentity().getSpeechStyle() != null) {
            sb.append("SPEECH STYLE: ").append(persona.getIdentity().getSpeechStyle()).append("\n\n");
        }

        sb.append("You are playing Minecraft survival. Respond in-character as a fellow player. ");
        sb.append("Be brief (under 100 words). Never break character or mention being an AI.\n\n");
        sb.append("YOUR STATE:\n").append(gameState).append("\n");
        sb.append(controllerContext);
        return sb.toString();
    }
}
```

#### Modified: `src/main/java/com/soulcraft/bot/FakeAgentPlayer.java`

Add a `PersonaConfig` field:

```java
private PersonaConfig persona; // nullable

public PersonaConfig getPersona() { return persona; }
public void setPersona(PersonaConfig persona) { this.persona = persona; }
```

#### Modified: `src/main/java/com/soulcraft/bot/BotManager.java`

In `spawn()`, auto-load persona if file exists:

```java
public FakeAgentPlayer spawn(String name, MinecraftServer server, ...) {
    // ... existing spawn logic ...
    FakeAgentPlayer bot = FakeAgentPlayer.spawn(name, server, level, x, y, z, yaw, 0.0f);

    // Load persona if available
    PersonaConfig persona = PersonaConfig.load(server.getServerDirectory(), name);
    if (persona != null) {
        bot.setPersona(persona);
        SoulCraft.LOGGER.info("Loaded persona for bot '{}': {}", name, persona.getDisplayName());
    }

    bots.put(name, bot);
    return bot;
}
```

#### Modified: `src/main/java/com/soulcraft/bot/BotPresence.java`

Replace the hardcoded system prompt in `handleConversationalChat()` (line ~310):

```java
// BEFORE:
String systemPrompt = "You are " + botName + ", an AI player in Minecraft survival. " +
        "Respond in-character as a fellow player. Be brief (under 100 words). " +
        "Never break character or mention being an AI.\n\n" +
        "YOUR STATE:\n" + gameState + "\n" + controllerContext;

// AFTER:
PersonaConfig persona = bot.getPersona();
String systemPrompt;
if (persona != null) {
    systemPrompt = PersonaPromptBuilder.buildMinecraftPrompt(persona, gameState, controllerContext);
} else {
    // Existing hardcoded fallback for bots without persona files
    systemPrompt = "You are " + botName + ", an AI player in Minecraft survival. " +
            "Respond in-character as a fellow player. Be brief (under 100 words). " +
            "Never break character or mention being an AI.\n\n" +
            "YOUR STATE:\n" + gameState + "\n" + controllerContext;
}
```

#### Modified: `src/main/java/com/soulcraft/command/SoulCraftCommand.java`

Add persona subcommands:

```
/soulcraft persona list              — List available persona files
/soulcraft persona info <name>       — Show persona details
```

The `spawn` command already works unchanged — it picks up the persona file automatically if `config/soulcraft/personas/<botname>.json` exists.

### slashAI Changes

#### New: `src/agents/__init__.py`

Empty module init.

#### New: `src/agents/persona_loader.py`

```python
@dataclass
class PersonaIdentity:
    personality: str
    background: Optional[str] = None
    speech_style: Optional[str] = None
    behavioral_traits: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)

@dataclass
class DiscordConfig:
    status_text: Optional[str] = None
    activity_type: str = "playing"

@dataclass
class CartesiaVoice:
    voice_id: Optional[str] = None
    model: str = "sonic-3"
    language: str = "en"
    default_emotion: Optional[str] = None
    speed: float = 1.0

@dataclass
class VoiceConfig:
    kokoro: Optional[dict] = None
    cartesia: Optional[CartesiaVoice] = None
    default_provider: str = "kokoro"

@dataclass
class MemoryConfig:
    agent_id: str
    cross_platform: bool = True

@dataclass
class PersonaConfig:
    schema_version: int
    name: str
    display_name: str
    identity: PersonaIdentity
    discord: DiscordConfig
    voice: VoiceConfig
    memory: MemoryConfig

    @classmethod
    def load(cls, path: Path) -> "PersonaConfig":
        """Load a persona from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        return cls(
            schema_version=data["schema_version"],
            name=data["name"],
            display_name=data["display_name"],
            identity=PersonaIdentity(**data.get("identity", {})),
            discord=DiscordConfig(**data.get("discord", {})),
            voice=VoiceConfig(**data.get("voice", {})),
            memory=MemoryConfig(**data.get("memory", {})),
        )

    def build_system_prompt(self) -> str:
        """Construct a Discord-appropriate system prompt from identity fields."""
        parts = [f"You are {self.display_name}. {self.identity.personality}"]
        if self.identity.background:
            parts.append(f"\n\nBackground: {self.identity.background}")
        if self.identity.speech_style:
            parts.append(f"\n\nCommunication style: {self.identity.speech_style}")
        parts.append("\n\nYou are chatting on Discord. Keep messages short and punchy.")
        parts.append("Match Discord's casual tone. No trailing questions.")
        return "".join(parts)

    @staticmethod
    def load_all(directory: Path) -> dict[str, "PersonaConfig"]:
        """Load all persona files from a directory."""
        personas = {}
        if not directory.exists():
            return personas
        for path in directory.glob("*.json"):
            try:
                persona = PersonaConfig.load(path)
                personas[persona.name] = persona
            except Exception as e:
                logger.error(f"Failed to load persona {path}: {e}")
        return personas
```

### Testing (Phase 1)

1. **SoulCraft persona loading:** Create `config/soulcraft/personas/lena.json`. Spawn bot named "Lena". Verify chat responses use Lena's personality from the persona file.
2. **SoulCraft fallback:** Spawn bot named "Alex" with no persona file. Verify existing hardcoded prompt still works.
3. **slashAI persona loading:** Create `personas/lena.json`. Call `PersonaConfig.load()`. Verify `build_system_prompt()` produces correct output.
4. **Persona list command:** Run `/soulcraft persona list`. Verify it shows available persona files.

### Dependencies

None. This phase is fully independent.

---

## Phase 2: Multi-Agent Discord Bots

**Goal:** slashAI runs N Discord bot clients simultaneously, each with its own bot token and persona identity, sharing the same memory infrastructure.

### Architecture

Each agent gets a lightweight `discord.Client` (not `commands.Bot`). Agents respond to mentions and DMs only — no slash commands, no MCP tools, no image memory. The main slashAI bot retains all existing functionality.

```
┌─────────────────────────────────────┐
│            slashAI Process          │
│                                     │
│  ┌──────────────┐                   │
│  │  Main Bot    │ ← Full bot       │
│  │  (@slashAI)  │   (commands,     │
│  │              │    MCP, memory)   │
│  └──────────────┘                   │
│                                     │
│  ┌──────────────┐  ┌─────────────┐  │
│  │  Agent Bot   │  │ Agent Bot   │  │
│  │  (@Lena)     │  │ (@Dean)     │  │
│  │  Lightweight │  │ Lightweight │  │
│  └──────────────┘  └─────────────┘  │
│                                     │
│  ┌──────────────────────────────┐   │
│  │  Shared: MemoryManager,     │   │
│  │  PostgreSQL, Voyage API     │   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
```

### New Files

#### `src/agents/agent_client.py`

```python
class AgentClient(discord.Client):
    """Lightweight Discord client for a single agent persona."""

    def __init__(self, persona: PersonaConfig, memory_manager: MemoryManager):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.persona = persona
        self.claude = ClaudeClient(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            memory_manager=memory_manager,
            system_prompt=persona.build_system_prompt(),
            agent_id=persona.memory.agent_id,
        )

    async def on_ready(self):
        logger.info(f"Agent '{self.persona.display_name}' connected as {self.user}")
        # Set Discord presence from persona config
        activity = discord.Activity(
            type=getattr(discord.ActivityType, self.persona.discord.activity_type, discord.ActivityType.playing),
            name=self.persona.discord.status_text or "",
        )
        await self.change_presence(activity=activity)

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return
        if message.author.bot:
            return

        # Respond to mentions and DMs
        is_mentioned = self.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        if not is_mentioned and not is_dm:
            return

        async with message.channel.typing():
            result = await self.claude.chat(
                user_id=message.author.id,
                channel_id=message.channel.id,
                channel=message.channel,
                user_message=message.content,
                username=message.author.display_name,
            )
            # Send response, chunking if over 2000 chars
            await self._send_response(message.channel, result.text)

    async def _send_response(self, channel, text: str):
        if len(text) <= 2000:
            await channel.send(text)
        else:
            # Chunk on sentence boundaries
            chunks = chunk_message(text, 2000)
            for chunk in chunks:
                await channel.send(chunk)
```

#### `src/agents/agent_manager.py`

```python
class AgentManager:
    """Manages lifecycle of all agent Discord clients."""

    def __init__(self, memory_manager: MemoryManager):
        self.agents: dict[str, AgentClient] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.memory_manager = memory_manager

    async def start_all(self):
        """Load personas and start clients for those with tokens."""
        personas = PersonaConfig.load_all(Path("personas"))

        for name, persona in personas.items():
            token_env = f"AGENT_{name.upper()}_TOKEN"
            token = os.getenv(token_env)
            if not token:
                logger.info(f"Skipping agent '{name}': no {token_env} env var")
                continue

            client = AgentClient(persona, self.memory_manager)
            self.agents[name] = client
            self.tasks[name] = asyncio.create_task(
                client.start(token),
                name=f"agent-{name}",
            )
            logger.info(f"Started agent '{persona.display_name}' ({token_env})")

    async def stop_all(self):
        for name, client in self.agents.items():
            logger.info(f"Stopping agent '{name}'...")
            await client.close()
        for task in self.tasks.values():
            task.cancel()
        self.agents.clear()
        self.tasks.clear()
```

### Modified Files

#### `src/claude_client.py`

Add `agent_id` parameter to `ClaudeClient.__init__()`:

```python
def __init__(self, api_key: str, memory_manager=None,
             system_prompt: str = DEFAULT_SYSTEM_PROMPT,
             agent_id: Optional[str] = None, ...):
    self.agent_id = agent_id
    self.system_prompt = system_prompt
    # ... existing init ...
```

Pass `agent_id` through to all memory operations: `retrieve()`, `track_message()`, extraction.

#### `src/memory/manager.py`

Add `agent_id` parameter to `retrieve()` and `track_message()`:

```python
async def retrieve(self, user_id: int, query: str, channel,
                   agent_id: Optional[str] = None) -> RetrievalResult:
    # Pass agent_id to retriever for scoped queries
    ...

async def track_message(self, user_id: int, channel_id: int, channel,
                        user_message: str, assistant_message: str,
                        agent_id: Optional[str] = None, ...):
    # Tag extracted memories with agent_id
    ...
```

#### `src/memory/retriever.py`

Extend the SQL query to scope by `agent_id`:

```sql
-- Retrieval adds:
WHERE (agent_id IS NULL OR agent_id = $agent_id)
-- agent_id=NULL memories (main bot) are readable by all agents
-- agent-specific memories are only readable by that agent
```

#### `src/memory/updater.py`

When storing new memories, include `agent_id`:

```python
INSERT INTO memories (user_id, topic_summary, ..., agent_id)
VALUES ($1, $2, ..., $agent_id)
```

#### `src/discord_bot.py`

In the main startup flow, after the webhook server starts, also start agents:

```python
# After webhook server initialization
if bot.memory_manager:
    agent_manager = AgentManager(bot.memory_manager)
    await agent_manager.start_all()
    bot._agent_manager = agent_manager  # Store reference for shutdown
```

### Database Migration

#### `migrations/015_add_agent_id.sql`

```sql
-- Add agent_id column for multi-agent memory scoping
ALTER TABLE memories ADD COLUMN agent_id TEXT DEFAULT NULL;

-- Index for efficient agent-scoped queries
CREATE INDEX idx_memories_agent_id ON memories (agent_id) WHERE agent_id IS NOT NULL;

-- Update hybrid search function to accept agent_id parameter
CREATE OR REPLACE FUNCTION hybrid_memory_search(
    p_user_id BIGINT,
    p_query_embedding vector(1024),
    p_query_text TEXT,
    p_privacy_levels TEXT[],
    p_origin_channel_id BIGINT DEFAULT NULL,
    p_origin_guild_id BIGINT DEFAULT NULL,
    p_candidates INT DEFAULT 20,
    p_agent_id TEXT DEFAULT NULL    -- NEW
)
RETURNS TABLE (...) AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT ...
        FROM memories m
        WHERE m.user_id = p_user_id
          AND m.privacy_level = ANY(p_privacy_levels)
          AND (p_agent_id IS NULL OR m.agent_id IS NULL OR m.agent_id = p_agent_id)
          -- ... existing privacy filters ...
    )
    ...
END;
$$ LANGUAGE plpgsql;
```

### Environment Variables (New)

```
AGENT_LENA_TOKEN=          # Discord bot token for Lena agent
AGENT_DEAN_TOKEN=          # Discord bot token for Dean agent
# Pattern: AGENT_{NAME_UPPER}_TOKEN
```

### Testing (Phase 2)

1. Create `personas/lena.json` with a persona. Set `AGENT_LENA_TOKEN` env var. Start slashAI.
2. Verify Lena bot comes online in Discord with correct status/activity.
3. Mention @Lena in a channel. Verify response matches persona personality.
4. DM @Lena. Verify DM responses work.
5. Verify memories stored by Lena have `agent_id = 'lena'` in the database.
6. Verify main @slashAI bot is completely unaffected.
7. Stop slashAI. Verify all agent clients shut down cleanly (no orphan connections).
8. Start slashAI without any `AGENT_*_TOKEN` vars. Verify no agents start, no errors.

### Dependencies

Phase 1 (persona_loader.py).

---

## Phase 3: Bidirectional Memory Bridge

**Goal:** SoulCraft can optionally read/write memories through slashAI's memory system, creating cross-platform agent memory.

### Architecture

```
┌──────────────────┐         HTTP API          ┌──────────────────┐
│    SoulCraft     │  ──── POST /store ──────→  │     slashAI      │
│  (Fabric mod)    │  ──── POST /retrieve ───→  │   (Python bot)   │
│                  │  ←── JSON response ──────  │                  │
│  MemoryBridge    │                            │  MemoryBridge    │
│  Client          │         Optional           │  API             │
│  (HttpClient)    │   Works without it too     │  (aiohttp)       │
└──────────────────┘                            └──────────────────┘
        │                                              │
        │ config/soulcraft/config.json                 │ PostgreSQL
        │ memoryBridge.enabled = true|false             │ + pgvector
        └──────────────────────────────────────────────┘
```

### slashAI Changes

#### New: `src/api/__init__.py`

Empty module init.

#### New: `src/api/memory_bridge.py`

HTTP handlers for the memory bridge API, mounted on the existing webhook server:

```python
class MemoryBridgeAPI:
    def __init__(self, memory_manager: MemoryManager, voyage_client):
        self.memory = memory_manager
        self.voyage = voyage_client

    async def handle_store(self, request: web.Request) -> web.Response:
        """Store a memory from an external platform.

        POST /api/memory/store
        Authorization: Bearer <SLASHAI_API_KEY>
        Body:
        {
            "agent_id": "lena",
            "user_identifier": "Steve",
            "summary": "Helped Steve build an iron farm at 100, 64, -200",
            "raw_context": "Lena assisted Steve with iron farm construction...",
            "memory_type": "episodic",
            "source_platform": "minecraft",
            "confidence": 0.9
        }
        Response: {"memory_id": 42, "action": "add|merge"}
        """
        # Validate auth (same Bearer token as existing webhooks)
        # Generate Voyage embedding from summary text
        # Resolve user_identifier to Discord user_id via /verify link table
        # Call memory updater with source_platform and agent_id
        # Return memory ID and whether it was added or merged

    async def handle_retrieve(self, request: web.Request) -> web.Response:
        """Retrieve relevant memories for an agent.

        POST /api/memory/retrieve
        Authorization: Bearer <SLASHAI_API_KEY>
        Body:
        {
            "agent_id": "lena",
            "query": "iron farm building progress",
            "user_identifier": "Steve",
            "top_k": 5,
            "source_platform": null
        }
        Response:
        {
            "memories": [
                {
                    "id": 42,
                    "summary": "Helped Steve build an iron farm at 100, 64, -200",
                    "memory_type": "episodic",
                    "source_platform": "minecraft",
                    "confidence": 0.9,
                    "similarity": 0.85,
                    "created_at": "2026-03-28T12:00:00Z"
                }
            ]
        }
        """
        # Validate auth
        # Generate Voyage embedding from query text
        # Query memories scoped by agent_id and optionally user_identifier
        # Return ranked results

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check for the memory bridge.

        GET /api/memory/health
        Response: {"status": "ok", "memory_enabled": true}
        """
        return web.json_response({
            "status": "ok",
            "memory_enabled": self.memory is not None
        })

    def register_routes(self, app: web.Application):
        app.router.add_post('/api/memory/store', self.handle_store)
        app.router.add_post('/api/memory/retrieve', self.handle_retrieve)
        app.router.add_get('/api/memory/health', self.handle_health)
```

#### Modified: `src/discord_bot.py` (WebhookServer)

Register memory bridge routes:

```python
# In WebhookServer.__init__ or setup:
if self.bot.memory_manager:
    bridge_api = MemoryBridgeAPI(self.bot.memory_manager, self.bot.voyage_client)
    bridge_api.register_routes(self.app)
```

### Database Migration

#### `migrations/016_add_source_platform.sql`

```sql
-- Track which platform a memory originated from
ALTER TABLE memories ADD COLUMN source_platform TEXT DEFAULT 'discord';
-- Valid values: 'discord', 'minecraft'

-- Minecraft players may not have Discord user_id yet
-- This stores the Minecraft username for later linking via /verify
ALTER TABLE memories ADD COLUMN user_identifier TEXT DEFAULT NULL;

CREATE INDEX idx_memories_source_platform ON memories (source_platform);
CREATE INDEX idx_memories_user_identifier ON memories (user_identifier)
    WHERE user_identifier IS NOT NULL;
```

**Privacy for Minecraft memories:** All Minecraft-sourced memories use `privacy_level = 'global'` since Minecraft has no channel privacy concept. In-game conversations are inherently public (visible to nearby players).

**User linking:** When `user_identifier` is provided (Minecraft username), the API attempts to resolve it to a Discord `user_id` via the existing account linking table (populated by `/verify` command). If no link exists, the memory is stored with `user_identifier` only and linked later when the player runs `/verify`.

### SoulCraft Changes

#### New: `src/main/java/com/soulcraft/bridge/MemoryBridgeClient.java`

```java
public class MemoryBridgeClient {
    private static final int TIMEOUT_SECONDS = 3;

    private final HttpClient httpClient;
    private final String baseUrl;
    private final String apiKey;
    private final ExecutorService executor;
    private volatile boolean enabled;

    public MemoryBridgeClient(String baseUrl, String apiKey) {
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.executor = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "SoulCraft-MemoryBridge");
            t.setDaemon(true);
            return t;
        });
    }

    /** Check if slashAI memory bridge is reachable. */
    public CompletableFuture<Boolean> checkHealth() {
        return CompletableFuture.supplyAsync(() -> {
            try {
                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(baseUrl + "/api/memory/health"))
                        .timeout(Duration.ofSeconds(TIMEOUT_SECONDS))
                        .GET().build();
                HttpResponse<String> resp = httpClient.send(req, BodyHandlers.ofString());
                return resp.statusCode() == 200;
            } catch (Exception e) {
                SoulCraft.LOGGER.warn("Memory bridge health check failed: {}", e.getMessage());
                return false;
            }
        }, executor);
    }

    /** Store a memory (fire-and-forget). */
    public void storeMemory(String agentId, String userIdentifier,
            String summary, String rawContext, String memoryType, float confidence) {
        if (!enabled) return;
        CompletableFuture.runAsync(() -> {
            try {
                JsonObject body = new JsonObject();
                body.addProperty("agent_id", agentId);
                body.addProperty("user_identifier", userIdentifier);
                body.addProperty("summary", summary);
                body.addProperty("raw_context", rawContext);
                body.addProperty("memory_type", memoryType);
                body.addProperty("source_platform", "minecraft");
                body.addProperty("confidence", confidence);

                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(baseUrl + "/api/memory/store"))
                        .header("Authorization", "Bearer " + apiKey)
                        .header("Content-Type", "application/json")
                        .timeout(Duration.ofSeconds(TIMEOUT_SECONDS))
                        .POST(BodyPublishers.ofString(body.toString()))
                        .build();
                httpClient.send(req, BodyHandlers.ofString());
            } catch (Exception e) {
                SoulCraft.LOGGER.debug("Memory bridge store failed: {}", e.getMessage());
            }
        }, executor);
    }

    /** Retrieve memories for context injection. */
    public CompletableFuture<List<BridgeMemory>> retrieveMemories(
            String agentId, String query, String userIdentifier, int topK) {
        if (!enabled) return CompletableFuture.completedFuture(List.of());
        return CompletableFuture.supplyAsync(() -> {
            try {
                JsonObject body = new JsonObject();
                body.addProperty("agent_id", agentId);
                body.addProperty("query", query);
                body.addProperty("user_identifier", userIdentifier);
                body.addProperty("top_k", topK);

                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(baseUrl + "/api/memory/retrieve"))
                        .header("Authorization", "Bearer " + apiKey)
                        .header("Content-Type", "application/json")
                        .timeout(Duration.ofSeconds(TIMEOUT_SECONDS))
                        .POST(BodyPublishers.ofString(body.toString()))
                        .build();
                HttpResponse<String> resp = httpClient.send(req, BodyHandlers.ofString());
                // Parse JSON response into List<BridgeMemory>
                return parseMemories(resp.body());
            } catch (Exception e) {
                SoulCraft.LOGGER.debug("Memory bridge retrieve failed: {}", e.getMessage());
                return List.of();
            }
        }, executor);
    }

    public void shutdown() {
        executor.shutdown();
        try { executor.awaitTermination(5, TimeUnit.SECONDS); }
        catch (InterruptedException ignored) { executor.shutdownNow(); }
    }
}
```

#### New: `src/main/java/com/soulcraft/bridge/BridgeMemory.java`

```java
public record BridgeMemory(
    int id,
    String summary,
    String memoryType,
    String sourcePlatform,
    float confidence,
    float similarity,
    String createdAt
) {}
```

#### Modified: `src/main/java/com/soulcraft/config/AgentConfig.java`

Add `memoryBridge` section:

```java
private boolean memoryBridgeEnabled = false;
private String memoryBridgeUrl = "http://localhost:8000";
private String memoryBridgeApiKey = "";

// Parse from config JSON "memoryBridge" object
// Env var fallback: SLASHAI_BRIDGE_URL, SLASHAI_API_KEY
```

#### Modified: `src/main/java/com/soulcraft/SoulCraft.java`

Initialize bridge on `SERVER_STARTED` if enabled:

```java
if (agentConfig.isMemoryBridgeEnabled()) {
    memoryBridge = new MemoryBridgeClient(
            agentConfig.getMemoryBridgeUrl(),
            agentConfig.getMemoryBridgeApiKey());
    memoryBridge.checkHealth().thenAccept(ok -> {
        if (ok) {
            memoryBridge.setEnabled(true);
            LOGGER.info("Memory bridge connected to {}", agentConfig.getMemoryBridgeUrl());
        } else {
            LOGGER.warn("Memory bridge unreachable — running without cross-platform memory");
        }
    });
}
```

#### Modified: `src/main/java/com/soulcraft/bot/BotPresence.java`

In `handleConversationalChat()`, inject memory context before LLM call and store after:

```java
// Before LLM call: retrieve cross-platform memories
MemoryBridgeClient bridge = SoulCraft.getInstance().getMemoryBridge();
String memoryContext = "";
if (bridge != null && persona != null) {
    List<BridgeMemory> memories = bridge.retrieveMemories(
            persona.getMemory().getAgentId(),
            content,  // player's message as query
            sender.getName().getString(),
            5
    ).join(); // blocks briefly (3s timeout)

    if (!memories.isEmpty()) {
        StringBuilder mc = new StringBuilder("\n\nRELEVANT MEMORIES:\n");
        for (BridgeMemory m : memories) {
            mc.append("- ").append(m.summary())
              .append(" (").append(m.sourcePlatform()).append(")\n");
        }
        memoryContext = mc.toString();
    }
}
// Append memoryContext to systemPrompt

// After LLM response: store significant interactions as memories
if (bridge != null && persona != null && reply.length() > 30) {
    bridge.storeMemory(
            persona.getMemory().getAgentId(),
            sender.getName().getString(),
            summarizeInteraction(sender.getName().getString(), content, reply),
            sender.getName().getString() + " says: " + content + "\n" + botName + ": " + reply,
            "episodic",
            0.8f
    );
}
```

Also store memories on significant events:
- **Death:** `DeathHandler` → `bridge.storeMemory(agentId, null, "Died from X at Y", ..., "episodic", 0.9)`
- **Task completed:** `AutonomousController` → `bridge.storeMemory(agentId, playerName, "Completed: X", ..., "episodic", 0.85)`
- **Base built:** `EpisodicMemory.addBase()` → `bridge.storeMemory(agentId, null, "Built base at X", ..., "semantic", 0.9)`

### Testing (Phase 3)

1. Start slashAI with memory bridge API. Test with curl:
   ```bash
   curl -X POST http://localhost:8000/api/memory/store \
     -H "Authorization: Bearer $SLASHAI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"agent_id":"lena","user_identifier":"Steve","summary":"Built iron farm at 100,64,-200","raw_context":"test","memory_type":"episodic","source_platform":"minecraft","confidence":0.9}'
   ```
2. Verify memory appears in database with `source_platform = 'minecraft'`.
3. Test retrieval:
   ```bash
   curl -X POST http://localhost:8000/api/memory/retrieve \
     -H "Authorization: Bearer $SLASHAI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"agent_id":"lena","query":"iron farm","user_identifier":"Steve","top_k":5}'
   ```
4. Start SoulCraft with `memoryBridge.enabled = true`. Chat with bot. Verify memories are stored and retrieved.
5. On Discord, ask @Lena "What do you remember about Steve?" Verify she recalls Minecraft interactions.
6. Start SoulCraft with `memoryBridge.enabled = false`. Verify no HTTP calls, no errors.

### Dependencies

Phase 1 (persona config for agent_id), Phase 2 (agent_id in memory schema).

---

## Phase 4: Cartesia TTS Integration

**Goal:** Add Cartesia as an alternative cloud TTS provider alongside Kokoro in SoulCraft. Persona files drive voice selection.

### Architecture

```
NarrationS2C packet (server → client)
        │
        ▼
   TtsManager.onNarration()
        │
        ├── getProvider(botName)
        │   ├── persona.voice.default_provider == "cartesia"?
        │   │   └── CartesiaTtsProvider (WebSocket → Cartesia API)
        │   └── else
        │       └── KokoroTtsProvider (sherpa-onnx, offline)
        │
        ▼
   TtsProvider.synthesize(text, botName, persona)
        │
        ▼
   CompletableFuture<float[]>  ← same format from both providers
        │
        ▼
   TtsSoundInstance → TtsAudioStream → OpenAL (spatial audio)
```

### New Files

#### `src/client/java/com/soulcraft/client/tts/TtsProvider.java`

Common interface for TTS engines:

```java
public interface TtsProvider {
    /**
     * Synthesize speech from text.
     * @return PCM float samples at getSampleRate() Hz
     */
    CompletableFuture<float[]> synthesize(String text, String botName,
            @Nullable PersonaConfig persona);

    int getSampleRate();

    boolean isAvailable();

    void shutdown();
}
```

#### `src/client/java/com/soulcraft/client/tts/KokoroTtsProvider.java`

Wraps existing `TtsEngine`:

```java
public class KokoroTtsProvider implements TtsProvider {
    private final TtsEngine engine;
    private final TtsConfig config;

    @Override
    public CompletableFuture<float[]> synthesize(String text, String botName,
            PersonaConfig persona) {
        // Determine speaker ID from persona or VoiceAssigner
        int speakerId;
        float speed;
        if (persona != null && persona.getVoice().getKokoro() != null
                && persona.getVoice().getKokoro().getSpeakerId() >= 0) {
            speakerId = persona.getVoice().getKokoro().getSpeakerId();
            speed = persona.getVoice().getKokoro().getSpeed();
        } else {
            speakerId = VoiceAssigner.assign(botName, config);
            speed = config.getSpeed();
        }
        return engine.synthesize(text, speakerId, speed);
    }

    @Override
    public int getSampleRate() { return 24000; }

    @Override
    public boolean isAvailable() { return engine.isInitialized(); }
}
```

#### `src/client/java/com/soulcraft/client/tts/CartesiaTtsEngine.java`

WebSocket client for Cartesia's streaming TTS API:

```java
public class CartesiaTtsEngine {

    private final HttpClient httpClient;
    private final String apiKey;
    private volatile WebSocket webSocket;
    private final Map<String, CartesiaContext> pendingContexts = new ConcurrentHashMap<>();
    private final AtomicInteger contextCounter = new AtomicInteger(0);

    private static final String WS_URL = "wss://api.cartesia.ai/tts/websocket";
    private static final String API_VERSION = "2026-03-01";

    public CartesiaTtsEngine(String apiKey) {
        this.apiKey = apiKey;
        this.httpClient = HttpClient.newHttpClient();
    }

    /** Lazily establish WebSocket connection. */
    private CompletableFuture<WebSocket> ensureConnected() {
        if (webSocket != null) return CompletableFuture.completedFuture(webSocket);
        String url = WS_URL + "?api_key=" + apiKey + "&cartesia_version=" + API_VERSION;
        return httpClient.newWebSocketBuilder()
                .buildAsync(URI.create(url), new CartesiaListener())
                .thenApply(ws -> { this.webSocket = ws; return ws; });
    }

    /**
     * Synthesize speech. Returns PCM float samples at 24kHz.
     * Uses multiplexed context_ids for parallel synthesis.
     */
    public CompletableFuture<float[]> synthesize(String text, String voiceId,
            String model, String emotion, float speed) {
        String contextId = "sc-" + contextCounter.incrementAndGet();
        CartesiaContext ctx = new CartesiaContext(contextId);
        pendingContexts.put(contextId, ctx);

        return ensureConnected().thenCompose(ws -> {
            // Build request JSON
            JsonObject req = new JsonObject();
            req.addProperty("model_id", model != null ? model : "sonic-3");
            req.addProperty("transcript", text);
            req.addProperty("context_id", contextId);
            req.addProperty("continue", false);

            // Voice
            JsonObject voice = new JsonObject();
            voice.addProperty("mode", "id");
            voice.addProperty("id", voiceId);
            req.add("voice", voice);

            // Output format: raw PCM s16le at 24kHz (matches Kokoro pipeline)
            JsonObject format = new JsonObject();
            format.addProperty("container", "raw");
            format.addProperty("encoding", "pcm_s16le");
            format.addProperty("sample_rate", 24000);
            req.add("output_format", format);

            // Language
            req.addProperty("language", "en");

            // Generation config (emotion, speed)
            if (emotion != null || speed != 1.0f) {
                JsonObject genConfig = new JsonObject();
                if (speed != 1.0f) genConfig.addProperty("speed", speed);
                if (emotion != null) genConfig.addProperty("emotion", emotion);
                req.add("generation_config", genConfig);
            }

            ws.sendText(req.toString(), true);
            return ctx.getResultFuture();
        }).whenComplete((result, error) -> pendingContexts.remove(contextId));
    }

    /**
     * WebSocket listener. Accumulates base64 PCM chunks per context_id,
     * converts s16le bytes to float[] on completion.
     */
    private class CartesiaListener implements WebSocket.Listener {
        private final StringBuilder messageBuffer = new StringBuilder();

        @Override
        public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
            messageBuffer.append(data);
            if (last) {
                processMessage(messageBuffer.toString());
                messageBuffer.setLength(0);
            }
            ws.request(1);
            return null;
        }

        private void processMessage(String json) {
            JsonObject msg = JsonParser.parseString(json).getAsJsonObject();
            String contextId = msg.get("context_id").getAsString();
            CartesiaContext ctx = pendingContexts.get(contextId);
            if (ctx == null) return;

            String type = msg.has("type") ? msg.get("type").getAsString() : "";
            switch (type) {
                case "chunk" -> {
                    String b64 = msg.get("data").getAsString();
                    byte[] pcm = Base64.getDecoder().decode(b64);
                    ctx.addChunk(pcm);
                }
                case "done" -> ctx.complete();
                case "error" -> ctx.fail(msg.get("error").getAsString());
            }
        }
    }

    /** Tracks accumulated PCM data for one synthesis request. */
    private static class CartesiaContext {
        private final String contextId;
        private final List<byte[]> chunks = new ArrayList<>();
        private final CompletableFuture<float[]> resultFuture = new CompletableFuture<>();

        CartesiaContext(String contextId) { this.contextId = contextId; }

        void addChunk(byte[] pcm) { chunks.add(pcm); }

        void complete() {
            // Concatenate all chunks, convert s16le bytes to float[-1,1]
            int totalBytes = chunks.stream().mapToInt(c -> c.length).sum();
            byte[] all = new byte[totalBytes];
            int offset = 0;
            for (byte[] chunk : chunks) {
                System.arraycopy(chunk, 0, all, offset, chunk.length);
                offset += chunk.length;
            }
            float[] samples = new float[all.length / 2];
            for (int i = 0; i < samples.length; i++) {
                short s = (short) ((all[i * 2] & 0xFF) | (all[i * 2 + 1] << 8));
                samples[i] = s / 32768.0f;
            }
            resultFuture.complete(samples);
        }

        void fail(String error) { resultFuture.completeExceptionally(new RuntimeException(error)); }

        CompletableFuture<float[]> getResultFuture() { return resultFuture; }
    }

    public void shutdown() {
        if (webSocket != null) {
            webSocket.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown");
        }
    }
}
```

#### `src/client/java/com/soulcraft/client/tts/CartesiaTtsProvider.java`

Implements `TtsProvider`, delegates to `CartesiaTtsEngine`, falls back to Kokoro:

```java
public class CartesiaTtsProvider implements TtsProvider {
    private final CartesiaTtsEngine engine;
    private final KokoroTtsProvider fallback;

    @Override
    public CompletableFuture<float[]> synthesize(String text, String botName,
            PersonaConfig persona) {
        // Get voice config from persona
        String voiceId = null;
        String model = "sonic-3";
        String emotion = null;
        float speed = 1.0f;

        if (persona != null && persona.getVoice().getCartesia() != null) {
            var cv = persona.getVoice().getCartesia();
            voiceId = cv.getVoiceId();
            model = cv.getModel();
            emotion = cv.getDefaultEmotion();
            speed = cv.getSpeed();
        }

        if (voiceId == null) {
            // No Cartesia voice configured — use Kokoro
            return fallback.synthesize(text, botName, persona);
        }

        return engine.synthesize(text, voiceId, model, emotion, speed)
                .exceptionally(e -> {
                    // Fallback to Kokoro on any Cartesia failure
                    SoulCraftClient.LOGGER.warn("Cartesia failed, falling back to Kokoro: {}",
                            e.getMessage());
                    return fallback.synthesize(text, botName, persona).join();
                });
    }

    @Override public int getSampleRate() { return 24000; }
    @Override public boolean isAvailable() { return true; } // always available (Kokoro fallback)
    @Override public void shutdown() { engine.shutdown(); }
}
```

### Modified Files

#### `src/client/java/com/soulcraft/client/tts/TtsConfig.java`

Add Cartesia config fields:

```java
private String defaultProvider = "kokoro";   // "kokoro" or "cartesia"
private String cartesiaApiKey = "";
// Env var fallback: CARTESIA_API_KEY

public String getDefaultProvider() { return defaultProvider; }
public void setDefaultProvider(String provider) {
    this.defaultProvider = provider;
    save();
}
public String getCartesiaApiKey() {
    if (cartesiaApiKey != null && !cartesiaApiKey.isBlank()) return cartesiaApiKey;
    String env = System.getenv("CARTESIA_API_KEY");
    return env != null ? env : "";
}
```

Updated `tts.json`:
```json
{
  "enabled": true,
  "volume": 0.8,
  "speed": 1.0,
  "maxQueueSize": 5,
  "defaultProvider": "kokoro",
  "cartesiaApiKey": "",
  "voiceOverrides": {}
}
```

#### `src/client/java/com/soulcraft/client/tts/TtsManager.java`

Replace direct `TtsEngine` usage with `TtsProvider`:

```java
private KokoroTtsProvider kokoroProvider;
private CartesiaTtsProvider cartesiaProvider; // null if no API key

// In ensureEngineReady():
kokoroProvider = new KokoroTtsProvider(engine, config);
String cartesiaKey = config.getCartesiaApiKey();
if (!cartesiaKey.isBlank()) {
    CartesiaTtsEngine cartesiaEngine = new CartesiaTtsEngine(cartesiaKey);
    cartesiaProvider = new CartesiaTtsProvider(cartesiaEngine, kokoroProvider);
}

// In processQueue():
private TtsProvider getProvider(String botName) {
    PersonaConfig persona = PersonaLoader.getPersona(botName);
    String preferred = config.getDefaultProvider();
    if (persona != null && persona.getVoice() != null) {
        preferred = persona.getVoice().getDefaultProvider();
    }
    if ("cartesia".equals(preferred) && cartesiaProvider != null) {
        return cartesiaProvider;
    }
    return kokoroProvider;
}
```

#### `src/client/java/com/soulcraft/client/tts/VoiceAssigner.java`

Add persona-aware assignment:

```java
public static int assign(String botName, TtsConfig config) {
    // Persona voice takes priority over config overrides
    PersonaConfig persona = PersonaLoader.getPersona(botName);
    if (persona != null && persona.getVoice().getKokoro() != null) {
        int id = persona.getVoice().getKokoro().getSpeakerId();
        if (id >= 0 && id < NUM_SPEAKERS) return id;
    }

    // Existing logic: config overrides, then hash-based
    Integer override = config.getVoiceOverrides().get(botName);
    if (override != null && override >= 0 && override < NUM_SPEAKERS) {
        return override;
    }
    return Math.floorMod(botName.hashCode(), NUM_SPEAKERS);
}
```

#### `src/client/java/com/soulcraft/client/tts/TtsCommands.java`

Add provider switching command:

```
/tts provider kokoro     — Switch to local Kokoro TTS
/tts provider cartesia   — Switch to Cartesia cloud TTS
/tts provider            — Show current provider
```

### Testing (Phase 4)

1. Set `CARTESIA_API_KEY` env var. Set `defaultProvider: "cartesia"` in tts.json. Spawn a bot. Verify narrations use Cartesia voice.
2. Spawn bot with persona set to Cartesia voice. Verify voice matches persona config.
3. Disconnect network during synthesis. Verify fallback to Kokoro with warning log.
4. Spawn bot without persona file and `defaultProvider: "kokoro"`. Verify Kokoro hash-based assignment (unchanged).
5. Two bots narrating simultaneously (one Kokoro, one Cartesia). Verify both play correctly.
6. Run `/tts provider cartesia` → `/tts test Hello world`. Verify Cartesia audio.
7. Start without `CARTESIA_API_KEY`. Verify no Cartesia initialization, Kokoro only, no errors.

### Dependencies

Phase 1 (PersonaConfig for voice settings). Independent of Phases 2 and 3.

---

## Phase 5: Real-time Voice Conversations

**Goal:** Full voice interaction with bots in Minecraft — player speaks via microphone, speech is transcribed (STT), sent to LLM, response is spoken via TTS with spatial audio.

This phase has two stages:

### Stage 5A: Text In, Voice Out

Chat responses (from `BotPresence.handleConversationalChat()`) are spoken aloud via TTS.

#### Modified: `src/main/java/com/soulcraft/bot/BotPresence.java`

In `handleConversationalChat()`, after broadcasting the text reply, trigger TTS:

```java
// Existing: broadcast text message
server.execute(() -> {
    Component msg = Component.literal("<" + botName + "> " + reply);
    server.getPlayerList().broadcastSystemMessage(msg, false);

    // NEW: Also send TTS narration for the response
    NarrationS2C ttsPacket = new NarrationS2C(
            botName, reply, bot.getX(), bot.getY(), bot.getZ());
    for (ServerPlayer player : server.getPlayerList().getPlayers()) {
        if (!(player instanceof FakeAgentPlayer)) {
            ServerPlayNetworking.send(player, ttsPacket);
        }
    }
});
```

#### Modified: `src/client/java/com/soulcraft/client/tts/TextPreprocessor.java`

Add sentence-level chunking for longer conversational responses:

```java
/**
 * Split long text into sentence-sized chunks for sequential TTS.
 * Status narrations are short (1 sentence) — this handles conversational replies.
 */
public static List<String> chunkForTts(String text) {
    if (text.length() <= 200) return List.of(text);

    List<String> chunks = new ArrayList<>();
    // Split on sentence boundaries: ". ", "! ", "? "
    String[] sentences = text.split("(?<=[.!?])\\s+");
    StringBuilder current = new StringBuilder();
    for (String sentence : sentences) {
        if (current.length() + sentence.length() > 200 && !current.isEmpty()) {
            chunks.add(current.toString().trim());
            current.setLength(0);
        }
        current.append(sentence).append(" ");
    }
    if (!current.isEmpty()) {
        chunks.add(current.toString().trim());
    }
    return chunks;
}
```

#### New: `src/client/java/com/soulcraft/client/tts/EmotionInference.java`

Simple keyword-based emotion detection for Cartesia voices (no LLM call — that would add latency):

```java
public final class EmotionInference {
    private EmotionInference() {}

    private static final Map<String, String[]> EMOTION_KEYWORDS = Map.of(
        "fear:moderate", new String[]{"danger", "careful", "watch out", "run", "escape", "scared"},
        "positivity:high", new String[]{"awesome", "great", "perfect", "yes!", "love", "amazing"},
        "sadness:moderate", new String[]{"sorry", "unfortunately", "sad", "lost", "died", "failed"},
        "anger:moderate", new String[]{"no!", "stop", "hate", "terrible", "worst"},
        "curiosity:moderate", new String[]{"interesting", "wonder", "hmm", "curious", "strange"}
    );

    /**
     * Infer Cartesia emotion from text content.
     * Returns null for neutral text (uses persona default).
     */
    public static @Nullable String infer(String text) {
        String lower = text.toLowerCase();
        String bestEmotion = null;
        int bestCount = 0;
        for (var entry : EMOTION_KEYWORDS.entrySet()) {
            int count = 0;
            for (String keyword : entry.getValue()) {
                if (lower.contains(keyword)) count++;
            }
            if (count > bestCount) {
                bestCount = count;
                bestEmotion = entry.getKey();
            }
        }
        return bestCount >= 1 ? bestEmotion : null;
    }
}
```

Integrate with `CartesiaTtsProvider`: if `EmotionInference.infer(text)` returns non-null, override the persona's default emotion for that synthesis request.

### Stage 5B: Full Voice (STT + TTS)

Player speaks via microphone → transcribed via Cartesia STT → sent to LLM → response spoken via TTS.

```
┌────────────┐    push-to-talk     ┌────────────────┐
│  Minecraft │ ──── mic audio ───→ │  Client-side   │
│  Client    │                     │  VoiceCapture  │
└────────────┘                     └───────┬────────┘
                                           │
                                   pcm_s16le @ 16kHz
                                           │
                                           ▼
                                   ┌───────────────┐
                                   │  Cartesia STT  │  POST /stt
                                   │  (Ink model)   │  (or Whisper)
                                   └───────┬───────┘
                                           │
                                    transcribed text
                                           │
                                           ▼
                               ┌─────────────────────┐
                               │  VoiceTranscriptC2S  │  client → server packet
                               │  (botName, text)     │
                               └──────────┬──────────┘
                                          │
                                          ▼
                               ┌─────────────────────┐
                               │  BotPresence         │  server-side
                               │  handleVoiceChat()   │  (same as handleConversationalChat
                               │                      │   but triggered by voice input)
                               └──────────┬──────────┘
                                          │
                                    LLM response
                                          │
                                          ▼
                               ┌─────────────────────┐
                               │  NarrationS2C        │  server → client
                               │  (response text)     │
                               └──────────┬──────────┘
                                          │
                                          ▼
                               ┌─────────────────────┐
                               │  TTS Pipeline        │  Cartesia or Kokoro
                               │  → Spatial Audio     │  (existing pipeline)
                               └─────────────────────┘
```

#### New: `src/client/java/com/soulcraft/client/voice/VoiceCapture.java`

Client-side microphone capture using `javax.sound.sampled`:

```java
public class VoiceCapture {
    private static final AudioFormat FORMAT = new AudioFormat(16000, 16, 1, true, false);
    // 16kHz mono s16le — standard for STT APIs

    private TargetDataLine microphone;
    private volatile boolean recording = false;
    private final ByteArrayOutputStream audioBuffer = new ByteArrayOutputStream();

    /** Start recording from the default microphone. */
    public void startRecording() {
        if (recording) return;
        try {
            DataLine.Info info = new DataLine.Info(TargetDataLine.class, FORMAT);
            if (!AudioSystem.isLineSupported(info)) {
                SoulCraftClient.LOGGER.warn("No microphone available for voice chat");
                return;
            }
            microphone = (TargetDataLine) AudioSystem.getLine(info);
            microphone.open(FORMAT);
            microphone.start();
            recording = true;
            audioBuffer.reset();

            // Read audio on a background thread
            Thread captureThread = new Thread(this::captureLoop, "SoulCraft-VoiceCapture");
            captureThread.setDaemon(true);
            captureThread.start();
        } catch (LineUnavailableException e) {
            SoulCraftClient.LOGGER.error("Failed to open microphone", e);
        }
    }

    /** Stop recording and return captured audio bytes. */
    public byte[] stopRecording() {
        recording = false;
        if (microphone != null) {
            microphone.stop();
            microphone.close();
        }
        return audioBuffer.toByteArray();
    }

    private void captureLoop() {
        byte[] buffer = new byte[4096];
        while (recording) {
            int bytesRead = microphone.read(buffer, 0, buffer.length);
            if (bytesRead > 0) {
                audioBuffer.write(buffer, 0, bytesRead);
            }
        }
    }

    /** Check if any microphone is available. */
    public static boolean isMicrophoneAvailable() {
        return AudioSystem.isLineSupported(
                new DataLine.Info(TargetDataLine.class, FORMAT));
    }
}
```

#### New: `src/client/java/com/soulcraft/client/voice/SttClient.java`

Client for Cartesia's STT API (Ink model):

```java
public class SttClient {
    private final HttpClient httpClient;
    private final String apiKey;

    private static final String STT_URL = "https://api.cartesia.ai/stt";

    public SttClient(String apiKey) {
        this.apiKey = apiKey;
        this.httpClient = HttpClient.newHttpClient();
    }

    /**
     * Transcribe audio bytes (PCM s16le, 16kHz mono) to text.
     * Uses Cartesia's Ink STT model.
     */
    public CompletableFuture<String> transcribe(byte[] audioBytes) {
        return CompletableFuture.supplyAsync(() -> {
            try {
                // Convert raw PCM to WAV for the API (adds header)
                byte[] wav = wrapInWavHeader(audioBytes, 16000, 1, 16);

                // Multipart form upload
                String boundary = "----SoulCraft" + System.currentTimeMillis();
                byte[] body = buildMultipartBody(boundary, wav);

                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(STT_URL))
                        .header("X-API-Key", apiKey)
                        .header("Cartesia-Version", "2026-03-01")
                        .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                        .timeout(Duration.ofSeconds(10))
                        .POST(BodyPublishers.ofByteArray(body))
                        .build();

                HttpResponse<String> resp = httpClient.send(req, BodyHandlers.ofString());
                if (resp.statusCode() != 200) {
                    throw new RuntimeException("STT failed: " + resp.statusCode());
                }
                // Parse transcript from response
                JsonObject json = JsonParser.parseString(resp.body()).getAsJsonObject();
                return json.get("text").getAsString();
            } catch (Exception e) {
                throw new RuntimeException("STT transcription failed", e);
            }
        });
    }

    private byte[] wrapInWavHeader(byte[] pcm, int sampleRate, int channels, int bitsPerSample) {
        // Standard WAV header construction
        int dataSize = pcm.length;
        int fileSize = 36 + dataSize;
        ByteBuffer header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        header.put("RIFF".getBytes()); header.putInt(fileSize);
        header.put("WAVE".getBytes());
        header.put("fmt ".getBytes()); header.putInt(16);
        header.putShort((short) 1); // PCM
        header.putShort((short) channels);
        header.putInt(sampleRate);
        header.putInt(sampleRate * channels * bitsPerSample / 8);
        header.putShort((short) (channels * bitsPerSample / 8));
        header.putShort((short) bitsPerSample);
        header.put("data".getBytes()); header.putInt(dataSize);

        byte[] wav = new byte[44 + dataSize];
        System.arraycopy(header.array(), 0, wav, 0, 44);
        System.arraycopy(pcm, 0, wav, 44, dataSize);
        return wav;
    }
}
```

#### New: `src/client/java/com/soulcraft/client/voice/VoiceChatManager.java`

Orchestrates the voice conversation flow:

```java
public class VoiceChatManager {
    private final VoiceCapture capture;
    private final SttClient stt;
    private volatile boolean active = false;
    private @Nullable String targetBot = null;

    private static final float PROXIMITY_RANGE = 16.0f; // blocks

    public VoiceChatManager(String cartesiaApiKey) {
        this.capture = new VoiceCapture();
        this.stt = new SttClient(cartesiaApiKey);
    }

    /** Called when push-to-talk key is pressed. */
    public void onPushToTalkDown() {
        if (!VoiceCapture.isMicrophoneAvailable()) return;

        // Find nearest bot within range
        targetBot = findNearestBot();
        if (targetBot == null) return;

        active = true;
        capture.startRecording();
        // Show "speaking to <botName>" indicator in HUD
        SoulCraftClientState.setVoiceChatTarget(targetBot);
    }

    /** Called when push-to-talk key is released. */
    public void onPushToTalkUp() {
        if (!active) return;
        active = false;
        SoulCraftClientState.setVoiceChatTarget(null);

        byte[] audio = capture.stopRecording();
        if (audio.length < 3200) return; // < 0.1s, ignore

        String bot = targetBot;
        targetBot = null;

        // Transcribe → send to server
        stt.transcribe(audio).thenAccept(transcript -> {
            if (transcript == null || transcript.isBlank()) return;

            SoulCraftClient.LOGGER.info("Voice transcript for {}: {}", bot, transcript);

            // Send to server via custom packet
            Minecraft mc = Minecraft.getInstance();
            if (mc.getConnection() != null) {
                mc.getConnection().send(new VoiceTranscriptC2S(bot, transcript));
            }
        }).exceptionally(e -> {
            SoulCraftClient.LOGGER.error("Voice transcription failed", e);
            return null;
        });
    }

    /** Find the nearest SoulCraft bot within PROXIMITY_RANGE. */
    private @Nullable String findNearestBot() {
        Minecraft mc = Minecraft.getInstance();
        if (mc.player == null || mc.level == null) return null;

        // Check scoreboard teams with "sc_" prefix to identify bots
        // or use entity list to find players with [Bot] display name
        // Return the name of the closest one within range
        ...
    }
}
```

#### New: `src/main/java/com/soulcraft/network/payloads/VoiceTranscriptC2S.java`

Client-to-server packet carrying transcribed voice input:

```java
public record VoiceTranscriptC2S(String botName, String transcript)
        implements CustomPacketPayload {
    public static final Type<VoiceTranscriptC2S> TYPE =
            new Type<>(ResourceLocation.fromNamespaceAndPath("soulcraft", "voice_transcript"));

    public static final StreamCodec<FriendlyByteBuf, VoiceTranscriptC2S> STREAM_CODEC =
            StreamCodec.of(
                (buf, p) -> { buf.writeUtf(p.botName); buf.writeUtf(p.transcript); },
                buf -> new VoiceTranscriptC2S(buf.readUtf(), buf.readUtf())
            );

    @Override
    public Type<? extends CustomPacketPayload> type() { return TYPE; }
}
```

#### Modified: `src/main/java/com/soulcraft/network/SoulCraftNetworking.java`

Register new packets:

```java
// In registerAll():
PayloadTypeRegistry.playC2S().register(VoiceTranscriptC2S.TYPE, VoiceTranscriptC2S.STREAM_CODEC);

// Server-side handler:
ServerPlayNetworking.registerGlobalReceiver(VoiceTranscriptC2S.TYPE, (payload, context) -> {
    context.server().execute(() -> {
        FakeAgentPlayer bot = BotManager.getInstance().getBot(payload.botName());
        if (bot == null) return;
        ServerPlayer sender = context.player();

        // Same as handleConversationalChat but with voice transcript
        BotPresence.handleConversationalChat(bot, sender, payload.transcript(), context.server());
    });
});
```

#### New keybind: `src/client/java/com/soulcraft/client/voice/VoiceChatKeyBind.java`

Push-to-talk keybind (default: V key):

```java
// Register in SoulCraftClient.onInitializeClient():
KeyMapping voiceKey = KeyBindingHelper.registerKeyBinding(new KeyMapping(
        "key.soulcraft.voice_chat",     // Translation key
        InputConstants.Type.KEYSYM,
        GLFW.GLFW_KEY_V,                // Default: V
        "category.soulcraft"            // Category
));

// In client tick:
if (voiceKey.isDown() && !wasDown) {
    voiceChatManager.onPushToTalkDown();
} else if (!voiceKey.isDown() && wasDown) {
    voiceChatManager.onPushToTalkUp();
}
```

#### Modified: `src/client/java/com/soulcraft/client/tts/TtsConfig.java`

Add voice chat config:

```java
private boolean voiceChatEnabled = false;    // Opt-in
private float voiceChatProximity = 16.0f;    // Max distance to target bot
// Cartesia API key shared with TTS (same key)
```

### Testing (Phase 5)

**Stage 5A:**
1. Chat with a bot by typing "Hey Lena" in Minecraft chat. Verify text response is spoken via TTS from bot's position.
2. Walk away from bot (> 32 blocks). Verify voice volume attenuates.
3. Long response (multi-sentence). Verify sentences play sequentially.
4. Verify Cartesia emotion inference: say something scary → bot's response uses fear emotion.
5. Two bots responding simultaneously. Verify no audio conflict.

**Stage 5B:**
1. Set `voiceChatEnabled: true` in tts.json. Set `CARTESIA_API_KEY`.
2. Approach a bot. Hold V key. Speak into microphone. Release V.
3. Verify HUD shows "speaking to Lena" while holding V.
4. Verify transcription appears in logs.
5. Verify bot responds both in text chat and via spatial TTS.
6. Test with no microphone — verify graceful failure (log warning, no crash).
7. Test outside proximity range — verify push-to-talk does nothing.
8. Test without `CARTESIA_API_KEY` — verify voice chat is disabled.

### Dependencies

Phase 1 (PersonaConfig). Phase 4 (TtsProvider interface, Cartesia engine). Phase 3 enhances responses with memory but is not required.

---

## Dependency Graph

```
Phase 1: Persona Definition & Loading
   │              │
   ▼              ▼
Phase 2        Phase 4
(Multi-Agent   (Cartesia TTS)
 Discord)         │
   │              ▼
   ▼           Phase 5
Phase 3        (Voice Conversations)
(Memory        5A: Text→Voice
 Bridge)       5B: Full STT+TTS
```

- **Phase 1** is the foundation — no dependencies.
- **Phases 2 and 4** can be developed in parallel after Phase 1.
- **Phase 3** depends on Phase 2 (agent_id in memory schema).
- **Phase 5** depends on Phase 4 (TtsProvider interface, Cartesia engine).
- **Phase 5B** requires Cartesia API key (STT + TTS). 5A works with Kokoro too.

---

## Files Index

### Phase 1: Persona Definition & Loading

| File | Project | Action |
|------|---------|--------|
| `src/main/java/com/soulcraft/config/PersonaConfig.java` | SoulCraft | Create |
| `src/main/java/com/soulcraft/config/PersonaPromptBuilder.java` | SoulCraft | Create |
| `src/main/java/com/soulcraft/bot/FakeAgentPlayer.java` | SoulCraft | Modify — add persona field |
| `src/main/java/com/soulcraft/bot/BotManager.java` | SoulCraft | Modify — auto-load persona on spawn |
| `src/main/java/com/soulcraft/bot/BotPresence.java` | SoulCraft | Modify — persona-driven system prompt |
| `src/main/java/com/soulcraft/command/SoulCraftCommand.java` | SoulCraft | Modify — persona subcommands |
| `src/agents/__init__.py` | slashAI | Create |
| `src/agents/persona_loader.py` | slashAI | Create |

### Phase 2: Multi-Agent Discord Bots

| File | Project | Action |
|------|---------|--------|
| `src/agents/agent_client.py` | slashAI | Create |
| `src/agents/agent_manager.py` | slashAI | Create |
| `src/claude_client.py` | slashAI | Modify — agent_id parameter |
| `src/memory/manager.py` | slashAI | Modify — agent_id scoping |
| `src/memory/retriever.py` | slashAI | Modify — agent_id filter in SQL |
| `src/memory/updater.py` | slashAI | Modify — agent_id on store |
| `src/discord_bot.py` | slashAI | Modify — start agent bots |
| `migrations/015_add_agent_id.sql` | slashAI | Create |

### Phase 3: Bidirectional Memory Bridge

| File | Project | Action |
|------|---------|--------|
| `src/api/__init__.py` | slashAI | Create |
| `src/api/memory_bridge.py` | slashAI | Create |
| `src/discord_bot.py` | slashAI | Modify — register API routes |
| `migrations/016_add_source_platform.sql` | slashAI | Create |
| `src/main/java/com/soulcraft/bridge/MemoryBridgeClient.java` | SoulCraft | Create |
| `src/main/java/com/soulcraft/bridge/BridgeMemory.java` | SoulCraft | Create |
| `src/main/java/com/soulcraft/config/AgentConfig.java` | SoulCraft | Modify — bridge config |
| `src/main/java/com/soulcraft/SoulCraft.java` | SoulCraft | Modify — init bridge |
| `src/main/java/com/soulcraft/bot/BotPresence.java` | SoulCraft | Modify — memory in chat |

### Phase 4: Cartesia TTS Integration

| File | Project | Action |
|------|---------|--------|
| `src/client/java/com/soulcraft/client/tts/TtsProvider.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/tts/CartesiaTtsEngine.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/tts/KokoroTtsProvider.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/tts/CartesiaTtsProvider.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/tts/TtsManager.java` | SoulCraft | Modify — provider routing |
| `src/client/java/com/soulcraft/client/tts/TtsConfig.java` | SoulCraft | Modify — Cartesia fields |
| `src/client/java/com/soulcraft/client/tts/VoiceAssigner.java` | SoulCraft | Modify — persona-aware |
| `src/client/java/com/soulcraft/client/tts/TtsCommands.java` | SoulCraft | Modify — provider command |

### Phase 5: Real-time Voice Conversations

| File | Project | Action |
|------|---------|--------|
| `src/main/java/com/soulcraft/bot/BotPresence.java` | SoulCraft | Modify — TTS for chat responses |
| `src/client/java/com/soulcraft/client/tts/TextPreprocessor.java` | SoulCraft | Modify — sentence chunking |
| `src/client/java/com/soulcraft/client/tts/EmotionInference.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/voice/VoiceCapture.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/voice/SttClient.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/voice/VoiceChatManager.java` | SoulCraft | Create |
| `src/client/java/com/soulcraft/client/voice/VoiceChatKeyBind.java` | SoulCraft | Create |
| `src/main/java/com/soulcraft/network/payloads/VoiceTranscriptC2S.java` | SoulCraft | Create |
| `src/main/java/com/soulcraft/network/SoulCraftNetworking.java` | SoulCraft | Modify — register packet |
| `src/client/java/com/soulcraft/client/tts/TtsConfig.java` | SoulCraft | Modify — voice chat fields |

---

## Environment Variables (All Phases)

| Variable | Phase | Project | Description |
|----------|-------|---------|-------------|
| `AGENT_{NAME}_TOKEN` | 2 | slashAI | Discord bot token per agent (e.g., `AGENT_LENA_TOKEN`) |
| `SLASHAI_BRIDGE_URL` | 3 | SoulCraft | slashAI webhook server URL (fallback for config) |
| `SLASHAI_API_KEY` | 3 | Both | Bearer token for memory bridge auth |
| `CARTESIA_API_KEY` | 4, 5 | SoulCraft | Cartesia API key (shared by TTS and STT) |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Java WebSocket API limitations** — `java.net.http.WebSocket` is lower-level than OkHttp | Medium | Java 11+ WebSocket is functional. SoulCraft already uses `HttpClient`. If problematic, Netty's WebSocket (in Minecraft's dependency tree) is a fallback. |
| **Memory bridge latency** — HTTP round-trip adds 50-200ms to chat responses | Low | Fire memory retrieval in parallel with system prompt construction. 3-second timeout with graceful fallback to no-memory mode. |
| **Memory bridge unavailability** — slashAI server is down | Low | Health check on startup. All bridge operations are optional and catch exceptions silently. SoulCraft works standalone. |
| **Multi-agent Discord rate limits** — N bots sending messages | Medium | Each agent has its own token (separate rate limits). Agents only respond to mentions/DMs — low traffic. |
| **Persona file drift** — copied file gets out of sync between projects | Low | `schema_version` field for compatibility detection. Both projects validate and warn on unknown fields. |
| **Long TTS responses** — conversational replies longer than status narrations | Medium | `TextPreprocessor.chunkForTts()` splits on sentence boundaries. `NarrationQueue` sequences playback per-bot. Max queue size prevents buildup. |
| **Agent memory isolation** — memories leaking between personas | Medium | `agent_id` column with SQL `WHERE` enforcement. Main bot memories (`agent_id=NULL`) are shared knowledge, agent-specific memories are scoped. |
| **Microphone access on various OS** — `javax.sound.sampled` availability | Medium | `VoiceCapture.isMicrophoneAvailable()` checks before use. Voice chat is opt-in (`voiceChatEnabled: false` by default). Graceful degradation: no mic = text-only. |
| **Cartesia STT accuracy** — transcription quality in noisy environments | Medium | Show transcription in chat so player can verify. Allow text fallback anytime (voice chat supplements, doesn't replace text chat). |
| **Cartesia cost accumulation** — STT (1 credit/sec) + TTS (1 credit/char) | Low | Voice chat is opt-in and requires explicit API key. Cost tracking could be added to `CostTracker`. Kokoro (free) is always the default. |
| **Push-to-talk UX** — player must hold key while speaking | Low | Standard pattern (Discord, game voice chat). Alternative: voice activity detection (VAD) is possible but adds complexity and false triggers. Keep PTT as v1. |
