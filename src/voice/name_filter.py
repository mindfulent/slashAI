# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Name-address filter for multi-participant voice channels.

When multiple humans share a voice channel with the bot, only utterances
that contain the persona's name (or a recognised alias) are forwarded
to the LLM. In 1-on-1 sessions every utterance passes through.
"""

import re
from typing import Sequence


class NameFilter:
    """Decides whether a voice transcript is addressed to the bot."""

    def __init__(self, display_name: str, aliases: Sequence[str] = ()):
        # Build the set of names to match (all lowercased).
        # display_name is always included automatically.
        names: set[str] = {display_name.lower()}
        for alias in aliases:
            stripped = alias.strip().lower()
            if stripped:
                names.add(stripped)

        # Pre-compile a single regex: \b(name1|name2|...)\b
        # Sorted longest-first so "Elena" matches before "Lena".
        escaped = [re.escape(n) for n in sorted(names, key=len, reverse=True)]
        self._pattern = re.compile(
            r"\b(" + "|".join(escaped) + r")\b",
            re.IGNORECASE,
        )
        self._names = names

    def is_addressed(self, transcript: str) -> bool:
        """Return True if the transcript contains any recognised name."""
        return bool(self._pattern.search(transcript))

    @property
    def names(self) -> frozenset[str]:
        """The set of names being matched (lowercase)."""
        return frozenset(self._names)
