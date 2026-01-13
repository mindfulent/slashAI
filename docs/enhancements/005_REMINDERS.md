# Scheduled Messages/Reminders for slashAI

## Overview

Add scheduled reminders to slashAI with full CRON support, natural language time parsing, and dual interfaces (chat + slash commands).

**Initial Version:** v0.9.17 (basic implementation)
**Enhanced Version:** v0.9.19 (conversational delivery, auto-detection)

## Requirements Summary

| Requirement | Implementation |
|-------------|----------------|
| Delivery | DM for regular users; OWNER_ID auto-detects public channel delivery |
| Recurrence | Full CRON support + natural language presets |
| Interface | Both natural language chat AND `/remind` slash commands |
| Permissions | Anyone can create personal reminders |
| Message Format | Conversational (Sonnet-generated) for notifications; structured embeds for slash command confirmations |

### Delivery Permission Model

| User | Reminder Set In | Delivery Location |
|------|-----------------|-------------------|
| Regular user | DM | DM |
| Regular user | Public channel | DM |
| OWNER_ID | DM | DM |
| OWNER_ID | Public channel | That public channel (auto-detected) |

**Auto-detection:** When OWNER_ID sets a reminder via natural language in a public guild channel, the system automatically sets `is_channel_delivery=True` and stores the source channel for delivery. No explicit `channel_id` parameter needed.

---

## Architecture

```
User Request
    ├─ Natural Language ("remind me at 10am...")
    │   └─ Claude Tools → time_parser → ReminderManager → DB
    │
    └─ Slash Command (/remind set)
        └─ ReminderCommands cog → time_parser → ReminderManager → DB

Background Scheduler (discord.ext.tasks, 1-min loop)
    └─ Query due reminders → Deliver via DM/channel → Update status
```

---

## Implementation Plan

### Phase 1: Database Schema

**Files:**
- `migrations/009_create_scheduled_reminders.sql`
- `migrations/010_create_user_settings.sql`

**scheduled_reminders table:**
```sql
CREATE TABLE scheduled_reminders (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    cron_expression TEXT,              -- NULL for one-time
    next_execution_at TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    delivery_channel_id BIGINT,        -- NULL = DM
    is_channel_delivery BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'active',      -- active/paused/completed/failed
    last_executed_at TIMESTAMPTZ,
    execution_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Critical index for scheduler queries
CREATE INDEX reminders_next_execution_idx
    ON scheduled_reminders(next_execution_at)
    WHERE status = 'active';

-- Index for user queries
CREATE INDEX reminders_user_idx
    ON scheduled_reminders(user_id, status);
```

**user_settings table:**
```sql
CREATE TABLE user_settings (
    user_id BIGINT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### Phase 2: Time Parsing Module

**File:** `src/reminders/time_parser.py`

**Dependencies (add to requirements.txt):**
```
dateparser>=1.2.0      # Natural language parsing
croniter>=2.0.0        # CRON expression handling
pytz>=2024.1           # Timezone support
```

**Key functions:**
- `parse_time_expression(expr, user_timezone)` → `ParsedTime`
  - Handles: "in 2 hours", "tomorrow at 10am", "next Monday"
  - Handles: CRON expressions "0 10 * * *", presets "hourly", "weekdays"
- `calculate_next_execution(cron_expr, timezone)` → `datetime`
- `validate_timezone(tz_name)` → `bool`

**CRON presets:**
```python
CRON_PRESETS = {
    "hourly": "0 * * * *",
    "daily": "0 9 * * *",
    "weekly": "0 9 * * 1",
    "weekdays": "0 9 * * 1-5",
    "monthly": "0 9 1 * *",
}
```

**ParsedTime dataclass:**
```python
@dataclass
class ParsedTime:
    next_execution: datetime    # UTC timestamp
    cron_expression: str | None # None for one-time
    is_recurring: bool
    original_input: str
    timezone: str
```

---

### Phase 3: Reminder Manager

**File:** `src/reminders/manager.py`

**Class:** `ReminderManager`

**Methods:**
| Method | Returns | Description |
|--------|---------|-------------|
| `create_reminder(user_id, content, parsed_time, delivery_channel_id)` | `int` | Create reminder, return ID |
| `list_reminders(user_id, include_completed, limit, offset)` | `(list, count)` | Paginated list |
| `get_reminder(reminder_id)` | `dict` | Full reminder details |
| `cancel_reminder(reminder_id, user_id)` | `bool` | Delete if owned |
| `pause_reminder(reminder_id, user_id)` | `bool` | Pause recurring |
| `resume_reminder(reminder_id, user_id)` | `bool` | Resume paused |
| `get_user_timezone(user_id)` | `str` | User's timezone (default: UTC) |
| `set_user_timezone(user_id, timezone)` | `bool` | Save timezone preference |

---

### Phase 4: Background Scheduler

**File:** `src/reminders/scheduler.py`

**Class:** `ReminderScheduler`

**Key behavior:**
- Uses `@tasks.loop(seconds=60)` decorator from discord.ext.tasks
- Queries: `WHERE status = 'active' AND next_execution_at <= NOW()`
- Delivers via DM or channel based on `is_channel_delivery`
- For recurring: calculates next execution from CRON, updates `next_execution_at`
- For one-time: marks `status = 'completed'`
- Failure handling: increments `failure_count`, marks failed after 5 attempts

**Delivery format: Conversational Messages**

All reminder notifications use conversational, personality-driven messages generated by Claude (Sonnet) at delivery time. This enables context-aware, natural-sounding reminders.

**Context gathered for message generation:**
1. **Channel context** (public channels only): Last 5-10 messages for conversational awareness
2. **User memories**: Privacy-aware retrieval related to the reminder content
   - Public channel → `guild_public` and `global` memories only
   - DM → Can include `dm` level memories
3. **Time context**: Current time with timezone shorthand (e.g., "10:00 AM PST")
4. **Recurrence context**: One-time, daily, weekly, etc.

**Message generation prompt:**
```
You're delivering a scheduled reminder to @{username}. Generate a natural, conversational message with personality.

Reminder content: {content}
Current time: {time} {timezone_short}
Recurrence: {one-time/daily/weekly/etc.}
{Recent channel messages (if public channel)}
{Relevant user memories}

Guidelines:
- Start with a friendly greeting and @mention (for channels) or just greeting (for DMs)
- Include the reminder content naturally
- Mention the time with timezone shorthand
- For recurring reminders, note the frequency naturally (e.g., "your daily reminder")
- Keep the personality warm and conversational
- Be contextually appropriate to any ongoing conversation
```

**Example outputs:**
- One-time (channel): "Hey @slashdaemon, just a heads up — time to check the Minecraft server backups! It's 10:00 AM PST."
- Daily (DM): "Good morning! Your daily reminder to review the server logs. It's 9:00 AM PST."
- Weekly (channel, with memory context): "Hey @slashdaemon, your weekly reminder to check on the creeper farm progress. Hope the new design is working out! It's Monday 10:00 AM PST."

**Fallback:** If Sonnet API fails, use simple template:
```
Hey @{user}, reminder: {content} ({time} {timezone})
```

**Slash command responses:** Confirmations from `/remind set`, `/remind list`, etc. continue to use structured embeds for clarity.

**Lifecycle integration:**
- `start()` called in `on_ready()` after command sync
- `stop()` called in `close()` for graceful shutdown
- `@_check_reminders.before_loop` waits for bot ready

---

### Phase 5: Claude Tools

**File:** `src/claude_client.py` (modify DISCORD_TOOLS)

**New tools:**

#### set_reminder
```python
{
    "name": "set_reminder",
    "description": "Create a reminder that will be delivered later. Can be one-time or recurring with CRON.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The reminder message content"
            },
            "time": {
                "type": "string",
                "description": "When: natural language ('in 2 hours', 'tomorrow at 10am') or CRON ('0 10 * * *')"
            },
            "channel_id": {
                "type": "string",
                "description": "Optional: Channel ID for admin channel delivery"
            }
        },
        "required": ["content", "time"]
    }
}
```

#### list_reminders
```python
{
    "name": "list_reminders",
    "description": "List scheduled reminders for the current user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "include_completed": {
                "type": "boolean",
                "description": "Include completed/failed reminders (default: false)"
            }
        }
    }
}
```

#### cancel_reminder
```python
{
    "name": "cancel_reminder",
    "description": "Cancel a scheduled reminder by ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reminder_id": {"type": "integer"}
        },
        "required": ["reminder_id"]
    }
}
```

#### set_user_timezone
```python
{
    "name": "set_user_timezone",
    "description": "Set the user's timezone preference. Use IANA timezone names. Call this when the user tells you their timezone in natural language - interpret their response (e.g., 'west coast' -> 'America/Los_Angeles').",
    "input_schema": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name (e.g., 'America/Los_Angeles', 'Europe/London')"
            }
        },
        "required": ["timezone"]
    }
}
```

**First-time user experience:**

When `set_reminder` is called and the user hasn't set their timezone:
1. Tool returns error prompting Claude to ask the user for their timezone
2. Claude asks conversationally: "What timezone are you in?"
3. User responds naturally: "west coast", "I'm in Seattle", "PST", etc.
4. Claude interprets and calls `set_user_timezone` with IANA timezone
5. Claude retries `set_reminder` with correct timezone

This ensures times are always interpreted correctly without requiring users to know IANA timezone names.

---

### Phase 6: Slash Commands

**File:** `src/commands/reminder_commands.py`

**Command group:** `/remind`

| Command | Parameters | Description |
|---------|------------|-------------|
| `/remind set` | `message`, `time`, `channel?` | Create reminder |
| `/remind list` | `include_completed?` | List your reminders |
| `/remind cancel` | `reminder_id` | Cancel a reminder |
| `/remind timezone` | `timezone` | Set your timezone |

**Features:**
- All responses ephemeral (private to user)
- Timezone autocomplete from `pytz.common_timezones`
- Helpful error messages with time format examples
- Channel parameter only effective for admin (OWNER_ID)

**Example interaction:**
```
User: /remind set message:"Check server logs" time:"every weekday at 9am"

Bot (ephemeral):
┌─────────────────────────────────────┐
│ ✅ Reminder Created                 │
├─────────────────────────────────────┤
│ ID: 42                              │
│ Message: Check server logs          │
│ Schedule: Recurring (0 9 * * 1-5)   │
│ Next: 2026-01-10 09:00 UTC          │
│ Delivery: DM                        │
│ Timezone: US/Pacific                │
└─────────────────────────────────────┘
```

---

### Phase 7: Integration

**File:** `src/discord_bot.py` (modify)

**In `setup_hook()`:**
```python
# Load reminder system (v0.9.16)
try:
    from reminders.manager import ReminderManager
    from reminders.scheduler import ReminderScheduler
    from commands.reminder_commands import ReminderCommands

    reminder_manager = ReminderManager(self.db_pool)
    self.reminder_scheduler = ReminderScheduler(self, self.db_pool)
    self.reminder_manager = reminder_manager  # For Claude tools

    await self.add_cog(ReminderCommands(
        self, self.db_pool, reminder_manager, owner_id
    ))
    logger.info("Reminder commands cog loaded")
except Exception as e:
    logger.error(f"Failed to load reminder system: {e}", exc_info=True)
```

**In `on_ready()`:**
```python
# Start reminder scheduler (after command sync)
if hasattr(self, 'reminder_scheduler') and self.enable_chat:
    self.reminder_scheduler.start()
```

**In `close()`:**
```python
# Stop scheduler on shutdown
if hasattr(self, 'reminder_scheduler'):
    self.reminder_scheduler.stop()
```

---

## Package Structure

```
src/
├── reminders/
│   ├── __init__.py
│   ├── time_parser.py      # NL + CRON parsing
│   ├── scheduler.py        # Background task loop
│   └── manager.py          # Database operations
├── commands/
│   ├── memory_commands.py  # (existing)
│   └── reminder_commands.py # NEW
├── discord_bot.py          # (modify)
└── claude_client.py        # (modify)

migrations/
├── ...existing...
├── 009_create_scheduled_reminders.sql
└── 010_create_user_settings.sql
```

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| Invalid time expression | Return helpful error with format examples |
| Invalid timezone | Suggest common timezones, link to tz database |
| User blocked DMs | Retry up to 5x, then mark reminder as failed |
| Channel deleted | Mark reminder as failed immediately |
| Bot lacks channel permissions | Retry up to 5x, then mark as failed |
| Invalid CRON expression | Validate with croniter before saving |
| Database connection error | Log error, retry on next scheduler loop |

---

## Verification Plan

### Automated Tests

1. **time_parser.py unit tests:**
   - Natural language: "in 2 hours", "tomorrow at 10am", "next Monday 3pm"
   - CRON expressions: "0 10 * * *", "0 9 * * 1-5", presets
   - Timezone conversions across DST boundaries
   - Invalid input rejection

2. **scheduler.py integration tests:**
   - Create reminder due in 1 minute, verify delivery
   - Create recurring reminder, verify rescheduling
   - Simulate failure (mock blocked DMs), verify retry logic

### Manual Testing Checklist

**Basic functionality:**
- [ ] Chat: "@slashAI remind me at 10am to check the server"
- [ ] Chat: "@slashAI remind me every weekday at 9am to check logs"
- [ ] Chat: "@slashAI list my reminders"
- [ ] Chat: "@slashAI cancel reminder 1"
- [ ] Slash: `/remind set message:"Daily standup" time:"0 9 * * 1-5"`
- [ ] Slash: `/remind list`
- [ ] Slash: `/remind cancel 1`
- [ ] Slash: `/remind timezone US/Pacific`
- [ ] Bot restart: verify missed reminders are delivered on recovery

**v0.9.19 Enhancements:**
- [ ] OWNER_ID in public channel: reminder auto-delivers to that channel (no explicit channel param needed)
- [ ] OWNER_ID in DM: reminder delivers via DM (not channel)
- [ ] Non-owner in public channel: reminder delivers via DM (not channel)
- [ ] Conversational delivery: verify message has personality, includes time + timezone shorthand
- [ ] Recurring context: daily reminders say "daily reminder", weekly say "weekly", etc.
- [ ] Channel context: verify delivery message is contextually appropriate to recent conversation
- [ ] Memory context: verify relevant user memories are incorporated when applicable
- [ ] Fallback: if Sonnet API fails, simple template is used

---

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `migrations/009_create_scheduled_reminders.sql` | Create | Reminders table |
| `migrations/010_create_user_settings.sql` | Create | User timezone prefs |
| `src/reminders/__init__.py` | Create | Package exports |
| `src/reminders/time_parser.py` | Create | NL + CRON parsing |
| `src/reminders/scheduler.py` | Create | Background delivery loop |
| `src/reminders/manager.py` | Create | Database operations |
| `src/commands/reminder_commands.py` | Create | Slash commands |
| `src/discord_bot.py` | Modify | Integration points |
| `src/claude_client.py` | Modify | Add reminder tools |
| `requirements.txt` | Modify | Add dateparser, croniter, pytz |
| `CHANGELOG.md` | Modify | Add v0.9.16 entry |
| `CLAUDE.md` | Modify | Document reminder feature |

---

## Future Enhancements (Out of Scope)

- Snooze functionality ("snooze 10 minutes")
- Reminder categories/tags
- Shared reminders (team notifications)
- Web dashboard for reminder management
- Rich reminder content (embeds, attachments)
