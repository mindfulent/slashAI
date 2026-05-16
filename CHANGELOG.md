# Changelog

All notable changes to slashAI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — StreamCraft webhook-error ops alert

New `POST /server/streamcraft-webhook-error` endpoint on the slashAI webhook server, paired with `notifyWebhookError()` in the theblockacademy backend. Discord embed in the ops channel whenever `/streamcraft/webhooks/livekit` (or future webhook handlers) throws inside the outer catch — source, event type, room name, first 900 chars of the error message. Caller-side 15-minute dedup per (source, eventType, normalized error-prefix) so a flapping bug fires once per window instead of spamming.

Motivation: the 2026-05-12 → 05-16 silent-drop of `participant_joined` webhooks was logged to `console.error` the whole time but nobody was reading the logs; lost 5 days of usage data. The next variant of this class of bug now pages within a few seconds.

### Planned
- **slashAI Desktop** — Tauri (Rust) system tray app for screen share vision in voice chat (see `docs/DESKTOP-PLAN.md`)
- Slash command support (`/ask`, `/summarize`, `/clear`)
- Rate limiting and token budget management
- Multi-guild configuration support
- User commands for build management (`/builds`, `/myprojects`)
- Automatic milestone detection with notifications

---

## [0.16.5] - 2026-05-08

### Added — Polish + dry-run tooling (Enhancement 015 band 5; system complete)

Last band of Enhancement 015. Ships the operator's primary tuning surface (`/proactive simulate`), a heuristic sycophancy detector for spotting mutual-validation loops, and a one-shot backfill script for first-deploy importance scoring.

- **`/proactive simulate <channel> [persona]`** — Dry-run the decider against the current channel state. Runs pre-filter, observer (full context bundle), and decider, but skips the actor entirely. Returns an embed with: pre-filter outcome + reason, remaining budget, the decider's would-be JSON (action, target, emoji, confidence, reasoning), context summary (recent message count, memories surfaced, reflections surfaced, active thread state). The single most useful command for tuning the decider prompt without consequences.
- **`src/proactive/scheduler.py:simulate_decision(channel)`** — Underlying method. Builds the same `PreFilterContext` and `DeciderInput` a real tick would; calls the decider; returns a structured dict. Skips actor, store recording, analytics, and natural-end checks.
- **`src/proactive/sycophancy.py`** — Heuristic agreement-language detector. `count_agreement_cues(text)` regex-matches a curated cue list (`agree`, `echoing`, `validating`, `spot on`, `fair point`, `+1`, `yeah`, `exactly`, etc.) with word-boundary discipline so "agreeable" doesn't match "agreement". `SycophancyDetector.per_persona(days=7)` aggregates reply counts and cue hits per persona over the lookback window. `per_thread(days=7, limit=20)` shows per-thread density so operators can spot the worst offenders. Detection runs against `proactive_actions.reasoning` (the decider's stated 'why') — not message bodies, which we don't store. Documented as a tuning signal, not a model-grade detector.
- **`/proactive sycophancy [days] [view]`** — Owner-only embed. `view=persona` shows per-persona agreement rate (cue_hits / reply_count); `view=thread` shows per-thread density with end_reason. Footnote: rate >0.5 is suggestive of a mutual-validation pattern.
- **`scripts/backfill_reflection_importance.py`** — CLI wrapper around `ReflectionEngine.score_unscored_actions`. Args: `--persona`, `--all-personas`, `--batch-size`, `--max-rows`, `--dry-run`. The heartbeat-time scorer batches at 20 rows/tick to keep ticks short; this script removes that bound for first-deploy backlogs. `--dry-run` counts unscored rows per persona and exits before touching Anthropic — useful for sizing a backfill before paying for it.
- **14 new tests** (`tests/test_proactive_sycophancy.py`) — Agreement-cue word-boundary discipline (agreement/agreeable both tested), case insensitivity, none/empty input, multi-hit aggregation, JSONB participants in str/list/malformed forms, zero-division safety in agreement-rate.

### Architectural notes

- **`/proactive simulate` is the primary tuning surface.** Operators tweak the decider prompt (or persona identity, or budgets) and run simulate against a channel they care about. The decider's reasoning + confidence reveal whether the change moved judgment in the desired direction. No more "wait an hour for the heartbeat to fire and produce one trace."
- **Sycophancy detection is a starting point.** A model-grade detector would compare consecutive turn embeddings for similarity drift, score insight novelty across a thread, and look at how often a persona's reasoning references the *other* persona's prior turn. The heuristic version ships as a cheap signal that doesn't require message-body access. The cue list is in `_AGREEMENT_CUES` and is meant to be tuned by the operator over time — append cues that show up in their server's actual chat.
- **Backfill script is one-shot, not idempotent**. Running it twice is safe (the SQL filters on `importance IS NULL`), but it's intended for first-deploy backlog clearance. Steady-state scoring belongs to the heartbeat.

### Enhancement 015 — system complete

Bands 0-5 all live:
- v0.16.0 — shadow-mode audit (proactive_actions, decider, sanitization, /proactive history)
- v0.16.1 — reactions live in allowlisted channels
- v0.16.2 — replies live, Lena turns on, cross-persona lockout
- v0.16.3 — inter-agent threads with all 5 termination conditions
- v0.16.4 — Park-style reflection + heartbeat new_topic
- v0.16.5 — /proactive simulate, sycophancy detection, backfill script

137 proactive tests, 4 migrations, 11 source files in `src/proactive/`, 1 cog with 8 commands.

Open questions from the spec that remain explicitly out-of-scope (see `docs/enhancements/015_PROACTIVE_INTERACTION.md`):
- Open Q1 (stage-0 heuristic before Haiku): defer until cost data justifies it.
- Open Q2 (reflection DM-privacy): reuses existing memory privacy filter.
- Open Q4 (guild-wide $ ceiling): defer until cost data justifies it.
- Open Q5 (reflection decay): defer; reflections are low-volume.

---

## [0.16.4] - 2026-05-08

### Added — Park-style reflection + heartbeat new_topic (Enhancement 015 band 4)

The proactive subsystem now accumulates beliefs about other personas, users, and channels, and breaks silence in genuinely quiet channels. Personas reflect on what they've done — and what it told them about the people they interact with — using the Generative Agents (Park et al. 2023) prompt sequence nearly verbatim.

- **Migration `018d_add_proactive_importance.sql`** — adds `importance INT NULL` to `proactive_actions` plus partial indexes for unscored rows and importance-sum queries. NULL = unscored; the reflection job batch-fills retroactively so the decider stays cheap.
- **`src/proactive/reflection.py`** — full `ReflectionEngine` implementation:
  - `score_importance(text)` — Park's 1-10 poignancy prompt with a strict "return a single integer" system message
  - `score_unscored_actions(persona_id)` — bounded batch (default 20) so a heartbeat can't run away on a backlog
  - `accumulated_importance_since_last_reflection(persona_id)` — sums scored rows since the persona's most recent `agent_reflections.created_at`; threshold defaults to Park's **150**
  - `salient_questions(memories)` — Park's exact 3-questions prompt; resilient parser tolerates non-numbered prose
  - `synthesize_for_question(question, memories)` — Park's "5 high-level insights" prompt with `(because of N, M)` citation format; falls back to citation-free insights when the model breaks format
  - `store_reflection(...)` — embeds content via Voyage 1024-dim (matches `agent_reflections.embedding` in migration 018c), inserts with provenance JSONB
  - `retrieve_about(persona_id, query, subject_filter, limit)` — vector search (cosine distance via pgvector ivfflat) filtered by subject_id; falls back to recency-ordered subject_filter match when Voyage is unavailable
  - `maybe_reflect(persona_id, force=False)` — full pipeline: score → check threshold → questions → per-question synthesis → store. Returns `ReflectStats` with counters for observability
- **`src/proactive/scheduler.py`** — heartbeat tick now calls `maybe_reflect` after channel iteration. Wrapped in `try/except` so a reflection failure can't kill the loop. Logs at INFO when reflections were stored, DEBUG when only scoring happened.
- **`src/proactive/observer.py`** — populates `DeciderInput.reflections_about_others` via `retrieve_about`. Subject filter combines other persona names + recent human author IDs + the channel ID, so reflections about anyone visible in this conversation surface to the decider. Query text is the triggering message content (or the most recent human message when on heartbeat).
- **`src/proactive/actor.py:_do_new_topic`** — silence-breaker generation. Pulls last 72h of human-only history (wider than the decider's 15-message window) plus relevant memories + reflections, feeds them to the persona's actor model with the spec's new-topic directive ("1-2 sentences, no preamble, no @-mentions, match the channel's tone"). Posts via `channel.send` (not `reference=` — this isn't a reply). Graduated out of `PROACTIVE_SHADOW_MODE`.
- **`/proactive reflect [persona]`** — owner-only force-trigger. Runs `maybe_reflect(force=True)` to bypass the importance threshold so the operator can verify the synthesis pipeline end-to-end. Reports newly-scored count, accumulated importance, salient questions, and stored reflection count.
- **34 new tests** (`tests/test_proactive_reflection.py`) — parsing helpers (importance, questions, insights with strict + fallback formats), threshold heuristic at boundary, `maybe_reflect` orchestrator branching (early returns at threshold-not-reached / no-memories / no-questions), Park importance prompt via mocked Anthropic, and `score_unscored_actions` issuing per-row UPDATEs.

### Architectural notes

- **Voyage is optional**. The engine lazy-initializes `voyageai.AsyncClient` only when first needed, and `_embed` returns `None` if the env var is missing or the import fails. `store_reflection` still inserts when embedding fails (with `embedding=NULL`); `retrieve_about` falls back to recency-ordered subject_filter match. This means deployments without Voyage still log reflections — they just can't vector-rank them.
- **Why score retroactively**: importance scoring is one extra LLM call per non-none decision. Doing it inline at decision time would double the proactive tick latency. Doing it in batches at heartbeat time spreads the cost and keeps the activity-path responsive. The trade-off is that the threshold check lags reality by up to one heartbeat interval.
- **Subject inference is heuristic**: actions targeting a persona → subject_type='persona', target_persona_id; otherwise → subject_type='channel', channel_id. Future work (band 5+) will infer 'user' subjects from `target_message_id → message author` lookups.
- **TODO from band 2 still live**: proactive replies aren't yet fed into the reflection job's importance source set (only proactive_actions rows are). Reactions and engage_persona openings are scored, replies are scored, but inbound mentions and chat-handler responses aren't visible to the reflection job. Band 5 will close this gap.

### Operator workflow (band 4)

1. Apply migration 018d (auto on bot restart).
2. Confirm `VOYAGE_API_KEY` is set if you want vector-ranked reflection retrieval. Without it, the engine falls back to recency-ordered subject_filter match.
3. Restart. The heartbeat will start scoring proactive_actions importance. Watch `/proactive history` for the importance column populating.
4. Once a persona's accumulated importance crosses 150 (typically several days for normal traffic), the next heartbeat tick will synthesize reflections automatically. Or use `/proactive reflect` to force-trigger.
5. Subsequent decider calls will surface reflections in the prompt's "What I've previously concluded about people in this channel" section — visible in `/proactive history` reasoning fields when the persona references them.

---

## [0.16.3] - 2026-05-08

### Added — Inter-agent threads (Enhancement 015 band 3)

Personas can intentionally engage one another with hard turn caps, engagement-probability decay per turn, and human-interrupt-wins semantics. The thread state machine is observation-only — both bots use their normal chat handlers for the back-and-forth; threads.py just watches for limit conditions.

- **`src/proactive/threads.py`** — Full `InterAgentThreads` implementation. `start_thread` supersedes any active thread in the same channel and returns a `ThreadState` with `turn_count=0`. `advance_thread` is called from the actor (after posting a seed) and from on_message hooks (when the other participant bot posts). `end_thread` is idempotent. `check_natural_end` returns `True` iff the persona's last 2 non-prefilter decisions in the thread are both `'none'` — pre-filter rejections (cooldowns, quiet hours) are excluded from the heuristic so they don't kill threads prematurely. `engagement_decay_factor(turn_count)` returns 1.0 at turn 0 → 0.2 floor at turn 4+, exposed as a percentage hint in the decider prompt.
- **`src/proactive/actor.py:_do_engage_persona`** — Resolves the target persona's bot user.id via the AgentManager-provided callable, generates an opening via the persona's actor model with the spec's engage directive (1-2 sentences, no @-mention written by the LLM since the actor prepends one), posts via `channel.send()`, then creates the thread row + advances to turn 1. Failure modes (no anthropic client, target not connected, send HTTP error) all log `actor_failed: <reason>` without leaving phantom thread rows. Graduated out of `PROACTIVE_SHADOW_MODE` like reactions and replies.
- **`src/proactive/scheduler.py:on_message_hook`** — Now handles bot messages too (previously filtered). Three concerns in one entry point: (1) human-interrupt termination — any non-bot message in an active-thread channel ends the thread immediately; (2) participant-bot message advances the thread + checks turn cap; (3) activity-path tick fires for participant-bot messages too while a thread is alive, so the persona can decide to continue. Natural-end check runs after every `'none'` decision in a thread.
- **`src/proactive/observer.py`** — Populates `DeciderInput.active_inter_agent_thread` with `{id, turn_count, max_turns, other_participant, decay_factor, we_are_initiator}`. Decider prompt renders this as a written cue: "Active bot-to-bot thread with @lena: turn 2/4. Engagement strength is decaying — only continue if there's a clear reason to. Probability hint: ~60%."
- **`src/agents/agent_manager.py`** — Now accepts `primary_bot` and exposes `resolve_persona_user_id(persona_id) -> Optional[int]`. The primary bot's `setup_hook` passes `primary_bot=self` and a closure to the primary's scheduler. AgentManager stops each persona's scheduler via `client.proactive_scheduler.stop()` on shutdown.
- **`src/discord_bot.py:on_message` and `src/agents/agent_client.py:on_message`** — Bot messages now reach the proactive hook (for thread observation). Self-messages still ignored. No chat / image / voice handling for bot messages.
- **`src/proactive/scheduler.py`** — `budget_exhausted` termination: when pre-filter rejects with `all_budgets_exhausted` AND the persona is the initiator of the active thread, the thread is ended with `reason='budget_exhausted'`.
- **`/proactive threads`** — Owner-only embed listing recent threads. Active threads show 🟢 with current turn count; ended ones show ⚫ with `ended_reason` and duration.
- **Analytics** — New `inter_agent_turn` event (category `system`) emitted on every `reply` / `engage_persona` decision while a thread is active, with `{thread_id, persona_id, target_persona_id, turn_count, action}`. Existing `proactive_decision` event now also carries `in_thread` flag.
- **24 new tests** (`tests/test_proactive_threads.py`) — Full coverage of all 5 termination conditions: `superseded` (start_thread auto-ends prior active), `turn_cap` (advance + post-advance check), `human_interrupt` (any non-bot message), `natural_end` (2 consecutive non-prefilter `'none'` decisions; pre-filter rejections correctly excluded), `budget_exhausted` (initiator-only). Plus engagement decay function, ThreadState helpers, and the actor's engage_persona path (success, target-not-connected, send-failure-no-phantom-thread, missing-client).

### Architectural notes

- **Turn counting model**: turn 0 = thread created. Turn 1 = seed message posted (advanced explicitly by the actor). Turn N ≥ 2 = each subsequent participant-bot message advances via the OTHER bot's `on_message` hook. Each cross-bot message increments exactly once (the posting bot doesn't see its own message; only the other does), so no deduplication needed.
- **Why observe-only state machine**: The originator and target use their normal mention/chat handlers for the actual back-and-forth. The thread state machine watches and terminates. This means we don't double up the LLM cost of replies — proactive replies still pay one Sonnet call, and the target's response to an `@-mention` comes from the existing chat path (which is already cached).
- **Memory tracking still skipped** for proactive replies and engage_persona openings (TODO in `_do_reply` from band 2 + same comment applies here). Band 4's reflection job will retroactively score importance and feed proactive contributions into Park-style reflections.

### Operator workflow (band 3)

1. Confirm `PROACTIVE_ENABLED=true` and both `personas/slashai.json` and `personas/lena.json` have proactive enabled with channel allowlists.
2. `AGENT_LENA_TOKEN` must be set so Lena's bot is connected (the resolver needs her bot user.id).
3. Restart. `/proactive threads` should be empty initially.
4. Watch `#devlog`. Threads start when one persona's decider returns `engage_persona`. Each thread caps at 4 turns. Any human message in the channel ends the thread immediately. Watch `/proactive threads` for live state and `/proactive history` for per-decision detail.
5. Cost gate: each thread costs at most ~4 Haiku decider calls + ~4 Sonnet generation calls per persona before turn_cap. With one thread/day per pair under default budgets, full-band-3 cost is bounded.

---

## [0.16.2] - 2026-05-08

### Added — Proactive replies live + Lena turns on (Enhancement 015 band 2)

The actor now generates and posts replies via the persona's actor model. Lena joins the proactive system in `#devlog` alongside `@slashAI`. The cross-persona lockout (already wired up in band 0) now does real work — when one persona acts, the other waits 5 seconds before considering the same channel.

- **`src/proactive/actor.py:_do_reply`** — Resolves channel + target message, calls `anthropic_client.messages.create(model=persona.proactive.actor_model, system=persona.build_system_prompt(), ...)` with a per-call user prompt that includes recent channel history and the spec's reply directive (1-3 sentences, no trailing questions, no @-mention). Posts via `channel.send(text, reference=target_message)` so Discord renders it as a native reply with the link visible. `discord.NotFound` / `HTTPException` paths log `actor_failed: <reason>` and continue. Long generations are truncated to Discord's 2000-char limit.
- **`src/proactive/scheduler.py`** — Threads `anthropic_client` through to the actor (was previously only on the decider). Same client object — no extra connections.
- **Reply path graduated out of `PROACTIVE_SHADOW_MODE`** — Replies fire whenever `PROACTIVE_ENABLED=true` and the channel is allowlisted, mirroring band 1's react treatment. `new_topic` and `engage_persona` remain shadow-stubbed and graduate in bands 4 and 3 respectively.
- **`personas/lena.json`** — `proactive.enabled=true` and `#devlog` (`1456400291623604479`) added to her allowlist. Cross-persona lockout (`PROACTIVE_CROSS_PERSONA_LOCKOUT_SECONDS=5`) prevents Lena and slashAI from jumping on the same opening simultaneously.
- **7 new tests** (`tests/test_proactive_actor.py`) — Successful reply with `reference=` argument verified, reply firing under shadow mode (band 2 graduation), missing anthropic_client records failure (no LLM call wasted), `discord.NotFound` short-circuits before generation, `HTTPException` on send records `send_reply_failed`, empty-generation guard, oversized-output truncation.

### Architectural notes

- Memory tracking is **deliberately skipped** for proactive replies in this band. Mention/DM replies feed into the memory extractor; proactive replies are not conversational turns the way mentions are. A TODO in `actor.py:_do_reply` flags this for revisit in band 4 — some proactive replies should feed into the reflection job (Park-style importance scoring) so personas accumulate beliefs from their own contributions, not just from inbound mentions.
- The reply directive lives in the per-call user message, not the system prompt, so the persona's static system prompt stays cacheable across calls.

### Operator workflow (band 2)

1. Confirm `PROACTIVE_ENABLED=true` in env.
2. Verify `personas/slashai.json` and `personas/lena.json` both have `proactive.enabled=true` and the test channel in their allowlists.
3. Provide `AGENT_LENA_TOKEN` env var if Lena's bot account is configured (otherwise only slashAI runs).
4. Restart. `/proactive status` confirms the primary's state; Lena's status is JSON-only for now (per-persona slash commands land later).
5. Watch `#devlog`. Reactions ~10%, replies ~4%, no_op ~85% per the design distribution. Cross-persona lockout means at most one persona acts per 5-second window.

---

## [0.16.1] - 2026-05-08

### Added — Proactive reactions live (Enhancement 015 band 1)

The actor now fires real reactions in allowlisted channels for the primary `@slashAI` bot. Reply / new_topic / engage_persona stay shadow-stubbed — they'll graduate band-by-band so the operator can tune the decider on cheap reaction traces first.

- **`src/proactive/actor.py`** — Per-action dispatch. `_do_reaction` calls `channel.fetch_message().add_reaction()`; `discord.NotFound` and `discord.HTTPException` paths log `actor_failed: <reason>` and continue without crashing the scheduler. The `react` path is graduated out of `PROACTIVE_SHADOW_MODE` — reactions fire whenever `PROACTIVE_ENABLED=true` and the channel is allowlisted. Reply / new_topic / engage_persona still shadow-mode-gate (or stub-log when shadow mode is off) so `/proactive history` shows the decider's intended behavior on those paths before they're implemented.
- **`/proactive status`** — Shows global config (enabled, shadow mode, heartbeat period), persona config (enabled, decider/actor models, scheduler running), allowlisted channels with names, today's used + remaining budget, and the `engages_with_personas` allowlist. Owner-only ephemeral embed.
- **`/proactive enable <channel>`** and **`/proactive disable <channel>`** — Mutate the in-memory `channel_allowlist` for the persona that owns the bot the command runs on (currently `slashai` only — Lena's allowlist is set via her JSON until per-persona slash commands land in band 2). Both commands include a footnote that the change is in-memory; persistent edits go in `personas/<name>.json`.
- **`personas/slashai.json`** — `proactive.enabled` set to `true` and `#devlog` (`1456400291623604479`) added to `channel_allowlist` so band-1 traces start populating after restart.
- **9 new tests** (`tests/test_proactive_actor.py`) — Successful reactions, reactions firing under shadow mode (the band-1 graduation), `discord.NotFound` and `discord.HTTPException` failure paths, fetch_channel fallback when the channel isn't in cache, no-op path bypasses Discord entirely, and the stub paths for reply/engage/new_topic log shadow vs stub reasoning correctly.

### Operator workflow (band 1)

1. Confirm migrations 018a/b/c are applied (auto on restart).
2. `PROACTIVE_ENABLED=true` in the env (master switch).
3. Verify `personas/slashai.json:proactive.enabled` is `true` and the channel_allowlist contains your test channel (defaults to `#devlog` `1456400291623604479`).
4. Restart the bot. Check `/proactive status` to confirm.
5. Watch the decider tick. Reactions should fire at ~10% rate when humans are active in the allowlisted channel. `/proactive history persona:slashai` shows every decision (incl. no-ops) with reasoning.
6. To test in another channel: `/proactive enable #foo`, then optionally edit `personas/slashai.json` to make it persistent.

---

## [0.16.0] - 2026-05-08

### Added — Proactive Interaction subsystem (Enhancement 015 band 0: shadow mode + audit)

The first band of the proactive-interaction subsystem. Personas (`@slashAI`, Lena, future personas) gain an autonomous decision loop that fires on heartbeat ticks and inbound messages — but the actor is no-op in shadow mode (default). Every decision is logged to `proactive_actions` so the operator can read traces and tune the decider prompt before any side-effects ship.

- **Three migrations** (`migrations/018a/b/c_*.sql`) — `proactive_actions` (audit log), `inter_agent_threads` (created but unused until band 3), `agent_reflections` (created but unused until band 4). Indexes for daily-budget queries and cross-persona lockout.
- **`src/proactive/` module** (10 files) — `config`, `policy`, `observer`, `decider`, `actor`, `store`, `scheduler`, plus stubs for `threads` and `reflection`. Pure-Python pre-filter (cooldowns, quiet hours, allowlists, cross-persona lockout, daily budgets); Haiku JSON decider with sanitization layer (MAST FM-2.6 / FM-1.2 defense); actor logs to audit table and skips real Discord side-effects in shadow mode.
- **Persona schema v2** (`personas/*.json`, `src/agents/persona_loader.py`) — `proactive` block with budgets, cooldowns, quiet-hours window (with timezone), engagement temperature, decider/actor model overrides, silence threshold, and `engages_with_personas` allowlist for inter-agent interaction. Backwards-compatible: missing block → `enabled=False` defaults.
- **`personas/slashai.json`** — Canonicalizes the primary bot as a persona (resolves Open Question 3 in spec). Same loader path as Lena.
- **Hooks into both bots** (`discord_bot.py`, `agents/agent_client.py`, `agents/agent_manager.py`) — `on_message` now calls `proactive_scheduler.on_message_hook(...)` for non-mention/non-DM messages; the agent manager attaches one `ProactiveScheduler` per loaded persona; lifecycle managed in `setup_hook` and `close()`.
- **`/proactive history` slash command** (`src/commands/proactive_commands.py`, owner-only) — paginated trace of every decision (incl. no-ops) with reasoning, confidence, tokens. Primary tuning surface. `/proactive status/enable/disable/threads/reflect/simulate` are stubbed and land in subsequent bands.
- **Env vars** — `PROACTIVE_ENABLED` (default `false`), `PROACTIVE_SHADOW_MODE` (default `true`), `PROACTIVE_HEARTBEAT_INTERVAL_SECONDS` (default `3600`), `PROACTIVE_DECIDER_MODEL` (default `claude-haiku-4-5-20251001`), `PROACTIVE_ACTOR_MODEL` (default `claude-sonnet-4-6`), `PROACTIVE_CROSS_PERSONA_LOCKOUT_SECONDS` (default `5`).
- **Analytics** — `proactive_decision` event (category `system`) on every decider call with `{persona_id, trigger, action, confidence, decider_model, input_tokens, output_tokens, reasoning_excerpt, shadow_mode}`.
- **Tests** — 51 tests across `test_proactive_policy.py` (quiet hours, lockout, budget, silence threshold) and `test_proactive_decider_validation.py` (sanitizer accepts/rejects, JSON extraction, emoji validation).

### Architectural notes

- AgentManager startup moved from `main()` into `DiscordBot.setup_hook` so `db_pool` and `anthropic_client` are guaranteed ready before persona schedulers attach. Existing behavior is preserved when `personas/` is empty.
- The decider sanitization layer is the project's defense against MAST failure modes FM-1.1, FM-1.2, FM-2.6: bad JSON, invalid actions, off-window message targets, non-Unicode emoji, and out-of-allowlist personas all fall back to `action='none'` with a `actor_sanitization_rejected: <reason>` reasoning string visible in `/proactive history`.

### Note on band naming

The Enhancement 015 spec was authored when the project was at 0.13.x and refers to bands as v0.14.0 → v0.14.5. Actual released versions are 0.16.x because the project shipped 0.14.0–0.15.12 in the interim. Spec band names remain unchanged in code comments for traceability against the design doc.

---

## [0.15.12] - 2026-05-08

### Fixed — Chat handler crash on Discord typing rate limit

- **Defensive `safe_typing` wrapper** (`utils/discord_typing.py`, `discord_bot.py`, `agents/agent_client.py`) — Discord's POST `/channels/{id}/typing` endpoint can return 429 with error code 40062 ("Service resource is being rate limited") on a per-channel shared bucket — common when datacenter egress IPs are deprioritized or multiple persona bots type in the same channel. discord.py raised `HTTPException` out of `__aenter__`, crashing `on_message` before the reply was sent and making the bot appear offline. The chat path now wraps `channel.typing()` in `safe_typing(...)`, which logs the rate-limit warning and proceeds without a typing indicator instead of aborting.

---

## [0.15.11] - 2026-04-15

### Fixed — License dashboard pagination

- **Embed overflow crash** (`streamcraft_commands.py`, `synthcraft_commands.py`, `scenecraft_commands.py`, `shapecraft_commands.py`) — License and server listing commands crashed with `400 Bad Request` when the embed description exceeded Discord's 4096-character limit. All four mod dashboards now paginate with Prev/Next buttons via `PaginationView` when content overflows.
- **Shared `paginate_lines` helper** (`views.py`) — Extracted line-level pagination into `views.py` so all command cogs share the same logic.

---

## [0.15.10] - 2026-04-05

### Fixed — Voice speaker identity and channel context

- **Speaker identity in voice** (`claude_client.py`) — Voice conversations now inject who is speaking and who else is in the channel (e.g., "You are in voice channel #General. **slashdaemon** is speaking to you. Also in the channel: Bob."). Previously Claude had no idea who it was talking to.
- **Display name resolution for voice memories** (`claude_client.py`) — `chat_streaming()` now passes `guild` to `_format_memories()`, so memory labels show proper display names instead of bare numeric user IDs.

---

## [0.15.9] - 2026-04-05

### Added — Auto-deploy voice agent on push

- **GitHub Actions workflow** (`deploy-voice.yml`) — Automatically deploys the voice agent to the DigitalOcean droplet when voice-related files are pushed to main. SSHes in, pulls code, rebuilds Docker image, restarts container with health check and one-deep rollback. Also supports manual `workflow_dispatch`.
- **Path-filtered triggers** — Only rebuilds when relevant files change (`src/voice/`, `src/agents/`, `src/claude_client.py`, `src/memory/`, `personas/`, `Dockerfile.voice`, `requirements.txt`).

---

## [0.15.8] - 2026-04-05

### Added — Multi-participant voice name filtering

- **Name-address filter** (`name_filter.py`, `session.py`) — In voice channels with 2+ humans, Lena only responds when addressed by name. Prevents responding to cross-talk between other participants. In 1-on-1 channels, all utterances are processed as before.
- **Configurable name aliases** (`persona_loader.py`, `lena.json`) — New `voice.name_aliases` field in persona JSON for common STT mishearings. Lena's aliases: "Alina", "Elena", "Lina", "Lenna". Display name is always matched automatically.
- **`NameFilter` utility** (`name_filter.py`) — Pre-compiled regex with word-boundary matching. Case-insensitive, prevents substring false positives (e.g., "Helena" won't match "Lena").

---

## [0.15.7] - 2026-04-05

### Improved — Timezone-aware timestamps for memory context

- **Timezone-aware system prompt** (`claude_client.py`) — Current date/time now uses the user's configured timezone (from `/remind timezone`) instead of always UTC. Includes time of day (e.g., "Saturday, April 05, 2026 at 2:14 PM PDT"). Falls back to UTC if no timezone is set or memory system is unavailable.
- **Richer memory age labels** (`claude_client.py`) — Memory ages now include absolute dates for anything older than today (e.g., "updated 3 days ago, Apr 02" instead of just "3 days ago"). When a memory was created much earlier than its last update, shows both: "updated 1 week ago, Mar 28, first noted Jan 15".
- **`created_at` in RetrievedMemory** (`retriever.py`) — Added `created_at` field to the memory retrieval dataclass and all query paths (hybrid search already returned it; semantic fallback now does too). Enables distinguishing old memories that were recently merged from genuinely recent ones.

---

## [0.15.6] - 2026-04-05

### Fixed — Voice VAD flush & TTS reconnection

- **VAD flush timer** (`vad.py`, `session.py`) — Discord stops sending audio packets when a user goes silent (Opus DTX), so the VAD silence timeout never fired. Added `flush()` method and a 200ms background timer that detects timed-out utterances without needing new audio packets. Users no longer have to make a second sound to trigger processing.
- **Cartesia TTS auto-reconnect** (`cartesia_tts.py`) — Cartesia drops idle WebSocket connections after ~10 minutes, but aiohttp still reports the socket as open. Added `_ensure_connected()` pre-check and try/except reconnection on `ClientConnectionResetError`, fixing "Cannot write to closing transport" crashes after idle periods.
- **`_is_speaking` stuck after TTS error** (`session.py`) — When TTS produced no audio (e.g., Cartesia rejecting a 1-char response), playback never started so `_on_playback_done` never fired, leaving `_is_speaking=True` permanently. All future audio was silently dropped. Now clears the flag when no playback was started.
- **Max utterance duration** (`vad.py`) — Long monologues were truncated by Whisper's ~30s limit. Added `max_utterance_bytes` (28s) that forces a flush mid-speech, so long utterances get chunked into segments the STT can handle.

---

## [0.15.5] - 2026-04-05

### Added — Agent Discord Tool Use
Agent personas (like Lena) now have Discord tool capabilities, enabling them to interact across channels rather than just reply to mentions.

- **Agent tool tier** (`claude_client.py`) — New `AGENT_TOOLS` permission tier between owner and community. Agents get: `send_message`, `edit_message`, `read_messages`, `list_channels`, `get_channel_info`, `describe_message_image`, `search_memories`.
- **`is_agent` flag** (`claude_client.py`) — `ClaudeClient` accepts `is_agent=True` to enable the agent tool tier without requiring owner privileges.
- **Discord tool methods** (`agent_client.py`) — `AgentClient` now implements the Discord operation interface (`send_message`, `edit_message`, `read_messages`, `list_channels`, `get_channel_info`, `get_message_image`) and passes `bot=self` to `ClaudeClient`.
- **Safety boundaries** — Agents cannot `delete_message`, use reminders, access owner analytics, or read GitHub docs. Destructive and owner-only tools are excluded from the agent tier.

---

## [0.15.4] - 2026-04-05

### Added — Voice Memory Integration
Voice conversations now participate in the persistent memory system. Lena recalls past context during voice calls and remembers what was said for future conversations.

- **Memory retrieval in voice** — `chat_streaming()` retrieves relevant memories before the LLM call, injecting context from past text, voice, and Minecraft conversations.
- **Memory tracking from voice** — Voice exchanges are tracked for extraction. After 5 exchanges, the extraction pipeline runs and persists memories to the database.
- **Cross-platform memory** — Same `agent_id` scoping ("lena") means Minecraft memories (via bridge), text chat memories, and voice memories all surface together.
- **Voice agent memory init** (`voice_agent.py`) — Initializes `asyncpg` pool + `MemoryManager` when `DATABASE_URL` and `VOYAGE_API_KEY` are set. Gracefully degrades if missing.
- **VoiceChannel privacy** (`memory/privacy.py`) — `classify_channel_privacy()` now handles `VoiceChannel` and `StageChannel` (checks `connect` permission). Also handles `channel=None` (defaults to `guild_public`).

---

## [0.15.3] - 2026-04-05

### Added — LLM Streaming with Sentence-Level TTS
- **`chat_streaming()`** (`claude_client.py`) — Streams LLM response via Anthropic streaming API, yielding text at sentence boundaries as they complete. Enables sentence-level TTS pipelining.
- **`_speak_streaming()`** (`session.py`) — Combines LLM streaming → per-sentence TTS → playback in one pipeline. First audio plays within ~500ms of LLM stream start instead of waiting for the full response.
- **`_split_sentence()`** — Sentence boundary detection for streaming token buffer.

### Fixed
- **Audio clipping** — Partial PCM frames were zero-padded every ~20ms, creating repeated clicks/pops. Now carries remainder across `feed()` calls; only the final frame gets padded.
- **Echo feedback** — Mutes audio reception (`_is_speaking` flag) while bot is playing TTS. Resets all user VADs on speak start to discard partial audio.
- **SSRC mapping reliability** — Infers SSRC→user from voice channel members when SPEAKING opcode hook fails (only one human in channel).

### Performance
- **Latency to first audio**: 1.3–4.7s → **1.2–1.7s** (consistent regardless of response length)
- STT: 107–191ms | LLM first sentence: ~1.1–1.6s | TTS first byte: ~130ms

---

## [0.15.1] - 2026-04-04

### Fixed
- **davey dependency** — discord.py 2.7.1 (production) requires the `davey` library for Discord's DAVE (Audio & Video E2EE) protocol. Added to requirements.txt alongside PyNaCl.
- **RTP extension bit** — Discord voice packets have the RTP extension bit set (`0x90`), not plain `0x80`. Fixed version check to mask top 2 bits only. Extended AAD header to include RTP extension data for correct AEAD decryption.
- **SSRC mapping** — Accept audio from unmapped SSRCs using SSRC as temporary user ID, since SPEAKING opcode mapping may arrive late.
- **Voice WebSocket hook** — Patch `ws._hook` directly on the already-connected WebSocket, not just `_connection.hook` (which only applies to future reconnects).

### Added
- **Standalone voice agent** (`src/voice_agent.py`) — Entry point for running persona agents independently on UDP-capable infrastructure (DO Droplet), since App Platform blocks UDP.
- **Dockerfile.voice** — Docker image for the voice agent container.
- **DAVE decrypt layer** (`receiver.py`) — Handles end-to-end encrypted voice channels via `davey.DaveSession.decrypt()`.

### Infrastructure
- **Droplet deployment** — Lena's voice agent runs on the `umami-stats` Droplet (resized 1GB→2GB) as a Docker container, separate from App Platform. `AGENT_LENA_TOKEN` moved from App Platform to Droplet.

### New Files
- `src/voice_agent.py`
- `Dockerfile.voice`

---

## [0.15.0] - 2026-04-04

### Added — INCEPTION Phase 6: Discord Voice Channels
Persona agents can now join Discord voice channels and have real-time voice conversations. Ported from SoulCraft's Minecraft voice pipeline to Python.

#### Voice Pipeline (`src/voice/`)
- **Audio receiver** (`receiver.py`) — Hooks into discord.py internals to receive, decrypt (AEAD XChaCha20-Poly1305), and decode Opus audio from other users via `SocketReader` and SPEAKING opcode interception.
- **Cartesia TTS** (`cartesia_tts.py`) — WebSocket streaming text-to-speech via Cartesia Sonic-3. Uses persona voice config (voice_id, emotion, speed).
- **Cartesia STT** (`cartesia_stt.py`) — REST speech-to-text via Cartesia ink-whisper model. Accepts 16kHz mono WAV.
- **Audio resampler** (`resampler.py`) — Format conversion between Cartesia (24kHz mono / 16kHz mono) and Discord (48kHz stereo) via `audioop-lts`.
- **Voice activity detection** (`vad.py`) — RMS-based VAD with configurable threshold (500.0), silence timeout (800ms), and minimum utterance length.
- **Echo guard** (`echo_guard.py`) — Two-layer echo cancellation: temporal (bot speaking window) + content (Jaccard word similarity).
- **Text processor** (`text_processor.py`) — TTS text cleaning (strip markdown/emotes/emoji/slang/URLs, convert laughter) and sentence chunking. Keyword-based emotion inference for Cartesia emotion tags.
- **Streaming audio source** (`audio_source.py`) — Thread-safe `discord.AudioSource` subclass with buffered PCM frame delivery and volume control.
- **Voice session** (`session.py`) — Orchestrates the full conversation loop: receive → downsample → VAD → STT → echo guard → ClaudeClient.chat() → TTS → upsample → play.

#### Agent Integration
- **Voice commands** — "@Lena join voice" / "@Lena leave voice" (regex-based, supports variations like "join vc", "hop into voice").
- **Auto-leave** — Bot automatically disconnects when all humans leave the voice channel.
- **Voice states intent** — Added to `AgentClient` for voice channel event tracking.

#### Tests (94 new)
- `test_text_processor.py` (24) — TTS cleaning, chunking, emotion inference
- `test_resampler.py` (11) — Format conversion, WAV headers, energy preservation
- `test_vad.py` (7) — Silence detection, utterance accumulation, thresholds
- `test_echo_guard.py` (8) — Temporal/content rejection, similarity calculation
- `test_audio_source.py` (10) — Frame buffering, threading, volume scaling
- `test_cartesia_stt.py` (6) — Mocked HTTP, headers, transcript parsing
- `test_cartesia_tts.py` (8) — Mocked WebSocket, payload structure, speed clamping
- `test_receiver.py` (12) — Socket listener registration, SSRC mapping, packet filtering
- `test_session.py` (8) — Join/leave lifecycle, utterance pipeline, echo guard integration

### New Files
- `src/voice/__init__.py`
- `src/voice/text_processor.py`
- `src/voice/resampler.py`
- `src/voice/vad.py`
- `src/voice/echo_guard.py`
- `src/voice/audio_source.py`
- `src/voice/cartesia_stt.py`
- `src/voice/cartesia_tts.py`
- `src/voice/receiver.py`
- `src/voice/session.py`
- `tests/test_text_processor.py`
- `tests/test_resampler.py`
- `tests/test_vad.py`
- `tests/test_echo_guard.py`
- `tests/test_audio_source.py`
- `tests/test_cartesia_stt.py`
- `tests/test_cartesia_tts.py`
- `tests/test_receiver.py`
- `tests/test_session.py`

### Dependencies
- Added `PyNaCl>=1.5.0,<1.6` for Discord voice encryption/decryption

### Configuration
- New env var: `CARTESIA_API_KEY` (required for voice features)

---

## [0.14.2] - 2026-03-29

### Fixed
- **Clear stale slash commands on agent startup** — Agent bots (e.g., Lena) now sync an empty command tree on connect, removing any leftover slash commands from previous bot token usage (e.g., OpenClaw `/bluebubbles`, `/coding_agent`).

---

## [0.14.1] - 2026-03-28

### Added
- **Agent filter on memory commands** — `/memories list`, `/memories search`, and `/memories stats` now accept an optional `agent` parameter to filter by persona (e.g., `/memories list agent:Lena`). Autocomplete shows agents that have memories about the user.
- **Per-agent stats breakdown** — `/memories stats` (without agent filter) now includes a "By Agent" section showing memory count per persona.
- **INCEPTION unit tests** — 51 tests covering persona loading, memory bridge API, and agent-scoped memory:
  - `tests/test_persona_loader.py` — JSON parsing, defaults, system prompt building, load_all
  - `tests/test_memory_bridge.py` — Auth, store/retrieve endpoints, user resolution
  - `tests/test_agent_memory.py` — agent_id passthrough in retriever and updater

### New Files
- `tests/test_persona_loader.py`
- `tests/test_memory_bridge.py`
- `tests/test_agent_memory.py`

---

## [0.14.0] - 2026-03-28

### Added — INCEPTION: Cross-Platform AI Agent Personas
Multi-agent Discord bots with shared persona files, bidirectional memory bridge, and agent-scoped memory. Part of the INCEPTION initiative bridging slashAI and SoulCraft.

#### Phase 1: Persona Definition & Loading
- **Persona loader** (`src/agents/persona_loader.py`) — Loads JSON persona files from `personas/` directory. Shared format with SoulCraft. Builds Discord-appropriate system prompts from identity fields.

#### Phase 2: Multi-Agent Discord Bots
- **Agent client** (`src/agents/agent_client.py`) — Lightweight `discord.Client` per persona. Responds to mentions and DMs with persona-appropriate personality. No slash commands or MCP tools.
- **Agent manager** (`src/agents/agent_manager.py`) — Starts/stops agent bots based on persona files + `AGENT_{NAME}_TOKEN` env vars.
- **Agent-scoped memory** — `agent_id` column on memories table. Agent-specific memories are scoped; main bot memories (agent_id=NULL) are shared.
- **Migration 015** — Adds `agent_id` column, index, and updates `hybrid_memory_search()` function.
- **Threading** — `agent_id` parameter flows through `ClaudeClient` → `MemoryManager` → `MemoryRetriever` → `MemoryUpdater`.

#### Phase 3: Bidirectional Memory Bridge
- **Memory bridge API** (`src/api/memory_bridge.py`) — HTTP endpoints on the webhook server for cross-platform memory access:
  - `POST /api/memory/store` — Store a memory from Minecraft (or any external platform)
  - `POST /api/memory/retrieve` — Query memories with embedding search, scoped by agent_id
  - `GET /api/memory/health` — Health check
- **Migration 016** — Adds `source_platform` and `user_identifier` columns for cross-platform tracking.
- **User linking** — Resolves Minecraft usernames to Discord user IDs via the `/verify` account linking system.

### New Files
- `src/agents/__init__.py`
- `src/agents/persona_loader.py`
- `src/agents/agent_client.py`
- `src/agents/agent_manager.py`
- `src/api/__init__.py`
- `src/api/memory_bridge.py`
- `migrations/015_add_agent_id.sql`
- `migrations/016_add_source_platform.sql`
- `personas/lena.json` (sample persona)
- `docs/INCEPTION.md` (implementation spec)

---

## [0.13.10] - 2026-03-25

### Changed
- **Separate "No Usage" category in server dashboards** — Trial servers with zero usage are now split into a distinct ⚪ **NO USAGE** section (grey) at the bottom, ordered by ID descending (newest first). Trial servers with actual usage remain under 🔵 **TRIAL** (now blue instead of grey). Applies to `/streamcraft servers`, `/synthcraft servers`, `/scenecraft servers`, and `/shapecraft servers`.

---

## [0.13.9] - 2026-03-20

### Added
- **Query expansion for broad retrieval** — Broad queries like "who am I" or "tell me about my builds" are automatically decomposed into multiple targeted sub-queries for better memory recall (`src/memory/expander.py`, `src/memory/config.py`)
- **Memory retrieval visibility** — Users now see a 🧠 reaction on their message while expanded retrieval runs, plus a footer on the bot's response showing retrieval metadata (e.g., `-# Retrieved 11 memories across 6 queries`)
  - `RetrievalResult` dataclass wraps memories with expansion metadata (`manager.py`)
  - `ChatResult` dataclass propagates metadata from `claude_client.chat()` to `discord_bot`
  - Footer only shown when expansion triggers; memory tracking uses raw text (no footer)
- **GitHub docs: CHANGELOG.md and README.md access** — The `read_github_file` tool can now read `CHANGELOG.md` and `README.md` from the repo root, in addition to `docs/**`. Enables the bot to answer questions about version history and project overview.

---

## [0.13.8] - 2026-03-18

### Changed
- **License sort by state priority** — `/streamcraft licenses`, `/synthcraft licenses`, `/scenecraft licenses` (and their `servers` subcommands) now sort by state priority: EXPIRED → GRACE → ACTIVE → TRIAL → other, with the original secondary sort preserved within each group
- **Compact license/server dashboard UI** — Replaced verbose embed fields with grouped description-based layout: state-emoji headers (🔴🟡🟢⚪), one-line-per-server format with dot-separated key info, dynamic embed color (red/orange/green) based on worst state, dropped full server ID hash and redundant IP display in favor of geo-resolved location

---

## [0.13.7] - 2026-03-17

### Added
- **StreamCraft MC version display** — `/streamcraft servers` and `/streamcraft licenses` now show the Minecraft version each server is running (e.g., `MC 1.21.1`, `MC 1.20.1`)
  - Reads new `minecraft_version` column from `streamcraft_licenses` table
  - Defaults to `1.21.1` for existing servers without the field

## [0.13.6] - 2026-03-14

### Added
- **TipSign admin commands** — New `/tipsign` slash command group (owner-only) for querying TipSign data from the theblockacademy backend API
  - `/tipsign list` — Paginated list of all tip signs with title, owner, location, and supporter link indicators
  - `/tipsign search <query>` — Filter signs by owner username or title
  - `/tipsign detail <sign_id>` — Full sign details including all pages, supporter URLs, and timestamps
  - `/tipsign stats` — Summary statistics: total signs, unique owners, Ko-fi/Patreon counts, most prolific author
  - Uses `TBA_API_URL` env var (falls back to `RECOGNITION_API_URL`)

---

## [0.13.5] - 2026-03-02

### Fixed
- **Critical: Recognition webhook infinite retry loop** — When the webhook POST to `/recognition/webhook/slashai` failed (401 Unauthorized), the scheduler would re-analyze the same submission with Claude Vision every 60 seconds indefinitely, burning ~$15-70/day in API credits
  - Root cause: JSON serialization mismatch — HMAC signature was computed on compact JSON (`separators=(',',':')`) but httpx sent the body with default separators (spaces), so the backend's `JSON.stringify(req.body)` verification produced a different digest
  - Fix: New `_signed_post()` method serializes payload once and uses those exact bytes for both the HMAC signature and the HTTP body, guaranteeing consistency
- **Recognition nomination webhook had same infinite loop risk** — Nomination reviews also used Anthropic API calls with no retry limit on webhook failure

### Added
- **Webhook retry limits** — Submissions and nominations now track webhook delivery failures per ID; after 3 consecutive failures, the scheduler skips the item and logs an actionable error message directing operators to check `SLASHAI_WEBHOOK_SECRET`
  - Applies to both explicit webhook failures (`submit_analysis_result`, `submit_nomination_review`) and unexpected exceptions during processing
  - Failure counters reset on successful delivery or bot restart

---

## [0.13.4] - 2026-02-18

### Changed
- **Model upgrade**: Upgraded from Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`) to Claude Sonnet 4.6 (`claude-sonnet-4-6`) across all components
  - Main chatbot (`claude_client.py`)
  - Memory extraction (`extractor.py`)
  - Memory merging (`updater.py`)
  - Image analysis and narration (`images/analyzer.py`, `images/narrator.py`)
  - Image memory config (`config.py`)
  - Reminder message generation (`reminders/scheduler.py`)
  - Build recognition analysis (`recognition/analyzer.py`)
  - Nomination review (`recognition/nominations.py`)

---

## [0.13.3] - 2026-02-12

### Added
- **Discord Scheduled Events update & delete sync**: Editing or deleting a TBA calendar event now syncs the change to the matching Discord Scheduled Event
  - `PUT /events/:id` dispatches fire-and-forget webhook to `/server/event-updated` — updates title, description, time, and location
  - `DELETE /events/:id` fetches `discord_event_id` before deleting, then dispatches to `/server/event-deleted`
  - Graceful handling: if the Discord event was manually deleted, the webhook logs a warning and returns success
  - External events require `location` and `end_time` on every edit call — handler always passes both
- **Backfill endpoint**: `POST /events/sync-discord` (admin-only) creates Discord Scheduled Events for existing upcoming events that lack a `discord_event_id`
  - Reuses existing `dispatchEventWebhook()` which stores the returned ID back in the database

### Technical Details
- New webhook helpers in `events.ts`: `dispatchEventUpdateWebhook()` and `dispatchEventDeleteWebhook()`
- New webhook handlers in `discord_bot.py`: `handle_event_updated()` and `handle_event_deleted()`
- Two new routes registered: `/server/event-updated` and `/server/event-deleted`
- DELETE handler now fetches `discord_event_id` alongside `created_by` in authorization query

---

## [0.13.2] - 2026-02-12

### Added
- **Discord Scheduled Events integration**: When a TBA calendar event is created (from website or slashAI chat), a matching Discord Scheduled Event is automatically created
  - Events appear in Discord's dedicated event bar at the top of the server
  - Users can mark "Interested" and get notified when events start
  - New `/server/event-created` webhook handler creates events via `guild.create_scheduled_event()`
  - Backend stores `discord_event_id` for future update/delete sync
  - Fire-and-forget: calendar event creation succeeds even if Discord event fails

### Technical Details
- New migration `036_event_discord_id.sql` adds nullable `discord_event_id` column to events table
- Webhook follows `/server/title-grant` pattern (reads response body to store ID back in DB)
- `dispatchEventWebhook()` helper in `events.ts` called after INSERT in both `POST /events` and `POST /events/bot`
- Discord event includes title, description with "Hosted by" attribution, start/end time, and location

---

## [0.13.1] - 2026-02-12

### Added
- **Reaction-based event confirmation**: Users can now click 👍 on an event draft to confirm instead of typing "yes"
  - New `register_event_draft` community tool — Claude calls it alongside the draft message
  - Bot adds 👍 reaction to draft messages as a visual hint
  - Accepts 👍, ✅, 👌, 🎉, ✨ as confirmation reactions
  - Only the original requester's reaction triggers creation (user_id check)
  - Automatic #events announcement on reaction confirmation (same as typed flow)
  - 30-minute TTL on pending drafts — expired drafts are pruned automatically
  - Deduplication: if user both reacts AND types "yes", only one event is created
  - Overwriting: requesting a new draft invalidates the previous one

### Technical Details
- New `PendingEventDraft` dataclass in `claude_client.py`
- Draft lifecycle: `register_event_draft` → `_pending_drafts_by_context` → linked to message ID after send → `_pending_event_drafts` on bot
- Reaction handler (`on_raw_reaction_add`) checks pending drafts before processing regular reaction signals
- `_confirm_event_draft()` calls `EventsAPIClient` directly, bypassing Claude's agentic loop
- System prompt updated to instruct Claude to always call `register_event_draft` when presenting drafts
- No database migrations required

---

## [0.13.0] - 2026-02-12

### Added
- **Discord-to-Calendar event creation**: Any allowlisted user can create TBA calendar events by chatting with slashAI
  - Natural language event descriptions → formatted draft → confirmation → published to calendar
  - New `create_event` community tool (available to all users, not just owner)
  - Tool split: `DISCORD_TOOLS` (owner-only) + `COMMUNITY_TOOLS` (all users)
  - After creation, slashAI auto-announces in #events channel with event link
  - Backend enforces allowlist check via Discord user ID lookup (no client-side gating)
- **Events API client** (`src/tools/events_api.py`): httpx-based client for `POST /api/events/bot`
- **TBA backend endpoint**: `POST /events/bot` with `authenticateServer` middleware
  - Accepts `discord_user_id` to look up the user, checks allowlist, creates event
  - Returns event data + URL

### Fixed
- `EventsAPIClient` now raises `EventCreationError` with the backend's actual error message instead of silently returning `None` — Claude can relay specific errors to users (e.g., "not on allowlist" vs "account not linked")

### Technical Details
- New env vars: `EVENTS_API_URL` (default: `https://theblock.academy/api/events`), `EVENTS_API_KEY`
- System prompt updated with event creation flow, category guide, and defaults
- `_execute_tool()` now receives `user_id` parameter for community tool context
- No database migrations required
- Requires `/verify` (Discord-Minecraft linking) for event creators

---

## [0.12.7] - 2026-02-06

### Added
- **Extraction prompt enhancement**: Reaction context included during memory extraction
  - When extracting memories from conversations, Claude sees which messages received reactions
  - Helps Claude weight importance and adjust confidence based on community validation
  - Agreement reactions (👍) suggest shared opinions → higher confidence
  - Excitement reactions (🔥) suggest important content → prioritize remembering
  - Mixed reactions → note controversy in memory

### How It Works
1. User conversation accumulates to extraction threshold
2. Before calling Claude for extraction, look up reactions on the message IDs
3. Format reaction context: `"I love copper builds..." received: 👍×3 🔥×2`
4. Include in extraction prompt so Claude can use community signals

### Technical Details
- New `reaction_context` parameter in `MemoryExtractor.extract_with_privacy()`
- New `_get_reaction_context_for_messages()` in MemoryManager
- `REACTION_CONTEXT_SECTION` added to extraction prompt
- No migration required

---

## [0.12.6] - 2026-02-06

### Added
- **Memory type promotion**: Episodic memories auto-promote to semantic based on reactions
  - Memories with strong, consistent positive reactions become permanent (no decay)
  - Applies to `episodic` and `community_observation` memory types
  - Configurable thresholds via environment variables

### Promotion Criteria (Defaults)
| Criterion | Default | Env Var |
|-----------|---------|---------|
| Min reactions | 4 | `MEMORY_PROMOTION_MIN_REACTIONS` |
| Min unique reactors | 3 | `MEMORY_PROMOTION_MIN_REACTORS` |
| Min sentiment | 0.6 | `MEMORY_PROMOTION_MIN_SENTIMENT` |
| Max controversy | 0.3 | `MEMORY_PROMOTION_MAX_CONTROVERSY` |
| Min age (days) | 3 | `MEMORY_PROMOTION_MIN_AGE_DAYS` |

### Technical Details
- Promotion check runs after reaction aggregation (every 15 minutes)
- Promoted memories get `memory_type = 'semantic'` and `confidence >= 0.8`
- Semantic memories don't decay, so promoted content becomes permanent
- Analytics event: `memory_promoted`
- Disable with `MEMORY_PROMOTION_ENABLED=false`

---

## [0.12.5] - 2026-02-06

### Added
- **Bidirectional reactor preference inference**: Learn about reactor preferences from reactions
  - When User B reacts 👍 to User A's message about "copper builds", infer User B also likes copper builds
  - Creates `inferred_preference` memories for reactors, not just observations about content
  - Only triggers for strong positive signals: agreement, appreciation, excitement (sentiment > 0.5)
  - Skips self-reactions (reactor == author)
  - New `MemoryManager.create_reactor_inference()` method
  - New `src/memory/reactions/inference.py` module

### How It Works
1. User A posts: "I love building with copper blocks"
2. User B reacts with 👍 (agreement intent, sentiment=1.0)
3. slashAI creates an inferred preference memory for User B: "Agrees with: I love building with copper blocks"
4. Now when chatting with User B, slashAI can remember their inferred preferences

### Technical Details
- `memory_type = "inferred_preference"` for these memories
- `confidence = 0.4` (lower since inferred, not stated directly)
- `privacy_level = "guild_public"`
- Deduplication: One inference per (reactor, message) pair
- Topic formatting: "Agrees with: ...", "Appreciates: ...", "Excited about: ..."

### Migration Required
- **`migrations/014f_add_inferred_preference_type.sql`**: Adds 'inferred_preference' to memory_type constraint

---

## [0.12.4] - 2026-02-06

### Added
- **Reaction-triggered community observations**: Passive memory creation from reacted messages
  - When a message receives a reaction but has no memory link, automatically create a "community_observation" memory
  - Captures community-curated content without requiring @mentions
  - New `MemoryManager.create_community_observation()` method
  - New `ReactionStore.has_memory_link()` check

### How It Works
1. User A posts a message in a public channel
2. User B reacts to that message with 🔥
3. slashAI checks if the message has any memory links
4. If not, creates a lightweight memory from the message content
5. Links the message to the new memory
6. Now `get_popular_memories` can find community-engaged content

### Technical Details
- `memory_type = "community_observation"` for these passive memories
- `confidence = 0.5` (moderate, since not LLM-extracted)
- `privacy_level = "guild_public"` for all observations
- Skips bot messages, DMs, and very short messages (<10 chars)
- Generates embedding for semantic search compatibility

### Migration Required
- **`migrations/014e_add_community_observation_type.sql`**: Adds 'community_observation' to memory_type constraint and makes embedding nullable

### Backfill Script
- **`scripts/backfill_community_observations.py`**: Creates observations for existing reacted messages
- Run: `python scripts/backfill_community_observations.py --guild <id> --apply`

---

## [0.12.3] - 2026-02-06

### Added
- **Community engagement filter**: `get_popular_memories` tool now filters out self-reactions
  - New `scope` parameter: "community" (default) excludes reactions from memory owner
  - New `min_unique_reactors` parameter: filter by diversity of engagement
  - Shows true community engagement, not self-validation

### Changed
- `get_popular_memories` now defaults to `scope: "community"`
- Output shows unique reactor count when multiple users reacted
- Empty results message explains community scope filter

### Technical Details
- Community scope uses live query joining memories → links → reactions
- Filters where `reactor_id != memory.user_id`
- Aggregates reaction count, unique reactors, and average sentiment at query time

---

## [0.12.2] - 2026-02-06

### Added
- **Popular memories tool**: New `get_popular_memories` agentic tool
  - Query memories by reaction engagement
  - Filter by minimum reaction count and sentiment
  - Returns top reacted content with emoji breakdown
  - Enables Claude to answer "what topics are popular?" directly

### Technical Details
- New `MemoryManager.get_popular_memories()` method
- SQL query orders by reaction count and sentiment score
- Tool output includes reaction count, sentiment label, and top emoji

---

## [0.12.1] - 2026-02-06

### Added
- **Explicit reaction visibility**: Claude now sees reaction data in memory context
  - `RetrievedMemory` dataclass includes `reaction_summary` field
  - Memory metadata displays reaction count and sentiment (e.g., "[3 positive reactions]")
  - Enables Claude to reference community engagement in responses

### Fixed
- **Aggregator JSONB encoding**: Fixed `asyncpg.DataError` when storing reaction summaries
  - Added `json.dumps()` for JSONB parameter encoding
- **Reaction summary parsing**: Handle both dict and JSON string formats from database

---

## [0.12.0] - 2026-02-06

### Added

#### Reaction-Based Memory Signals
Emoji reactions on Discord messages now inform memory confidence, decay resistance, and retrieval ranking. This bidirectional model means a single reaction can inform memories about BOTH the message author AND the reactor.

**Multi-Dimensional Emoji Classification:**
- 100+ emoji mapped across four dimensions: sentiment, intensity, intent, relevance
- Intent categories: agreement, disagreement, appreciation, amusement, excitement, surprise, sadness, thinking, confusion, attention, support, celebration
- Context-dependent emoji (e.g., 💀, 🙃) flagged for Claude interpretation
- Custom server emoji ignored in v0.12.0 (unicode only)

**Reaction Storage:**
- New `message_reactions` table tracks every reaction event
- Soft deletion preserves reaction history for churn analysis
- Indexes optimized for message, reactor, author, and channel queries

**Memory-Message Linking:**
- New `memory_message_links` table connects memories to source messages
- Enables reaction aggregation for memory confidence calculation
- Links created during memory extraction

**Confidence & Decay Integration:**
- `reaction_summary` JSONB on memories stores aggregated metrics
- Confidence boost: -0.1 to +0.2 based on sentiment, intensity, count
- Decay resistance: reactions count as 0.5 retrievals each
- Controversy detection: mixed sentiment reactions reduce confidence

**Retrieval Ranking Boost:**
- Memories with positive reactions rank up to 15% higher
- Logarithmic scaling prevents single viral message from dominating
- Re-sorts results after applying reaction boost

**Background Aggregation:**
- Runs every 15 minutes to update memory reaction summaries
- Calculates weighted sentiment, intensity, controversy scores
- Top emoji and intent distribution tracking

**Database Migrations:**
- `014a_create_message_reactions.sql` - Reaction tracking table
- `014b_create_memory_message_links.sql` - Memory-message linking
- `014c_add_reaction_metadata.sql` - Add reaction columns to memories
- `014d_update_hybrid_search_for_reactions.sql` - Update search function

**CLI Tools:**
- `scripts/backfill_reactions.py` - Historical reaction import
  - Phase 1: Bot's own messages
  - Phase 2: Threads with bot participation
  - Phase 3: All public channel messages
  - Supports dry-run mode and date filtering

### Changed
- `discord_bot.py`: Added `intents.reactions = True`
- `memory/manager.py`: `track_message()` now accepts message IDs
- `memory/decay.py`: Decay resistance includes reaction count
- `memory/retriever.py`: Retrieval adds reaction boost to similarity
- `claude_client.py`: `chat()` accepts `skip_memory_tracking` param

### Technical Details
- Reaction confidence boost formula: `(sentiment * 0.1 * intensity_multiplier) + count_bonus - controversy_penalty`
- Decay resistance formula: `min(1.0, (retrieval_count + reaction_count * 0.5) / 10)`
- Retrieval boost formula: `similarity * (1 + min(0.15, log10(total + 1) * 0.05 * sentiment))`

---

## [0.11.0] - 2026-02-06

### Added

#### Core Curriculum Recognition Extension
A complete integration with The Block Academy's recognition system for AI-assisted build reviews. slashAI now processes build submissions, provides constructive feedback, and handles public announcements.

**Build Analysis Pipeline:**
- **Vision-based analysis** using Claude Sonnet 4.5 for quality assessment
- Technical scoring: palette quality, depth usage, proportion balance, detail level
- Style assessment comparing to player's previous work
- Recognition recommendation with confidence score
- Ownership stats integration (total blocks placed, unique block types)

**Feedback Generation:**
- Constructive, encouraging feedback tailored to each player
- Highlights strengths and areas for growth
- Personalized based on player history and craft development

**DM Approval Flow:**
- Players receive a DM before public announcement
- Preview of announcement text and screenshots
- "Share" or "Keep Private" buttons
- 48-hour expiration for pending approvals

**Discord Announcements:**
- Multi-image embeds with up to 4 screenshots
- BlueMap coordinate links for build location
- Player avatars via MC-Heads API
- Conversational announcement text generated by Claude

**Nomination System:**
- Peer nomination processing with anti-gaming checks
- Admin review flow for flagged nominations
- Automatic approval for clean nominations

**Server Event Webhooks:**
- Gamemode change announcements with player avatars
- Title grant announcements with Discord mentions
- Title revocation message deletion
- Teaching and attendance credit processing

**Background Scheduler:**
- 60-second polling interval (configurable via `RECOGNITION_POLL_INTERVAL`)
- Processes pending submissions, nominations, events, and deletions
- Graceful error handling with per-item recovery

#### Discord-Minecraft Account Linking
New `/verify` command for linking Discord accounts to Minecraft:

| Command | Description |
|---------|-------------|
| `/verify <code>` | Link Discord to Minecraft using code from `/discord link` in-game |

- Works in servers, DMs, and private channels
- Enables DM notifications for build reviews
- Integrates with CoreCurriculum recognition system

#### StreamCraft Commands (Owner-Only)
New `/streamcraft` command group for viewing license and usage data:

| Command | Description |
|---------|-------------|
| `/streamcraft licenses` | List all StreamCraft licenses with status and credit |
| `/streamcraft player <name_or_uuid>` | Look up player's streaming usage and sessions |
| `/streamcraft servers [server_id]` | Per-server usage summary with filtering |
| `/streamcraft active` | Currently active rooms and participants |

### Fixed

- **PostgreSQL type casting in decay queries** - Fixed type errors when running memory decay jobs
- **pgvector type error in cluster centroid update** - Fixed vector format for centroid updates
- **Recognition API 302 redirects** - Fixed requests getting redirected by OG router
- **Webhook signature JSON serialization** - Fixed mismatch causing signature verification failures
- **Nomination decision value** - Changed "reject" to "rejected" for consistency
- **Null handling in build analysis** - Fixed crashes when optional fields are missing
- **Build analyzer name usage** - Now uses player-provided build name consistently
- **Missing share_submission calls** - Fixed builds not appearing in website feed after approval
- **Player avatar API** - Switched from Crafatar to MC-Heads (Crafatar returning 521 errors)

### Technical Details

#### New Files
- `src/recognition/__init__.py` - Package exports
- `src/recognition/analyzer.py` - Claude Vision build analysis
- `src/recognition/feedback.py` - Feedback message generation
- `src/recognition/progression.py` - Title progression evaluation
- `src/recognition/nominations.py` - Nomination review with anti-gaming checks
- `src/recognition/api.py` - Recognition API client
- `src/recognition/scheduler.py` - Background processing loop
- `src/recognition/approval.py` - DM approval flow and Discord views
- `src/commands/link_commands.py` - `/verify` command
- `src/commands/streamcraft_commands.py` - `/streamcraft` command group

#### New Environment Variables
| Variable | Required | Description |
|----------|----------|-------------|
| `RECOGNITION_API_URL` | For recognition | theblockacademy Recognition API URL |
| `RECOGNITION_API_KEY` | For recognition | API key for recognition webhooks |
| `RECOGNITION_ANNOUNCEMENTS_CHANNEL` | No | Channel ID for public build announcements |
| `NOMINATIONS_CHANNEL_ID` | No | Channel for nomination announcements |
| `RECOGNITION_POLL_INTERVAL` | No | Polling interval in seconds (default: 60) |

#### Webhook Endpoints
The bot now exposes HTTP endpoints for server events:
- `POST /server/gamemode` - Gamemode change notifications
- `POST /server/title-grant` - Title grant announcements
- `POST /server/delete-message` - Message deletion requests

#### No Breaking Changes
- Recognition system is opt-in via `RECOGNITION_API_URL`
- StreamCraft commands require owner permissions
- All existing functionality unchanged

---

## [0.10.1] - 2026-01-12

### Added

#### Relevance-Weighted Confidence Decay (Spec 011)
Implements automatic memory confidence decay with reinforcement on access, making Claude's responses more appropriately hedged for old memories.

**Problem Solved:**
Old episodic memories maintained original confidence indefinitely:
- User mentions building an iron farm in October
- In January, Claude still confidently says "You're building an iron farm!"
- User finished that project months ago

**Solution:**
- Episodic memories decay over time when not accessed (semantic memories don't decay)
- Frequently-retrieved memories resist decay (relevance-weighted)
- Accessing a memory reinforces its confidence
- Very low confidence memories flagged for potential cleanup

**Decay Algorithm:**
```
decay_resistance = min(1.0, retrieval_count / 10)
effective_decay_rate = 0.95 + (0.04 × decay_resistance)
new_confidence = confidence × (effective_decay_rate ^ periods_since_access)
```

| Retrievals | Decay Rate | Per-Period Loss |
|------------|------------|-----------------|
| 0 | 0.95 | 5% per 30 days |
| 5 | 0.97 | 3% per 30 days |
| 10+ | 0.99 | 1% per 30 days |

**Reinforcement on Access (per memory type):**
- Semantic: +0.05 (cap 0.99) - facts should stay high
- Procedural: +0.04 (cap 0.97) - patterns reinforced through use
- Episodic: +0.03 (cap 0.95) - events can strengthen but not become facts

**New Schema (migration 013):**
- `decay_policy` - 'none', 'standard', 'aggressive', 'pending_deletion'
- `retrieval_count` - tracks how often memory is retrieved
- `is_protected` - manual protection from decay

**Background Job:**
- Runs every 6 hours
- Applies decay to eligible memories
- Flags very low confidence old memories for cleanup
- Identifies consolidation candidates (frequently-accessed episodic memories)

**New Files:**
- `migrations/013_add_confidence_decay.sql` - Schema changes
- `src/memory/decay.py` - MemoryDecayJob background task
- `scripts/memory_decay_cli.py` - CLI for decay management

**Configuration:**
- `MEMORY_DECAY_ENABLED=false` to disable (enabled by default)

**CLI Commands:**
```bash
python scripts/memory_decay_cli.py run --dry-run  # Preview decay
python scripts/memory_decay_cli.py run            # Run manually
python scripts/memory_decay_cli.py stats          # View statistics
python scripts/memory_decay_cli.py candidates     # Consolidation candidates
python scripts/memory_decay_cli.py protect 42     # Protect a memory
```

See `docs/enhancements/011_CONFIDENCE_DECAY.md` for full specification.

---

## [0.10.0] - 2026-01-12

### Added

#### Hybrid Search (Spec 010)
Combines lexical (full-text) and semantic (vector) search using Reciprocal Rank Fusion for optimal recall across query types.

**Problem Solved:**
Semantic-only search failed for exact term queries:
- Player names: "What did ilmango say?" → "ilmango" has no semantic meaning
- Coordinates: "My base at x:1000" → Numbers embed poorly
- Mod names: "Install OptiFine" → Technical terms vary in embedding

**Solution:**
- Added PostgreSQL full-text search (tsvector + GIN index) alongside pgvector
- Reciprocal Rank Fusion (RRF) combines both result sets by rank position
- Documents appearing in both lexical and semantic results score higher
- No hyperparameter tuning needed (k=60 works universally)

**Implementation:**
- `tsv` column with weighted tsvector (Weight A = exact matches via 'simple', Weight B = stemmed via 'english')
- `hybrid_memory_search()` SQL function with privacy filtering and RRF
- Automatic trigger maintains tsvector on insert/update
- Graceful fallback to semantic-only if migration not run

**Performance:**
- Latency increase: ~10ms per query (+10%)
- Storage increase: ~25% per memory (minimal, only stores lexemes)

**New files:**
- `migrations/012_add_hybrid_search.sql` - tsvector column, GIN index, trigger, SQL function
- `tests/test_hybrid_search.py` - 17 unit tests

**Configuration:**
- `MEMORY_HYBRID_SEARCH=false` to disable (enabled by default)

See `docs/enhancements/010_HYBRID_SEARCH.md` for full specification.

#### Database Backup System (Spec 008)
Implemented automated backup system for the shared PostgreSQL database (used by both slashAI and theblockacademy).

**Features:**
- **Daily automated backups** at 6:00 AM UTC via GitHub Actions scheduled workflow
- **On-demand backups** triggered via CLI or workflow_dispatch (types: `pre-migration`, `manual`, `daily`)
- **Offsite storage** in DigitalOcean Spaces (`db-backups/` prefix) with 30-day retention
- **Pre-migration backups** exempt from auto-deletion for safety
- **Discord notifications** for backup success/failure
- **Restore workflow** with artifact download and manual restore instructions

**Implementation:**
- Workflows live in `theblockacademy` repo (where DO/database secrets are configured)
- Backups named `tba_<type>_<timestamp>.dump` (e.g., `tba_pre-migration_20260113_020047.dump`)
- Uses PostgreSQL 18 client to match DO managed database version

**New files:**
- `theblockacademy/.github/workflows/db-backup.yml` - Backup workflow
- `theblockacademy/.github/workflows/db-restore.yml` - Restore workflow
- `scripts/backup_db.py` - CLI tool (triggers workflow in theblockacademy repo)

**Usage:**
```bash
# Before running a migration
python scripts/backup_db.py backup --type pre-migration

# List all backups
python scripts/backup_db.py list
```

See `docs/enhancements/008_DATABASE_BACKUP.md` for full specification.

#### GitHub Documentation Reader (Spec 009)
Enables slashAI to read its own documentation from GitHub, improving accuracy when discussing its implementation.

**Features:**
- **`read_github_file`**: Read a documentation file from the slashAI repository (must start with `docs/`)
- **`list_github_docs`**: List files in a docs subdirectory (e.g., `enhancements`)
- **Branch support**: Optional `ref` parameter for branch/commit SHA (default: `main`)
- **Caching**: 5-minute TTL cache reduces API calls (50 entries max)
- **Rate limiting**: Uses `GITHUB_TOKEN` for higher limits (5,000 vs 60 req/hr)

**Security:**
- Repository hardcoded to `mindfulent/slashAI`
- Path validation prevents traversal attacks (`..`, `/`, control chars)
- Read-only access to `/docs/**` only

**New files:**
- `src/tools/__init__.py` - Tools package initialization
- `src/tools/github_docs.py` - GitHub API client with caching
- `tests/test_github_docs.py` - Unit and integration tests (24 tests)

**Usage (owner-only via chatbot):**
```
"What does the memory techspec say about embedding dimensions?"
→ slashAI reads docs/MEMORY_TECHSPEC.md and provides accurate answer

"List the enhancement specs"
→ slashAI lists all files in docs/enhancements/
```

See `docs/enhancements/009_GITHUB_DOC_READER.md` for full specification.

---

## [0.9.23] - 2026-01-12

### Added

#### Query-Relevant Image Retrieval
When you ask about images ("what images have I shared?", "my builds"), the system now semantically searches stored image observations—not just text memories *about* the image system.

**How it works:**
1. Query is embedded using Voyage multimodal API (`multimodal_embed()`) - same embedding space as stored images
2. `image_observations` table is searched by embedding similarity
3. Relevant image descriptions, summaries, and tags are formatted into context
4. Uses calibrated image thresholds (0.15 minimum, 0.40 high relevance)

**Example context injected:**
```markdown
## Relevant Image Memories
- Grand neoclassical library building with columned facade (part of Library Build)
  [moderately relevant] [2 weeks ago] Tags: library, neoclassical, columns
```

This completes the image memory retrieval pipeline. Previously, `get_build_context()` returned recent clusters by time, but didn't match images to the user's query.

### Fixed

- **Multimodal API call**: Must use `multimodal_embed()` (not `embed()`) for text queries against image embeddings. Both images and text are embedded in the same vector space by voyage-multimodal-3.

### Validated in Production

Tested with real queries:
- "what can you remember about images I've shared?" → Retrieved 5 relevant image observations
- "what about minecraft builds?" → Retrieved 5 text memories + 5 image memories

Bot correctly reported specific build details (library with quartz ceiling, custom tree with purple foliage, Old Vic theatre) from stored observations without hallucination.

### Technical Details

#### Files Modified
- `src/memory/manager.py` - Added `retrieve_images()` method with privacy-filtered semantic search using `multimodal_embed()`
- `src/claude_client.py` - Added `_format_images()` and `_image_relevance_label()`, integrated into chat context
- `src/memory/__init__.py` - Exported `RetrievedImage` dataclass

#### New Configuration (ImageMemoryConfig)
| Threshold | Value | Meaning |
|-----------|-------|---------|
| `image_minimum_relevance` | 0.15 | Top ~50% of similarities |
| `image_moderate_relevance` | 0.25 | Top ~25% |
| `image_high_relevance` | 0.40 | Top ~6% |

---

## [0.9.22] - 2026-01-11

### Fixed

#### Image Memory Retrieval Gap
Build context from image observations was stored but never retrieved during chat. The `get_build_context()` method existed but was never called.

**Fix:** Wired up `get_build_context()` in `claude_client.py:chat()` - build context is now retrieved alongside text memories and combined into the system prompt.

#### Embedding Threshold Miscalibration
Image and text embeddings have completely different similarity distributions but shared the same thresholds:

| Embedding Model | Mean Similarity | Range | Old Threshold |
|-----------------|-----------------|-------|---------------|
| voyage-3.5-lite (text) | 0.63 | 0.44-0.88 | 0.30 (passed 100%) |
| voyage-multimodal (images) | 0.19 | -0.04-1.0 | 0.72 (passed <1%) |

**Calibrated thresholds:**
- Text similarity: 0.30 → 0.50 (captures top ~50% of matches)
- Image clustering: 0.72 → 0.35 (top ~10%, was nearly unreachable)
- Text relevance labels: "highly relevant" ≥0.70, "moderately relevant" ≥0.55

**Result:** Related images now cluster together, and text retrieval is more selective.

#### System Prompt Accuracy
Updated Image Memory section to:
- Clarify that build context is automatically retrieved
- Note that visual similarity doesn't capture semantic relationships (exterior vs interior)
- Add "Memory Accuracy" guardrail to prevent hallucination when no memories are retrieved

### Added

#### Expanded Cluster Naming Vocabulary
Build clusters now get meaningful names from 90+ structure types (was 16):
- Civic: library, museum, courthouse, government, parliament
- Commercial: shop, market, tavern, warehouse, bakery
- Maritime: dock, harbor, port, lighthouse, pier
- Minecraft-specific: iron_farm, creeper_farm, sorting_system, xp_farm

### Technical Details

See [docs/enhancements/007_IMAGE_MEMORY_FIXES.md](docs/enhancements/007_IMAGE_MEMORY_FIXES.md) for full analysis and calibration data.

#### Files Modified
- `src/claude_client.py` - Retrieval gap fix, system prompt updates, calibrated relevance labels
- `src/memory/config.py` - Separate thresholds for text/image, calibration documentation
- `src/memory/images/clusterer.py` - Calibrated thresholds, expanded naming vocabulary
- `CLAUDE.md` - Updated Key Constants table

---

## [0.9.21] - 2026-01-11

### Fixed

#### Aggressive Image Resizing for API Reliability
Large screenshots (4K+) were hitting Anthropic's 5MB base64 limit despite existing resize logic. The old approach only resized as a "last resort" after quality compression failed.

**Root cause:** Base64 encoding adds ~33% overhead, so a 4.5MB image becomes ~6MB after encoding.

**New approach:**
- **Dimension limit reduced:** 8000px → 2048px (Anthropic downsamples to ~1.15MP internally anyway)
- **Byte limit reduced:** 5MB → 1MB (accounts for base64 overhead)
- **Resize-first strategy:** Always scale down oversized images before compression, not as a fallback

**Result:** Screenshots that previously failed at 5-6MB now compress to ~200-500KB with no loss of detail for LLM analysis.

---

## [0.9.20] - 2026-01-11

### Added

#### Memory Introspection
Claude now has visibility into memory metadata, enabling smarter handling of conflicting or uncertain information:

**Phase 1: Metadata Transparency**
- Retrieved memories now include human-readable labels: `[relevance] [confidence] [privacy] [recency]`
- Example: `[highly relevant] [stated explicitly] [public] [3 days ago]`
- System prompt guidance helps Claude use metadata appropriately without narrating it

**Phase 2: Memory Query Tool (Owner Only)**
- New `search_memories` agentic tool for explicit memory searches
- Use cases: verify uncertain facts, answer "what do you remember about X?", reconcile conflicts
- Returns formatted results with relevance scores, confidence, privacy levels, and context

#### Metadata Labels

| Metadata | Thresholds | Labels |
|----------|------------|--------|
| **Relevance** | ≥0.8 / ≥0.5 / <0.5 | highly relevant / moderately relevant / tangentially relevant |
| **Confidence** | ≥0.9 / ≥0.7 / ≥0.5 / <0.5 | stated explicitly / high confidence / inferred / uncertain |
| **Privacy** | dm / channel_restricted / guild_public / global | dm-private / restricted / public / global |
| **Recency** | <1d / <7d / <30d / ≥30d | today / N days ago / N weeks ago / N months ago |

### Technical Details

#### Files Modified
- `src/memory/retriever.py` - Added `confidence` field to `RetrievedMemory`, updated SQL queries
- `src/claude_client.py` - Added metadata helper methods, updated `_format_memories`, added `search_memories` tool
- `src/memory/manager.py` - Added `search()` method for semantic memory search

#### Design Document
See [006_META_MEMORY.md](docs/enhancements/006_META_MEMORY.md) for full implementation plan.

---

## [0.9.19] - 2026-01-11

### Added

#### Conversational Reminder Delivery
Reminders are now delivered as natural, context-aware messages instead of structured embeds:

- **AI-Generated Messages** - Uses Claude Sonnet to craft personalized reminder messages at delivery time
- **Channel Context** - Reads recent messages in public channels to match conversation tone
- **Memory Integration** - Retrieves relevant user memories for personalized context
- **Time with Timezone** - Includes time with shorthand timezone (e.g., "10:00 AM PST")
- **Natural Recurrence** - Says "your daily reminder" or "weekly reminder" instead of showing CRON
- **Fallback Template** - Simple message if API unavailable

#### Auto-Detect Channel Delivery for Owner
When `OWNER_ID` sets a reminder in a public channel via natural language, the reminder automatically delivers to that channel (no explicit `channel_id` parameter needed). DM reminders still deliver via DM.

| User | Sets Reminder In | Delivery Location |
|------|------------------|-------------------|
| Regular user | Anywhere | DM |
| OWNER_ID | DM | DM |
| OWNER_ID | Public channel | That channel |

### Fixed

- **Bot messages counted in analytics** - Changed `message.author == self.user` to `message.author.bot` to filter ALL bot messages (was tracking other bots like DeanBot in analytics)
- **`/analytics users` improvements** - Now shows all users with resolved usernames and DM vs public message breakdown
- **Database connection pool exhaustion** - Limited main pool to 2-5 connections (was default 10) to stay under managed Postgres limits
- **Discord mention format** - Fixed @mentions in reminder messages to use numeric user ID instead of literal `<@{user.id}>`
- **Timezone display in confirmation** - Fixed reminder confirmation showing UTC time with user's timezone label; now converts back to local time

### Technical Details

#### Files Modified
- `src/reminders/scheduler.py` - Added Sonnet message generation, channel context, memory retrieval
- `src/claude_client.py` - Auto-detect owner + public channel for channel delivery
- `src/discord_bot.py` - Filter all bot messages from processing
- `src/commands/analytics_commands.py` - Enhanced `/analytics users` output
- `docs/enhancements/005_REMINDERS.md` - Updated with v0.9.19 enhancements

---

## [0.9.18] - 2026-01-11

### Fixed

- **Timezone bug in one-time reminders** - Fixed issue where reminders like "at 9:42pm" would fire 1 day late when the user's local time and UTC were on different calendar days. Added `RELATIVE_BASE` to dateparser settings to anchor time parsing to the user's local "now" instead of system/UTC time.

---

## [0.9.17] - 2026-01-10

### Added

#### Scheduled Reminders System
A full-featured reminder system with natural language parsing and CRON support:

- **Natural Language Time Parsing** - "in 2 hours", "tomorrow at 10am", "next Monday 3pm"
- **Recurring Reminders** - "every weekday at 9am", "every 2 hours", full CRON expressions
- **CRON Presets** - `hourly`, `daily`, `weekly`, `weekdays`, `weekends`, `monthly`
- **Per-User Timezone Support** - Reminders respect user's configured timezone
- **Background Scheduler** - 60-second polling loop for reliable delivery
- **Retry Logic** - Up to 5 delivery attempts before marking failed

#### Reminder Slash Commands
New `/remind` command group for all users:

| Command | Description |
|---------|-------------|
| `/remind set <message> <time>` | Create a reminder |
| `/remind list` | View your scheduled reminders |
| `/remind cancel <id>` | Cancel a reminder |
| `/remind pause <id>` | Pause a recurring reminder |
| `/remind resume <id>` | Resume a paused reminder |
| `/remind timezone <tz>` | Set your timezone (e.g., America/Los_Angeles) |

#### Agentic Reminder Tools (Owner Only)
Claude can now set reminders via natural language for the bot owner:
- `set_reminder` - Create reminders with natural language or CRON
- `list_reminders` - View scheduled reminders
- `cancel_reminder` - Cancel a reminder by ID
- `set_user_timezone` - Set timezone preference (Claude interprets natural language like "west coast" → America/Los_Angeles)

Owner can also set reminders that post to specific channels.

#### First-Time User Experience
When creating a reminder without a timezone set, Claude now asks for your timezone conversationally. You can respond naturally ("I'm on the west coast", "NYC", "Pacific time") and Claude will interpret it correctly.

#### New Database Migrations
- `migrations/010_create_scheduled_reminders.sql` - Reminders table with CRON support
- `migrations/011_create_user_settings.sql` - User timezone preferences

#### New Dependencies
- `dateparser>=1.2.0` - Natural language time parsing
- `croniter>=2.0.0` - CRON expression handling
- `pytz>=2024.1` - Timezone support

### Fixed
- **Timezone not applied to agentic reminders** - The `set_reminder` tool was hardcoded to use UTC instead of fetching the user's timezone preference. Now correctly uses the user's stored timezone.

### Technical Details

#### Files Created
- `src/reminders/__init__.py` - Package exports
- `src/reminders/time_parser.py` - Natural language + CRON parsing
- `src/reminders/manager.py` - Database operations
- `src/reminders/scheduler.py` - Background delivery loop
- `src/commands/reminder_commands.py` - Slash command implementations

#### Files Modified
- `src/discord_bot.py` - Reminder system integration (init, start/stop scheduler)
- `src/claude_client.py` - Added reminder tools and system prompt section
- `requirements.txt` - Added dateparser, croniter, pytz
- `CLAUDE.md` - Full reminder documentation

#### Migration Steps
1. Install new dependencies:
   ```bash
   pip install dateparser croniter pytz
   ```
2. Run migrations:
   ```bash
   psql $DATABASE_URL -f migrations/010_create_scheduled_reminders.sql
   psql $DATABASE_URL -f migrations/011_create_user_settings.sql
   ```
3. Restart bot to load reminder system

#### No Breaking Changes
- Reminders are automatically enabled when database is available
- No new required environment variables
- Fully backwards compatible

---

## [0.9.16] - 2026-01-09

### Added

#### Native PostgreSQL Analytics System
A comprehensive analytics system for tracking bot usage, performance, and errors:

- **Event Tracking** - Fire-and-forget async tracking with `track()` and `track_async()` functions
- **7 Event Categories**: message, memory, command, tool, api, error, system
- **Core Events Tracked**:
  - `message_received` - User messages with guild/channel context
  - `response_sent` - Bot responses with latency metrics
  - `claude_api_call` - API calls with token usage and cache stats
  - `tool_executed` - Agentic tool calls with success/failure
  - `command_used` - Slash command invocations
  - `memory_created`, `retrieval_performed`, `extraction_triggered` - Memory system events
  - `bot_error` - Errors with type, message, and traceback

#### Analytics Slash Commands (Owner Only)
New `/analytics` command group for real-time insights:

| Command | Description |
|---------|-------------|
| `/analytics summary` | 24-hour overview (messages, users, tokens, errors) |
| `/analytics dau [days]` | Daily active users over time |
| `/analytics tokens [days]` | Token usage breakdown with cache stats |
| `/analytics commands [days]` | Most-used slash commands |
| `/analytics errors [limit]` | Recent error log |
| `/analytics users [days]` | Most active users |
| `/analytics memory` | Memory system statistics |

#### Analytics CLI Tool
New `scripts/analytics_query.py` for command-line analytics:

```bash
python scripts/analytics_query.py summary    # 24-hour overview
python scripts/analytics_query.py dau        # Daily active users
python scripts/analytics_query.py tokens     # Token usage by day
python scripts/analytics_query.py commands   # Command usage
python scripts/analytics_query.py errors     # Recent errors
python scripts/analytics_query.py latency    # Response latency percentiles
python scripts/analytics_query.py memory     # Memory system stats
python scripts/analytics_query.py tools      # Tool execution stats
```

#### New Database Migration
- `migrations/009_create_analytics.sql` - Analytics events table with 7 indexes

#### New Environment Variable
- `ANALYTICS_ENABLED` - Set to "true" to enable analytics (default: disabled)

### Technical Details

#### Files Created
- `src/analytics.py` - Core analytics module with lazy connection pool
- `src/commands/analytics_commands.py` - Owner-only slash commands with @owner_only() decorator
- `scripts/analytics_query.py` - CLI tool with predefined queries
- `migrations/009_create_analytics.sql` - Database schema

#### Files Modified
- `src/discord_bot.py` - Added message_received/response_sent/bot_error tracking, analytics cog
- `src/claude_client.py` - Added claude_api_call/tool_executed/tool_error tracking
- `src/memory/manager.py` - Added memory system event tracking
- `src/commands/memory_commands.py` - Added command_used tracking for all memory commands
- `CLAUDE.md` - Added analytics documentation

#### Migration Steps
1. Deploy code changes
2. Run migration:
   ```bash
   psql $DATABASE_URL -f migrations/009_create_analytics.sql
   ```
3. Set `ANALYTICS_ENABLED=true` in environment
4. Restart bot to load analytics cog

#### No Breaking Changes
- Analytics is opt-in via `ANALYTICS_ENABLED` environment variable
- Without it, behavior is identical to v0.9.15

---

## [0.9.15] - 2026-01-03

### Fixed

#### MCP Server No Longer Wipes Slash Commands
- **Critical bug fix**: The MCP server (used by Claude Code) was syncing an empty command tree on startup, which wiped out the `/memories` slash commands registered by the production bot
- Root cause: `on_ready()` called `tree.sync()` unconditionally, but in MCP-only mode (`enable_chat=False`), no cogs are loaded, resulting in an empty tree overwriting production commands
- Fix: Skip command sync when `enable_chat=False` (MCP-only mode)
- This resolves the issue where slash commands would stop working ~1 day after deployment (whenever Claude Code was used)

---

## [0.9.14] - 2026-01-01

### Added

#### Message Search MCP Tool
- **`search_messages`** - New MCP tool for finding messages in Discord channels
  - Full-text search with case-insensitive matching
  - **Cross-channel search** - omit `channel` to search all accessible channels
  - **Channel name resolution** - use names like "server-general" instead of numeric IDs
    - Handles emoji prefixes (e.g., "server-general" matches "🖥️server-general")
    - Supports partial matching and case-insensitive lookup
  - Optional author filter with automatic username → ID resolution
  - Supports username, display name, and partial matches
  - Returns message ID, channel info, author info, content snippet, and timestamp
  - Results sorted by timestamp (most recent first)
- Use cases:
  - "Find my post about modpacks" → `search_messages("modpack", author="slashAI")` (searches everywhere)
  - "Find my post in server-general" → `search_messages("modpack", channel="server-general", author="slashAI")`
  - "What did Slash say about redstone?" → `search_messages("redstone", author="SlashDaemon")`

#### Channel Name Resolution
- New `resolve_channel()` helper method for converting channel names to IDs
- Available for future use by other MCP tools (send_message, edit_message, etc.)

#### Dual Licensing (AGPL-3.0 + Commercial)
- **LICENSE.md** - Full AGPL-3.0 license text with commercial licensing option
- **CLA.md** - Contributor License Agreement for PR submissions
- **NOTICE.md** - Third-party software attribution (discord.py, anthropic, asyncpg, etc.)
- **pyproject.toml** - Package metadata with dual licensing classifiers
- **AGPL-3.0 headers** added to all 23 Python source files
- **GitHub Actions CLA workflow** (`.github/workflows/cla.yml`) - Automatically requests CLA signature from first-time contributors

### Technical Details

#### Files Modified
- `src/discord_bot.py`:
  - Added `resolve_channel()` method for name → ID resolution
  - Updated `search_messages()` to support cross-channel search
- `src/mcp_server.py`:
  - Added `search_messages` MCP tool with channel name support

#### No Breaking Changes
- No database migrations required
- No new environment variables
- Fully backwards compatible

---

## [0.9.13] - 2025-12-30

### Added

#### Prompt Caching for System Prompt
- **Anthropic prompt caching** enabled for the base system prompt (~1,100 tokens)
- Cache reduces input token costs by ~15-20% on cache hits
- Faster response times when cache is active

#### Cache Statistics Tracking
- New tracking counters: `cache_creation_tokens`, `cache_read_tokens`
- Updated `get_usage_stats()` returns cache metrics:
  - `cache_creation_tokens` - Tokens written to cache
  - `cache_read_tokens` - Tokens read from cache (savings)
  - `cache_savings_usd` - Estimated money saved from cache hits

### Technical Details

#### How It Works
- Base system prompt is wrapped with `cache_control: {"type": "ephemeral"}`
- Memory context (dynamic per-request) is appended separately and NOT cached
- Cache expires after 5 minutes of inactivity per conversation
- Cache is per-user/channel (not shared across conversations)

#### Files Modified
- `src/claude_client.py`:
  - System prompt now sent as array with cache_control block
  - Added `total_cache_creation_tokens` and `total_cache_read_tokens` counters
  - Updated both `chat()` and `chat_single()` methods
  - Enhanced `get_usage_stats()` with cache pricing calculations

#### Cache Pricing (Claude Sonnet 4.5)
- Cache write: 25% of base input price ($0.75/M tokens)
- Cache read: 10% of base input price ($0.30/M tokens)
- Savings: 90% on cached tokens when hit

#### No Breaking Changes
- No database migrations required
- No new environment variables
- Fully backwards compatible

---

## [0.9.12] - 2025-12-30

### Added

#### Agentic Discord Tools (Owner Only)
The bot owner can now trigger Discord actions directly through chat conversations:

- **Tool Use in Chat** - Claude can call Discord tools when the owner requests actions
  - "Post 'Hello everyone!' in #general"
  - "Read the last 10 messages from #announcements"
  - "Delete my last message in that channel"

- **Available Tools** (same as MCP server, plus one new):
  - `send_message` - Post to any accessible channel
  - `edit_message` - Edit bot's previous messages
  - `delete_message` - Delete bot's messages
  - `read_messages` - Fetch channel history
  - `list_channels` - List available channels
  - `get_channel_info` - Get channel metadata
  - `describe_message_image` - Fetch and describe images from past messages (NEW)

- **Security Model**:
  - Tools are **only enabled for the owner** (configured via `OWNER_ID`)
  - Other users chat normally with no tool access
  - Tool calls require explicit user request (never automatic)
  - Agentic loop with 10-iteration safety limit

#### New Environment Variable
- `OWNER_ID` - Discord user ID allowed to trigger agentic actions
  - Leave empty to disable tool use entirely (falls back to v0.9.11 behavior)

### Technical Details

#### Files Modified
- `src/claude_client.py`:
  - Added `DISCORD_TOOLS` constant with 7 Anthropic-format tool schemas
  - Added `bot` and `owner_id` parameters to `ClaudeClient.__init__()`
  - Implemented agentic loop in `chat()` method
  - Added `_execute_tool()` helper for tool execution
  - `describe_message_image` makes a separate Claude Vision API call
  - Updated system prompt with "Discord Actions (Owner Only)" section
- `src/discord_bot.py`:
  - Added `OWNER_ID` environment variable loading
  - Pass `bot=self` and `owner_id` to ClaudeClient
  - Added `get_message_image()` method to fetch image attachments

#### New Documentation
- `docs/enhancements/003_AGENTIC_TOOLS.md` - Design document for this feature

#### No Breaking Changes
- No database migrations required
- Feature is opt-in via `OWNER_ID` environment variable
- Without `OWNER_ID`, behavior is identical to v0.9.11

---

## [0.9.11] - 2025-12-30

### Added

#### Memory Management Slash Commands
Users can now view and manage their memories directly through Discord slash commands:

- `/memories list [page] [privacy]` - List your memories with pagination
  - Optional privacy filter: all, dm, channel_restricted, guild_public, global
  - Shows memory ID, type, privacy level, summary, and last updated date

- `/memories search <query> [page]` - Search your memories by text
  - Searches both topic summaries and source dialogue
  - Same pagination as list

- `/memories mentions [page]` - View others' public memories that mention you
  - Searches guild_public memories from other users
  - Looks for your username, display name, and IGN
  - Read-only (cannot delete others' memories)

- `/memories view <memory_id>` - View full memory details
  - Shows complete summary, source dialogue, and metadata
  - Privacy level, confidence score, timestamps
  - Delete button for your own memories

- `/memories delete <memory_id>` - Delete one of your memories
  - Confirmation dialog before deletion
  - Only works on your own memories
  - Deletion is logged for audit purposes

- `/memories stats` - View your memory statistics
  - Total memory count
  - Breakdown by privacy level
  - Breakdown by type (semantic/episodic)
  - Last updated timestamp

#### Privacy & Security Features
- All command responses are **ephemeral** (only visible to you)
- Ownership checks prevent deleting others' memories
- Button interactions verify the user matches the command invoker
- Mentions feature only shows guild_public memories from same server
- Audit table logs all deletions for debugging/recovery

#### New Database Migration
- `migrations/008_add_deletion_log.sql` - Audit table for memory deletions

### Technical Details

#### New Files
- `src/commands/__init__.py` - Commands package
- `src/commands/memory_commands.py` - Slash command implementations
- `src/commands/views.py` - Discord UI components (pagination, confirmation dialogs)
- `migrations/008_add_deletion_log.sql` - Deletion audit table

#### Modified Files
- `src/memory/manager.py` - Added query methods for commands
  - `list_user_memories()` - List with pagination and privacy filter
  - `search_user_memories()` - Text search
  - `find_mentions()` - Find others' public memories mentioning user
  - `get_memory()` - Get single memory by ID
  - `delete_memory()` - Delete with ownership check and audit logging
  - `get_user_stats()` - Statistics summary
- `src/discord_bot.py` - Load commands cog and sync command tree

#### Migration Steps
1. Deploy code changes
2. Run migration to create audit table:
   ```bash
   psql $DATABASE_URL -f migrations/008_add_deletion_log.sql
   ```
3. Restart bot to sync slash commands to Discord

---

## [0.9.10] - 2025-12-30

### Added

#### Memory Attribution System
- Memories now include clear attribution showing WHO each memory belongs to
- When retrieving memories from multiple users, each person's context is grouped separately
- Display names are resolved in real-time via Discord API (handles name changes automatically)
- Added debug logging showing retrieved memories with user_id and similarity scores (Phase 1.5)

#### Pronoun-Neutral Memory Format
- Memory summaries are now extracted in pronoun-neutral format
- Old: "User's IGN is slashdaemon", "User built a creeper farm"
- New: "IGN: slashdaemon", "Built creeper farm"
- Prevents ambiguity when memories from multiple users are retrieved together

#### New CLI Tools (`scripts/`)
- `migrate_memory_format.py` - One-time migration to convert existing memories to new format
  - Dry-run mode by default (safe to test)
  - Uses Claude Haiku for fast, accurate reformatting
  - Batch processing with rate limiting
- `memory_inspector.py` - Debug tool for the memory system
  - List memories with filters (user, privacy level, guild)
  - Show system statistics
  - Inspect individual memories
  - Search by content
  - Export to JSON with `--all` flag for complete backups

### Fixed

#### Cross-User Memory Confusion (Rain/SlashDaemon Incident)
- When Rain asked "what do you remember about me?", slashAI incorrectly attributed SlashDaemon's memories to Rain
- Root cause: `_format_memories()` didn't indicate WHO each memory belonged to
- Now memories are formatted with clear sections:
  - "Your History With This User" for the current user's memories
  - "Public Knowledge From This Server" with each person's memories grouped under their display name

### Technical Details

#### Files Modified
- `src/memory/retriever.py` - Added `user_id` to `RetrievedMemory` dataclass and SQL queries
- `src/claude_client.py` - Updated `_format_memories()` with attribution logic; added `_resolve_display_name()`
- `src/memory/extractor.py` - Updated extraction prompt for pronoun-neutral format

#### New Files
- `scripts/migrate_memory_format.py` - Migration script for existing memories
- `scripts/memory_inspector.py` - Debug CLI tool

#### No Breaking Changes
- No database schema changes required
- Existing memories continue to work (just lack attribution until migrated)
- New format only affects newly extracted memories

#### Migration Steps
1. Deploy code changes (Phase 1+2 take effect immediately)
2. **Create backup before migration** (required):
   ```bash
   DATABASE_URL=... python scripts/memory_inspector.py export --all -o backups/memories_pre_migration.json
   ```
3. Run migration script in dry-run mode to preview changes:
   ```bash
   DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/migrate_memory_format.py
   ```
4. Review output, then apply:
   ```bash
   DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/migrate_memory_format.py --apply
   ```

---

## [0.9.9] - 2025-12-28

### Fixed

#### Cross-User Guild Memory Sharing
- `guild_public` memories were incorrectly user-scoped, preventing cross-user knowledge sharing
- When User A shared information in a public channel, User B couldn't access that memory
- Now `guild_public` memories are properly shared across all users in the same guild

#### Files Updated
- `src/memory/retriever.py` - Text memory retrieval now cross-user for guild_public
- `src/memory/images/narrator.py` - Image memory context now cross-user for guild_public
- `src/memory/images/clusterer.py` - Build cluster listing now cross-user for guild_public

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required
- Privacy model unchanged: DMs and restricted channels remain user-scoped

---

## [0.9.8] - 2025-12-27

### Changed
- Strengthened "no trailing questions" rule in system prompt—now a hard ban instead of soft guidance
- Explicitly bans curious follow-ups like "what are you working on?" and "how's it going?"
- Only allowed exception: when needing info to help (e.g., "which file?")

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.7] - 2025-12-27

### Fixed
- "Could not process image" errors from Anthropic API on certain JPEG images (e.g., Google Pixel photos)
- Added image normalization to fix CMYK color space, progressive JPEG encoding, and problematic EXIF metadata

### Technical Details
- Added `normalize_image_for_api()` function in `discord_bot.py` and `analyzer.py`
- All JPEGs are now re-encoded through PIL before sending to API
- Converts unsupported color modes (CMYK, YCCK, LAB, P) to RGB/RGBA
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.6] - 2025-12-27

### Changed
- Reduced trailing question frequency in responses—bot no longer ends every message with a question
- Added guidance to Communication Style: questions are fine when genuinely curious, not as conversational filler
- Added "Not a conversation prolonger" to personality constraints

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.5] - 2025-12-27

### Added
- Self-knowledge in system prompt—bot can now accurately answer questions about its own capabilities
- Covers text memory, image memory, privacy boundaries, real-time vision, and limitations

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.4] - 2025-12-27

### Fixed
- Crash when posting image-only messages with memory enabled
- Voyage API rejects empty strings for embedding; now skip memory retrieval for empty queries

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.3] - 2025-12-27

### Fixed

#### Large Image Handling
- Images exceeding Anthropic's 5MB limit are now automatically resized
- Progressive JPEG compression (85 → 70 → 55 → 40 quality) before dimension reduction
- Proper MIME type updates when images are converted to JPEG

#### Memory Optimization for Constrained Workers
- Voyage embeddings now resize images to max 512px (reduces RAM from ~36MB to ~750KB for phone photos)
- PIL images explicitly closed in `finally` blocks to prevent memory leaks
- Explicit `gc.collect()` after image processing completes
- Fixed `UnboundLocalError` for `result_bytes` in resize function

#### Diagnostic Logging
- Added `[MSG]` logging at start of `on_message` showing attachments, embeds, mentions, and DM status
- Helps diagnose mobile upload issues and silent failures

### Technical Details
- No new dependencies
- No new environment variables
- No database migrations required

---

## [0.9.2] - 2025-12-26

### Added

#### Image Memory System
- Full image memory pipeline for tracking Minecraft build projects
- **ImageObserver** - Entry point orchestrating moderation, analysis, storage, and clustering
- **ImageAnalyzer** - Claude Vision for structured image analysis:
  - Detailed descriptions, one-line summaries, and tags
  - Structured element detection (biome, time, structures, materials, style, completion stage)
  - Observation type classification (build_progress, landscape, redstone, farm, other)
  - Voyage multimodal-3 embeddings for semantic similarity
- **ImageStorage** - DigitalOcean Spaces integration:
  - Private ACL with signed URL access
  - Hash-based deduplication
  - Organized storage: `images/{user_id}/{year}/{month}/{hash}.{ext}`
- **BuildClusterer** - Groups related observations into project clusters:
  - Cosine similarity matching against cluster centroids (threshold: 0.72)
  - Privacy-compatible cluster assignment
  - Automatic cluster naming based on detected tags
  - Rolling centroid updates for efficiency
- **BuildNarrator** - Generates progression narratives:
  - Chronological timeline with milestone detection
  - Brief context injection for chat responses
  - LLM-generated summaries celebrating progress

#### Content Moderation
- Pre-storage moderation check for all images
- Multi-tier confidence handling:
  - High confidence (≥0.7): Delete message, warn user, notify moderators
  - Uncertain (0.5-0.7): Flag for review, continue processing
  - Low confidence (<0.5): Proceed normally
- Text-only moderation log (violated images never stored)
- Moderator notifications via configured channel

#### Database Schema (migrations 005-007)
- `build_clusters` table for project grouping with centroid embeddings
- `image_observations` table with full metadata, embeddings, and cluster references
- `image_moderation_log` table for violation tracking
- Privacy-aware indexes for efficient retrieval

#### Real-time Image Understanding
- Images in chat messages are now passed to Claude Vision
- Bot can see and respond to images shared in conversation

### Fixed
- pgvector embedding format for database inserts (string format `[0.1,0.2,...]`)
- pgvector centroid parsing in cluster matching
- Voyage multimodal embedding to use PIL Image objects (not base64)

### Technical Details
- New dependencies: `boto3`, `Pillow`
- Environment variables: `DO_SPACES_KEY`, `DO_SPACES_SECRET`, `DO_SPACES_BUCKET`, `DO_SPACES_REGION`, `IMAGE_MEMORY_ENABLED`, `IMAGE_MODERATION_ENABLED`, `MOD_CHANNEL_ID`

---

## [0.9.1] - 2025-12-26

### Added

#### Privacy-Aware Persistent Memory
- Cross-session memory using PostgreSQL + pgvector + Voyage AI
- Four privacy levels with channel-based classification:
  - `dm` - DM conversations, retrievable only in DMs
  - `channel_restricted` - Role-gated channels, retrievable in same channel only
  - `guild_public` - Public channels, retrievable anywhere in same guild
  - `global` - Explicit facts (IGN, timezone), retrievable everywhere
- **MemoryExtractor** - LLM-based topic extraction:
  - Triggers after 5 message exchanges (lowered from 10 for faster learning)
  - Structured JSON output with topics, sentiments, and privacy levels
  - Handles multi-topic conversations
- **MemoryRetriever** - Semantic search with privacy filtering:
  - Voyage AI embeddings (voyage-3.5-lite)
  - pgvector cosine similarity (threshold: 0.3)
  - Privacy-filtered results based on conversation context
- **MemoryUpdater** - ADD/MERGE logic for memory updates:
  - New information creates new memories
  - Related information merges with existing memories
  - Same-privacy-level constraint for merges
- **MemoryManager** - Facade orchestrating all memory operations

#### Message Handling
- Automatic message chunking for responses exceeding Discord's 2000 character limit
- Semantic splitting on markdown headers (##, ###)
- File attachment reading (.md, .txt, .py, .json, .yaml, .csv, etc.)
- Support for up to 100KB attachments

#### Database Schema (migrations 001-004)
- `memories` table with embeddings, privacy levels, and metadata
- `sessions` table for conversation tracking
- pgvector extension for vector similarity search
- Efficient indexes for privacy-filtered retrieval

### Fixed
- JSONB handling for session messages
- Memory extraction prompt escaping for Python .format()
- Duplicate message responses
- Privacy filter application to similarity debug logging

### Technical Details
- New dependencies: `asyncpg`, `voyageai`, `numpy`
- Environment variables: `DATABASE_URL`, `VOYAGE_API_KEY`, `MEMORY_ENABLED`
- Fallback: Set `MEMORY_ENABLED=false` to disable memory and return to v0.9.0 behavior

---

## [0.9.0] - 2025-12-25

### Added

#### Discord Bot
- Initial Discord bot implementation using discord.py 2.6.4
- Chatbot functionality powered by Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
- Responds to @mentions in any channel the bot can access
- Direct message (DM) support for private conversations
- Per-user, per-channel conversation history (up to 20 messages retained)
- Custom system prompt with configurable personality
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
| 0.11.0 | 2026-02-06 | Core Curriculum recognition, /verify linking, StreamCraft commands |
| 0.10.0 | 2026-01-12 | Hybrid search (lexical + semantic RRF), database backups, GitHub doc reader |
| 0.9.23 | 2026-01-12 | Query-relevant image retrieval |
| 0.9.17 | 2026-01-10 | Scheduled reminders with natural language + CRON support |
| 0.9.16 | 2026-01-09 | Native PostgreSQL analytics with slash commands and CLI tool |
| 0.9.15 | 2026-01-03 | Fix MCP server wiping slash commands |
| 0.9.14 | 2026-01-01 | Message search tool with cross-channel and channel name resolution |
| 0.9.13 | 2025-12-30 | Prompt caching for system prompt (15-20% cost reduction) |
| 0.9.12 | 2025-12-30 | Agentic Discord tools for owner-only chat actions |
| 0.9.11 | 2025-12-30 | Memory management slash commands |
| 0.9.10 | 2025-12-30 | Memory attribution system and pronoun-neutral format |
| 0.9.9 | 2025-12-28 | Fix cross-user guild_public memory sharing |
| 0.9.8 | 2025-12-27 | Hard ban on trailing questions |
| 0.9.7 | 2025-12-27 | Fix image processing errors on Pixel photos |
| 0.9.6 | 2025-12-27 | Reduce trailing questions in responses |
| 0.9.5 | 2025-12-27 | Self-knowledge capabilities in system prompt |
| 0.9.4 | 2025-12-27 | Fix crash on image-only messages |
| 0.9.3 | 2025-12-27 | Large image handling and memory optimization fixes |
| 0.9.2 | 2025-12-26 | Image memory system with build tracking and clustering |
| 0.9.1 | 2025-12-26 | Privacy-aware persistent text memory |
| 0.9.0 | 2025-12-25 | Initial release with Discord bot and MCP server |

---

## Upgrade Notes

### Migrating to 0.9.2

1. Run database migrations 005-007:
   ```sql
   \i migrations/005_create_build_clusters.sql
   \i migrations/006_create_image_observations.sql
   \i migrations/007_create_image_moderation_and_indexes.sql
   ```

2. Configure DigitalOcean Spaces credentials (required for image storage)

3. Set `IMAGE_MEMORY_ENABLED=true` to enable image memory

### Migrating to 0.9.1

1. Set up PostgreSQL with pgvector extension

2. Run database migrations 001-004:
   ```sql
   \i migrations/001_enable_pgvector.sql
   \i migrations/002_create_memories.sql
   \i migrations/003_create_sessions.sql
   \i migrations/004_add_indexes.sql
   ```

3. Configure `DATABASE_URL` and `VOYAGE_API_KEY` environment variables

4. Set `MEMORY_ENABLED=true` to enable memory system

### Breaking Changes

None across 0.9.x releases. All features are opt-in via environment variables.

---

[Unreleased]: https://github.com/mindfulent/slashAI/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/mindfulent/slashAI/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/mindfulent/slashAI/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/mindfulent/slashAI/compare/v0.9.23...v0.10.0
[0.9.23]: https://github.com/mindfulent/slashAI/compare/v0.9.17...v0.9.23
[0.9.17]: https://github.com/mindfulent/slashAI/compare/v0.9.16...v0.9.17
[0.9.16]: https://github.com/mindfulent/slashAI/compare/v0.9.15...v0.9.16
[0.9.15]: https://github.com/mindfulent/slashAI/compare/v0.9.14...v0.9.15
[0.9.14]: https://github.com/mindfulent/slashAI/compare/v0.9.13...v0.9.14
[0.9.13]: https://github.com/mindfulent/slashAI/compare/v0.9.12...v0.9.13
[0.9.12]: https://github.com/mindfulent/slashAI/compare/v0.9.11...v0.9.12
[0.9.11]: https://github.com/mindfulent/slashAI/compare/v0.9.10...v0.9.11
[0.9.10]: https://github.com/mindfulent/slashAI/compare/v0.9.9...v0.9.10
[0.9.9]: https://github.com/mindfulent/slashAI/compare/v0.9.8...v0.9.9
[0.9.8]: https://github.com/mindfulent/slashAI/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/mindfulent/slashAI/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/mindfulent/slashAI/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/mindfulent/slashAI/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/mindfulent/slashAI/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/mindfulent/slashAI/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/mindfulent/slashAI/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/mindfulent/slashAI/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/mindfulent/slashAI/releases/tag/v0.9.0
