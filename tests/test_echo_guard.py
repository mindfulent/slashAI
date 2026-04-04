# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for echo guard."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.echo_guard import EchoGuard


class TestEchoGuard:
    def test_temporal_rejection(self):
        guard = EchoGuard(temporal_margin_ms=500.0)
        guard.mark_bot_speaking(2.0)
        assert guard.should_reject("anything") is True

    def test_temporal_passes_after_window(self):
        guard = EchoGuard(temporal_margin_ms=0.0)
        guard.mark_bot_speaking(0.0)
        # With 0 duration and 0 margin, should pass immediately
        # (monotonic time has already advanced past the mark)
        time.sleep(0.01)
        assert guard.should_reject("anything") is False

    def test_content_rejection(self):
        guard = EchoGuard(similarity_threshold=0.7)
        guard.add_bot_text("hello world how are you today")
        assert guard.should_reject("hello world how are you today") is True

    def test_content_different_text_passes(self):
        guard = EchoGuard(similarity_threshold=0.7)
        guard.add_bot_text("hello world")
        assert guard.should_reject("something completely different") is False

    def test_content_partial_match_below_threshold(self):
        guard = EchoGuard(similarity_threshold=0.8)
        guard.add_bot_text("the quick brown fox jumps over the lazy dog")
        # Only a few overlapping words
        assert guard.should_reject("the brown cat jumps high") is False

    def test_similarity_calculation(self):
        # Identical
        assert EchoGuard._similarity("hello world", "hello world") == 1.0
        # No overlap
        assert EchoGuard._similarity("hello", "world") == 0.0
        # Partial overlap: {a, b} & {b, c} = {b}, union = {a, b, c} -> 1/3
        assert abs(EchoGuard._similarity("a b", "b c") - 1 / 3) < 0.01

    def test_empty_similarity(self):
        assert EchoGuard._similarity("", "") == 1.0
        assert EchoGuard._similarity("hello", "") == 0.0
        assert EchoGuard._similarity("", "world") == 0.0

    def test_recent_texts_capped(self):
        guard = EchoGuard()
        for i in range(10):
            guard.add_bot_text(f"message {i}")
        assert len(guard._recent_bot_texts) == 5  # maxlen=5
