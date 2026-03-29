# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Memory Bridge API (cross-platform memory access)."""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.memory_bridge import MemoryBridgeAPI


def _make_request(headers=None, body=None, method="POST", content_type="application/json"):
    """Create a mock aiohttp web.Request."""
    req = MagicMock()
    req.headers = headers or {}
    req.method = method

    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(side_effect=json.JSONDecodeError("", "", 0))

    return req


@pytest.fixture
def mock_db():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.fetchval = AsyncMock()
    return pool


@pytest.fixture
def mock_memory_manager():
    return MagicMock()


@pytest.fixture
def mock_voyage():
    """Mock voyageai.AsyncClient."""
    voyage = MagicMock()
    embed_result = MagicMock()
    embed_result.embeddings = [[0.1] * 1024]
    voyage.embed = AsyncMock(return_value=embed_result)
    return voyage


@pytest.fixture
def bridge(mock_memory_manager, mock_db, mock_voyage):
    with patch("api.memory_bridge.voyageai") as mock_voyageai:
        mock_voyageai.AsyncClient.return_value = mock_voyage
        api = MemoryBridgeAPI(mock_memory_manager, mock_db)
        api.voyage = mock_voyage
        return api


class TestCheckAuth:
    """Tests for _check_auth()."""

    def test_no_key_configured_allows_all(self, bridge):
        with patch.dict("os.environ", {}, clear=True):
            # Remove SLASHAI_API_KEY if present
            import os
            os.environ.pop("SLASHAI_API_KEY", None)
            req = _make_request(headers={})
            assert bridge._check_auth(req) is True

    def test_valid_bearer_token(self, bridge):
        with patch.dict("os.environ", {"SLASHAI_API_KEY": "secret123"}):
            req = _make_request(headers={"Authorization": "Bearer secret123"})
            assert bridge._check_auth(req) is True

    def test_invalid_bearer_token(self, bridge):
        with patch.dict("os.environ", {"SLASHAI_API_KEY": "secret123"}):
            req = _make_request(headers={"Authorization": "Bearer wrong"})
            assert bridge._check_auth(req) is False

    def test_missing_auth_header(self, bridge):
        with patch.dict("os.environ", {"SLASHAI_API_KEY": "secret123"}):
            req = _make_request(headers={})
            assert bridge._check_auth(req) is False


class TestHandleHealth:
    """Tests for handle_health()."""

    @pytest.mark.asyncio
    async def test_returns_ok(self, bridge):
        req = _make_request()
        resp = await bridge.handle_health(req)
        body = json.loads(resp.body)

        assert body["status"] == "ok"
        assert body["memory_enabled"] is True

    @pytest.mark.asyncio
    async def test_memory_disabled(self, mock_db, mock_voyage):
        with patch("api.memory_bridge.voyageai") as mock_voyageai:
            mock_voyageai.AsyncClient.return_value = mock_voyage
            api = MemoryBridgeAPI(None, mock_db)
            req = _make_request()
            resp = await api.handle_health(req)
            body = json.loads(resp.body)
            assert body["memory_enabled"] is False


class TestHandleStore:
    """Tests for handle_store()."""

    @pytest.mark.asyncio
    async def test_rejects_unauthorized(self, bridge):
        with patch.dict("os.environ", {"SLASHAI_API_KEY": "secret"}):
            req = _make_request(
                headers={"Authorization": "Bearer wrong"},
                body={"summary": "test"},
            )
            resp = await bridge.handle_store(req)
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self, bridge):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)
            req = _make_request(body=None)  # triggers JSONDecodeError
            resp = await bridge.handle_store(req)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_rejects_missing_summary(self, bridge):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)
            req = _make_request(body={"agent_id": "lena"})
            resp = await bridge.handle_store(req)
            assert resp.status == 400
            body = json.loads(resp.body)
            assert "summary" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_stores_memory_successfully(self, bridge, mock_db):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)

            # Mock user resolution (no linked account)
            mock_db.fetchrow.side_effect = [
                None,  # _resolve_user_id returns no match
                {"id": 42, "is_insert": True},  # INSERT returns new memory
            ]

            req = _make_request(body={
                "agent_id": "lena",
                "user_identifier": "Steve",
                "summary": "Built iron farm at 100,64,-200",
                "raw_context": "conversation about farming",
                "memory_type": "episodic",
                "source_platform": "minecraft",
                "confidence": 0.9,
            })
            resp = await bridge.handle_store(req)

            assert resp.status == 200
            body = json.loads(resp.body)
            assert body["memory_id"] == 42
            assert body["action"] == "add"

    @pytest.mark.asyncio
    async def test_store_returns_merge_action(self, bridge, mock_db):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)

            # No user_identifier → _resolve_user_id(None) returns 0 without DB call
            # So only one fetchrow call: the INSERT
            mock_db.fetchrow.return_value = {"id": 42, "is_insert": False}

            req = _make_request(body={
                "summary": "Updated iron farm",
                "memory_type": "episodic",
            })
            resp = await bridge.handle_store(req)
            body = json.loads(resp.body)
            assert body["action"] == "merge"

    @pytest.mark.asyncio
    async def test_store_passes_agent_id_to_sql(self, bridge, mock_db):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)

            mock_db.fetchrow.side_effect = [
                {"discord_user_id": 555},  # _resolve_user_id finds linked player
                {"id": 1, "is_insert": True},  # INSERT
            ]

            req = _make_request(body={
                "agent_id": "lena",
                "user_identifier": "Steve",
                "summary": "Test memory",
                "source_platform": "minecraft",
            })
            await bridge.handle_store(req)

            # Second fetchrow call is the INSERT
            insert_call = mock_db.fetchrow.call_args_list[1]
            args = insert_call[0]
            # args: (sql, user_id, summary, raw_context, embedding, memory_type,
            #        confidence, agent_id, source_platform, user_identifier)
            assert args[7] == "lena"  # agent_id
            assert args[8] == "minecraft"  # source_platform


class TestHandleRetrieve:
    """Tests for handle_retrieve()."""

    @pytest.mark.asyncio
    async def test_rejects_unauthorized(self, bridge):
        with patch.dict("os.environ", {"SLASHAI_API_KEY": "secret"}):
            req = _make_request(
                headers={"Authorization": "Bearer wrong"},
                body={"query": "test"},
            )
            resp = await bridge.handle_retrieve(req)
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_rejects_missing_query(self, bridge):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)
            req = _make_request(body={"agent_id": "lena"})
            resp = await bridge.handle_retrieve(req)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_caps_top_k_at_20(self, bridge, mock_db):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)

            mock_db.fetchrow.return_value = None  # _resolve_user_id
            mock_db.fetch.return_value = []

            req = _make_request(body={
                "query": "iron farm",
                "top_k": 100,
            })
            await bridge.handle_retrieve(req)

            # Check that the LIMIT parameter (4th positional arg) is capped at 20
            fetch_call = mock_db.fetch.call_args
            args = fetch_call[0]
            assert args[4] == 20  # top_k capped

    @pytest.mark.asyncio
    async def test_returns_memories(self, bridge, mock_db):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)

            mock_db.fetchrow.return_value = None  # _resolve_user_id

            mock_record = MagicMock()
            mock_record.__getitem__ = lambda self, key: {
                "id": 42,
                "topic_summary": "Built iron farm",
                "memory_type": "episodic",
                "source_platform": "minecraft",
                "confidence": 0.9,
                "similarity": 0.85,
                "created_at": datetime(2026, 3, 28, 12, 0, 0),
            }[key]

            mock_db.fetch.return_value = [mock_record]

            req = _make_request(body={
                "agent_id": "lena",
                "query": "iron farm",
                "user_identifier": "Steve",
            })
            resp = await bridge.handle_retrieve(req)

            body = json.loads(resp.body)
            assert len(body["memories"]) == 1
            mem = body["memories"][0]
            assert mem["id"] == 42
            assert mem["summary"] == "Built iron farm"
            assert mem["source_platform"] == "minecraft"

    @pytest.mark.asyncio
    async def test_retrieve_scopes_by_agent_id(self, bridge, mock_db):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("SLASHAI_API_KEY", None)

            mock_db.fetchrow.return_value = None
            mock_db.fetch.return_value = []

            req = _make_request(body={
                "agent_id": "lena",
                "query": "iron farm",
            })
            await bridge.handle_retrieve(req)

            fetch_call = mock_db.fetch.call_args
            args = fetch_call[0]
            # SQL has agent_id as $3
            assert args[3] == "lena"


class TestResolveUserId:
    """Tests for _resolve_user_id()."""

    @pytest.mark.asyncio
    async def test_returns_zero_for_none(self, bridge):
        result = await bridge._resolve_user_id(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_for_unknown_user(self, bridge, mock_db):
        mock_db.fetchrow.return_value = None
        result = await bridge._resolve_user_id("UnknownPlayer")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_discord_id_for_linked_player(self, bridge, mock_db):
        mock_db.fetchrow.return_value = {"discord_user_id": 123456789}
        result = await bridge._resolve_user_id("Steve")
        assert result == 123456789

    @pytest.mark.asyncio
    async def test_handles_db_error_gracefully(self, bridge, mock_db):
        mock_db.fetchrow.side_effect = Exception("connection error")
        result = await bridge._resolve_user_id("Steve")
        assert result == 0
