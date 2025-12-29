"""
Build Clusterer - Groups related image observations into build/project clusters.

Clustering algorithm uses:
1. Semantic similarity (embedding distance to cluster centroids)
2. Temporal proximity (recent activity in cluster)
3. Privacy compatibility (same privacy scope only)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import asyncpg
import numpy as np


@dataclass
class ClusterConfig:
    """Configuration for build clustering."""

    # Similarity thresholds
    assignment_threshold: float = 0.72  # Min similarity to assign to existing cluster
    new_cluster_threshold: float = 0.65  # Below this, definitely new cluster

    # Temporal settings
    active_window_days: int = 30  # Cluster considered "active" if updated within
    stale_window_days: int = 90  # Mark as "abandoned" if no activity

    # Cluster limits
    max_clusters_per_user: int = 50  # Prevent unbounded growth
    min_observations_for_cluster: int = 1  # Single image can start a cluster


@dataclass
class ClusterAssignment:
    """Result of cluster assignment."""

    cluster_id: int
    is_new_cluster: bool
    similarity_score: Optional[float]
    cluster_name: str


class BuildClusterer:
    """Groups image observations into build clusters."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        config: Optional[ClusterConfig] = None,
    ):
        self.db = db_pool
        self.config = config or ClusterConfig()

    async def assign_to_cluster(
        self,
        user_id: int,
        observation_id: int,
        embedding: list[float],
        observation_type: str,
        tags: list[str],
        privacy_level: str,
        guild_id: Optional[int],
    ) -> ClusterAssignment:
        """
        Assign an observation to an existing or new cluster.

        Args:
            user_id: Discord user ID
            observation_id: ID of the image_observation record
            embedding: Voyage multimodal embedding
            observation_type: Type classification from analysis
            tags: Tags from analysis
            privacy_level: Privacy level of the observation
            guild_id: Guild ID (None for DMs)

        Returns:
            ClusterAssignment with cluster info
        """
        # Find candidate clusters (active, same privacy scope)
        candidates = await self._find_candidate_clusters(
            user_id, privacy_level, guild_id
        )

        if not candidates:
            # No existing clusters - create new
            return await self._create_cluster(
                user_id,
                observation_id,
                embedding,
                observation_type,
                tags,
                privacy_level,
                guild_id,
            )

        # Find best matching cluster
        best_match = self._find_best_match(embedding, candidates)

        if best_match and best_match["similarity"] >= self.config.assignment_threshold:
            # Assign to existing cluster
            await self._add_to_cluster(best_match["id"], observation_id, embedding)
            return ClusterAssignment(
                cluster_id=best_match["id"],
                is_new_cluster=False,
                similarity_score=best_match["similarity"],
                cluster_name=best_match["auto_name"],
            )
        else:
            # Create new cluster
            return await self._create_cluster(
                user_id,
                observation_id,
                embedding,
                observation_type,
                tags,
                privacy_level,
                guild_id,
            )

    async def _find_candidate_clusters(
        self,
        user_id: int,
        privacy_level: str,
        guild_id: Optional[int],
    ) -> list[dict]:
        """Find clusters that could potentially match (active, compatible privacy)."""
        cutoff = datetime.utcnow() - timedelta(days=self.config.active_window_days)

        # Build privacy filter based on level
        if privacy_level == "dm":
            sql = """
                SELECT id, auto_name, centroid_embedding, observation_count, last_observation_at
                FROM build_clusters
                WHERE user_id = $1
                  AND status = 'active'
                  AND last_observation_at > $2
                  AND privacy_level = 'dm'
                ORDER BY last_observation_at DESC
                LIMIT 20
            """
            rows = await self.db.fetch(sql, user_id, cutoff)

        elif privacy_level == "channel_restricted":
            sql = """
                SELECT id, auto_name, centroid_embedding, observation_count, last_observation_at
                FROM build_clusters
                WHERE user_id = $1
                  AND status = 'active'
                  AND last_observation_at > $2
                  AND privacy_level IN ('dm', 'channel_restricted')
                ORDER BY last_observation_at DESC
                LIMIT 20
            """
            rows = await self.db.fetch(sql, user_id, cutoff)

        else:  # guild_public or global
            sql = """
                SELECT id, auto_name, centroid_embedding, observation_count, last_observation_at
                FROM build_clusters
                WHERE user_id = $1
                  AND status = 'active'
                  AND last_observation_at > $2
                  AND privacy_level IN ('guild_public', 'global')
                  AND origin_guild_id = $3
                ORDER BY last_observation_at DESC
                LIMIT 20
            """
            rows = await self.db.fetch(sql, user_id, cutoff, guild_id)

        return [dict(r) for r in rows]

    def _find_best_match(
        self,
        embedding: list[float],
        candidates: list[dict],
    ) -> Optional[dict]:
        """Find the best matching cluster by cosine similarity."""
        if not candidates:
            return None

        embedding_array = np.array(embedding, dtype=np.float32)
        best_match = None
        best_similarity = -1.0

        for candidate in candidates:
            if candidate["centroid_embedding"] is None:
                continue

            # Parse centroid from database (comes as string or list depending on driver)
            raw_centroid = candidate["centroid_embedding"]
            if isinstance(raw_centroid, str):
                # pgvector returns string like '[0.1,0.2,...]' - parse it
                centroid = np.array([float(x) for x in raw_centroid.strip('[]').split(',')], dtype=np.float32)
            else:
                centroid = np.array(raw_centroid, dtype=np.float32)

            # Cosine similarity
            dot_product = np.dot(embedding_array, centroid)
            norm_product = np.linalg.norm(embedding_array) * np.linalg.norm(centroid)

            if norm_product == 0:
                continue

            similarity = float(dot_product / norm_product)

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = {**candidate, "similarity": similarity}

        return best_match

    async def _create_cluster(
        self,
        user_id: int,
        observation_id: int,
        embedding: list[float],
        observation_type: str,
        tags: list[str],
        privacy_level: str,
        guild_id: Optional[int],
    ) -> ClusterAssignment:
        """Create a new cluster for this observation."""
        # Generate auto-name from tags and type
        auto_name = self._generate_cluster_name(observation_type, tags)

        # Convert embedding to pgvector string format
        embedding_str = '[' + ','.join(str(x) for x in embedding) + ']'
        
        # Insert cluster
        row = await self.db.fetchrow(
            """
            INSERT INTO build_clusters (
                user_id, auto_name, centroid_embedding, build_type, style_tags,
                observation_count, privacy_level, origin_guild_id,
                first_observation_at, last_observation_at
            ) VALUES ($1, $2, $3, $4, $5, 1, $6, $7, NOW(), NOW())
            RETURNING id
            """,
            user_id,
            auto_name,
            embedding_str,
            observation_type,
            tags,
            privacy_level,
            guild_id,
        )

        cluster_id = row["id"]

        # Link observation to cluster
        await self.db.execute(
            "UPDATE image_observations SET build_cluster_id = $1 WHERE id = $2",
            cluster_id,
            observation_id,
        )

        return ClusterAssignment(
            cluster_id=cluster_id,
            is_new_cluster=True,
            similarity_score=None,
            cluster_name=auto_name,
        )

    async def _add_to_cluster(
        self,
        cluster_id: int,
        observation_id: int,
        embedding: list[float],
    ) -> None:
        """Add observation to existing cluster and update centroid."""
        # Link observation
        await self.db.execute(
            "UPDATE image_observations SET build_cluster_id = $1 WHERE id = $2",
            cluster_id,
            observation_id,
        )

        # Convert embedding to pgvector string format
        embedding_str = '[' + ','.join(str(x) for x in embedding) + ']'
        
        # Update cluster: increment count, update timestamps, recalculate centroid
        # Using rolling average for efficiency
        await self.db.execute(
            """
            UPDATE build_clusters SET
                observation_count = observation_count + 1,
                last_observation_at = NOW(),
                updated_at = NOW(),
                centroid_embedding = (
                    (centroid_embedding * observation_count + $2::vector) / (observation_count + 1)
                )
            WHERE id = $1
            """,
            cluster_id,
            embedding_str,
        )

    def _generate_cluster_name(
        self,
        observation_type: str,
        tags: list[str],
    ) -> str:
        """Generate a human-readable cluster name."""
        # Priority tags for naming
        priority_tags = [
            "castle",
            "house",
            "tower",
            "farm",
            "bridge",
            "cathedral",
            "village",
            "ship",
            "statue",
            "wall",
            "gate",
            "garden",
            "mansion",
            "temple",
            "fortress",
            "lighthouse",
        ]

        for tag in priority_tags:
            if tag in tags:
                return f"{tag.title()} Build"

        # Fall back to observation type
        type_names = {
            "build_progress": "Construction Project",
            "landscape": "Landscape",
            "redstone": "Redstone Contraption",
            "farm": "Farm Build",
            "other": "Project",
            "unknown": "Build Project",
        }

        return type_names.get(observation_type, "Build Project")

    async def update_cluster_status(self, user_id: int) -> int:
        """
        Update cluster statuses based on activity.

        Returns number of clusters updated.
        """
        stale_cutoff = datetime.utcnow() - timedelta(days=self.config.stale_window_days)

        result = await self.db.execute(
            """
            UPDATE build_clusters
            SET status = 'abandoned', updated_at = NOW()
            WHERE user_id = $1
              AND status = 'active'
              AND last_observation_at < $2
            """,
            user_id,
            stale_cutoff,
        )

        # Extract count from result (e.g., "UPDATE 3")
        if result:
            parts = result.split()
            if len(parts) == 2:
                return int(parts[1])
        return 0

    async def get_user_clusters(
        self,
        user_id: int,
        privacy_level: str,
        guild_id: Optional[int],
        status: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Get clusters with privacy filtering - cross-user for guild_public."""
        # Build privacy filter - cross-user for guild_public
        if privacy_level == "dm":
            # DM: only user's own clusters
            privacy_filter = "user_id = $1"
            params = [user_id, limit]
        elif privacy_level == "channel_restricted":
            # Restricted: user's global + any user's guild_public + user's channel_restricted
            privacy_filter = """
                (user_id = $1 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $3)
                OR (user_id = $1 AND privacy_level = 'channel_restricted' AND origin_guild_id = $3)
            """
            params = [user_id, limit, guild_id]
        else:
            # Guild public: user's global + any user's guild_public from same guild
            privacy_filter = """
                (user_id = $1 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $3)
            """
            params = [user_id, limit, guild_id]

        status_filter = "AND status = $4" if status else ""
        if status:
            params.append(status)

        sql = f"""
            SELECT id, auto_name, user_name, description, build_type,
                   observation_count, status, privacy_level,
                   first_observation_at, last_observation_at
            FROM build_clusters
            WHERE ({privacy_filter}) {status_filter}
            ORDER BY last_observation_at DESC
            LIMIT $2
        """

        rows = await self.db.fetch(sql, *params)
        return [dict(r) for r in rows]
