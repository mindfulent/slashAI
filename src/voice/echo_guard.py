# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Echo guard to prevent the bot from transcribing its own audio output.

Ported from SoulCraft's two-layer echo guard:
1. Temporal: Reject if bot was speaking within a margin
2. Content: Reject if transcript is similar to recent bot output
"""

import collections
import time


class EchoGuard:
    """Two-layer echo guard: temporal + content similarity."""

    def __init__(
        self,
        temporal_margin_ms: float = 500.0,
        similarity_threshold: float = 0.7,
    ):
        self._temporal_margin_ms = temporal_margin_ms
        self._similarity_threshold = similarity_threshold
        self._bot_speaking_until = 0.0
        self._recent_bot_texts: collections.deque[str] = collections.deque(maxlen=5)

    def mark_bot_speaking(self, duration_seconds: float) -> None:
        """Called when bot starts playing audio."""
        margin = self._temporal_margin_ms / 1000.0
        self._bot_speaking_until = time.monotonic() + duration_seconds + margin

    def add_bot_text(self, text: str) -> None:
        """Track what the bot said for content matching."""
        self._recent_bot_texts.append(text.lower().strip())

    def should_reject(self, transcript: str) -> bool:
        """Returns True if transcript is likely echo of bot's own speech."""
        # Layer 1: temporal check
        if time.monotonic() < self._bot_speaking_until:
            return True

        # Layer 2: content similarity
        normalized = transcript.lower().strip()
        for bot_text in self._recent_bot_texts:
            if self._similarity(normalized, bot_text) >= self._similarity_threshold:
                return True

        return False

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Word-level Jaccard similarity."""
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a and not words_b:
            return 1.0
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)
