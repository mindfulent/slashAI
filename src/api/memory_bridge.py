# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Memory Bridge API — HTTP endpoints for cross-platform memory access.
Enables SoulCraft (and other external systems) to read/write memories
stored in slashAI's PostgreSQL database.
"""

import json
import logging
import os
from typing import Optional

import voyageai
from aiohttp import web

logger = logging.getLogger(__name__)


class MemoryBridgeAPI:
    """HTTP handlers for the memory bridge API."""

    def __init__(self, memory_manager, db_pool):
        self.memory = memory_manager
        self.db = db_pool
        self.voyage = voyageai.AsyncClient()

    def register_routes(self, app: web.Application):
        """Register memory bridge routes on an aiohttp app."""
        app.router.add_post("/api/memory/store", self.handle_store)
        app.router.add_post("/api/memory/retrieve", self.handle_retrieve)
        app.router.add_get("/api/memory/health", self.handle_health)
        logger.info("Memory bridge API routes registered")

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check for the memory bridge."""
        return web.json_response({
            "status": "ok",
            "memory_enabled": self.memory is not None,
        })

    async def handle_store(self, request: web.Request) -> web.Response:
        """Store a memory from an external platform.

        POST /api/memory/store
        Authorization: Bearer <SLASHAI_API_KEY>
        Body: {
            "agent_id": "lena",
            "user_identifier": "Steve",       // Minecraft username
            "summary": "Built iron farm...",
            "raw_context": "conversation...",
            "memory_type": "episodic",         // episodic | semantic
            "source_platform": "minecraft",
            "confidence": 0.9
        }
        """
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        agent_id = data.get("agent_id")
        user_identifier = data.get("user_identifier")
        summary = data.get("summary")
        raw_context = data.get("raw_context", "")
        memory_type = data.get("memory_type", "episodic")
        source_platform = data.get("source_platform", "minecraft")
        confidence = data.get("confidence", 0.8)

        if not summary:
            return web.json_response({"error": "Missing 'summary'"}, status=400)

        try:
            # Generate embedding from summary
            result = await self.voyage.embed(
                [summary], model="voyage-3.5-lite", input_type="document"
            )
            embedding = result.embeddings[0]
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            # Resolve user_identifier to Discord user_id via account linking
            user_id = await self._resolve_user_id(user_identifier)

            # Store the memory
            row = await self.db.fetchrow(
                """
                INSERT INTO memories (
                    user_id, topic_summary, raw_dialogue, embedding,
                    memory_type, confidence, privacy_level,
                    agent_id, source_platform, user_identifier
                ) VALUES ($1, $2, $3, $4::vector, $5, $6, 'global', $7, $8, $9)
                ON CONFLICT (user_id, md5(topic_summary)) DO UPDATE SET
                    raw_dialogue = EXCLUDED.raw_dialogue,
                    embedding = EXCLUDED.embedding,
                    confidence = GREATEST(memories.confidence, EXCLUDED.confidence),
                    updated_at = NOW(),
                    source_count = memories.source_count + 1
                RETURNING id, (xmax = 0) AS is_insert
                """,
                user_id,  # may be 0 if not linked
                summary,
                raw_context,
                embedding_str,
                memory_type,
                confidence,
                agent_id,
                source_platform,
                user_identifier,
            )

            memory_id = row["id"]
            action = "add" if row["is_insert"] else "merge"
            logger.info(
                f"Memory bridge: {action} memory {memory_id} "
                f"(agent={agent_id}, platform={source_platform})"
            )

            return web.json_response({
                "memory_id": memory_id,
                "action": action,
            })

        except Exception as e:
            logger.error(f"Memory bridge store error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_retrieve(self, request: web.Request) -> web.Response:
        """Retrieve relevant memories for an agent.

        POST /api/memory/retrieve
        Authorization: Bearer <SLASHAI_API_KEY>
        Body: {
            "agent_id": "lena",
            "query": "iron farm building",
            "user_identifier": "Steve",
            "top_k": 5
        }
        """
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        agent_id = data.get("agent_id")
        query = data.get("query")
        user_identifier = data.get("user_identifier")
        top_k = min(data.get("top_k", 5), 20)

        if not query:
            return web.json_response({"error": "Missing 'query'"}, status=400)

        try:
            # Generate query embedding
            result = await self.voyage.embed(
                [query], model="voyage-3.5-lite", input_type="query"
            )
            embedding = result.embeddings[0]
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            # Resolve user_identifier
            user_id = await self._resolve_user_id(user_identifier)

            # Query memories scoped by agent_id
            rows = await self.db.fetch(
                """
                SELECT id, topic_summary, memory_type, source_platform,
                       confidence, created_at,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM memories
                WHERE (user_id = $2 OR user_id = 0)
                  AND (agent_id IS NULL OR agent_id = $3)
                  AND privacy_level IN ('global', 'guild_public')
                ORDER BY embedding <=> $1::vector
                LIMIT $4
                """,
                embedding_str,
                user_id,
                agent_id,
                top_k,
            )

            memories = [
                {
                    "id": row["id"],
                    "summary": row["topic_summary"],
                    "memory_type": row["memory_type"],
                    "source_platform": row["source_platform"] or "discord",
                    "confidence": float(row["confidence"]),
                    "similarity": float(row["similarity"]),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in rows
            ]

            return web.json_response({"memories": memories})

        except Exception as e:
            logger.error(f"Memory bridge retrieve error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _resolve_user_id(self, user_identifier: Optional[str]) -> int:
        """Resolve a Minecraft username to a Discord user ID via account linking."""
        if not user_identifier:
            return 0

        try:
            row = await self.db.fetchrow(
                """
                SELECT discord_user_id FROM user_settings
                WHERE minecraft_username ILIKE $1
                LIMIT 1
                """,
                user_identifier,
            )
            if row:
                return row["discord_user_id"]
        except Exception:
            pass  # Table may not exist or no linking data

        return 0

    def _check_auth(self, request: web.Request) -> bool:
        """Check Bearer token authorization."""
        expected_key = os.getenv("SLASHAI_API_KEY")
        if not expected_key:
            return True  # No key configured = open access (dev mode)
        auth_header = request.headers.get("Authorization", "")
        return auth_header == f"Bearer {expected_key}"
