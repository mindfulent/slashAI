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

"""
GitHub Documentation Reader

Provides read-only access to slashAI documentation files via GitHub API.
Restricted to /docs/** paths for security.
"""

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger("slashAI.tools.github_docs")

# Configuration
GITHUB_REPO = "mindfulent/slashAI"  # Hardcoded for security
GITHUB_API_BASE = "https://api.github.com"
CACHE_TTL_SECONDS = 300  # 5 minutes
CACHE_MAX_ENTRIES = 50

# Path validation
ALLOWED_PATH_PREFIX = "docs/"
FORBIDDEN_PATTERNS = [
    r"\.\.",           # Parent directory traversal
    r"^\s*/",          # Absolute paths
    r"[\x00-\x1f]",    # Control characters
]


# Tool definitions for Claude API
READ_GITHUB_FILE_TOOL = {
    "name": "read_github_file",
    "description": """Read a documentation file from the slashAI GitHub repository.

Use this tool when the user asks about slashAI's documentation, specifications,
or implementation details that are documented in the /docs folder.

Examples of when to use:
- "What does the memory techspec say about X?"
- "Read the confidence decay specification"
- "What's in the privacy documentation?"

The tool only has access to files under /docs in the repository.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, starting with 'docs/'. Example: 'docs/MEMORY_TECHSPEC.md'"
            },
            "ref": {
                "type": "string",
                "description": "Branch or commit SHA. Default: 'main'",
                "default": "main"
            }
        },
        "required": ["path"]
    }
}

LIST_GITHUB_DOCS_TOOL = {
    "name": "list_github_docs",
    "description": """List available documentation files in the slashAI repository.

Use this tool to discover what documentation exists before reading specific files.
Returns a list of files and subdirectories under /docs.

Examples of when to use:
- "What documentation do you have?"
- "List the enhancement specs"
- "What files are in docs/enhancements?"
""",
    "input_schema": {
        "type": "object",
        "properties": {
            "subdir": {
                "type": "string",
                "description": "Subdirectory under docs/ to list. Example: 'enhancements'. Default: '' (root)",
                "default": ""
            },
            "ref": {
                "type": "string",
                "description": "Branch or commit SHA. Default: 'main'",
                "default": "main"
            }
        },
        "required": []
    }
}


@dataclass
class CacheEntry:
    """Cached file content with expiration."""
    content: str
    expires_at: float


class GitHubDocsError(Exception):
    """Base exception for GitHub docs operations."""
    pass


class PathValidationError(GitHubDocsError):
    """Raised when path fails validation."""
    pass


class RateLimitError(GitHubDocsError):
    """Raised when GitHub rate limit is exceeded."""
    def __init__(self, reset_time: int):
        self.reset_time = reset_time
        minutes = max(1, (reset_time - int(time.time())) // 60)
        super().__init__(f"GitHub API rate limit exceeded. Try again in {minutes} minute(s).")


class FileNotFoundError(GitHubDocsError):
    """Raised when file doesn't exist."""
    pass


class GitHubDocsReader:
    """
    Read-only access to slashAI documentation via GitHub API.

    Security:
    - Only files under /docs are accessible
    - Repository is hardcoded (not parameterized)
    - Read-only operations only
    """

    def __init__(self, github_token: Optional[str] = None):
        """
        Initialize the reader.

        Args:
            github_token: Optional GitHub personal access token for higher rate limits.
                         If not provided, uses unauthenticated access (60 req/hr).
        """
        self.token = github_token or os.getenv("GITHUB_TOKEN")
        self._cache: dict[tuple[str, str], CacheEntry] = {}
        self._rate_limit_remaining: Optional[int] = None
        self._rate_limit_reset: Optional[int] = None

    def _validate_path(self, path: str) -> str:
        """
        Validate and normalize a file path.

        Args:
            path: Requested path (e.g., "docs/MEMORY_TECHSPEC.md")

        Returns:
            Normalized path

        Raises:
            PathValidationError: If path is invalid or outside allowed scope
        """
        # Strip whitespace
        path = path.strip()

        # Check for forbidden patterns
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, path):
                raise PathValidationError("Invalid path: contains forbidden pattern")

        # Ensure path starts with docs/ or is exactly "docs"
        if path != "docs" and not path.startswith(ALLOWED_PATH_PREFIX):
            raise PathValidationError(
                f"Access denied: path must start with '{ALLOWED_PATH_PREFIX}'. "
                f"Got: '{path}'"
            )

        # Normalize path (remove duplicate slashes, etc.)
        path = re.sub(r"/+", "/", path)

        return path

    def _get_cache_key(self, path: str, ref: str) -> tuple[str, str]:
        """Generate cache key for a path/ref combination."""
        return (path.lower(), ref.lower())

    def _get_cached(self, path: str, ref: str) -> Optional[str]:
        """Get content from cache if valid."""
        key = self._get_cache_key(path, ref)
        entry = self._cache.get(key)

        if entry and entry.expires_at > time.time():
            logger.debug(f"Cache hit for {path}@{ref}")
            return entry.content

        if entry:
            # Expired, remove it
            del self._cache[key]

        return None

    def _set_cached(self, path: str, ref: str, content: str) -> None:
        """Store content in cache."""
        # Evict oldest entries if at capacity
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            # Remove entries with earliest expiration
            sorted_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k].expires_at
            )
            for key in sorted_keys[:10]:  # Remove 10 oldest
                del self._cache[key]

        key = self._get_cache_key(path, ref)
        self._cache[key] = CacheEntry(
            content=content,
            expires_at=time.time() + CACHE_TTL_SECONDS
        )
        logger.debug(f"Cached {path}@{ref} (expires in {CACHE_TTL_SECONDS}s)")

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for GitHub API requests."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "slashAI-bot",
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    async def read_file(self, path: str, ref: str = "main") -> str:
        """
        Read a documentation file from the repository.

        Args:
            path: Path to file (must start with "docs/")
            ref: Branch name or commit SHA (default: "main")

        Returns:
            File contents as string

        Raises:
            PathValidationError: If path is invalid or outside /docs
            FileNotFoundError: If file doesn't exist
            RateLimitError: If GitHub rate limit exceeded
            GitHubDocsError: For other API errors
        """
        # Validate path
        path = self._validate_path(path)

        # Check cache
        cached = self._get_cached(path, ref)
        if cached is not None:
            return cached

        # Make API request
        url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
        params = {"ref": ref}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._get_headers(), params=params) as resp:
                # Update rate limit tracking
                self._rate_limit_remaining = int(resp.headers.get("X-RateLimit-Remaining", 0))
                self._rate_limit_reset = int(resp.headers.get("X-RateLimit-Reset", 0))

                if self._rate_limit_remaining and self._rate_limit_remaining < 10:
                    logger.warning(f"GitHub rate limit low: {self._rate_limit_remaining} remaining")

                if resp.status == 404:
                    raise FileNotFoundError(f"File not found: {path} (branch: {ref})")

                if resp.status == 403:
                    # Check if rate limited
                    resp_text = await resp.text()
                    if "rate limit" in resp_text.lower():
                        raise RateLimitError(self._rate_limit_reset or int(time.time()) + 3600)
                    raise GitHubDocsError(f"Access forbidden: {path}")

                if resp.status != 200:
                    raise GitHubDocsError(f"GitHub API error: {resp.status}")

                data = await resp.json()

        # Decode content
        if data.get("encoding") != "base64":
            raise GitHubDocsError(f"Unexpected encoding: {data.get('encoding')}")

        content = base64.b64decode(data["content"]).decode("utf-8")

        # Cache and return
        self._set_cached(path, ref, content)

        logger.info(f"Read {path}@{ref} ({len(content)} bytes)")
        return content

    async def list_directory(self, subdir: str = "", ref: str = "main") -> list[dict]:
        """
        List files in a documentation directory.

        Args:
            subdir: Subdirectory under docs/ (e.g., "enhancements")
            ref: Branch name or commit SHA (default: "main")

        Returns:
            List of dicts with 'name', 'type' ('file' or 'dir'), and 'path'

        Raises:
            PathValidationError: If path is invalid
            FileNotFoundError: If directory doesn't exist
            RateLimitError: If GitHub rate limit exceeded
        """
        # Build and validate path
        if subdir:
            path = f"docs/{subdir.strip('/')}"
        else:
            path = "docs"

        path = self._validate_path(path)

        # Make API request
        url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
        params = {"ref": ref}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._get_headers(), params=params) as resp:
                # Update rate limit tracking
                self._rate_limit_remaining = int(resp.headers.get("X-RateLimit-Remaining", 0))
                self._rate_limit_reset = int(resp.headers.get("X-RateLimit-Reset", 0))

                if resp.status == 404:
                    raise FileNotFoundError(f"Directory not found: {path} (branch: {ref})")

                if resp.status == 403:
                    resp_text = await resp.text()
                    if "rate limit" in resp_text.lower():
                        raise RateLimitError(self._rate_limit_reset or int(time.time()) + 3600)
                    raise GitHubDocsError(f"Access forbidden: {path}")

                if resp.status != 200:
                    raise GitHubDocsError(f"GitHub API error: {resp.status}")

                data = await resp.json()

        # Parse response
        if not isinstance(data, list):
            raise GitHubDocsError(f"Expected directory, got file: {path}")

        items = []
        for item in data:
            items.append({
                "name": item["name"],
                "type": "dir" if item["type"] == "dir" else "file",
                "path": item["path"],
            })

        # Sort: directories first, then files, alphabetically
        items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))

        logger.info(f"Listed {path}@{ref} ({len(items)} items)")
        return items

    @property
    def rate_limit_status(self) -> dict:
        """Get current rate limit status."""
        return {
            "remaining": self._rate_limit_remaining,
            "reset_at": self._rate_limit_reset,
            "authenticated": bool(self.token),
        }


# Singleton instance
_reader: Optional[GitHubDocsReader] = None


def get_reader() -> GitHubDocsReader:
    """Get or create the singleton reader instance."""
    global _reader
    if _reader is None:
        _reader = GitHubDocsReader()
    return _reader


# Tool handler functions for Claude API
async def handle_read_github_file(path: str, ref: str = "main") -> str:
    """
    Tool handler: Read a documentation file.

    Args:
        path: Path to file (must start with "docs/")
        ref: Branch or commit SHA

    Returns:
        File contents or error message
    """
    reader = get_reader()
    try:
        content = await reader.read_file(path, ref)
        return content
    except PathValidationError as e:
        return f"Error: {e}"
    except FileNotFoundError as e:
        return f"Error: {e}"
    except RateLimitError as e:
        return f"Error: {e}"
    except GitHubDocsError as e:
        return f"Error: {e}"


async def handle_list_github_docs(subdir: str = "", ref: str = "main") -> str:
    """
    Tool handler: List documentation files.

    Args:
        subdir: Subdirectory under docs/
        ref: Branch or commit SHA

    Returns:
        Formatted list of files or error message
    """
    reader = get_reader()
    try:
        items = await reader.list_directory(subdir, ref)

        if not items:
            return f"No files found in docs/{subdir}" if subdir else "No files found in docs/"

        lines = []
        dir_path = f"docs/{subdir}" if subdir else "docs"
        lines.append(f"Files in {dir_path}:")
        lines.append("")

        for item in items:
            icon = "[dir]" if item["type"] == "dir" else "[file]"
            lines.append(f"  {icon} {item['name']}")

        return "\n".join(lines)

    except PathValidationError as e:
        return f"Error: {e}"
    except FileNotFoundError as e:
        return f"Error: {e}"
    except RateLimitError as e:
        return f"Error: {e}"
    except GitHubDocsError as e:
        return f"Error: {e}"
