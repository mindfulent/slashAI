"""
Build Narrator - Generates progression narratives for build clusters.

Creates story-like summaries of a user's build journey based on
chronological image observations.
"""

import json
from dataclasses import dataclass
from typing import Optional

import asyncpg
from anthropic import AsyncAnthropic


@dataclass
class BuildNarrative:
    """Generated narrative for a build cluster."""

    cluster_id: int
    cluster_name: str
    summary: str  # One-paragraph summary
    timeline: list[dict]  # Chronological progression
    milestones: list[str]  # Key achievements
    current_status: str  # Where they are now
    suggested_next_steps: list[str]  # Optional suggestions


class BuildNarrator:
    """Generates progression narratives for build clusters."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        anthropic_client: AsyncAnthropic,
    ):
        self.db = db_pool
        self.anthropic = anthropic_client

    async def generate_narrative(
        self,
        cluster_id: int,
        max_observations: int = 10,
    ) -> Optional[BuildNarrative]:
        """
        Generate a narrative story for a build cluster.

        Args:
            cluster_id: ID of the build cluster
            max_observations: Maximum observations to include

        Returns:
            BuildNarrative or None if cluster not found
        """
        # Fetch cluster info
        cluster = await self.db.fetchrow(
            "SELECT * FROM build_clusters WHERE id = $1", cluster_id
        )

        if not cluster:
            return None

        # Fetch observations in chronological order
        observations = await self.db.fetch(
            """
            SELECT id, description, summary, tags, detected_elements,
                   captured_at, accompanying_text
            FROM image_observations
            WHERE build_cluster_id = $1
            ORDER BY captured_at ASC
            LIMIT $2
            """,
            cluster_id,
            max_observations,
        )

        if not observations:
            return None

        # Build timeline
        timeline = [
            {
                "date": obs["captured_at"].isoformat(),
                "description": obs["description"],
                "stage": (obs["detected_elements"] or {}).get(
                    "completion_stage", "unknown"
                ),
            }
            for obs in observations
        ]

        # Generate narrative via Claude
        narrative_text = await self._generate_narrative_text(
            dict(cluster), [dict(obs) for obs in observations]
        )

        return BuildNarrative(
            cluster_id=cluster_id,
            cluster_name=cluster["user_name"] or cluster["auto_name"],
            summary=narrative_text.get("summary", "Build in progress."),
            timeline=timeline,
            milestones=narrative_text.get("milestones", []),
            current_status=narrative_text.get("current_status", "In progress"),
            suggested_next_steps=narrative_text.get("suggestions", []),
        )

    async def _generate_narrative_text(
        self,
        cluster: dict,
        observations: list[dict],
    ) -> dict:
        """Use Claude to generate narrative text from observations."""
        # Format observations for prompt
        obs_text = "\n\n".join(
            [
                f"**{obs['captured_at'].strftime('%B %d, %Y')}**\n{obs['description']}"
                for obs in observations
            ]
        )

        first_date = (
            cluster["first_observation_at"].strftime("%B %d, %Y")
            if cluster.get("first_observation_at")
            else "Unknown"
        )
        last_date = (
            cluster["last_observation_at"].strftime("%B %d, %Y")
            if cluster.get("last_observation_at")
            else "Unknown"
        )

        prompt = f"""
You are helping tell the story of a Minecraft build's progression.

## Build Info
- Name: {cluster['auto_name']}
- Type: {cluster.get('build_type', 'unknown')}
- Started: {first_date}
- Last Update: {last_date}
- Total Snapshots: {cluster.get('observation_count', len(observations))}

## Chronological Observations
{obs_text}

## Task
Generate a narrative summary of this build's progression. Return JSON:

```json
{{
  "summary": "2-3 sentence narrative summary of the build's journey",
  "milestones": ["Milestone 1", "Milestone 2"],
  "current_status": "Where the build currently stands",
  "suggestions": ["Optional suggestion 1"]
}}
```

Be specific about what changed between observations. Celebrate progress!
"""

        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        return self._parse_json_response(response.content[0].text)

    async def get_brief_context(
        self,
        user_id: int,
        privacy_level: str,
        guild_id: Optional[int],
        max_clusters: int = 3,
    ) -> str:
        """
        Get brief build context for injection into chat responses.

        Returns formatted markdown string suitable for system prompt injection.
        """
        # Build privacy filter - cross-user for guild_public
        if privacy_level == "dm":
            # DM: only user's own clusters
            privacy_filter = "user_id = $1"
            params = [user_id, max_clusters]
        elif privacy_level == "channel_restricted":
            # Restricted: user's global + any user's guild_public + user's channel_restricted
            privacy_filter = """
                (user_id = $1 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $3)
                OR (user_id = $1 AND privacy_level = 'channel_restricted' AND origin_guild_id = $3)
            """
            params = [user_id, max_clusters, guild_id]
        else:
            # Guild public: user's global + any user's guild_public from same guild
            privacy_filter = """
                (user_id = $1 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $3)
            """
            params = [user_id, max_clusters, guild_id]

        sql = f"""
            SELECT id, auto_name, user_name, description, observation_count,
                   first_observation_at, last_observation_at, status
            FROM build_clusters
            WHERE {privacy_filter}
            ORDER BY last_observation_at DESC
            LIMIT $2
        """

        clusters = await self.db.fetch(sql, *params)

        if not clusters:
            return ""

        lines = ["## User's Recent Builds"]

        for c in clusters:
            name = c["user_name"] or c["auto_name"]
            duration = ""

            if c["first_observation_at"] and c["last_observation_at"]:
                days = (c["last_observation_at"] - c["first_observation_at"]).days
                if days > 0:
                    duration = f" ({days} days)"

            status_indicator = ""
            if c["status"] == "completed":
                status_indicator = " [Complete]"
            elif c["status"] == "abandoned":
                status_indicator = " [Inactive]"

            lines.append(
                f"- **{name}**: {c['observation_count']} snapshots{duration}{status_indicator}"
            )

            if c["description"]:
                desc = c["description"][:100]
                if len(c["description"]) > 100:
                    desc += "..."
                lines.append(f"  {desc}")

        return "\n".join(lines)

    async def get_latest_observation_context(
        self,
        user_id: int,
        privacy_level: str,
        guild_id: Optional[int],
    ) -> Optional[dict]:
        """
        Get the most recent image observation for context.

        Returns dict with observation details or None.
        """
        # Build privacy filter - cross-user for guild_public
        if privacy_level == "dm":
            # DM: only user's own observations
            privacy_filter = "io.user_id = $1"
            params = [user_id]
        elif privacy_level == "channel_restricted":
            # Restricted: user's global + any user's guild_public + user's channel_restricted
            privacy_filter = """
                (io.user_id = $1 AND io.privacy_level = 'global')
                OR (io.privacy_level = 'guild_public' AND io.guild_id = $2)
                OR (io.user_id = $1 AND io.privacy_level = 'channel_restricted' AND io.guild_id = $2)
            """
            params = [user_id, guild_id]
        else:
            # Guild public: user's global + any user's guild_public from same guild
            privacy_filter = """
                (io.user_id = $1 AND io.privacy_level = 'global')
                OR (io.privacy_level = 'guild_public' AND io.guild_id = $2)
            """
            params = [user_id, guild_id]

        sql = f"""
            SELECT io.*, bc.auto_name as cluster_name
            FROM image_observations io
            LEFT JOIN build_clusters bc ON io.build_cluster_id = bc.id
            WHERE {privacy_filter}
            ORDER BY io.captured_at DESC
            LIMIT 1
        """

        row = await self.db.fetchrow(sql, *params)
        return dict(row) if row else None

    def _parse_json_response(self, response_text: str) -> dict:
        """Extract JSON from Claude response."""
        text = response_text.strip()

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
                if text.startswith(("\n", "json")):
                    lines = text.split("\n", 1)
                    text = lines[1] if len(lines) > 1 else ""

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {}
