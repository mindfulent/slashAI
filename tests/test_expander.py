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

"""Tests for query expansion heuristics."""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.config import MemoryConfig
from memory.expander import ExpandedQuery, expand_query


@pytest.fixture
def config():
    return MemoryConfig()


class TestBroadPersonalExpansion:
    """Broad personal queries should expand into 5-6 sub-queries."""

    @pytest.mark.parametrize(
        "query",
        [
            "who am I",
            "tell me about myself",
            "draft a profile of who I am",
            "what do you know about me",
            "everything you know about me",
            "summarize what you know about me",
            "describe me",
            "about me",
            "my profile",
            # Wider phrasings (v0.13.9)
            "tell me about who I am",
            "what qualities of my character have you discovered",
            "what have you learned about me",
            "who i am as a person",
            "everything you remember about me",
            "what kind of person am I",
            "my personality",
            "my identity",
        ],
    )
    def test_broad_personal_triggers(self, config, query):
        result = expand_query(query, config)
        assert result.reason == "broad_personal"
        assert len(result.queries) == 6  # original + 5 sub-queries
        assert result.queries[0] == query  # original is always first
        assert result.top_k == config.expanded_top_k

    def test_broad_personal_case_insensitive(self, config):
        result = expand_query("WHO AM I", config)
        assert result.reason == "broad_personal"


class TestTopicScopedExpansion:
    """Topic-scoped queries should expand into 3-4 sub-queries."""

    @pytest.mark.parametrize(
        "query,reason",
        [
            ("my builds", "topic_scoped"),
            ("my creations", "topic_scoped"),
            ("my projects", "topic_scoped"),
            ("my preferences", "topic_scoped"),
            ("my skills", "topic_scoped"),
            ("my history", "topic_scoped"),
            ("my expertise", "topic_scoped"),
        ],
    )
    def test_topic_triggers(self, config, query, reason):
        result = expand_query(query, config)
        assert result.reason == reason
        assert len(result.queries) == 4  # original + 3 sub-queries
        assert result.queries[0] == query
        assert result.top_k == config.expanded_top_k


class TestNoExpansion:
    """Specific queries should pass through unchanged."""

    @pytest.mark.parametrize(
        "query",
        [
            "what's my timezone",
            "do you remember my IGN",
            "creeper farm progress",
            "hello",
            "how's the weather",
            "tell me a joke",
        ],
    )
    def test_specific_queries_no_expansion(self, config, query):
        result = expand_query(query, config)
        assert result.reason == "none"
        assert len(result.queries) == 1
        assert result.queries[0] == query
        assert result.top_k == config.top_k


class TestExpansionDisabled:
    """When expansion is disabled, always return original only."""

    def test_disabled_broad_query(self):
        config = MemoryConfig(expansion_enabled=False)
        result = expand_query("who am I", config)
        assert result.reason == "none"
        assert len(result.queries) == 1

    def test_disabled_topic_query(self):
        config = MemoryConfig(expansion_enabled=False)
        result = expand_query("my builds", config)
        assert result.reason == "none"
        assert len(result.queries) == 1


class TestConfigFields:
    """Config fields for expansion are correctly loaded."""

    def test_defaults(self):
        config = MemoryConfig()
        assert config.expansion_enabled is True
        assert config.expanded_top_k == 12

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_EXPANSION_ENABLED", "false")
        monkeypatch.setenv("MEMORY_EXPANDED_TOP_K", "20")
        config = MemoryConfig.from_env()
        assert config.expansion_enabled is False
        assert config.expanded_top_k == 20
