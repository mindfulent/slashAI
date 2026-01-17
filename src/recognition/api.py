# slashAI - Recognition API Client
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
API Client for theblockacademy Recognition API

Handles all communication with the Recognition API:
- Fetching pending submissions
- Sending analysis results via webhooks
- Player profile lookups
"""

import os
import hmac
import hashlib
import logging
import json
from typing import Optional, Any
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Submission:
    """Build submission from the Recognition API"""

    id: str
    player_uuid: str
    build_name: str
    description: Optional[str]
    screenshot_urls: list[str]
    coordinates: dict[str, Any]
    submission_type: str  # 'submission' or 'feedback'
    status: str


@dataclass
class Nomination:
    """Peer nomination from the Recognition API"""

    id: str
    nominator_uuid: str
    nominee_uuid: str
    category: str
    reason: str
    anonymous: bool
    status: str


@dataclass
class PlayerProfile:
    """Player recognition profile"""

    uuid: str
    minecraft_username: Optional[str]
    discord_id: Optional[str]
    recognized_builds: int
    total_submissions: int
    earned_titles: list[dict]
    active_title: Optional[dict]


class RecognitionAPIClient:
    """Client for the theblockacademy Recognition API"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        self.base_url = base_url or os.getenv(
            "RECOGNITION_API_URL", "https://theblock.academy/api/recognition"
        )
        self.api_key = api_key or os.getenv("RECOGNITION_API_KEY")
        self.webhook_secret = webhook_secret or os.getenv("SLASHAI_WEBHOOK_SECRET")

        if not self.api_key:
            logger.warning(
                "RECOGNITION_API_KEY not set - API calls will fail authentication"
            )

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the HTTP client"""
        await self._client.aclose()

    async def get_pending_submissions(self, limit: int = 10) -> list[Submission]:
        """Fetch pending submissions for analysis"""
        try:
            response = await self._client.get(
                "/pending",
                params={"limit": limit},
            )
            response.raise_for_status()
            data = response.json()

            return [
                Submission(
                    id=s["id"],
                    player_uuid=s["player_uuid"],
                    build_name=s["build_name"],
                    description=s.get("description"),
                    screenshot_urls=s["screenshot_urls"],
                    coordinates=s["coordinates"],
                    submission_type=s["submission_type"],
                    status=s["status"],
                )
                for s in data.get("data", [])
            ]
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch pending submissions: {e}")
            return []

    async def get_pending_nominations(self, limit: int = 10) -> list[Nomination]:
        """Fetch pending nominations for review"""
        try:
            response = await self._client.get(
                "/admin/nominations",
                params={"status": "pending", "limit": limit},
            )
            response.raise_for_status()
            data = response.json()

            return [
                Nomination(
                    id=n["id"],
                    nominator_uuid=n["nominator_uuid"],
                    nominee_uuid=n["nominee_uuid"],
                    category=n["category"],
                    reason=n["reason"],
                    anonymous=n["anonymous"],
                    status=n["status"],
                )
                for n in data.get("data", [])
            ]
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch pending nominations: {e}")
            return []

    async def get_player_profile(self, player_uuid: str) -> Optional[PlayerProfile]:
        """Fetch player's recognition profile"""
        try:
            response = await self._client.get(f"/player/{player_uuid}")
            response.raise_for_status()
            data = response.json().get("data", {})

            return PlayerProfile(
                uuid=data["uuid"],
                minecraft_username=data.get("minecraft_username"),
                discord_id=data.get("discord_id"),
                recognized_builds=data.get("stats", {}).get("recognized_builds", 0),
                total_submissions=data.get("stats", {}).get("total_submissions", 0),
                earned_titles=data.get("earned_titles", []),
                active_title=data.get("active_title"),
            )
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch player profile: {e}")
            return None

    async def submit_analysis_result(
        self,
        submission_id: str,
        recognized: bool,
        assessment: str,
        title_recommendation: Optional[str] = None,
        confidence: float = 0.8,
        share_publicly: bool = True,
        announcement_text: Optional[str] = None,
        screenshot_urls: Optional[list[str]] = None,
    ) -> bool:
        """Send analysis results back to the Recognition API via webhook"""
        payload = {
            "submission_id": submission_id,
            "recognized": recognized,
            "assessment": assessment,
            "title_recommendation": title_recommendation,
            "confidence": confidence,
            "share_publicly": share_publicly,
            "announcement_text": announcement_text,
            "screenshot_urls": screenshot_urls,
        }

        # Sign the webhook payload
        signature = self._sign_payload(payload)

        try:
            response = await self._client.post(
                "/webhook/slashai",
                json=payload,
                headers={"X-SlashAI-Signature": signature},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to submit analysis result: {e}")
            return False

    async def submit_nomination_review(
        self,
        nomination_id: str,
        decision: str,  # 'approved', 'flagged', 'rejected'
        notes: str,
        confidence: float = 0.8,
    ) -> bool:
        """Send nomination review result to the Recognition API"""
        payload = {
            "nomination_id": nomination_id,
            "decision": decision,
            "notes": notes,
            "confidence": confidence,
        }

        signature = self._sign_payload(payload)

        try:
            response = await self._client.post(
                "/webhook/slashai/nomination",
                json=payload,
                headers={"X-SlashAI-Signature": signature},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to submit nomination review: {e}")
            return False

    async def apply_admin_nomination_action(
        self,
        nomination_id: str,
        action: str,  # 'approve' or 'reject'
        reason: str,
        admin_id: str,
    ) -> bool:
        """Apply admin action to a nomination (approve/reject flagged nominations)"""
        payload = {
            "action": action,
            "reason": reason,
            "admin_id": admin_id,
        }

        try:
            response = await self._client.post(
                f"/admin/nominations/{nomination_id}/action",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to apply admin action to nomination: {e}")
            return False

    async def trigger_event_processing(self) -> int:
        """
        Trigger processing of ended events.
        Returns the number of events processed.
        """
        try:
            response = await self._client.post(
                "/events/process",
                json={},
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            return data.get("events_processed", 0)
        except httpx.HTTPError as e:
            logger.error(f"Failed to trigger event processing: {e}")
            return 0

    async def report_message_posted(
        self, submission_id: str, discord_message_id: str
    ) -> bool:
        """
        Report Discord message ID to Recognition API after posting to showcase.
        This allows the API to track which message to delete if the submission is deleted.
        """
        payload = {
            "submission_id": submission_id,
            "discord_message_id": discord_message_id,
        }
        signature = self._sign_payload(payload)

        try:
            response = await self._client.post(
                "/webhook/message-posted",
                json=payload,
                headers={"X-SlashAI-Signature": signature},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to report message posted: {e}")
            return False

    def _sign_payload(self, payload: dict) -> str:
        """Generate HMAC-SHA256 signature for webhook payload"""
        if not self.webhook_secret:
            logger.warning("SLASHAI_WEBHOOK_SECRET not set - webhooks will fail auth")
            return ""

        # Use compact JSON (no spaces) to match JavaScript's JSON.stringify()
        payload_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
        signature = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return signature
