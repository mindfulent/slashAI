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

"""Tests for GitHub documentation reader."""

import sys
import time
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.github_docs import (
    GitHubDocsReader,
    PathValidationError,
    FileNotFoundError,
    handle_read_github_file,
    handle_list_github_docs,
)


class TestPathValidation:
    """Test path validation security."""

    def test_valid_docs_path(self):
        reader = GitHubDocsReader()
        assert reader._validate_path("docs/MEMORY_TECHSPEC.md") == "docs/MEMORY_TECHSPEC.md"
        assert reader._validate_path("docs/enhancements/011_CONFIDENCE_DECAY.md") == \
            "docs/enhancements/011_CONFIDENCE_DECAY.md"

    def test_valid_path_with_spaces(self):
        reader = GitHubDocsReader()
        # Leading/trailing spaces should be stripped
        assert reader._validate_path("  docs/README.md  ") == "docs/README.md"

    def test_reject_non_docs_path(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("src/memory/manager.py")
        with pytest.raises(PathValidationError):
            reader._validate_path("README.md")
        with pytest.raises(PathValidationError):
            reader._validate_path("migrations/001.sql")

    def test_reject_traversal_attempts(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/../src/secret.py")
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/../../etc/passwd")
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/foo/../../../etc/passwd")

    def test_reject_absolute_paths(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("/docs/MEMORY_TECHSPEC.md")
        with pytest.raises(PathValidationError):
            reader._validate_path(" /docs/MEMORY_TECHSPEC.md")

    def test_reject_control_characters(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/test\x00file.md")
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/test\nfile.md")

    def test_normalize_duplicate_slashes(self):
        reader = GitHubDocsReader()
        assert reader._validate_path("docs//enhancements///010.md") == \
            "docs/enhancements/010.md"


class TestCaching:
    """Test cache behavior."""

    def test_cache_hit(self):
        reader = GitHubDocsReader()
        reader._set_cached("docs/test.md", "main", "content")
        assert reader._get_cached("docs/test.md", "main") == "content"

    def test_cache_miss_different_ref(self):
        reader = GitHubDocsReader()
        reader._set_cached("docs/test.md", "main", "content")
        assert reader._get_cached("docs/test.md", "feature") is None

    def test_cache_case_insensitive(self):
        reader = GitHubDocsReader()
        reader._set_cached("docs/TEST.md", "MAIN", "content")
        assert reader._get_cached("docs/test.md", "main") == "content"

    def test_cache_key_generation(self):
        reader = GitHubDocsReader()
        key1 = reader._get_cache_key("docs/FILE.md", "MAIN")
        key2 = reader._get_cache_key("docs/file.md", "main")
        assert key1 == key2

    def test_cache_expiration(self):
        """Test that expired cache entries are not returned."""
        reader = GitHubDocsReader()
        # Manually set an expired entry
        from tools.github_docs import CacheEntry
        key = reader._get_cache_key("docs/expired.md", "main")
        reader._cache[key] = CacheEntry(
            content="old content",
            expires_at=time.time() - 100  # Expired 100 seconds ago
        )
        assert reader._get_cached("docs/expired.md", "main") is None
        # Entry should have been removed
        assert key not in reader._cache

    def test_cache_eviction(self):
        """Test that cache evicts oldest entries when full."""
        reader = GitHubDocsReader()
        # Fill cache to max capacity
        from tools.github_docs import CACHE_MAX_ENTRIES
        for i in range(CACHE_MAX_ENTRIES):
            reader._set_cached(f"docs/file{i}.md", "main", f"content{i}")

        assert len(reader._cache) == CACHE_MAX_ENTRIES

        # Add one more - should trigger eviction
        reader._set_cached("docs/new.md", "main", "new content")

        # Cache should have evicted some entries (10 oldest)
        assert len(reader._cache) <= CACHE_MAX_ENTRIES


class TestRateLimitStatus:
    """Test rate limit tracking."""

    def test_rate_limit_status_initial(self):
        reader = GitHubDocsReader()
        status = reader.rate_limit_status
        assert status["remaining"] is None
        assert status["reset_at"] is None

    def test_rate_limit_status_with_token(self):
        reader = GitHubDocsReader(github_token="test_token")
        status = reader.rate_limit_status
        assert status["authenticated"] is True

    def test_rate_limit_status_without_token(self):
        # Ensure GITHUB_TOKEN is not set for this test
        import os
        orig = os.environ.get("GITHUB_TOKEN")
        try:
            os.environ.pop("GITHUB_TOKEN", None)
            reader = GitHubDocsReader()
            status = reader.rate_limit_status
            assert status["authenticated"] is False
        finally:
            if orig:
                os.environ["GITHUB_TOKEN"] = orig


class TestIntegration:
    """Integration tests (require network)."""

    @pytest.mark.asyncio
    async def test_read_existing_file(self):
        """Test reading a file that should exist."""
        reader = GitHubDocsReader()
        content = await reader.read_file("docs/ARCHITECTURE.md")
        assert "slashAI" in content or "Architecture" in content
        assert len(content) > 100

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        """Test reading a file that doesn't exist."""
        reader = GitHubDocsReader()
        with pytest.raises(FileNotFoundError):
            await reader.read_file("docs/NONEXISTENT_FILE_12345.md")

    @pytest.mark.asyncio
    async def test_list_docs_root(self):
        """Test listing the docs root directory."""
        reader = GitHubDocsReader()
        items = await reader.list_directory()
        assert len(items) > 0
        # Should have the enhancements directory
        names = [item["name"] for item in items]
        assert "enhancements" in names

    @pytest.mark.asyncio
    async def test_list_enhancements(self):
        """Test listing the enhancements subdirectory."""
        reader = GitHubDocsReader()
        items = await reader.list_directory("enhancements")
        assert len(items) > 0
        # Should have markdown files
        assert any(item["name"].endswith(".md") for item in items)

    @pytest.mark.asyncio
    async def test_handle_read_github_file_success(self):
        """Test the handler function for successful reads."""
        result = await handle_read_github_file("docs/ARCHITECTURE.md")
        assert not result.startswith("Error:")
        assert len(result) > 100

    @pytest.mark.asyncio
    async def test_handle_read_github_file_invalid_path(self):
        """Test the handler function with invalid path."""
        result = await handle_read_github_file("src/secret.py")
        assert result.startswith("Error:")
        assert "docs/" in result

    @pytest.mark.asyncio
    async def test_handle_list_github_docs_success(self):
        """Test the handler function for listing docs."""
        result = await handle_list_github_docs()
        assert not result.startswith("Error:")
        assert "docs" in result
        assert "enhancements" in result

    @pytest.mark.asyncio
    async def test_cache_populated_after_read(self):
        """Test that cache is populated after reading a file."""
        reader = GitHubDocsReader()
        # Clear any existing cache
        reader._cache.clear()

        # Read a file
        content1 = await reader.read_file("docs/ARCHITECTURE.md")

        # Should be cached now
        cached = reader._get_cached("docs/ARCHITECTURE.md", "main")
        assert cached is not None
        assert cached == content1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
