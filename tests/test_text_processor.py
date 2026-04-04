# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for voice text preprocessing and emotion inference."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice.text_processor import EmotionInference, TextPreprocessor


class TestCleanForTts:
    def test_strips_emotes(self):
        assert TextPreprocessor.clean_for_tts("Hello *waves excitedly*") == "Hello"

    def test_strips_discord_custom_emotes(self):
        assert TextPreprocessor.clean_for_tts("Cool <:fire:123456>") == "Cool"

    def test_strips_emoji(self):
        result = TextPreprocessor.clean_for_tts("Hello \U0001F600 world")
        assert "\U0001F600" not in result
        assert "Hello" in result and "world" in result

    def test_converts_laughter(self):
        assert "[laughter]" in TextPreprocessor.clean_for_tts("That was so funny hahaha")
        assert "[laughter]" in TextPreprocessor.clean_for_tts("lol that's great")
        assert "[laughter]" in TextPreprocessor.clean_for_tts("LMAO")

    def test_strips_slang(self):
        result = TextPreprocessor.clean_for_tts("tbh I think ngl it's fine idk")
        assert "tbh" not in result.lower()
        assert "ngl" not in result.lower()
        assert "idk" not in result.lower()
        assert "think" in result
        assert "fine" in result

    def test_truncates_long_text(self):
        long_text = " ".join(f"word{i}" for i in range(100))
        result = TextPreprocessor.clean_for_tts(long_text, max_words=50)
        assert len(result.split()) == 50

    def test_strips_urls(self):
        result = TextPreprocessor.clean_for_tts("Check https://example.com for info")
        assert "https" not in result
        assert "Check" in result
        assert "info" in result

    def test_strips_markdown(self):
        result = TextPreprocessor.clean_for_tts("This is **bold** and ~~struck~~")
        assert "**" not in result
        assert "~~" not in result
        assert "bold" in result

    def test_collapses_whitespace(self):
        result = TextPreprocessor.clean_for_tts("hello    world\n\nfoo")
        assert "  " not in result

    def test_empty_input(self):
        assert TextPreprocessor.clean_for_tts("") == ""
        assert TextPreprocessor.clean_for_tts(None) == ""  # type: ignore


class TestChunkForTts:
    def test_short_text_single_chunk(self):
        assert TextPreprocessor.chunk_for_tts("Hello world.") == ["Hello world."]

    def test_empty_input(self):
        assert TextPreprocessor.chunk_for_tts("") == []

    def test_splits_at_sentence_boundary(self):
        text = "First sentence. " + "A" * 190
        chunks = TextPreprocessor.chunk_for_tts(text, max_chars=200)
        assert len(chunks) >= 2
        assert chunks[0] == "First sentence."

    def test_all_chunks_within_limit(self):
        text = ". ".join(f"Sentence number {i} with some extra words" for i in range(20))
        chunks = TextPreprocessor.chunk_for_tts(text, max_chars=200)
        for chunk in chunks:
            assert len(chunk) <= 200

    def test_preserves_all_content(self):
        text = "Hello world. This is a test. More text here."
        chunks = TextPreprocessor.chunk_for_tts(text, max_chars=20)
        reconstructed = " ".join(chunks)
        # All words should be present
        for word in text.split():
            assert word.rstrip(".") in reconstructed


class TestEmotionInference:
    def test_detects_scared(self):
        assert EmotionInference.infer("That creeper is terrifying!") == "scared"

    def test_detects_excited(self):
        assert EmotionInference.infer("That's awesome and amazing!") == "excited"

    def test_detects_sad(self):
        assert EmotionInference.infer("Sorry, I unfortunately failed") == "sad"

    def test_detects_angry(self):
        assert EmotionInference.infer("Stop! That's terrible and annoying") == "angry"

    def test_detects_curious(self):
        assert EmotionInference.infer("Hmm, that's interesting, I wonder why") == "curious"

    def test_neutral_text(self):
        assert EmotionInference.infer("The weather is fine today") is None

    def test_case_insensitive(self):
        assert EmotionInference.infer("THAT IS AWESOME") == "excited"

    def test_empty_input(self):
        assert EmotionInference.infer("") is None
        assert EmotionInference.infer(None) is None  # type: ignore

    def test_best_match_wins(self):
        # "awesome amazing wonderful" has 3 excited keywords vs 1 scared
        result = EmotionInference.infer("awesome amazing wonderful but scary")
        assert result == "excited"
