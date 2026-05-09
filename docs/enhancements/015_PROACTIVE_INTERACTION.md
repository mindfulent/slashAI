# Enhancement 015: Proactive Interaction & Agent-to-Agent Conversation

**Version**: 0.14.x (planned)
**Status**: 📋 Draft
**Author**: Slash + Claude
**Created**: 2026-05-08

## Overview

slashAI and persona agents (Lena, future personas) currently only speak when @-mentioned or DM'd. This enhancement gives every persona an autonomous interaction loop: each persona decides on its own whether to add an emoji reaction, reply to a recent message, post a new conversation starter, or engage another persona — bounded by daily budgets, channel allowlists, and quiet hours.

The system is multi-persona aware from inception. slashAI and Lena (and future personas) each run their own scheduler instance with independent budgets, but share an audit table for cross-persona coordination. Bot-to-bot conversation is a first-class feature, not a forbidden side-effect — bounded by hard turn caps, engagement-probability decay per turn, and human-interrupt-wins semantics.

## Research Foundations

Three pieces of prior art shape the design:

**Generative Agents (Park et al. 2023)** — the canonical multi-agent social-behavior architecture. Memory stream + reflection + planning, with a retrieval scoring formula and reflection trigger that we adopt nearly verbatim. The key insight is that *reflection* is what makes inter-agent interaction meaningful over time rather than random: agents accumulate higher-level beliefs about each other.

**Why Do Multi-Agent LLM Systems Fail? (Cemri et al. 2025, MAST taxonomy)** — 14 named failure modes across 3 categories. Several have direct mappings to defensive design choices in this system. The paper's central finding: structural fixes (verification, role anchoring, termination criteria) yield +9.4% to +15.6% on success rates *independent of model capability*. Translation: the hard part is the orchestration, not the model.

**Google's A2A Protocol** — open agent-interop standard contributed to Linux Foundation. Not adopted as a substrate (Discord *is* the message bus, and the charm of bot-to-bot interaction here is that humans can see and join it), but the agent-card concept influences the persona JSON schema.

## Key Insight: The Decision Pyramid

The decider must produce mostly silence. Concretely, target distribution per check:

```
~85% of decision ticks → no action
~10%                  → emoji reaction on a recent message
~4%                   → reply jumping into existing conversation
~1%                   → new topic / question to revive a quiet channel
```

Reactions are the sweet spot — low-stakes, charming, cheap. Replies and new-topics carry more risk and get smaller budgets. A misjudging proactive bot feels worse than a silent one.

---

## Part 1: Trigger Model (Heartbeat + Activity)

Two complementary paths feed the same decision pipeline:

### Activity path (high-frequency, low-stakes)

```
on_message (in allowlisted channel)
  → pre-filter (cooldown? budget? quiet hours? human conversation just-active?)
  → cheap decider (Haiku) → {react | reply | none}
  → mostly produces reactions
```

The on-message hook fires inside `discord_bot.on_message` and `agents/agent_client.AgentClient.on_message`, immediately after the existing mention/DM gate. Pre-filter is pure-Python and short-circuits before any LLM call.

### Heartbeat path (lower-frequency, higher-stakes)

```
discord.ext.tasks.loop(seconds=3600)
  → for each allowlisted channel:
      → if silent > silence_threshold during active hours
      → and "new topic" budget remaining
      → decider → {new_topic | engage_persona | none}
  → produces silence-breakers and persona-to-persona kickoffs
```

The heartbeat targets *quiet* channels that the activity path will never fire in. Without it, the system can only react to existing activity. With it, slashAI and Lena can break silence with a question or trade observations.

### Why both

Activity-driven is responsive but only fires when humans are talking. Heartbeat fires regardless and surfaces the silence-breaker behavior. The two paths share all downstream code: same decider, same actor, same budget tracking.

---

## Part 2: Persona Config Extensions

The `personas/*.json` schema gains a `proactive` section. This makes per-persona policy a first-class config artifact (echoing A2A's "agent card" concept) and lets Lena and slashAI have different temperaments.

```json
{
  "schema_version": 2,
  "name": "lena",
  "display_name": "Lena",
  "identity": { ... },
  "discord": { ... },
  "voice": { ... },
  "memory": { ... },

  "proactive": {
    "enabled": true,
    "channel_allowlist": ["1453800829986279554"],
    "budgets": {
      "reactions_per_day": 20,
      "replies_per_day": 4,
      "new_topics_per_day": 1,
      "inter_agent_turns_per_day": 6
    },
    "cooldowns": {
      "reaction_seconds": 600,
      "reply_seconds": 1800,
      "new_topic_seconds": 43200
    },
    "quiet_hours": {
      "timezone": "America/Los_Angeles",
      "start": "22:00",
      "end": "07:00"
    },
    "engagement_temperature": 0.85,
    "decider_model": "claude-haiku-4-5-20251001",
    "actor_model": "claude-sonnet-4-6",
    "silence_threshold_hours": 4,
    "engages_with_personas": ["slashai"]
  }
}
```

Defaults live in `proactive/policy.py` so personas can omit the section entirely. slashAI's primary bot gets a config block in `discord_bot.py` driven by env vars.

`engages_with_personas` is the explicit allowlist for inter-agent interaction. A persona must opt in to talking to another persona. Empty list = will not initiate persona-to-persona threads (but may still respond if engaged).

---

## Part 3: Module Layout

```
src/proactive/
├── __init__.py
├── scheduler.py       # tasks.loop heartbeat + on_message hook entry
├── policy.py          # cooldowns, budgets, allowlists, quiet hours, cross-persona lockout
├── observer.py        # context bundle: recent msgs + memory + persona state + budgets
├── decider.py         # Haiku call → action JSON + reasoning
├── actor.py           # add_reaction / send_message via existing primitives
├── store.py           # proactive_actions audit table operations
├── threads.py         # inter-agent thread lifecycle (start, advance, terminate)
└── reflection.py      # Park-style reflection job over inter-agent interactions
```

Each `ProactiveScheduler` instance is bound to one persona. `discord_bot.py` instantiates one for the primary `@slashAI` bot; `agents/agent_manager.py` instantiates one per loaded persona. They share the same `policy`, `store`, and `threads` modules so cross-persona coordination works.

---

## Part 4: Data Model

### `proactive_actions` (Migration 018a)

The audit table. Every decision is logged — actions *and* no-ops — so the decider prompt can be tuned from real traces.

```sql
-- Migration 018a: Create proactive_actions table
CREATE TABLE proactive_actions (
    id BIGSERIAL PRIMARY KEY,

    -- Who acted (persona ID matches personas/*.json `name`, or 'slashai' for primary)
    persona_id TEXT NOT NULL,

    -- Where
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    -- Decision
    decision TEXT NOT NULL,              -- 'none', 'react', 'reply', 'new_topic', 'engage_persona'
    trigger TEXT NOT NULL,               -- 'activity' or 'heartbeat'

    -- Action artifacts (NULL for decision='none')
    target_message_id BIGINT,            -- message reacted/replied to
    target_persona_id TEXT,              -- persona engaged (for engage_persona / reply-to-persona)
    emoji TEXT,                          -- for decision='react'
    posted_message_id BIGINT,            -- the message we created (for reply/new_topic)
    inter_agent_thread_id BIGINT,        -- if part of a persona-to-persona thread

    -- Decider trace
    reasoning TEXT,                      -- LLM's stated reason; debug/tuning
    confidence FLOAT,                    -- 0.0-1.0 from decider
    decider_model TEXT,                  -- which model made the call

    -- Cost tracking
    input_tokens INT,
    output_tokens INT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_proactive_persona_channel ON proactive_actions(persona_id, channel_id, created_at DESC);
CREATE INDEX idx_proactive_persona_day ON proactive_actions(persona_id, created_at DESC) WHERE decision != 'none';
CREATE INDEX idx_proactive_channel_recent ON proactive_actions(channel_id, created_at DESC) WHERE decision != 'none';
CREATE INDEX idx_proactive_thread ON proactive_actions(inter_agent_thread_id) WHERE inter_agent_thread_id IS NOT NULL;
```

The `idx_proactive_persona_day` index makes daily-budget queries cheap: `WHERE persona_id = $1 AND created_at >= $2 AND decision = $3`.

### `inter_agent_threads` (Migration 018b)

Tracks bot-to-bot conversation lifecycle so we can enforce turn caps, decay, and human-interrupt termination.

```sql
-- Migration 018b: Create inter_agent_threads table
CREATE TABLE inter_agent_threads (
    id BIGSERIAL PRIMARY KEY,

    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    initiator_persona_id TEXT NOT NULL,
    -- Participants is a JSONB array because future threads may have >2 personas
    participants JSONB NOT NULL,         -- ["slashai", "lena"]

    turn_count INT NOT NULL DEFAULT 0,
    max_turns INT NOT NULL DEFAULT 4,

    -- The seed: what triggered this thread
    seed_message_id BIGINT,              -- if reacting to a human message
    seed_topic TEXT,                     -- if cold-start (new_topic into engage_persona)

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_turn_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    ended_reason TEXT                    -- 'turn_cap', 'human_interrupt', 'natural_end', 'budget_exhausted'
);

CREATE INDEX idx_threads_active ON inter_agent_threads(channel_id) WHERE ended_at IS NULL;
CREATE INDEX idx_threads_participants ON inter_agent_threads USING GIN(participants);
```

There can be at most one active thread per channel. Starting a new one auto-terminates any existing active thread in that channel with `ended_reason='superseded'`.

### `agent_reflections` (Migration 018c)

Park-style reflections about other personas (and about humans, in scope). Distinct from regular memories because they cite source events and form trees.

```sql
-- Migration 018c: Create agent_reflections table
CREATE TABLE agent_reflections (
    id BIGSERIAL PRIMARY KEY,

    persona_id TEXT NOT NULL,            -- the persona doing the reflecting
    subject_type TEXT NOT NULL,          -- 'persona', 'user', 'channel', 'self'
    subject_id TEXT NOT NULL,            -- 'lena', '<discord_user_id>', '<channel_id>', or persona_id for self

    content TEXT NOT NULL,               -- "Lena tends to push back when I'm being formal"
    embedding vector(1024),              -- voyage-3.5-lite, for retrieval

    importance INT NOT NULL,             -- 1-10 from Park's importance prompt
    confidence FLOAT NOT NULL DEFAULT 0.7,

    -- Provenance: which observations (proactive_actions or message_ids) supported this reflection
    cites JSONB NOT NULL DEFAULT '[]',   -- [{"type": "action", "id": 123}, {"type": "message", "id": 456}]
    parent_reflection_id BIGINT REFERENCES agent_reflections(id),  -- for reflection trees

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_retrieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retrieval_count INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_reflections_persona_subject ON agent_reflections(persona_id, subject_type, subject_id);
CREATE INDEX idx_reflections_embedding ON agent_reflections USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_reflections_importance ON agent_reflections(persona_id, importance DESC);
```

Reflections feed back into the decider's context bundle. When slashAI considers replying to Lena, it retrieves prior reflections about Lena.

---

## Part 5: Pre-Filter (No-LLM Gate)

The pre-filter is the cheapest stage. It runs before any LLM call and rejects most ticks. Pure SQL + Python.

```python
# src/proactive/policy.py

@dataclass
class PreFilterContext:
    persona_id: str
    channel_id: int
    trigger: str  # 'activity' or 'heartbeat'
    now: datetime
    last_human_message_at: Optional[datetime]
    last_persona_action_at: Optional[datetime]   # this persona, this channel
    last_any_action_at: Optional[datetime]       # any persona, this channel (cross-persona lockout)


async def can_consider_acting(ctx: PreFilterContext, policy: ProactivePolicy) -> tuple[bool, str]:
    """
    Returns (allowed, reason). Reason is for logging/debugging when allowed=False.
    """
    if not policy.enabled:
        return False, "persona_disabled"

    if ctx.channel_id not in policy.channel_allowlist:
        return False, "channel_not_allowlisted"

    if _in_quiet_hours(ctx.now, policy.quiet_hours):
        return False, "quiet_hours"

    # Cross-persona lockout: any persona acted recently → wait
    if ctx.last_any_action_at:
        elapsed = (ctx.now - ctx.last_any_action_at).total_seconds()
        if elapsed < policy.cross_persona_lockout_seconds:
            return False, f"cross_persona_lockout ({elapsed:.0f}s < {policy.cross_persona_lockout_seconds}s)"

    # This persona's own cooldown (uses tightest of the per-action-type cooldowns)
    if ctx.last_persona_action_at:
        elapsed = (ctx.now - ctx.last_persona_action_at).total_seconds()
        if elapsed < policy.cooldowns.reaction_seconds:
            return False, f"persona_cooldown ({elapsed:.0f}s)"

    # Heartbeat-only: silence threshold
    if ctx.trigger == "heartbeat":
        if ctx.last_human_message_at is None:
            return False, "no_recent_activity_to_evaluate"
        silence = (ctx.now - ctx.last_human_message_at).total_seconds() / 3600
        if silence < policy.silence_threshold_hours:
            return False, f"channel_not_silent_enough ({silence:.1f}h)"

    # Daily budget exhausted? (cheap query against proactive_actions)
    budget = await store.daily_budget_remaining(ctx.persona_id, ctx.now)
    if budget.reactions == 0 and budget.replies == 0 and budget.new_topics == 0:
        return False, "all_budgets_exhausted"

    return True, "ok"
```

The cross-persona lockout default is **5 seconds** — short, just enough to avoid Lena and slashAI both jumping on the same opening at the same instant. It does *not* prevent multi-turn bot-to-bot threads (those are governed by the thread state).

---

## Part 6: The Decider

The decider is a Haiku call with a structured-output (JSON) response. It owns the "should I act, and what?" question. The actor takes its instruction and executes.

### Decider input

```python
# src/proactive/observer.py

@dataclass
class DeciderInput:
    persona: PersonaConfig
    channel_id: int
    trigger: str                          # 'activity' or 'heartbeat'

    recent_messages: list[FormattedMsg]   # last 10-20 messages, oldest first
    triggering_message: Optional[FormattedMsg]  # for activity trigger

    relevant_memories: list[str]          # top 3 from memory.retrieve(channel_query)
    reflections_about_others: list[str]   # top 3 reflections about other participants

    budget_remaining: BudgetSummary
    last_action_summary: str              # "Reacted with 🔥 ~12 min ago" or "No prior action today"

    other_personas_present: list[str]     # personas with bots in this guild
    other_personas_recent: list[str]      # personas that acted in this channel within last hour

    active_inter_agent_thread: Optional[ThreadState]  # if one is running

    now_local: str                        # "Friday 2026-05-08 14:23 PDT"
    is_human_conversation_active: bool    # 2+ humans, msgs within last 2 min
```

### Decider prompt (template)

```
You are deciding whether {persona_display_name} should act in a Discord channel.

# Persona
{persona_summary}

# Current channel state
Channel: #{channel_name}
Time: {now_local}
Trigger: {trigger}  ({"new message arrived" if trigger == "activity" else "scheduled silence check"})

Recent messages (oldest first):
{formatted_recent_messages}

{if triggering_message}
The triggering message:
{triggering_message_formatted}
{endif}

# What {persona_display_name} knows
Relevant memories:
{relevant_memories or "(none)"}

What I've previously concluded about people in this channel:
{reflections or "(none)"}

# State
Budget remaining today: {budget.reactions} reactions, {budget.replies} replies, {budget.new_topics} new topics
{last_action_summary}
{if other_personas_present}
Other AI personas in this server: {other_personas_present}
{endif}
{if active_inter_agent_thread}
Active thread with {thread.other_participant}: turn {thread.turn_count}/{thread.max_turns}.
{endif}
Human conversation active: {yes/no}

# Decide
Respond with strict JSON. Bias HARD toward "none" — most ticks should be no-ops.

Reactions are the sweet spot: low-stakes, charming. Replies should be sparing — only when there's a clear opening. New topics should be rare — only in genuinely quiet channels with a real reason to engage.

If a human conversation is active, prefer "none" or a single reaction. Don't crowd humans out.
If another persona just acted, prefer "none" — don't pile on.

Schema:
{
  "action": "none" | "react" | "reply" | "new_topic" | "engage_persona",
  "target_message_id": <int or null>,    // required for react, reply
  "target_persona_id": <string or null>, // for engage_persona, or reply targeting another persona
  "emoji": <string or null>,             // single unicode emoji, required for react
  "reasoning": <string>,                 // 1-2 sentences; will be logged
  "confidence": <float 0.0-1.0>
}
```

### Decider sanitization

The actor validates the decider's output before executing:

- `action` must be one of the budgeted options that has remaining budget
- `target_message_id` must exist in `recent_messages`
- `emoji` must be a single unicode codepoint sequence (no custom emoji, no text)
- `target_persona_id` (if set) must be in this persona's `engages_with_personas` list
- If decider hallucinates anything, the actor logs it and falls back to `action="none"`

This addresses **MAST FM-2.6 (Reasoning-Action Mismatch, 13.2%)** and **FM-1.2 (Disobey Role Specification, 1.5%)**: the decider can suggest, but the actor enforces.

---

## Part 7: The Actor

```python
# src/proactive/actor.py

class ProactiveActor:
    def __init__(self, persona, bot, claude_client, store, threads):
        self.persona = persona
        self.bot = bot
        self.claude = claude_client
        self.store = store
        self.threads = threads

    async def execute(self, decision: ValidatedDecision, ctx: DeciderInput) -> ActionResult:
        if decision.action == "none":
            return await self._log_noop(decision, ctx)

        if decision.action == "react":
            return await self._do_reaction(decision, ctx)

        if decision.action == "reply":
            return await self._do_reply(decision, ctx)

        if decision.action == "new_topic":
            return await self._do_new_topic(decision, ctx)

        if decision.action == "engage_persona":
            return await self._do_engage_persona(decision, ctx)

    async def _do_reaction(self, decision, ctx):
        channel = self.bot.get_channel(ctx.channel_id)
        message = await channel.fetch_message(decision.target_message_id)
        await message.add_reaction(decision.emoji)
        await self.store.record_action(...)
        return ActionResult(success=True)
```

### Reply generation

For `action="reply"`, the actor makes a *second* LLM call (Sonnet) using the persona's full system prompt + the same context bundle, with one extra instruction:

> You decided to reply to message-id {target_id}: "{message_content}". Write the reply. Keep it short — 1-3 sentences max. Match the channel's tone. No trailing questions.

This separation matters: the cheap decider chooses *whether*; the expensive actor crafts *what*. It's also where `actor_model` from the persona config plays — different personas can have different voices.

### New topic generation

For `action="new_topic"`, the actor pulls the channel's recent activity history (a wider window, last few days), retrieves relevant memories, and generates a starter:

> The channel #{channel_name} has been quiet for {silence_hours}h. As {persona_display_name}, write a short message that sparks conversation — could be a question, an observation, a callback to a recent topic. 1-2 sentences. No "Hey everyone!" preamble.

### Engage persona

For `action="engage_persona"`, the actor:

1. Opens an `inter_agent_threads` row with `turn_count=0`, participants `[self_persona, target_persona]`
2. Generates an opening that @-mentions the target bot
3. Sends the message
4. The target persona's activity-path on_message hook fires when it receives the @-mention; the existing chat flow takes over for the response (no special path needed for the target — they just see a message addressed to them)

The originator and target both use their normal chat handlers for the actual back-and-forth. The thread state machine in `threads.py` simply observes and terminates when limits hit.

---

## Part 8: Inter-Agent Threads (A2A)

### Lifecycle

```
[active] --(turn_count >= max_turns)--> [ended: turn_cap]
[active] --(human message arrives)----> [ended: human_interrupt]
[active] --(decider returns "none" 2x in a row)--> [ended: natural_end]
[active] --(originator's daily budget hits 0)----> [ended: budget_exhausted]
[active] --(another thread starts in same channel)--> [ended: superseded]
```

### Turn engagement-probability decay

Each turn, the engagement probability for the *next* turn drops. Even if budget allows, the system biases toward winding down a thread:

```python
def engagement_decay_factor(turn_count: int) -> float:
    """At turn 0, no decay. At turn 4, ~20% probability."""
    return max(0.2, 1.0 - 0.2 * turn_count)
```

This is applied in the decider prompt: when an active thread is in context, the decider is told "you've already had {turn_count} turns with {target_persona}; further engagement should be only if there's a strong reason."

### Human-interrupt-wins

`on_message` checks: if a human posts in a channel with an active thread, the thread is immediately ended with `ended_reason='human_interrupt'`. The bots stop responding to *each other* and revert to normal mention-only behavior. This is the single most important guardrail — without it, the bots can crowd humans out of their own channel.

### One thread per channel

There can be at most one active `inter_agent_threads` row per channel. Starting a new one (e.g., a different persona pair) supersedes any existing.

This addresses **MAST FM-1.5 (Unaware of Termination Conditions, 12.4%)** and **FM-1.3 (Step Repetition, 15.7%)** — the two most prevalent failure modes in multi-agent systems are exactly the ones bot-to-bot Discord chat is most prone to.

---

## Part 9: Reflection Job (Park-Style)

Reflections are what make A2A meaningful over time. Without them, slashAI and Lena chatting is two LLMs wiggling at each other. With them, they accumulate beliefs about each other ("Lena pushes back when I'm overly formal") that surface in future exchanges.

### Adopted nearly verbatim from Park et al. (2023)

**Importance scoring** — for each new observation (a turn within an inter-agent thread, a notable user reaction, a memory extraction event), call the LLM with Park's exact prompt:

```
On the scale of 1 to 10, where 1 is purely mundane (e.g., brushing teeth, making bed)
and 10 is extremely poignant (e.g., a break up, college acceptance), rate the likely
poignancy of the following piece of memory.
Memory: {observation_text}
Rating:
```

This rating is stored on the observation row.

**Reflection trigger** — track the running sum of importance scores per persona since their last reflection. When it exceeds **150** (Park's threshold), trigger reflection. In Park's sim this fired ~2-3 times per agent per day; in slashAI with much less activity it will be slower, which is fine.

**Salient questions prompt** — given the 100 most recent memories for this persona, generate questions:

```
Given only the information above, what are 3 most salient high-level questions
we can answer about the subjects in the statements?
```

**Reflection synthesis** — for each generated question, retrieve relevant memories using Park's scoring formula (below), then synthesize:

```
Statements about {subject}:
1. {memory_1}
2. {memory_2}
...
What 5 high-level insights can you infer from the above statements?
(example format: insight (because of 1, 5, 3))
```

The cited indices form provenance; we store them in `agent_reflections.cites`.

### Memory retrieval scoring (Park formula)

```python
def retrieval_score(
    memory: Memory,
    query_embedding: list[float],
    now: datetime,
) -> float:
    # All α = 1 in Park's implementation; tunable here
    alpha_recency = 1.0
    alpha_importance = 1.0
    alpha_relevance = 1.0

    # Recency: exponential decay over hours since last retrieval, factor 0.995
    hours_since = (now - memory.last_retrieved_at).total_seconds() / 3600
    recency = 0.995 ** hours_since

    # Importance: 1-10 LLM rating, normalized to [0,1]
    importance = memory.importance / 10.0

    # Relevance: cosine similarity
    relevance = cosine_similarity(memory.embedding, query_embedding)

    # Min-max normalize all three within the candidate set (caller does this batch-wise)
    return alpha_recency * recency + alpha_importance * importance + alpha_relevance * relevance
```

Park does min-max normalization across the *candidate set* before combining — the function above returns the pre-normalized components, and the caller normalizes a batch.

### Reflection retrieval into the decider

Before each decider call, the observer runs a reflection retrieval:

```python
relevant_reflections = await reflections.retrieve(
    persona_id=persona.name,
    query=triggering_message_or_channel_topic,
    subject_filter=other_participants,  # only reflections about people in this channel
    limit=3,
)
```

These get formatted into the decider prompt under "What I've previously concluded about people in this channel."

---

## Part 10: Failure-Mode Defenses (MAST mapping)

Each named failure mode from Cemri et al. with prevalence and the specific defense:

| MAST Failure Mode | Prev | Defense in this design |
|---|---|---|
| FM-1.1 Disobey Task Specification | 11.8% | Strict JSON schema for decider; actor validates and falls back to `none` on any violation |
| FM-1.2 Disobey Role Specification | 1.5% | Persona system prompt is non-negotiable in actor calls; decider cannot mutate it |
| FM-1.3 Step Repetition | 15.7% | One thread per channel; `idx_proactive_persona_channel` powers cooldown checks |
| FM-1.4 Loss of Conversation History | 2.8% | Thread context bundle includes last N turns from `proactive_actions` |
| FM-1.5 Unaware of Termination Conditions | 12.4% | Hard turn cap + decay + human-interrupt + budget-exhaustion = 4 separate stop conditions |
| FM-2.1 Conversation Reset | 2.2% | Thread state stored in DB; `last_turn_at` tracked; resets are explicit not implicit |
| FM-2.3 Task Derailment | 7.4% | Heartbeat decider always re-anchors on channel topic + persona identity each call |
| FM-2.6 Reasoning-Action Mismatch | 13.2% | Actor sanitization layer: target_message_id must exist; emoji must be valid unicode; otherwise no-op |
| FM-3.1 Premature Termination | 6.2% | Decider prompt distinguishes "thread is winding down" from "stop entirely" |
| FM-3.2 No or Incomplete Verification | 8.2% | Audit table captures reasoning + confidence for every decision; weekly review surfaces patterns |

Two MAST modes are *not* defended against because they don't apply: FM-2.4 (Information Withholding) is not relevant when there's no shared task, and FM-2.5 (Ignored Other Agent's Input) is actually *desirable* sometimes — a persona may decide not to engage a peer's message, and that's a valid decision, not a failure.

### Sycophancy spirals (separate concern)

Cemri's MAST doesn't list sycophancy explicitly, but it's the most-cited multi-agent risk in adjacent literature (~58% baseline in single-LLM interactions). Defenses:

- Different `actor_model` between personas (slashAI on Sonnet, Lena on Opus, or different temperatures)
- Persona system prompts include explicit "you can disagree with other AI personas; you are not them"
- Reflection job will surface sycophancy patterns after the fact ("I keep agreeing with Lena even when I shouldn't") — the user can read these and tune

---

## Part 11: Budgets and Rate Limits

### Per-persona daily budgets (defaults)

| Action | Default cap | Notes |
|---|---|---|
| Reactions | 15 | Most common action; cheap |
| Replies | 3 | More cautious — needs an opening |
| New topics | 1 | Spiciest; only when channel is genuinely quiet |
| Inter-agent turns | 4 | Across at most 1 thread; counts in originating persona's budget |

A 4-turn slashAI ↔ Lena exchange consumes 2 of slashAI's "reply" budget AND 2 of Lena's. The *initiator* of an `engage_persona` action also consumes 1 "new topic" if cold-started.

### Cooldowns (defaults)

| Cooldown | Default | Why |
|---|---|---|
| Per-persona per-channel reaction | 10 min | Prevents reaction-spam in active channels |
| Per-persona per-channel reply | 30 min | Replies are higher-stakes |
| Per-persona per-channel new-topic | 12 hours | Genuinely rare |
| Cross-persona any-action | 5 sec | Only stops simultaneous jumps — not designed to prevent threads |

### Daily budget query

```python
async def daily_budget_remaining(persona_id: str, now: datetime) -> BudgetSummary:
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = await db.fetch("""
        SELECT decision, COUNT(*) as n
        FROM proactive_actions
        WHERE persona_id = $1
          AND created_at >= $2
          AND decision != 'none'
        GROUP BY decision
    """, persona_id, today_start)
    used = {r['decision']: r['n'] for r in rows}
    return BudgetSummary(
        reactions=max(0, policy.budgets.reactions_per_day - used.get('react', 0)),
        replies=max(0, policy.budgets.replies_per_day - used.get('reply', 0) - used.get('engage_persona', 0)),
        new_topics=max(0, policy.budgets.new_topics_per_day - used.get('new_topic', 0)),
    )
```

Note that `engage_persona` counts as a reply (it's a message creating an opening for another persona).

---

## Part 12: Slash Commands

Owner-only commands for tuning and debugging:

| Command | Description |
|---|---|
| `/proactive enable <channel>` | Add channel to allowlist (per-persona; runs on the persona that received the command) |
| `/proactive disable <channel>` | Remove channel from allowlist |
| `/proactive status` | Show current state: enabled, allowlist, today's actions, remaining budget, active threads |
| `/proactive history [persona] [channel] [limit]` | Last N decisions (incl. no-ops) with reasoning — primary tuning surface |
| `/proactive threads` | Active and recent inter-agent threads |
| `/proactive simulate <channel>` | Run the decider against current state without acting; show the JSON it would have returned |
| `/proactive reflect <persona>` | Force-run reflection job for a persona (for testing) |

`/proactive simulate` is the single most useful command for tuning — it lets the operator probe the decider's judgment without consequences.

---

## Part 13: Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PROACTIVE_ENABLED` | No | `false` | Master kill switch — must be `true` for any persona to act |
| `PROACTIVE_SHADOW_MODE` | No | `true` | When `true`, decider runs and logs but actor is no-op (pure logging mode) |
| `PROACTIVE_HEARTBEAT_INTERVAL_SECONDS` | No | `3600` | Heartbeat loop period |
| `PROACTIVE_DECIDER_MODEL` | No | `claude-haiku-4-5-20251001` | Default decider model |
| `PROACTIVE_ACTOR_MODEL` | No | `claude-sonnet-4-6` | Default actor model |
| `PROACTIVE_CROSS_PERSONA_LOCKOUT_SECONDS` | No | `5` | Min gap between any persona's actions in a channel |

Per-persona overrides live in `personas/*.json`; for the primary `@slashAI` bot, the same config block lives at `PROACTIVE_PRIMARY_CONFIG_JSON` env or a default in code.

`PROACTIVE_SHADOW_MODE=true` is the default so the system can be deployed and observed before any actual actions fire.

---

## Part 14: State Progression by Version

(Deliberately no time estimates — each band is a coherent state the system can ship in.)

### v0.14.0 — Foundations: shadow mode, audit-only

```
├── Migration 018a: proactive_actions
├── policy.py with full pre-filter logic + tests
├── observer.py (read-only context bundle assembly)
├── decider.py with Haiku call + JSON validation
├── store.py (audit writes, daily budget queries)
├── scheduler.py: heartbeat loop + on_message hook (running, but actor is no-op)
├── PROACTIVE_SHADOW_MODE=true forced
└── /proactive history command for reading the trace
```

State at end of band: every channel decision is logged with full reasoning, but no Discord action is taken. The operator reads the log to tune the decider prompt.

### v0.14.1 — Reactions only

```
├── actor.py: react path implemented; reply / new_topic / engage_persona still no-op
├── /proactive enable, /proactive disable, /proactive status commands
└── PROACTIVE_SHADOW_MODE removed; PROACTIVE_ENABLED gates actor, not just logging
```

State: reactions fire in allowlisted channels for one persona (slashAI) in one channel (chosen by operator). Lena still shadow-only.

### v0.14.2 — Multi-persona + replies

```
├── agents/agent_manager.py: instantiate ProactiveScheduler per persona
├── personas/lena.json: proactive section added
├── actor.py: reply path implemented (Sonnet call with persona prompt)
└── Cross-persona lockout enforced via store query
```

State: slashAI and Lena both proactive in the same channel. Replies fire. Inter-agent interactions are not yet modeled — Lena replying to slashAI is just a normal reply that happens to target slashAI's message.

### v0.14.3 — Inter-agent threads (A2A)

```
├── Migration 018b: inter_agent_threads
├── threads.py: lifecycle state machine (start, advance, terminate)
├── actor.py: engage_persona path
├── decider.py: thread-aware context (turn_count, decay applied to next-turn probability)
├── Human-interrupt termination path in on_message
└── /proactive threads command
```

State: slashAI can intentionally engage Lena (and vice versa). Threads have hard turn caps, decay, and human-interrupt-wins. Reflections still not produced.

### v0.14.4 — Reflection + heartbeat

```
├── Migration 018c: agent_reflections
├── reflection.py: importance scoring (Park prompt), threshold tracking, salient questions, synthesis
├── observer.py: includes top-3 relevant reflections in decider context
├── Heartbeat loop produces new_topic decisions
└── /proactive reflect command for testing
```

State: full system. Personas accumulate reflections about each other and about humans. Heartbeat fires silence-breakers in genuinely quiet channels.

### v0.14.5 — Polish

```
├── Sycophancy detection: weekly job analyzes inter-agent threads for agreement spirals
├── Backfill reflections: importance-rate existing memories
├── /proactive simulate command (decider dry-run)
└── Documentation + CHANGELOG
```

---

## Part 15: Open Questions

1. **Should the activity path also use Haiku, or is even Haiku too expensive at on_message frequency in busy channels?** — May need a heuristic-only "stage 0" filter (e.g., "did the message contain a question mark, the persona's name, or a topic from the persona's interests list?") before invoking even Haiku.

2. **How does the reflection job handle DMs?** — Reflections about people the persona only knows from DMs need to be scoped to `dm` privacy in the existing memory privacy model, and not surface in the decider when the persona is in a public channel.

3. **What happens when a persona has no `personas/*.json` config but is the primary `@slashAI` bot?** — The primary bot needs its own pseudo-persona representation in `proactive_actions` (we use `persona_id='slashai'`) but doesn't go through `personas/*.json`. May want to canonicalize this by writing `personas/slashai.json` even though no separate bot token exists.

4. **Cost controls when bot-to-bot threads happen during traffic spikes?** — The 4-turn cap handles individual threads, but a guild-wide daily $ budget may also be needed. Prometheus-style cost metric per persona per day, with a hard ceiling that disables the actor.

5. **Should reflections decay?** — slashAI's existing memory decay system (Enhancement 011) may want to apply. A reflection from 6 months ago about a user no longer in the server is dead weight. Probably yes, but with a longer half-life than ordinary observations because reflections are more important.

---

## Part 16: Files to Create / Modify

### New files

```
src/proactive/__init__.py
src/proactive/scheduler.py
src/proactive/policy.py
src/proactive/observer.py
src/proactive/decider.py
src/proactive/actor.py
src/proactive/store.py
src/proactive/threads.py
src/proactive/reflection.py
migrations/018a_create_proactive_actions.sql
migrations/018b_create_inter_agent_threads.sql
migrations/018c_create_agent_reflections.sql
src/commands/proactive_commands.py
tests/test_proactive_policy.py
tests/test_proactive_decider_validation.py
tests/test_proactive_threads.py
tests/test_reflection_scoring.py
```

### Modified files

```
src/discord_bot.py          - on_message activity-path hook; instantiate ProactiveScheduler for primary
src/agents/agent_client.py  - on_message activity-path hook for personas
src/agents/agent_manager.py - instantiate ProactiveScheduler per persona on start_all
src/agents/persona_loader.py - parse new `proactive` section in PersonaConfig
personas/lena.json          - add `proactive` block
src/claude_client.py        - expose actor-call path with persona-specific actor_model
src/analytics.py            - add proactive_decision and inter_agent_turn event types
CLAUDE.md                   - document proactive system, env vars, migrations 018a-c
docs/enhancements/README.md - add Enhancement 015 to the index
```

---

## References

- Park, J.S., O'Brien, J.C., Cai, C.J., Morris, M.R., Liang, P., Bernstein, M.S. (2023). *Generative Agents: Interactive Simulacra of Human Behavior*. UIST '23. arXiv:2304.03442. — memory retrieval formula, importance prompt, reflection threshold (150), salient-questions prompt, planning cascade.
- Cemri, M., Pan, M.Z., Yang, S., et al. (2025). *Why Do Multi-Agent LLM Systems Fail?* arXiv:2503.13657. — 14-mode MAST taxonomy with prevalence; structural defenses yielding +9.4% to +15.6% on success rates.
- Google. *Agent2Agent (A2A) Protocol*. https://a2a-protocol.org/. — agent-card concept influencing persona JSON schema.
