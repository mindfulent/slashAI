# slashAI - Discord Bot and MCP Server
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Events API Client for theblockacademy Events API

Creates events on The Block Academy calendar via the bot endpoint.
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CreatedEvent:
    """Event created via the API"""

    id: str
    title: str
    url: str
    event_date: str
    category: str


class EventsAPIClient:
    """Client for the theblockacademy Events API (bot endpoint)"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url or os.getenv(
            "EVENTS_API_URL", "https://theblock.academy/api/events"
        )
        self.api_key = api_key or os.getenv("EVENTS_API_KEY")

        if not self.api_key:
            logger.warning(
                "EVENTS_API_KEY not set - event creation will fail authentication"
            )

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the HTTP client"""
        await self._client.aclose()

    async def create_event(
        self,
        discord_user_id: str,
        title: str,
        event_date: str,
        category: str,
        description: Optional[str] = None,
        duration_minutes: int = 60,
        location: Optional[str] = None,
        timezone: str = "America/Los_Angeles",
        max_capacity: Optional[int] = None,
        is_recurring: bool = False,
        recurrence_pattern: Optional[str] = None,
    ) -> Optional[CreatedEvent]:
        """
        Create an event on The Block Academy calendar.

        Args:
            discord_user_id: Discord user ID of the event creator
            title: Event title (max 100 chars)
            event_date: Date/time in YYYY-MM-DDTHH:MM format
            category: One of 'class', 'performance', 'lets-play', 'lets-build'
            description: Event description (max 2000 chars)
            duration_minutes: Duration in minutes (15-480, default 60)
            location: Location string (max 255 chars)
            timezone: IANA timezone (default: America/Los_Angeles)
            max_capacity: Maximum attendees (optional)
            is_recurring: Whether the event repeats
            recurrence_pattern: One of 'weekly', 'biweekly', 'monthly'

        Returns:
            CreatedEvent on success, None on failure
        """
        payload = {
            "discord_user_id": discord_user_id,
            "title": title,
            "event_date": event_date,
            "category": category,
            "duration_minutes": duration_minutes,
            "timezone": timezone,
        }

        if description:
            payload["description"] = description
        if location:
            payload["location"] = location
        if max_capacity is not None:
            payload["max_capacity"] = max_capacity
        if is_recurring:
            payload["is_recurring"] = True
            if recurrence_pattern:
                payload["recurrence_pattern"] = recurrence_pattern

        try:
            response = await self._client.post("/bot", json=payload)
            response.raise_for_status()
            data = response.json()

            event = data.get("event", {})
            return CreatedEvent(
                id=event.get("id", ""),
                title=event.get("title", title),
                url=data.get("url", ""),
                event_date=event.get("event_date", event_date),
                category=event.get("category", category),
            )
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.json().get("error", "")
            except Exception:
                error_body = e.response.text[:200]
            logger.error(f"Failed to create event (HTTP {e.response.status_code}): {error_body}")
            return None
        except httpx.HTTPError as e:
            logger.error(f"Failed to create event: {e}")
            return None
