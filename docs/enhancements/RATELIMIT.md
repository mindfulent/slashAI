# slashAI Rate Limiting & Subscription Plan

## Overview

Add usage-based rate limiting to slashAI with Patreon integration for paid tiers. Users get a free monthly allocation; exceeding it requires a Patreon subscription.

**Target Version:** v0.9.17

## Requirements Summary

| Requirement | Implementation |
|-------------|----------------|
| Message limits | Per-user monthly cap on Claude API responses |
| Image limits | Per-user monthly cap on user-uploaded images analyzed |
| Reset period | 1st of each month (UTC) |
| Paid access | Patreon subscription unlocks higher/unlimited tiers |
| Exemptions | `OWNER_ID` always exempt |
| Graceful UX | Friendly limit message with usage stats + Patreon link |
| Warnings | Proactive warnings at 80% and 95% thresholds |

---

## Design Decisions

### What Counts as Usage?

| Action | Counts? | Rationale |
|--------|---------|-----------|
| Message triggering Claude response | **Yes** | Direct API cost |
| Tool-only calls (no text response) | No | Not user-visible interaction |
| Memory extraction (background) | No | User can't control timing |
| User-uploaded image analyzed | **Yes** | User-initiated, API cost |
| Image in memory pipeline (auto) | No | Background process |
| `/memories` slash commands | No | No Claude API cost |

### Tracking Approach: Dedicated Tables vs. Analytics

| Approach | Pros | Cons |
|----------|------|------|
| **Dedicated tables** | Fast atomic increments, no dependencies, optimized for rate checking | Separate from analytics data |
| Analytics queries | Single source of truth, no new tables | Slow COUNT queries on every message, analytics becomes critical path |

**Decision:** Dedicated tables for usage tracking. Analytics can still record events independently for reporting.

### Patreon Integration: Webhook vs. Polling

| Approach | Pros | Cons |
|----------|------|------|
| **Webhook** | Real-time updates, no polling overhead | Requires HTTP endpoint, can miss events |
| API polling | Simple, no infrastructure | Delayed (up to 1hr), API rate limits |
| **Hybrid** | Best of both, webhook + daily fallback | More complexity |

**Decision:** Hybrid approach - webhook for real-time + daily sync fallback + manual `/subscribe link` command.

### Webhook Hosting Options

| Option | Pros | Cons |
|--------|------|------|
| **Add web component to slashAI App Platform app** | Same codebase, shared DB, managed infrastructure, minimal added cost | Slightly more complex app spec |
| Umami droplet | Already running | Couples unrelated services, manual management |
| DigitalOcean Functions | Serverless, pay-per-use | Cold starts, separate deployment |
| Separate web service | Clean isolation | More cost, another thing to manage |

**Decision:** Add a lightweight web service component to the existing slashAI DigitalOcean App Platform app.

The app would have two components:
- **Worker**: Discord bot (existing)
- **Web**: Webhook receiver (new, tiny aiohttp app on port 8080)

Both share the same codebase and `DATABASE_URL`. The web component only needs to handle occasional Patreon webhook POSTs, so resource usage is minimal.

**App spec addition (.do/app.yaml):**
```yaml
services:
  - name: patreon-webhook
    source_dir: /
    github:
      repo: mindfulent/slashAI
      branch: main
    run_command: python src/patreon/webhook_server.py
    http_port: 8080
    instance_count: 1
    instance_size_slug: apps-s-1vcpu-0.5gb
    routes:
      - path: /patreon
    envs:
      - key: DATABASE_URL
        scope: RUN_TIME
        value: ${db.DATABASE_URL}
      - key: PATREON_WEBHOOK_SECRET
        scope: RUN_TIME
        type: SECRET
```

### Image Rate Limiting Scope

**Important:** Only rate limit *new* user-uploaded images going forward. Do not:
- Retroactively count existing images in the image_observations table
- Count background memory pipeline operations
- Count images the bot processes automatically

The image count starts fresh from when rate limiting is deployed.

### Warning System

Users receive proactive warnings before hitting their limit:

| Threshold | Trigger | UX |
|-----------|---------|-----|
| **80%** | After response that crosses 80% | Subtle footer on normal response |
| **95%** | After response that crosses 95% | More prominent warning in response |
| **100%** | Before API call | Full limit-reached message, no response |

**Design principles:**
- Warnings are **non-intrusive** - added as embed footer, not separate messages
- Only warn **once per threshold** - don't spam on every message after 80%
- Track `last_warning_threshold` to avoid duplicate warnings

**Warning text examples:**

**80% threshold (footer):**
```
You've used 40 of 50 messages this month. Upgrade at patreon.com/slashAI for more.
```

**95% threshold (footer, more urgent):**
```
⚠️ Only 2 messages remaining this month! Upgrade at patreon.com/slashAI to continue chatting.
```

---

## Tier Configuration

```python
TIER_LIMITS = {
    "free": {
        "messages": 50,
        "images": 10,
        "description": "Free tier - great for trying out slashAI"
    },
    "supporter": {
        "messages": 500,
        "images": 100,
        "patreon_cents": 500,  # $5/month
        "description": "Supporter tier - 10x the limits"
    },
    "premium": {
        "messages": 2000,
        "images": 500,
        "patreon_cents": 1000,  # $10/month
        "description": "Premium tier - heavy usage"
    },
    "unlimited": {
        "messages": None,  # No limit
        "images": None,
        "patreon_cents": 2000,  # $20/month
        "description": "Unlimited tier - no limits"
    },
}
```

**Tier assignment logic:**
```python
def get_tier_for_pledge(pledge_cents: int) -> str:
    if pledge_cents >= 2000:
        return "unlimited"
    elif pledge_cents >= 1000:
        return "premium"
    elif pledge_cents >= 500:
        return "supporter"
    return "free"
```

---

## Architecture

```
User sends message
    │
    ├─ Is OWNER_ID? ──Yes──→ Proceed (exempt)
    │
    ▼
RateLimitManager.check_and_increment()
    │
    ├─ Get/create monthly usage record
    ├─ Get user tier (subscription table, default: free)
    ├─ Compare usage vs tier limits
    │
    ├─ Under limit? ──Yes──→ Increment count, proceed
    │
    ▼
    Return limit_reached response
        → Friendly message with stats
        → Patreon link
        → No Claude API call
```

---

## Phase 1: Database Schema

### Migration 011: User Subscriptions

```sql
-- Migration 011: Create user subscriptions table
-- Tracks Patreon links and subscription tiers

CREATE TABLE user_subscriptions (
    user_id BIGINT PRIMARY KEY,

    -- Tier info
    tier TEXT NOT NULL DEFAULT 'free',
    tier_override TEXT,  -- Admin-set tier (takes precedence)

    -- Patreon link
    patreon_user_id TEXT UNIQUE,
    patreon_email TEXT,
    patreon_pledge_cents INT DEFAULT 0,
    patreon_access_token TEXT,  -- Encrypted in production
    patreon_refresh_token TEXT,

    -- Status
    subscription_active BOOLEAN DEFAULT TRUE,
    linked_at TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for Patreon lookups during webhook processing
CREATE INDEX idx_subscriptions_patreon_id ON user_subscriptions(patreon_user_id);
```

### Migration 012: Monthly Usage Tracking

```sql
-- Migration 012: Create monthly usage tracking table
-- Atomic counters for rate limiting

CREATE TABLE user_monthly_usage (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,

    -- Month identifier (first day of month in UTC)
    month_start DATE NOT NULL,

    -- Usage counters
    message_count INT DEFAULT 0,
    image_count INT DEFAULT 0,

    -- Warning tracking (to avoid duplicate warnings)
    last_message_warning_pct INT DEFAULT 0,  -- 0, 80, or 95
    last_image_warning_pct INT DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- One record per user per month
    CONSTRAINT unique_user_month UNIQUE(user_id, month_start)
);

-- Fast lookup for current month usage
CREATE INDEX idx_usage_user_month ON user_monthly_usage(user_id, month_start DESC);
```

---

## Phase 2: Rate Limit Module

### File: `src/ratelimit/__init__.py`

```python
from .manager import RateLimitManager
from .tiers import TIER_LIMITS, get_tier_for_pledge

__all__ = ["RateLimitManager", "TIER_LIMITS", "get_tier_for_pledge"]
```

### File: `src/ratelimit/tiers.py`

```python
"""
Subscription tier configuration.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TierLimits:
    messages: Optional[int]  # None = unlimited
    images: Optional[int]
    patreon_cents: int
    description: str


TIER_LIMITS: dict[str, TierLimits] = {
    "free": TierLimits(
        messages=50,
        images=10,
        patreon_cents=0,
        description="Free tier"
    ),
    "supporter": TierLimits(
        messages=500,
        images=100,
        patreon_cents=500,
        description="Supporter ($5/mo)"
    ),
    "premium": TierLimits(
        messages=2000,
        images=500,
        patreon_cents=1000,
        description="Premium ($10/mo)"
    ),
    "unlimited": TierLimits(
        messages=None,
        images=None,
        patreon_cents=2000,
        description="Unlimited ($20/mo)"
    ),
}

TIER_ORDER = ["free", "supporter", "premium", "unlimited"]


def get_tier_for_pledge(pledge_cents: int) -> str:
    """Determine tier based on Patreon pledge amount."""
    for tier_name in reversed(TIER_ORDER):
        tier = TIER_LIMITS[tier_name]
        if pledge_cents >= tier.patreon_cents:
            return tier_name
    return "free"
```

### File: `src/ratelimit/manager.py`

```python
"""
Rate limit manager for tracking and enforcing usage limits.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import asyncpg

from .tiers import TIER_LIMITS, TierLimits

logger = logging.getLogger("slashAI.ratelimit")


@dataclass
class UsageStatus:
    """Current usage status for a user."""
    user_id: int
    tier: str
    tier_limits: TierLimits
    message_count: int
    image_count: int
    message_limit: Optional[int]
    image_limit: Optional[int]
    messages_remaining: Optional[int]
    images_remaining: Optional[int]
    is_limited: bool
    limit_type: Optional[str]  # "messages" or "images" if limited
    month_start: date
    last_message_warning_pct: int  # 0, 80, or 95
    last_image_warning_pct: int

    @property
    def message_percent(self) -> float:
        if self.message_limit is None:
            return 0.0
        return (self.message_count / self.message_limit) * 100

    @property
    def image_percent(self) -> float:
        if self.image_limit is None:
            return 0.0
        return (self.image_count / self.image_limit) * 100

    @property
    def message_warning_threshold(self) -> Optional[int]:
        """Returns 80 or 95 if we should warn, None otherwise."""
        pct = self.message_percent
        if pct >= 95 and self.last_message_warning_pct < 95:
            return 95
        elif pct >= 80 and self.last_message_warning_pct < 80:
            return 80
        return None

    @property
    def image_warning_threshold(self) -> Optional[int]:
        """Returns 80 or 95 if we should warn, None otherwise."""
        pct = self.image_percent
        if pct >= 95 and self.last_image_warning_pct < 95:
            return 95
        elif pct >= 80 and self.last_image_warning_pct < 80:
            return 80
        return None


class RateLimitManager:
    """Manages rate limiting and usage tracking."""

    def __init__(self, db_pool: asyncpg.Pool, owner_id: Optional[int] = None):
        self.db = db_pool
        self.owner_id = owner_id

    def _get_month_start(self) -> date:
        """Get the first day of the current month (UTC)."""
        now = datetime.now(timezone.utc)
        return date(now.year, now.month, 1)

    async def get_user_tier(self, user_id: int) -> str:
        """Get user's current tier (checks override first, then Patreon)."""
        row = await self.db.fetchrow(
            """
            SELECT tier, tier_override, subscription_active
            FROM user_subscriptions
            WHERE user_id = $1
            """,
            user_id
        )

        if row is None:
            return "free"

        # Admin override takes precedence
        if row["tier_override"]:
            return row["tier_override"]

        # Check if subscription is active
        if not row["subscription_active"]:
            return "free"

        return row["tier"]

    async def get_usage(self, user_id: int) -> UsageStatus:
        """Get current usage status for a user."""
        month_start = self._get_month_start()
        tier = await self.get_user_tier(user_id)
        tier_limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

        # Get or create usage record
        row = await self.db.fetchrow(
            """
            INSERT INTO user_monthly_usage (user_id, month_start)
            VALUES ($1, $2)
            ON CONFLICT (user_id, month_start) DO UPDATE
                SET updated_at = NOW()
            RETURNING message_count, image_count, last_message_warning_pct, last_image_warning_pct
            """,
            user_id,
            month_start
        )

        message_count = row["message_count"]
        image_count = row["image_count"]
        last_message_warning_pct = row["last_message_warning_pct"] or 0
        last_image_warning_pct = row["last_image_warning_pct"] or 0

        # Calculate remaining
        msg_remaining = None if tier_limits.messages is None else max(0, tier_limits.messages - message_count)
        img_remaining = None if tier_limits.images is None else max(0, tier_limits.images - image_count)

        # Check if limited
        is_limited = False
        limit_type = None

        if tier_limits.messages is not None and message_count >= tier_limits.messages:
            is_limited = True
            limit_type = "messages"
        elif tier_limits.images is not None and image_count >= tier_limits.images:
            is_limited = True
            limit_type = "images"

        return UsageStatus(
            user_id=user_id,
            tier=tier,
            tier_limits=tier_limits,
            message_count=message_count,
            image_count=image_count,
            message_limit=tier_limits.messages,
            image_limit=tier_limits.images,
            messages_remaining=msg_remaining,
            images_remaining=img_remaining,
            is_limited=is_limited,
            limit_type=limit_type,
            month_start=month_start,
            last_message_warning_pct=last_message_warning_pct,
            last_image_warning_pct=last_image_warning_pct,
        )

    async def check_message_limit(self, user_id: int) -> tuple[bool, Optional[UsageStatus]]:
        """
        Check if user can send a message.

        Returns:
            (allowed, usage_status) - allowed is True if under limit
        """
        # Owner is always exempt
        if self.owner_id and user_id == self.owner_id:
            return True, None

        usage = await self.get_usage(user_id)

        if usage.is_limited and usage.limit_type == "messages":
            return False, usage

        return True, usage

    async def check_image_limit(self, user_id: int) -> tuple[bool, Optional[UsageStatus]]:
        """
        Check if user can upload an image.

        Returns:
            (allowed, usage_status) - allowed is True if under limit
        """
        if self.owner_id and user_id == self.owner_id:
            return True, None

        usage = await self.get_usage(user_id)

        if usage.is_limited and usage.limit_type == "images":
            return False, usage

        # Also check if incrementing would exceed
        if usage.image_limit is not None and usage.image_count >= usage.image_limit:
            return False, usage

        return True, usage

    async def increment_message_count(self, user_id: int) -> None:
        """Increment message count for current month."""
        month_start = self._get_month_start()
        await self.db.execute(
            """
            INSERT INTO user_monthly_usage (user_id, month_start, message_count)
            VALUES ($1, $2, 1)
            ON CONFLICT (user_id, month_start) DO UPDATE
                SET message_count = user_monthly_usage.message_count + 1,
                    updated_at = NOW()
            """,
            user_id,
            month_start
        )

    async def increment_image_count(self, user_id: int) -> None:
        """Increment image count for current month."""
        month_start = self._get_month_start()
        await self.db.execute(
            """
            INSERT INTO user_monthly_usage (user_id, month_start, image_count)
            VALUES ($1, $2, 1)
            ON CONFLICT (user_id, month_start) DO UPDATE
                SET image_count = user_monthly_usage.image_count + 1,
                    updated_at = NOW()
            """,
            user_id,
            month_start
        )

    async def update_message_warning(self, user_id: int, threshold: int) -> None:
        """Mark that we've shown a warning at the given threshold (80 or 95)."""
        month_start = self._get_month_start()
        await self.db.execute(
            """
            UPDATE user_monthly_usage
            SET last_message_warning_pct = $3, updated_at = NOW()
            WHERE user_id = $1 AND month_start = $2
            """,
            user_id,
            month_start,
            threshold
        )

    async def update_image_warning(self, user_id: int, threshold: int) -> None:
        """Mark that we've shown a warning at the given threshold (80 or 95)."""
        month_start = self._get_month_start()
        await self.db.execute(
            """
            UPDATE user_monthly_usage
            SET last_image_warning_pct = $3, updated_at = NOW()
            WHERE user_id = $1 AND month_start = $2
            """,
            user_id,
            month_start,
            threshold
        )

    # =========================================================================
    # Admin Methods
    # =========================================================================

    async def set_tier_override(self, user_id: int, tier: Optional[str]) -> bool:
        """Admin: Set or clear a tier override for a user."""
        if tier and tier not in TIER_LIMITS:
            return False

        await self.db.execute(
            """
            INSERT INTO user_subscriptions (user_id, tier_override)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
                SET tier_override = $2, updated_at = NOW()
            """,
            user_id,
            tier
        )
        return True

    async def reset_usage(self, user_id: int) -> None:
        """Admin: Reset a user's usage for current month."""
        month_start = self._get_month_start()
        await self.db.execute(
            """
            UPDATE user_monthly_usage
            SET message_count = 0, image_count = 0, updated_at = NOW()
            WHERE user_id = $1 AND month_start = $2
            """,
            user_id,
            month_start
        )

    async def get_all_usage_stats(self) -> dict:
        """Admin: Get aggregate usage statistics."""
        month_start = self._get_month_start()

        row = await self.db.fetchrow(
            """
            SELECT
                COUNT(DISTINCT user_id) as active_users,
                SUM(message_count) as total_messages,
                SUM(image_count) as total_images,
                AVG(message_count) as avg_messages,
                AVG(image_count) as avg_images
            FROM user_monthly_usage
            WHERE month_start = $1
            """,
            month_start
        )

        tier_counts = await self.db.fetch(
            """
            SELECT
                COALESCE(tier_override, tier, 'free') as tier,
                COUNT(*) as count
            FROM user_subscriptions
            GROUP BY COALESCE(tier_override, tier, 'free')
            """
        )

        return {
            "month": month_start.isoformat(),
            "active_users": row["active_users"] or 0,
            "total_messages": row["total_messages"] or 0,
            "total_images": row["total_images"] or 0,
            "avg_messages": float(row["avg_messages"] or 0),
            "avg_images": float(row["avg_images"] or 0),
            "tier_distribution": {r["tier"]: r["count"] for r in tier_counts}
        }
```

---

## Phase 3: Integration Points

### Modify `discord_bot.py`

**In message handler (before Claude API call):**

```python
# Check rate limit before calling Claude
usage = None
if self.rate_limit_manager:
    allowed, usage = await self.rate_limit_manager.check_message_limit(message.author.id)
    if not allowed:
        await self._send_limit_reached_message(message.channel, usage)
        return

# ... existing Claude API call ...

# After successful response, increment counter and check for warnings
if self.rate_limit_manager and usage:
    await self.rate_limit_manager.increment_message_count(message.author.id)

    # Re-fetch usage to get updated counts
    usage = await self.rate_limit_manager.get_usage(message.author.id)

    # Check if we need to show a warning
    warning_threshold = usage.message_warning_threshold
    if warning_threshold:
        await self._add_usage_warning_to_response(message.channel, usage, warning_threshold)
        await self.rate_limit_manager.update_message_warning(message.author.id, warning_threshold)
```

**New helper method:**

```python
async def _send_limit_reached_message(
    self,
    channel: discord.abc.Messageable,
    usage: UsageStatus
) -> None:
    """Send a friendly message when user hits their limit."""
    embed = discord.Embed(
        title="Monthly Limit Reached",
        color=discord.Color.orange(),
        description=(
            f"You've used all **{usage.message_count}** of your "
            f"**{usage.message_limit}** monthly messages.\n\n"
            f"Your limit resets on **{self._get_next_reset_date()}**."
        )
    )

    embed.add_field(
        name="Want more?",
        value=(
            "Support slashAI on Patreon to unlock higher limits!\n\n"
            "**Supporter** ($5/mo): 500 messages, 100 images\n"
            "**Premium** ($10/mo): 2,000 messages, 500 images\n"
            "**Unlimited** ($20/mo): No limits\n\n"
            "[Subscribe on Patreon](https://patreon.com/YOUR_PATREON)"
        ),
        inline=False
    )

    embed.add_field(
        name="Current Usage",
        value=(
            f"Messages: {usage.message_count}/{usage.message_limit}\n"
            f"Images: {usage.image_count}/{usage.image_limit or '∞'}"
        ),
        inline=True
    )

    embed.add_field(
        name="Your Tier",
        value=usage.tier_limits.description,
        inline=True
    )

    embed.set_footer(text="Use /subscribe status to check your usage anytime")

    await channel.send(embed=embed)

def _get_next_reset_date(self) -> str:
    """Get the date of the next monthly reset."""
    from datetime import datetime, timezone
    from calendar import monthrange

    now = datetime.now(timezone.utc)
    _, days_in_month = monthrange(now.year, now.month)

    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)

    return next_month.strftime("%B %d, %Y")

async def _add_usage_warning_to_response(
    self,
    channel: discord.abc.Messageable,
    usage: UsageStatus,
    threshold: int
) -> None:
    """Add a subtle warning footer after the bot's response."""
    patreon_url = os.getenv("PATREON_URL", "https://patreon.com/slashAI")

    if threshold == 95:
        # Urgent warning
        remaining = usage.messages_remaining or 0
        text = f"⚠️ Only {remaining} message{'s' if remaining != 1 else ''} remaining this month! Upgrade at {patreon_url} to continue chatting."
        color = discord.Color.orange()
    else:  # 80%
        # Gentle heads-up
        text = f"You've used {usage.message_count} of {usage.message_limit} messages this month. Upgrade at {patreon_url} for more."
        color = discord.Color.light_grey()

    embed = discord.Embed(description=text, color=color)
    await channel.send(embed=embed)
```

**For image uploads (in image memory pipeline):**

```python
# In memory/images/observer.py or wherever images are processed

async def process_image(self, message: discord.Message, attachment: discord.Attachment):
    # Check rate limit first
    if self.rate_limit_manager:
        allowed, usage = await self.rate_limit_manager.check_image_limit(message.author.id)
        if not allowed:
            # Don't process, optionally notify user
            logger.info(f"User {message.author.id} hit image limit")
            return

    # ... existing processing ...

    # After successful processing, increment
    if self.rate_limit_manager:
        await self.rate_limit_manager.increment_image_count(message.author.id)
```

---

## Phase 4: Slash Commands

### File: `src/commands/subscribe_commands.py`

```python
"""
Subscription management slash commands.

Commands:
- /subscribe status - View your usage and tier
- /subscribe link - Link your Patreon account
- /subscribe unlink - Unlink your Patreon account
"""

import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from ratelimit import RateLimitManager, TIER_LIMITS

logger = logging.getLogger("slashAI.commands.subscribe")

PATREON_URL = os.getenv("PATREON_URL", "https://patreon.com/YOUR_PATREON")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))


class SubscribeCommands(commands.Cog):
    """Subscription and usage management commands."""

    subscribe_group = app_commands.Group(
        name="subscribe",
        description="Manage your slashAI subscription and usage"
    )

    def __init__(self, bot: commands.Bot, rate_limit_manager: RateLimitManager):
        self.bot = bot
        self.rl = rate_limit_manager

    # =========================================================================
    # /subscribe status
    # =========================================================================

    @subscribe_group.command(name="status")
    async def status(self, interaction: discord.Interaction):
        """View your current usage and subscription tier."""
        await interaction.response.defer(ephemeral=True)

        usage = await self.rl.get_usage(interaction.user.id)

        # Progress bars
        msg_bar = self._progress_bar(usage.message_count, usage.message_limit)
        img_bar = self._progress_bar(usage.image_count, usage.image_limit)

        embed = discord.Embed(
            title="Your slashAI Subscription",
            color=self._tier_color(usage.tier),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name=f"Tier: {usage.tier.title()}",
            value=usage.tier_limits.description,
            inline=False
        )

        # Messages
        msg_limit_str = str(usage.message_limit) if usage.message_limit else "∞"
        msg_remaining = f"({usage.messages_remaining} remaining)" if usage.messages_remaining is not None else ""
        embed.add_field(
            name=f"Messages: {usage.message_count}/{msg_limit_str}",
            value=f"{msg_bar} {msg_remaining}",
            inline=False
        )

        # Images
        img_limit_str = str(usage.image_limit) if usage.image_limit else "∞"
        img_remaining = f"({usage.images_remaining} remaining)" if usage.images_remaining is not None else ""
        embed.add_field(
            name=f"Images: {usage.image_count}/{img_limit_str}",
            value=f"{img_bar} {img_remaining}",
            inline=False
        )

        # Reset date
        from calendar import monthrange
        now = datetime.now(timezone.utc)
        if now.month == 12:
            reset = datetime(now.year + 1, 1, 1)
        else:
            reset = datetime(now.year, now.month + 1, 1)

        embed.set_footer(text=f"Resets {reset.strftime('%B %d, %Y')}")

        # Upgrade prompt for free tier
        if usage.tier == "free":
            embed.add_field(
                name="Upgrade",
                value=f"[Support on Patreon]({PATREON_URL}) for higher limits!",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    def _progress_bar(self, current: int, limit: int | None, width: int = 20) -> str:
        """Generate a text progress bar."""
        if limit is None:
            return "▓" * width  # Unlimited

        ratio = min(current / limit, 1.0)
        filled = int(ratio * width)
        empty = width - filled

        if ratio >= 1.0:
            return "█" * width + " (limit reached)"
        elif ratio >= 0.8:
            return "█" * filled + "░" * empty + " ⚠️"
        else:
            return "█" * filled + "░" * empty

    def _tier_color(self, tier: str) -> discord.Color:
        """Get color for tier embeds."""
        return {
            "free": discord.Color.light_grey(),
            "supporter": discord.Color.blue(),
            "premium": discord.Color.purple(),
            "unlimited": discord.Color.gold(),
        }.get(tier, discord.Color.default())

    # =========================================================================
    # /subscribe link
    # =========================================================================

    @subscribe_group.command(name="link")
    async def link(self, interaction: discord.Interaction):
        """Link your Patreon account to unlock higher tiers."""
        await interaction.response.defer(ephemeral=True)

        # TODO: Implement OAuth flow
        # For now, provide manual instructions

        embed = discord.Embed(
            title="Link Your Patreon Account",
            color=discord.Color.blue(),
            description=(
                "To link your Patreon account:\n\n"
                f"1. Subscribe at [{PATREON_URL}]({PATREON_URL})\n"
                "2. Connect your Discord in Patreon settings\n"
                "3. Your tier will sync automatically within 24 hours\n\n"
                "Need help? Contact the bot owner."
            )
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /subscribe tiers
    # =========================================================================

    @subscribe_group.command(name="tiers")
    async def tiers(self, interaction: discord.Interaction):
        """View available subscription tiers."""
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="slashAI Subscription Tiers",
            color=discord.Color.gold(),
            description="Choose the tier that fits your needs:"
        )

        for tier_name, tier in TIER_LIMITS.items():
            msg_str = str(tier.messages) if tier.messages else "Unlimited"
            img_str = str(tier.images) if tier.images else "Unlimited"
            price = "Free" if tier.patreon_cents == 0 else f"${tier.patreon_cents / 100:.0f}/month"

            embed.add_field(
                name=f"{tier_name.title()} - {price}",
                value=f"Messages: {msg_str}/mo\nImages: {img_str}/mo",
                inline=True
            )

        embed.add_field(
            name="Subscribe",
            value=f"[Support on Patreon]({PATREON_URL})",
            inline=False
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, rate_limit_manager: RateLimitManager):
    """Register the subscribe commands cog."""
    await bot.add_cog(SubscribeCommands(bot, rate_limit_manager))
```

---

## Phase 5: Patreon Integration

### Webhook Setup

Patreon webhooks notify us of pledge events. We need a small HTTP endpoint.

**File: `src/patreon/webhook.py`**

```python
"""
Patreon webhook handler for real-time pledge updates.

Webhook events:
- members:pledge:create - New pledge
- members:pledge:update - Pledge amount changed
- members:pledge:delete - Pledge cancelled
"""

import hashlib
import hmac
import json
import logging
import os
from aiohttp import web

logger = logging.getLogger("slashAI.patreon.webhook")

PATREON_WEBHOOK_SECRET = os.getenv("PATREON_WEBHOOK_SECRET", "")


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify Patreon webhook signature."""
    if not PATREON_WEBHOOK_SECRET:
        logger.warning("No webhook secret configured, skipping verification")
        return True

    expected = hmac.new(
        PATREON_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.md5
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


async def handle_webhook(request: web.Request) -> web.Response:
    """Handle incoming Patreon webhook."""
    # Verify signature
    signature = request.headers.get("X-Patreon-Signature", "")
    payload = await request.read()

    if not verify_signature(payload, signature):
        logger.warning("Invalid webhook signature")
        return web.Response(status=401)

    # Parse event
    try:
        data = json.loads(payload)
        event_type = request.headers.get("X-Patreon-Event", "unknown")
    except json.JSONDecodeError:
        return web.Response(status=400)

    logger.info(f"Patreon webhook: {event_type}")

    # Extract Discord user ID from social connections
    discord_id = None
    included = data.get("included", [])
    for item in included:
        if item.get("type") == "user":
            social = item.get("attributes", {}).get("social_connections", {})
            discord_data = social.get("discord")
            if discord_data:
                discord_id = discord_data.get("user_id")
                break

    if not discord_id:
        logger.info("Webhook has no linked Discord account")
        return web.Response(status=200, text="OK")

    # Get pledge amount
    pledge_cents = 0
    member_data = data.get("data", {}).get("attributes", {})
    if member_data.get("patron_status") == "active_patron":
        pledge_cents = member_data.get("currently_entitled_amount_cents", 0)

    # Update subscription
    rate_limit_manager = request.app.get("rate_limit_manager")
    if rate_limit_manager:
        from ratelimit.tiers import get_tier_for_pledge
        tier = get_tier_for_pledge(pledge_cents)

        await rate_limit_manager.db.execute(
            """
            INSERT INTO user_subscriptions (user_id, tier, patreon_pledge_cents, last_verified_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                tier = $2,
                patreon_pledge_cents = $3,
                subscription_active = TRUE,
                last_verified_at = NOW(),
                updated_at = NOW()
            """,
            int(discord_id),
            tier,
            pledge_cents
        )
        logger.info(f"Updated user {discord_id} to tier {tier} ({pledge_cents} cents)")

    return web.Response(status=200, text="OK")


def create_webhook_app(rate_limit_manager) -> web.Application:
    """Create aiohttp app for webhook handling."""
    app = web.Application()
    app["rate_limit_manager"] = rate_limit_manager
    app.router.add_post("/patreon/webhook", handle_webhook)
    return app
```

### File: `src/patreon/webhook_server.py`

Standalone server for the App Platform web component:

```python
"""
Standalone webhook server for DigitalOcean App Platform web component.

Run with: python src/patreon/webhook_server.py
Listens on port 8080 by default.
"""

import asyncio
import logging
import os

import asyncpg
from aiohttp import web

from webhook import handle_webhook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slashAI.patreon.server")

PORT = int(os.getenv("PORT", "8080"))
DATABASE_URL = os.getenv("DATABASE_URL")


async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint for App Platform."""
    return web.Response(text="OK")


async def init_app() -> web.Application:
    """Initialize the webhook server application."""
    app = web.Application()

    # Database pool for updating subscriptions
    if DATABASE_URL:
        app["db_pool"] = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
        logger.info("Database pool initialized")
    else:
        logger.warning("DATABASE_URL not set, webhook updates will be no-ops")
        app["db_pool"] = None

    # Routes
    app.router.add_get("/health", health_check)
    app.router.add_post("/patreon/webhook", handle_webhook)

    return app


def main():
    """Run the webhook server."""
    logger.info(f"Starting Patreon webhook server on port {PORT}")
    app = asyncio.get_event_loop().run_until_complete(init_app())
    web.run_app(app, port=PORT)


if __name__ == "__main__":
    main()
```

### Daily Sync (Fallback)

**File: `src/patreon/sync.py`**

```python
"""
Daily Patreon sync for catching missed webhooks.
Uses discord.ext.tasks for scheduling.
"""

import logging
import os
from datetime import datetime, timezone

import aiohttp
from discord.ext import tasks

from ratelimit import RateLimitManager
from ratelimit.tiers import get_tier_for_pledge

logger = logging.getLogger("slashAI.patreon.sync")

PATREON_ACCESS_TOKEN = os.getenv("PATREON_CREATOR_ACCESS_TOKEN", "")
PATREON_CAMPAIGN_ID = os.getenv("PATREON_CAMPAIGN_ID", "")


class PatreonSyncer:
    """Syncs Patreon pledges daily."""

    def __init__(self, rate_limit_manager: RateLimitManager):
        self.rl = rate_limit_manager

    def start(self):
        """Start the daily sync task."""
        self._sync_task.start()

    def stop(self):
        """Stop the sync task."""
        self._sync_task.cancel()

    @tasks.loop(hours=24)
    async def _sync_task(self):
        """Daily sync of all Patreon pledges."""
        if not PATREON_ACCESS_TOKEN or not PATREON_CAMPAIGN_ID:
            logger.warning("Patreon credentials not configured, skipping sync")
            return

        logger.info("Starting daily Patreon sync")

        try:
            await self._sync_all_pledges()
            logger.info("Patreon sync completed")
        except Exception as e:
            logger.error(f"Patreon sync failed: {e}", exc_info=True)

    @_sync_task.before_loop
    async def _before_sync(self):
        """Wait for bot to be ready."""
        # Delay first run to avoid startup conflicts
        import asyncio
        await asyncio.sleep(60)  # Wait 1 minute after startup

    async def _sync_all_pledges(self):
        """Fetch all campaign members and update subscriptions."""
        url = f"https://www.patreon.com/api/oauth2/v2/campaigns/{PATREON_CAMPAIGN_ID}/members"
        params = {
            "include": "user",
            "fields[member]": "patron_status,currently_entitled_amount_cents",
            "fields[user]": "social_connections"
        }
        headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}

        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        logger.error(f"Patreon API error: {resp.status}")
                        return

                    data = await resp.json()

                # Process members
                for member in data.get("data", []):
                    await self._process_member(member, data.get("included", []))

                # Pagination
                url = data.get("links", {}).get("next")
                params = {}  # Next URL includes params

    async def _process_member(self, member: dict, included: list):
        """Process a single campaign member."""
        attrs = member.get("attributes", {})

        # Skip non-active patrons
        if attrs.get("patron_status") != "active_patron":
            return

        # Find linked Discord account
        user_id = member.get("relationships", {}).get("user", {}).get("data", {}).get("id")
        if not user_id:
            return

        discord_id = None
        for item in included:
            if item.get("id") == user_id and item.get("type") == "user":
                social = item.get("attributes", {}).get("social_connections", {})
                discord_data = social.get("discord")
                if discord_data:
                    discord_id = discord_data.get("user_id")
                break

        if not discord_id:
            return

        # Update subscription
        pledge_cents = attrs.get("currently_entitled_amount_cents", 0)
        tier = get_tier_for_pledge(pledge_cents)

        await self.rl.db.execute(
            """
            INSERT INTO user_subscriptions (user_id, tier, patreon_pledge_cents, last_verified_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                tier = $2,
                patreon_pledge_cents = $3,
                subscription_active = TRUE,
                last_verified_at = NOW(),
                updated_at = NOW()
            """,
            int(discord_id),
            tier,
            pledge_cents
        )
```

---

## Phase 6: Admin Commands

### File: `src/commands/admin_commands.py` (add to existing or create)

```python
"""
Admin-only commands for managing subscriptions and usage.
"""

import os
import discord
from discord import app_commands
from discord.ext import commands

from ratelimit import RateLimitManager, TIER_LIMITS

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


class AdminCommands(commands.Cog):
    """Admin commands for subscription management."""

    admin_group = app_commands.Group(
        name="admin",
        description="Admin commands (owner only)"
    )

    def __init__(self, bot: commands.Bot, rate_limit_manager: RateLimitManager):
        self.bot = bot
        self.rl = rate_limit_manager

    @admin_group.command(name="usage")
    @owner_only()
    @app_commands.describe(user="The user to check")
    async def usage(self, interaction: discord.Interaction, user: discord.User):
        """View a user's current usage and tier."""
        await interaction.response.defer(ephemeral=True)

        usage = await self.rl.get_usage(user.id)

        embed = discord.Embed(
            title=f"Usage: {user.display_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Tier", value=usage.tier, inline=True)
        embed.add_field(name="Messages", value=f"{usage.message_count}/{usage.message_limit or '∞'}", inline=True)
        embed.add_field(name="Images", value=f"{usage.image_count}/{usage.image_limit or '∞'}", inline=True)
        embed.add_field(name="Limited?", value="Yes" if usage.is_limited else "No", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin_group.command(name="set-tier")
    @owner_only()
    @app_commands.describe(
        user="The user to modify",
        tier="The tier to set (or 'none' to clear override)"
    )
    @app_commands.choices(tier=[
        app_commands.Choice(name="Free", value="free"),
        app_commands.Choice(name="Supporter", value="supporter"),
        app_commands.Choice(name="Premium", value="premium"),
        app_commands.Choice(name="Unlimited", value="unlimited"),
        app_commands.Choice(name="Clear Override", value="none"),
    ])
    async def set_tier(self, interaction: discord.Interaction, user: discord.User, tier: str):
        """Manually set a user's tier (overrides Patreon)."""
        await interaction.response.defer(ephemeral=True)

        tier_value = None if tier == "none" else tier
        success = await self.rl.set_tier_override(user.id, tier_value)

        if success:
            if tier_value:
                await interaction.followup.send(
                    f"Set {user.display_name}'s tier override to **{tier}**.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"Cleared tier override for {user.display_name}.",
                    ephemeral=True
                )
        else:
            await interaction.followup.send("Failed to update tier.", ephemeral=True)

    @admin_group.command(name="reset-usage")
    @owner_only()
    @app_commands.describe(user="The user whose usage to reset")
    async def reset_usage(self, interaction: discord.Interaction, user: discord.User):
        """Reset a user's usage counters for the current month."""
        await interaction.response.defer(ephemeral=True)

        await self.rl.reset_usage(user.id)

        await interaction.followup.send(
            f"Reset usage counters for {user.display_name}.",
            ephemeral=True
        )

    @admin_group.command(name="stats")
    @owner_only()
    async def stats(self, interaction: discord.Interaction):
        """View aggregate usage statistics."""
        await interaction.response.defer(ephemeral=True)

        stats = await self.rl.get_all_usage_stats()

        embed = discord.Embed(
            title=f"Usage Stats ({stats['month']})",
            color=discord.Color.green()
        )
        embed.add_field(name="Active Users", value=stats["active_users"], inline=True)
        embed.add_field(name="Total Messages", value=f"{stats['total_messages']:,}", inline=True)
        embed.add_field(name="Total Images", value=f"{stats['total_images']:,}", inline=True)
        embed.add_field(name="Avg Messages/User", value=f"{stats['avg_messages']:.1f}", inline=True)
        embed.add_field(name="Avg Images/User", value=f"{stats['avg_images']:.1f}", inline=True)

        tier_str = "\n".join(f"{k}: {v}" for k, v in stats["tier_distribution"].items())
        embed.add_field(name="Tier Distribution", value=tier_str or "No data", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
```

---

## Package Structure

```
src/
├── ratelimit/
│   ├── __init__.py
│   ├── tiers.py           # Tier configuration
│   └── manager.py         # Usage tracking & limits
├── patreon/
│   ├── __init__.py
│   ├── webhook.py         # Webhook handler logic
│   ├── webhook_server.py  # Standalone server for App Platform
│   └── sync.py            # Daily sync task
├── commands/
│   ├── subscribe_commands.py  # User commands
│   └── admin_commands.py      # Admin commands (may extend existing)
├── discord_bot.py         # (modify: add rate limit checks)
└── ...

migrations/
├── ...existing...
├── 011_create_user_subscriptions.sql
└── 012_create_monthly_usage.sql
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OWNER_ID` | Yes | Discord user ID exempt from limits |
| `PATREON_URL` | Yes | Your Patreon page URL |
| `PATREON_WEBHOOK_SECRET` | For webhooks | Webhook signing secret from Patreon |
| `PATREON_CREATOR_ACCESS_TOKEN` | For daily sync | Creator access token |
| `PATREON_CAMPAIGN_ID` | For daily sync | Your campaign ID |
| `WEBHOOK_PORT` | No | Port for webhook server (default: 8080) |

---

## Implementation Checklist

### Database
- [ ] Create migration `011_create_user_subscriptions.sql`
- [ ] Create migration `012_create_monthly_usage.sql`
- [ ] Run migrations on production

### Core Module
- [ ] Create `src/ratelimit/__init__.py`
- [ ] Create `src/ratelimit/tiers.py`
- [ ] Create `src/ratelimit/manager.py`
- [ ] Add tests for tier calculation

### Integration
- [ ] Modify `discord_bot.py` message handler for rate limit check
- [ ] Add 80% and 95% warning system
- [ ] Modify image processing for rate limit check
- [ ] Add limit-reached embed message
- [ ] Ensure `/memories` commands bypass limits

### Slash Commands
- [ ] Create `src/commands/subscribe_commands.py`
- [ ] Register in `discord_bot.py` setup
- [ ] Test all commands

### Patreon Integration
- [ ] Create `src/patreon/webhook.py`
- [ ] Create `src/patreon/sync.py`
- [ ] Set up Patreon webhook in dashboard
- [ ] Test webhook delivery
- [ ] Test daily sync

### Admin Commands
- [ ] Add admin commands (usage, set-tier, reset-usage, stats)
- [ ] Test owner-only access control

### Documentation
- [ ] Update CLAUDE.md with rate limit section
- [ ] Document environment variables
- [ ] Add Patreon setup instructions

---

## UX Examples

### Limit Reached Message

```
┌─────────────────────────────────────────────────────┐
│  Monthly Limit Reached                              │
├─────────────────────────────────────────────────────┤
│  You've used all 50 of your 50 monthly messages.    │
│  Your limit resets on February 1, 2026.             │
│                                                     │
│  Want more?                                         │
│  Support slashAI on Patreon to unlock higher limits!│
│                                                     │
│  Supporter ($5/mo): 500 messages, 100 images        │
│  Premium ($10/mo): 2,000 messages, 500 images       │
│  Unlimited ($20/mo): No limits                      │
│                                                     │
│  [Subscribe on Patreon]                             │
├─────────────────────────────────────────────────────┤
│  Current Usage          │  Your Tier                │
│  Messages: 50/50        │  Free tier                │
│  Images: 3/10           │                           │
└─────────────────────────────────────────────────────┘
```

### /subscribe status

```
┌─────────────────────────────────────────────────────┐
│  Your slashAI Subscription                          │
├─────────────────────────────────────────────────────┤
│  Tier: Supporter                                    │
│  Supporter ($5/mo)                                  │
│                                                     │
│  Messages: 127/500                                  │
│  ██████░░░░░░░░░░░░░░ (373 remaining)              │
│                                                     │
│  Images: 23/100                                     │
│  █████░░░░░░░░░░░░░░░ (77 remaining)               │
├─────────────────────────────────────────────────────┤
│  Resets February 1, 2026                           │
└─────────────────────────────────────────────────────┘
```

---

### 80% Warning (subtle)

```
┌─────────────────────────────────────────────────────┐
│  [Normal bot response here...]                      │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  You've used 40 of 50 messages this month.          │
│  Upgrade at patreon.com/slashAI for more.           │
└─────────────────────────────────────────────────────┘
```

### 95% Warning (urgent)

```
┌─────────────────────────────────────────────────────┐
│  [Normal bot response here...]                      │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  ⚠️ Only 2 messages remaining this month!           │
│  Upgrade at patreon.com/slashAI to continue         │
│  chatting.                                          │
└─────────────────────────────────────────────────────┘
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-09 | Initial plan |
| 1.1 | 2026-01-09 | Added warning system (80%/95%), webhook hosting decision, image scope clarification |
