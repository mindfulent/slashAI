# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for agent_id scoping in memory retrieval and storage."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.config import MemoryConfig


@pytest.fixture
def config():
    return MemoryConfig()


@pytest.fixture
def mock_db():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=True)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def mock_channel():
    channel = MagicMock()
    channel.guild = MagicMock()
    channel.guild.id = 100
    channel.id = 200
    return channel


def _make_mock_record(**kwargs):
    """Create a dict-like mock record matching asyncpg.Record behavior."""
    data = {
        "id": 1,
        "user_id": 111,
        "topic_summary": "Test memory",
        "raw_dialogue": "conversation",
        "memory_type": "semantic",
        "privacy_level": "guild_public",
        "similarity": 0.85,
        "confidence": 0.9,
        "updated_at": None,
        "reaction_summary": None,
    }
    data.update(kwargs)
    record = MagicMock()
    record.__getitem__ = lambda self, key: data[key]
    record.get = lambda key, default=None: data.get(key, default)
    record.keys = lambda: data.keys()
    return record


class TestRetrieverAgentId:
    """Tests that retriever passes agent_id through to SQL queries."""

    @pytest.mark.asyncio
    async def test_retrieve_passes_agent_id_to_hybrid(self, mock_db, config, mock_channel):
        with patch("memory.retriever.voyageai") as mock_voyageai:
            mock_client = MagicMock()
            embed_result = MagicMock()
            embed_result.embeddings = [[0.1] * 1024]
            mock_client.embed = AsyncMock(return_value=embed_result)
            mock_voyageai.AsyncClient.return_value = mock_client

            # hybrid_memory_search available
            mock_db.fetchval.return_value = True

            from memory.retriever import MemoryRetriever

            with patch("memory.retriever.classify_channel_privacy", new_callable=AsyncMock) as mock_privacy:
                from memory.privacy import PrivacyLevel
                mock_privacy.return_value = PrivacyLevel.GUILD_PUBLIC

                retriever = MemoryRetriever(mock_db, config)
                await retriever.retrieve(111, "test query", mock_channel, agent_id="lena")

                # hybrid_memory_search is called via db.fetch
                fetch_call = mock_db.fetch.call_args
                assert fetch_call is not None

                args = fetch_call[0]
                # Last arg to hybrid_memory_search is agent_id
                assert args[-1] == "lena"

    @pytest.mark.asyncio
    async def test_retrieve_none_agent_id(self, mock_db, config, mock_channel):
        with patch("memory.retriever.voyageai") as mock_voyageai:
            mock_client = MagicMock()
            embed_result = MagicMock()
            embed_result.embeddings = [[0.1] * 1024]
            mock_client.embed = AsyncMock(return_value=embed_result)
            mock_voyageai.AsyncClient.return_value = mock_client

            mock_db.fetchval.return_value = True

            from memory.retriever import MemoryRetriever

            with patch("memory.retriever.classify_channel_privacy", new_callable=AsyncMock) as mock_privacy:
                from memory.privacy import PrivacyLevel
                mock_privacy.return_value = PrivacyLevel.GUILD_PUBLIC

                retriever = MemoryRetriever(mock_db, config)
                await retriever.retrieve(111, "test query", mock_channel, agent_id=None)

                fetch_call = mock_db.fetch.call_args
                args = fetch_call[0]
                # Last arg should be None when no agent_id specified
                assert args[-1] is None

    @pytest.mark.asyncio
    async def test_retrieve_multi_passes_agent_id(self, mock_db, config, mock_channel):
        with patch("memory.retriever.voyageai") as mock_voyageai:
            mock_client = MagicMock()
            embed_result = MagicMock()
            embed_result.embeddings = [[0.1] * 1024, [0.2] * 1024]
            mock_client.embed = AsyncMock(return_value=embed_result)
            mock_voyageai.AsyncClient.return_value = mock_client

            mock_db.fetchval.return_value = True

            from memory.retriever import MemoryRetriever

            with patch("memory.retriever.classify_channel_privacy", new_callable=AsyncMock) as mock_privacy:
                from memory.privacy import PrivacyLevel
                mock_privacy.return_value = PrivacyLevel.GUILD_PUBLIC

                retriever = MemoryRetriever(mock_db, config)
                await retriever.retrieve_multi(
                    111, ["query 1", "query 2"], mock_channel, agent_id="dean"
                )

                # Each query triggers a separate hybrid search call
                for call in mock_db.fetch.call_args_list:
                    args = call[0]
                    assert args[-1] == "dean"


class TestUpdaterAgentId:
    """Tests that updater includes agent_id when storing memories."""

    @pytest.mark.asyncio
    async def test_add_includes_agent_id(self, mock_db, config):
        with patch("memory.retriever.voyageai") as mock_voyageai:
            mock_client = MagicMock()
            embed_result = MagicMock()
            embed_result.embeddings = [[0.1] * 1024]
            mock_client.embed = AsyncMock(return_value=embed_result)
            mock_voyageai.AsyncClient.return_value = mock_client

            from memory.extractor import ExtractedMemory
            from memory.privacy import PrivacyLevel
            from memory.retriever import MemoryRetriever
            from memory.updater import MemoryUpdater

            retriever = MemoryRetriever(mock_db, config)
            mock_anthropic = MagicMock()
            updater = MemoryUpdater(mock_db, retriever, mock_anthropic, config)

            memory = ExtractedMemory(
                summary="Lena helped build iron farm",
                memory_type="episodic",
                raw_dialogue="conversation about iron farms",
                confidence=0.85,
                global_safe=True,
            )

            # _find_similar returns None → goes to _add
            mock_db.fetchrow.side_effect = [
                None,  # _find_similar
                {"id": 42},  # _add INSERT RETURNING
            ]

            result = await updater.update(
                user_id=111,
                memory=memory,
                privacy_level=PrivacyLevel.GUILD_PUBLIC,
                channel_id=200,
                guild_id=100,
                agent_id="lena",
            )

            assert result == 42

            # Second fetchrow is the INSERT call from _add
            insert_call = mock_db.fetchrow.call_args_list[1]
            args = insert_call[0]
            sql = args[0]

            # Verify agent_id column is in the INSERT
            assert "agent_id" in sql

            # agent_id is the 10th positional parameter ($10)
            assert args[10] == "lena"

    @pytest.mark.asyncio
    async def test_add_without_agent_id(self, mock_db, config):
        with patch("memory.retriever.voyageai") as mock_voyageai:
            mock_client = MagicMock()
            embed_result = MagicMock()
            embed_result.embeddings = [[0.1] * 1024]
            mock_client.embed = AsyncMock(return_value=embed_result)
            mock_voyageai.AsyncClient.return_value = mock_client

            from memory.extractor import ExtractedMemory
            from memory.privacy import PrivacyLevel
            from memory.retriever import MemoryRetriever
            from memory.updater import MemoryUpdater

            retriever = MemoryRetriever(mock_db, config)
            mock_anthropic = MagicMock()
            updater = MemoryUpdater(mock_db, retriever, mock_anthropic, config)

            memory = ExtractedMemory(
                summary="General memory without agent",
                memory_type="semantic",
                raw_dialogue="conversation",
                confidence=0.8,
                global_safe=True,
            )

            mock_db.fetchrow.side_effect = [
                None,  # _find_similar
                {"id": 10},  # _add INSERT RETURNING
            ]

            result = await updater.update(
                user_id=111,
                memory=memory,
                privacy_level=PrivacyLevel.GUILD_PUBLIC,
                channel_id=200,
                guild_id=100,
            )

            assert result == 10

            # agent_id should be None (default)
            insert_call = mock_db.fetchrow.call_args_list[1]
            args = insert_call[0]
            assert args[10] is None  # agent_id position
