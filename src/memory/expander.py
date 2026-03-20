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
Query Expansion for Memory Retrieval

Decomposes broad queries into multiple targeted sub-queries for better
recall when users ask sweeping questions like "who am I" or "my profile".
Each sub-query is run independently and results are merged/deduplicated.
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import MemoryConfig


@dataclass
class ExpandedQuery:
    """Result of query expansion."""

    queries: list[str]  # Sub-queries to run (original always first)
    top_k: int  # Recommended top_k for merged results
    reason: str  # "broad_personal" | "topic_scoped" | "none"


# Broad personal patterns — user asking for a holistic view of themselves
_BROAD_PERSONAL_PATTERNS = [
    re.compile(r"\bwho am i\b", re.IGNORECASE),
    re.compile(r"\babout me\b", re.IGNORECASE),
    re.compile(r"\bmy profile\b", re.IGNORECASE),
    re.compile(r"\beverything you know\b", re.IGNORECASE),
    re.compile(r"\ball you know\b", re.IGNORECASE),
    re.compile(r"\bwhat do you know about me\b", re.IGNORECASE),
    re.compile(r"\bsummar\w* (?:of )?(?:what you know|me|my)\b", re.IGNORECASE),
    re.compile(r"\bdraft a profile\b", re.IGNORECASE),
    re.compile(r"\bdescribe me\b", re.IGNORECASE),
    re.compile(r"\btell me about myself\b", re.IGNORECASE),
]

_BROAD_PERSONAL_SUBQUERIES = [
    "projects builds creations",
    "preferences opinions likes dislikes",
    "personality communication style",
    "skills expertise background knowledge",
    "community involvement server role contributions",
]

# Topic-scoped patterns — user asking about a specific facet
_TOPIC_PATTERNS: list[tuple[re.Pattern, list[str]]] = [
    (
        re.compile(r"\bmy (?:builds?|creations?|structures?)\b", re.IGNORECASE),
        [
            "building style materials techniques",
            "specific builds projects completed",
            "build feedback reviews",
        ],
    ),
    (
        re.compile(r"\bmy projects?\b", re.IGNORECASE),
        [
            "current ongoing projects goals",
            "completed past projects",
            "project collaboration contributions",
        ],
    ),
    (
        re.compile(r"\bmy prefer\w+\b", re.IGNORECASE),
        [
            "favorite things likes",
            "dislikes things to avoid",
            "communication style preferences",
        ],
    ),
    (
        re.compile(r"\bmy (?:skills?|expertise|experience)\b", re.IGNORECASE),
        [
            "technical skills programming knowledge",
            "minecraft gameplay expertise",
            "learning goals areas of interest",
        ],
    ),
    (
        re.compile(r"\bmy (?:history|past|timeline)\b", re.IGNORECASE),
        [
            "early interactions first conversations",
            "milestones achievements",
            "changes over time progression",
        ],
    ),
]


def expand_query(query: str, config: "MemoryConfig") -> ExpandedQuery:
    """
    Expand a broad query into multiple targeted sub-queries.

    Returns the original query plus additional sub-queries when patterns
    match. When no patterns match, returns only the original query with
    zero overhead.

    Args:
        query: The user's raw message/query
        config: Memory config (uses expanded_top_k)

    Returns:
        ExpandedQuery with sub-queries and recommended top_k
    """
    if not config.expansion_enabled:
        return ExpandedQuery(queries=[query], top_k=config.top_k, reason="none")

    # Check broad personal patterns first
    for pattern in _BROAD_PERSONAL_PATTERNS:
        if pattern.search(query):
            return ExpandedQuery(
                queries=[query] + _BROAD_PERSONAL_SUBQUERIES,
                top_k=config.expanded_top_k,
                reason="broad_personal",
            )

    # Check topic-scoped patterns
    for pattern, subqueries in _TOPIC_PATTERNS:
        if pattern.search(query):
            return ExpandedQuery(
                queries=[query] + subqueries,
                top_k=config.expanded_top_k,
                reason="topic_scoped",
            )

    # No match — return original only
    return ExpandedQuery(queries=[query], top_k=config.top_k, reason="none")
