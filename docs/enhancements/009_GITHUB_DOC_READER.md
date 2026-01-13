# GitHub Documentation Reader Tool Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.1.0 |
| Created | 2026-01-12 |
| Status | Implemented |
| Author | Slash + Claude |
| Target Version | v0.10.x |
| Priority | P2 - Medium (after backup) |
| Dependencies | GitHub API |

---

## 1. Problem Statement

### 1.1 Current Behavior

When discussing slashAI's own documentation, users must manually copy-paste markdown files into the chat:

```
User: "What does the memory techspec say about privacy levels?"
*opens file, copies 800 lines, pastes into chat*
slashAI: "The privacy model defines four levels..."
```

**Pain points:**
- Friction: Users must leave Discord, open files, copy content
- Context limits: Large docs consume conversation context
- Staleness: Pasted content may be outdated vs main branch
- Comparison difficulty: Comparing two docs requires pasting both

### 1.2 Desired Behavior

```
User: "What does the memory techspec say about privacy levels?"
slashAI: *reads docs/MEMORY_TECHSPEC.md from GitHub*
         "The privacy model defines four levels: dm, channel_restricted,
          guild_public, and global. Each level determines..."

User: "Compare that to the privacy spec"
slashAI: *reads docs/MEMORY_PRIVACY.md from GitHub*
         "The techspec defines the levels, while the privacy spec
          details the retrieval filtering logic..."
```

### 1.3 Success Criteria

1. slashAI can read any file under `/docs/**` from the repository
2. Path validation prevents access outside `/docs`
3. Responses use current content from specified branch (default: main)
4. Rate limits handled gracefully
5. Caching reduces redundant API calls
6. Works for both chatbot mode and agentic tool use

---

## 2. Technical Design

### 2.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     GitHub Doc Reader Architecture                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Claude Tool Call                                                           │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    read_github_file Tool                             │   │
│  │                                                                     │   │
│  │  1. Validate path (must start with "docs/")                         │   │
│  │  2. Check cache (5-minute TTL)                                      │   │
│  │  3. If cache miss → GitHub API call                                 │   │
│  │  4. Decode base64 content                                           │   │
│  │  5. Cache result                                                    │   │
│  │  6. Return content string                                           │   │
│  │                                                                     │   │
│  └──────────────────────────────┬──────────────────────────────────────┘   │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         GitHub API                                   │   │
│  │                                                                     │   │
│  │  GET /repos/mindfulent/slashAI/contents/{path}?ref={branch}         │   │
│  │                                                                     │   │
│  │  Response: { content: "base64...", encoding: "base64", ... }        │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Optional: list_github_docs Tool                                            │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  GET /repos/mindfulent/slashAI/contents/docs/{subdir}               │   │
│  │  Returns: List of files in directory                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Security Model

**Hardcoded restrictions:**

| Element | Value | Rationale |
|---------|-------|-----------|
| Repository | `mindfulent/slashAI` | Hardcoded, never from user input |
| Path prefix | `docs/` | Only documentation accessible |
| Operations | Read-only | No write/delete possible |
| Branch default | `main` | Production content by default |

**Path validation rules:**
1. Must start with `docs/` (case-sensitive)
2. No `..` sequences allowed (traversal prevention)
3. No null bytes or control characters
4. Reject absolute paths (starting with `/`)

### 2.3 Caching Strategy

**Why cache:**
- Reduce GitHub API calls (rate limits)
- Faster responses for repeated reads
- Same doc often referenced multiple times in conversation

**Cache parameters:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| TTL | 5 minutes | Balance freshness vs API calls |
| Max entries | 50 | Reasonable memory footprint |
| Key | `(path, ref)` tuple | Different branches cached separately |
| Invalidation | Time-based only | No webhook integration needed |

### 2.4 Rate Limit Handling

**GitHub API limits:**

| Auth Type | Limit | Reset |
|-----------|-------|-------|
| Unauthenticated | 60 req/hr | Rolling window |
| With `GITHUB_TOKEN` | 5,000 req/hr | Rolling window |

**Strategy:**
1. Use `GITHUB_TOKEN` if available (strongly recommended)
2. Check `X-RateLimit-Remaining` header on each response
3. If remaining < 10, warn in logs
4. If rate limited (403), return friendly error message
5. Include reset time in error: "Rate limited. Try again in X minutes."

---

## 3. Tool Definitions

### 3.1 read_github_file

```python
# Tool definition for Claude API
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
```

### 3.2 list_github_docs

```python
# Tool definition for Claude API
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
```

---

## 4. Python Implementation

### 4.1 GitHub Client Module

```python
# src/tools/github_docs.py

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
                raise PathValidationError(f"Invalid path: contains forbidden pattern")

        # Ensure path starts with docs/
        if not path.startswith(ALLOWED_PATH_PREFIX):
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
                    if "rate limit" in (await resp.text()).lower():
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
                    if "rate limit" in (await resp.text()).lower():
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
            icon = "📁" if item["type"] == "dir" else "📄"
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
```

### 4.2 Integration with Claude Client

```python
# src/claude_client.py - additions

from tools.github_docs import (
    READ_GITHUB_FILE_TOOL,
    LIST_GITHUB_DOCS_TOOL,
    handle_read_github_file,
    handle_list_github_docs,
)

# Add to DISCORD_TOOLS list
GITHUB_TOOLS = [
    READ_GITHUB_FILE_TOOL,
    LIST_GITHUB_DOCS_TOOL,
]

# In the tool execution handler
async def execute_tool(self, tool_name: str, tool_input: dict) -> str:
    """Execute a tool and return result."""

    # ... existing tool handlers ...

    if tool_name == "read_github_file":
        return await handle_read_github_file(
            path=tool_input["path"],
            ref=tool_input.get("ref", "main"),
        )

    if tool_name == "list_github_docs":
        return await handle_list_github_docs(
            subdir=tool_input.get("subdir", ""),
            ref=tool_input.get("ref", "main"),
        )

    # ... rest of handlers ...
```

---

## 5. Usage Examples

### 5.1 Reading Documentation

```
User: "What does the memory techspec say about embedding dimensions?"

slashAI: *invokes read_github_file with path="docs/MEMORY_TECHSPEC.md"*

"According to the memory techspec, slashAI uses Voyage AI embeddings with
1024 dimensions. The text memory system uses `voyage-3.5-lite` for text,
and `voyage-multimodal-3` for image observations. The embedding dimensions
are stored in the PostgreSQL `vector(1024)` column type."
```

### 5.2 Listing Available Docs

```
User: "What enhancement specs do you have?"

slashAI: *invokes list_github_docs with subdir="enhancements"*

"Here are the enhancement specifications in docs/enhancements:

  📄 001_MEMORY_ATTRIBUTION.md
  📄 002_MEMORY_MANAGEMENT.md
  📄 003_AGENTIC_TOOLS.md
  📄 004_ANALYTICS.md
  📄 005_REMINDERS.md
  📄 006_META_MEMORY.md
  📄 007_IMAGE_MEMORY_FIXES.md
  📄 008_DATABASE_BACKUP.md
  📄 009_GITHUB_DOC_READER.md
  📄 010_HYBRID_SEARCH.md
  📄 011_CONFIDENCE_DECAY.md
  📄 012_DETERMINISTIC_EXPORT.md
  📄 013_AUDIT_LOG.md

Would you like me to read any of these?"
```

### 5.3 Comparing Documents

```
User: "Compare the hybrid search spec to the confidence decay spec"

slashAI: *invokes read_github_file for both files in parallel*

"Both specs are targeted for v0.10.x and rated P1 priority:

**Hybrid Search (010)**
- Combines BM25 lexical + pgvector semantic search
- Uses Reciprocal Rank Fusion (RRF) for result merging
- Estimated effort: 1-2 days

**Confidence Decay (011)**
- Relevance-weighted decay based on retrieval frequency
- Memories retrieved often decay slower (0.99 vs 0.95 rate)
- Estimated effort: 1 day

Both improve memory retrieval quality but from different angles:
hybrid search helps find exact matches, decay helps prioritize current info."
```

### 5.4 Reading from a Branch

```
User: "What's in the draft backup spec on the feature branch?"

slashAI: *invokes read_github_file with ref="feature/backup-restore"*

"The draft backup spec on the feature/backup-restore branch includes..."
```

---

## 6. Testing

### 6.1 Unit Tests

```python
# tests/test_github_docs.py

import pytest
from tools.github_docs import GitHubDocsReader, PathValidationError

class TestPathValidation:
    """Test path validation security."""

    def test_valid_docs_path(self):
        reader = GitHubDocsReader()
        assert reader._validate_path("docs/MEMORY_TECHSPEC.md") == "docs/MEMORY_TECHSPEC.md"
        assert reader._validate_path("docs/enhancements/011_CONFIDENCE_DECAY.md") == \
            "docs/enhancements/011_CONFIDENCE_DECAY.md"

    def test_reject_non_docs_path(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("src/memory/manager.py")
        with pytest.raises(PathValidationError):
            reader._validate_path("README.md")

    def test_reject_traversal_attempts(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/../src/secret.py")
        with pytest.raises(PathValidationError):
            reader._validate_path("docs/../../etc/passwd")

    def test_reject_absolute_paths(self):
        reader = GitHubDocsReader()
        with pytest.raises(PathValidationError):
            reader._validate_path("/docs/MEMORY_TECHSPEC.md")

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


class TestIntegration:
    """Integration tests (require network)."""

    @pytest.mark.asyncio
    async def test_read_existing_file(self):
        reader = GitHubDocsReader()
        content = await reader.read_file("docs/MEMORY_TECHSPEC.md")
        assert "Memory" in content
        assert len(content) > 100

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        from tools.github_docs import FileNotFoundError
        reader = GitHubDocsReader()
        with pytest.raises(FileNotFoundError):
            await reader.read_file("docs/NONEXISTENT_FILE.md")

    @pytest.mark.asyncio
    async def test_list_docs_root(self):
        reader = GitHubDocsReader()
        items = await reader.list_directory()
        assert len(items) > 0
        assert any(item["name"] == "enhancements" for item in items)
```

---

## 7. Environment Configuration

### 7.1 Required Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Recommended | Personal access token for higher rate limits |

### 7.2 Rate Limit Comparison

| Config | Requests/Hour | Recommended For |
|--------|---------------|-----------------|
| No token | 60 | Development only |
| With token | 5,000 | Production use |

### 7.3 Token Permissions

The `GITHUB_TOKEN` only needs:
- **public_repo** scope (or no scope for public repos)
- Read-only access is sufficient

---

## 8. Rollout Plan

### Phase 1: Development
1. Create `src/tools/github_docs.py` module
2. Add tool definitions to Claude client
3. Add tests
4. Add `GITHUB_TOKEN` to environment

### Phase 2: Testing
1. Unit tests for path validation
2. Integration tests with real API
3. Rate limit handling verification
4. Cache behavior testing

### Phase 3: Deployment
1. Deploy to production
2. Monitor rate limit usage
3. Verify caching effectiveness
4. Document in CLAUDE.md

---

## 9. Security Checklist

- [x] Path validation prevents `..` traversal
- [x] Repository name hardcoded (not from user input)
- [x] Only read operations possible
- [x] Path must start with `docs/`
- [x] Rate limit handling graceful
- [x] Token stored in environment, not code
- [x] No control characters in paths allowed

---

## 10. Future Enhancements

### 10.1 Search Within Docs

```python
async def search_github_docs(query: str, ref: str = "main") -> list[dict]:
    """Search for text across all documentation files."""
    # Use GitHub code search API or fetch + local search
```

### 10.2 Diff Between Branches

```python
async def diff_github_doc(path: str, base: str = "main", head: str = "HEAD") -> str:
    """Show differences between versions of a doc."""
    # Useful for reviewing changes before merge
```

### 10.3 Webhook-Based Cache Invalidation

Instead of TTL-based expiration, use GitHub webhooks to invalidate cache on push:
- More efficient for frequently-read docs
- Requires webhook endpoint (complexity)
- Lower priority unless TTL proves problematic

---

## Appendix A: Error Messages

| Error | User-Facing Message |
|-------|---------------------|
| Path outside /docs | "I can only read files in the /docs directory. That path isn't accessible." |
| File not found | "I couldn't find that file. Use `/docs list` to see available files." |
| Rate limited | "GitHub API rate limit reached. Please try again in X minutes." |
| Network error | "I couldn't reach GitHub. Please try again in a moment." |

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial specification |
| 0.2.0 | 2026-01-12 | Claude | Implemented: `src/tools/github_docs.py`, tests, claude_client.py integration |
