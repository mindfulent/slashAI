# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for voice name-address filter."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.name_filter import NameFilter


class TestNameFilter:
    def test_display_name_matches(self):
        nf = NameFilter("Lena")
        assert nf.is_addressed("hey Lena how are you") is True

    def test_case_insensitive(self):
        nf = NameFilter("Lena")
        assert nf.is_addressed("hey lena") is True
        assert nf.is_addressed("LENA help me") is True

    def test_alias_matches(self):
        nf = NameFilter("Lena", aliases=["Alina", "Elena"])
        assert nf.is_addressed("hey Alina") is True
        assert nf.is_addressed("Elena can you help") is True

    def test_no_match_returns_false(self):
        nf = NameFilter("Lena", aliases=["Alina"])
        assert nf.is_addressed("hey John what do you think") is False

    def test_word_boundary_prevents_substring(self):
        nf = NameFilter("Lena")
        assert nf.is_addressed("Helena is great") is False

    def test_name_mid_sentence(self):
        nf = NameFilter("Lena")
        assert nf.is_addressed("so I told Lena about it") is True

    def test_empty_transcript(self):
        nf = NameFilter("Lena")
        assert nf.is_addressed("") is False

    def test_empty_aliases_ignored(self):
        nf = NameFilter("Lena", aliases=["", "  "])
        assert len(nf.names) == 1  # only "lena"

    def test_display_name_always_included(self):
        nf = NameFilter("Lena", aliases=["Alina"])
        assert "lena" in nf.names

    def test_names_property_is_frozen(self):
        nf = NameFilter("Lena")
        assert isinstance(nf.names, frozenset)

    def test_no_aliases_works(self):
        nf = NameFilter("Bot")
        assert nf.is_addressed("hey Bot") is True
        assert nf.is_addressed("hello there") is False
