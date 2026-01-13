# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing: [slashdaemon@protonmail.com]

"""Tests for hybrid search functionality."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.config import MemoryConfig
from memory.retriever import MemoryRetriever, RetrievedMemory


class TestMemoryConfig:
    """Test hybrid search configuration."""

    def test_default_config(self):
        config = MemoryConfig()
        assert config.hybrid_search_enabled is True
        assert config.hybrid_candidate_limit == 20
        assert config.rrf_k == 60

    def test_config_from_env_default(self):
        with patch.dict("os.environ", {}, clear=True):
            config = MemoryConfig.from_env()
            assert config.hybrid_search_enabled is True
            assert config.hybrid_candidate_limit == 20
            assert config.rrf_k == 60

    def test_config_from_env_disabled(self):
        with patch.dict("os.environ", {"MEMORY_HYBRID_SEARCH": "false"}):
            config = MemoryConfig.from_env()
            assert config.hybrid_search_enabled is False

    def test_config_from_env_custom_values(self):
        with patch.dict("os.environ", {
            "MEMORY_HYBRID_SEARCH": "true",
            "MEMORY_HYBRID_CANDIDATES": "30",
            "MEMORY_RRF_K": "100",
        }):
            config = MemoryConfig.from_env()
            assert config.hybrid_search_enabled is True
            assert config.hybrid_candidate_limit == 30
            assert config.rrf_k == 100


@pytest.fixture
def mock_voyage():
    """Mock voyageai.AsyncClient to avoid API key requirement."""
    with patch('memory.retriever.voyageai.AsyncClient'):
        yield


class TestHybridAvailabilityCheck:
    """Test hybrid search availability detection."""

    @pytest.mark.asyncio
    async def test_hybrid_available_when_column_exists(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=True)

        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        result = await retriever._is_hybrid_available()
        assert result is True
        assert retriever._hybrid_available is True

    @pytest.mark.asyncio
    async def test_hybrid_unavailable_when_column_missing(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=False)

        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        result = await retriever._is_hybrid_available()
        assert result is False
        assert retriever._hybrid_available is False

    @pytest.mark.asyncio
    async def test_hybrid_check_cached(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=True)

        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        # First call
        await retriever._is_hybrid_available()
        # Second call should use cached value
        await retriever._is_hybrid_available()

        # fetchval should only be called once
        assert mock_pool.fetchval.call_count == 1

    @pytest.mark.asyncio
    async def test_hybrid_check_handles_exception(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(side_effect=Exception("DB error"))

        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        result = await retriever._is_hybrid_available()
        assert result is False
        assert retriever._hybrid_available is False


class TestHybridRetrieve:
    """Test hybrid search retrieval path."""

    @pytest.mark.asyncio
    async def test_uses_hybrid_when_available(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=True)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.execute = AsyncMock()

        config = MemoryConfig(hybrid_search_enabled=True)
        retriever = MemoryRetriever(mock_pool, config)

        # Mock embedding
        with patch.object(retriever, '_embed', new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Create mock channel
            mock_channel = MagicMock()
            mock_channel.guild = MagicMock()
            mock_channel.guild.id = 123456
            mock_channel.id = 789012

            # Mock classify_channel_privacy
            with patch('memory.retriever.classify_channel_privacy', new_callable=AsyncMock) as mock_classify:
                from memory.privacy import PrivacyLevel
                mock_classify.return_value = PrivacyLevel.GUILD_PUBLIC

                await retriever.retrieve(111, "test query", mock_channel)

        # Should call fetch with hybrid_memory_search
        fetch_calls = mock_pool.fetch.call_args_list
        assert len(fetch_calls) >= 1
        # First fetch should be hybrid search
        first_call_sql = fetch_calls[0][0][0]
        assert "hybrid_memory_search" in first_call_sql

    @pytest.mark.asyncio
    async def test_falls_back_to_semantic_when_disabled(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.execute = AsyncMock()

        config = MemoryConfig(hybrid_search_enabled=False)
        retriever = MemoryRetriever(mock_pool, config)

        with patch.object(retriever, '_embed', new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_channel = MagicMock()
            mock_channel.guild = MagicMock()
            mock_channel.guild.id = 123456
            mock_channel.id = 789012

            with patch('memory.retriever.classify_channel_privacy', new_callable=AsyncMock) as mock_classify:
                from memory.privacy import PrivacyLevel
                mock_classify.return_value = PrivacyLevel.GUILD_PUBLIC

                await retriever.retrieve(111, "test query", mock_channel)

        # Should call semantic search, not hybrid
        fetch_calls = mock_pool.fetch.call_args_list
        assert len(fetch_calls) >= 1
        first_call_sql = fetch_calls[0][0][0]
        assert "hybrid_memory_search" not in first_call_sql

    @pytest.mark.asyncio
    async def test_falls_back_on_hybrid_failure(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=True)

        # First fetch (hybrid) fails, second (semantic) succeeds
        mock_pool.fetch = AsyncMock(side_effect=[
            Exception("Hybrid search failed"),
            []  # Semantic fallback succeeds
        ])
        mock_pool.execute = AsyncMock()

        config = MemoryConfig(hybrid_search_enabled=True)
        retriever = MemoryRetriever(mock_pool, config)

        with patch.object(retriever, '_embed', new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_channel = MagicMock()
            mock_channel.guild = MagicMock()
            mock_channel.guild.id = 123456
            mock_channel.id = 789012

            with patch('memory.retriever.classify_channel_privacy', new_callable=AsyncMock) as mock_classify:
                from memory.privacy import PrivacyLevel
                mock_classify.return_value = PrivacyLevel.GUILD_PUBLIC

                # Should not raise, should fall back
                result = await retriever.retrieve(111, "test query", mock_channel)
                assert result == []
                # After failure, hybrid should be marked unavailable
                assert retriever._hybrid_available is False


class TestEmptyQuery:
    """Test handling of empty queries."""

    @pytest.mark.asyncio
    async def test_empty_string_returns_empty(self, mock_voyage):
        mock_pool = MagicMock()
        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        result = await retriever.retrieve(111, "", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty(self, mock_voyage):
        mock_pool = MagicMock()
        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        result = await retriever.retrieve(111, "   ", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_none_query_returns_empty(self, mock_voyage):
        mock_pool = MagicMock()
        config = MemoryConfig()
        retriever = MemoryRetriever(mock_pool, config)

        result = await retriever.retrieve(111, None, MagicMock())
        assert result == []


class TestRetrievedMemoryParsing:
    """Test that retrieval results are correctly parsed."""

    @pytest.mark.asyncio
    async def test_parses_hybrid_results(self, mock_voyage):
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=True)

        # Mock a record with dict-like access
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: {
            "id": 1,
            "user_id": 111,
            "topic_summary": "Test memory",
            "raw_dialogue": "Raw text",
            "memory_type": "episodic",
            "privacy_level": "guild_public",
            "confidence": 0.9,
            "updated_at": "2026-01-12T00:00:00Z",
            "similarity": 0.85,
            "rrf_score": 0.032,
        }[key]
        mock_record.keys = lambda: ["id", "user_id", "topic_summary", "raw_dialogue",
                                     "memory_type", "privacy_level", "confidence",
                                     "updated_at", "similarity", "rrf_score"]

        mock_pool.fetch = AsyncMock(return_value=[mock_record])
        mock_pool.execute = AsyncMock()

        config = MemoryConfig(hybrid_search_enabled=True)
        retriever = MemoryRetriever(mock_pool, config)

        with patch.object(retriever, '_embed', new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_channel = MagicMock()
            mock_channel.guild = MagicMock()
            mock_channel.guild.id = 123456
            mock_channel.id = 789012

            with patch('memory.retriever.classify_channel_privacy', new_callable=AsyncMock) as mock_classify:
                from memory.privacy import PrivacyLevel
                mock_classify.return_value = PrivacyLevel.GUILD_PUBLIC

                results = await retriever.retrieve(111, "test query", mock_channel)

        assert len(results) == 1
        memory = results[0]
        assert isinstance(memory, RetrievedMemory)
        assert memory.id == 1
        assert memory.user_id == 111
        assert memory.summary == "Test memory"
        assert memory.similarity == 0.85


class TestRRFFormula:
    """Test RRF scoring behavior (conceptual tests)."""

    def test_rrf_score_calculation(self):
        """Verify RRF formula: 1/(k + rank)."""
        k = 60

        # Document at rank 1 in lexical
        lex_rank = 1
        lex_score = 1.0 / (k + lex_rank)
        assert lex_score == pytest.approx(1.0 / 61, rel=1e-6)

        # Document at rank 5 in semantic
        sem_rank = 5
        sem_score = 1.0 / (k + sem_rank)
        assert sem_score == pytest.approx(1.0 / 65, rel=1e-6)

        # Combined RRF score
        combined = lex_score + sem_score
        assert combined == pytest.approx(1.0/61 + 1.0/65, rel=1e-6)

    def test_rrf_favors_dual_presence(self):
        """Documents in both result sets should score higher."""
        k = 60

        # Document A: Only in semantic at rank 1
        score_a = 1.0 / (k + 1)  # ~0.0164

        # Document B: In both at rank 5 each
        score_b = (1.0 / (k + 5)) + (1.0 / (k + 5))  # ~0.0308

        assert score_b > score_a


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
