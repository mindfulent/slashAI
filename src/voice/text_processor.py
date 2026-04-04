# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Text preprocessing and emotion inference for TTS.

Ported from SoulCraft's TextPreprocessor.java and EmotionInference.java.
"""

import re
from typing import Optional


class TextPreprocessor:
    """Clean and chunk text for TTS synthesis."""

    # Patterns compiled once
    _EMOTE_RE = re.compile(r"(?<!\*)\*(?!\*)[^*]+\*(?!\*)")
    _DISCORD_EMOTE_RE = re.compile(r"<a?:\w+:\d+>")
    _EMOJI_RE = re.compile(
        "["
        "\U0001F000-\U0001FFFF"  # Emoticons, symbols, etc.
        "\U00002600-\U000027BF"  # Misc symbols
        "\U0000FE00-\U0000FE0F"  # Variation selectors
        "\U0000200D"  # Zero-width joiner
        "\U000020E3"  # Combining enclosing keycap
        "\U000E0020-\U000E007F"  # Tags
        "]+",
        flags=re.UNICODE,
    )
    _LAUGHTER_RE = re.compile(r"\b(?:lol|lmao|rofl|haha(?:ha)*)\b", re.IGNORECASE)
    _SLANG_RE = re.compile(
        r"\b(?:lmfao|smh|tbh|imo|imho|brb|afk|gg|wp|ngl|idk|ikr|omw|ftw|fwiw)\b",
        re.IGNORECASE,
    )
    _URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
    _MARKDOWN_RE = re.compile(r"(\*{1,2}|__|~~|`{1,3}|^#{1,6}\s)", re.MULTILINE)
    _WHITESPACE_RE = re.compile(r"\s+")

    @classmethod
    def clean_for_tts(cls, text: str, max_words: int = 50) -> str:
        """Strip Discord formatting, emotes, emoji, slang, URLs; truncate."""
        if not text:
            return ""

        result = text
        # Strip action emotes *like this*
        result = cls._EMOTE_RE.sub("", result)
        # Strip Discord custom emotes <:name:id>
        result = cls._DISCORD_EMOTE_RE.sub("", result)
        # Strip emoji
        result = cls._EMOJI_RE.sub("", result)
        # Convert laughter
        result = cls._LAUGHTER_RE.sub("[laughter]", result)
        # Strip unspeakable slang
        result = cls._SLANG_RE.sub("", result)
        # Strip URLs
        result = cls._URL_RE.sub("", result)
        # Strip markdown formatting
        result = cls._MARKDOWN_RE.sub("", result)
        # Collapse whitespace
        result = cls._WHITESPACE_RE.sub(" ", result).strip()
        # Truncate to max_words
        words = result.split()
        if len(words) > max_words:
            result = " ".join(words[:max_words])

        return result

    @classmethod
    def chunk_for_tts(cls, text: str, max_chars: int = 200) -> list[str]:
        """Split text into chunks at sentence boundaries for streaming TTS."""
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        chunks: list[str] = []
        remaining = text

        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break

            # Find last sentence boundary before limit
            split_at = max_chars
            for sep in [". ", "! ", "? ", "; ", ", ", " "]:
                idx = remaining[:max_chars].rfind(sep)
                if idx > 0:
                    split_at = idx + len(sep)
                    break

            chunk = remaining[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].strip()

        return chunks


class EmotionInference:
    """Keyword-based emotion detection for Cartesia TTS emotion tags.

    Ported from SoulCraft's EmotionInference.java.
    """

    EMOTION_KEYWORDS: dict[str, list[str]] = {
        "scared": [
            "danger", "careful", "watch out", "run", "escape",
            "scared", "creeper", "behind you", "get out", "skeleton",
            "wither", "scary", "afraid", "terrifying", "frightening",
        ],
        "excited": [
            "awesome", "great", "perfect", "yes!", "love",
            "amazing", "nice", "excellent", "fantastic", "wonderful",
            "done!", "incredible", "brilliant",
        ],
        "sad": [
            "sorry", "unfortunately", "sad", "lost", "died",
            "failed", "gone", "destroyed", "burned", "despawned",
            "miss", "tragic",
        ],
        "angry": [
            "no!", "stop", "hate", "terrible", "worst",
            "annoying", "frustrating", "broke", "ruined", "furious",
        ],
        "curious": [
            "interesting", "wonder", "hmm", "curious", "strange",
            "what if", "maybe", "how about", "let's try", "odd",
        ],
    }

    @classmethod
    def infer(cls, text: str) -> Optional[str]:
        """Return Cartesia emotion string or None for neutral."""
        if not text:
            return None

        lower = text.lower()
        best_emotion: Optional[str] = None
        best_count = 0

        for emotion, keywords in cls.EMOTION_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in lower)
            if count > best_count:
                best_count = count
                best_emotion = emotion

        return best_emotion if best_count > 0 else None
