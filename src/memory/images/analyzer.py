"""
Image Analyzer - Claude Vision analysis and Voyage multimodal embeddings.

Handles:
- Content moderation (check for policy violations before storage)
- Structured image analysis (description, tags, elements)
- Multimodal embeddings for semantic similarity
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic
import voyageai


# Analysis prompt for Minecraft screenshots
IMAGE_ANALYSIS_PROMPT = """
You are analyzing a Minecraft screenshot shared in the Minecraft College Discord community.

## Task
Analyze this image and provide:
1. A detailed description of what you see
2. A one-line summary suitable for search/retrieval
3. Relevant tags for categorization
4. Structured element detection
5. An observation type classification

## Output Format
Return a JSON object with these fields:

```json
{
  "description": "Detailed 2-3 sentence description of the image",
  "summary": "One-line summary (under 100 chars)",
  "tags": ["tag1", "tag2", "tag3"],
  "detected_elements": {
    "biome": "plains|forest|desert|nether|end|ocean|mountain|swamp|other",
    "time_of_day": "day|night|sunset|sunrise|unknown",
    "structures": ["tower", "wall", "house", "farm", "bridge"],
    "materials": ["stone", "wood", "glass", "concrete"],
    "style": "medieval|modern|rustic|futuristic|organic|other",
    "completion_stage": "foundation|early|mid|late|complete|unknown"
  },
  "observation_type": "build_progress|landscape|redstone|farm|other"
}
```

## Guidelines
- Focus on Minecraft-specific elements (blocks, structures, biomes)
- Note architectural style and building techniques
- Identify the apparent stage of construction if it's a build
- Be specific about materials and design choices
- If this appears to be a continuation of a previous build, note distinguishing features

Analyze the provided image:
"""

# Content moderation prompt
CONTENT_MODERATION_PROMPT = """
You are a content moderation system. Analyze this image for policy violations.

## Check For
1. **NSFW content**: Nudity, sexual content, suggestive imagery
2. **Violence**: Gore, graphic violence, harm to people/animals
3. **Illegal content**: Drug use, weapons in threatening context, CSAM indicators
4. **Harassment**: Targeted harassment, doxxing, personal information exposure
5. **Spam/Scam**: Phishing, scam content, malicious links in screenshots

## Output Format
Return JSON:

```json
{
  "is_safe": true|false,
  "confidence": 0.0-1.0,
  "flags": [],
  "violation_type": null|"nsfw"|"violence"|"illegal"|"harassment"|"spam",
  "description": "Brief description of violation if any, or 'No policy violations detected'"
}
```

## Guidelines
- Minecraft violence (combat, mobs) is ALLOWED
- Pixel art should be evaluated for content, not dismissed as "just pixels"
- When uncertain, flag for review rather than auto-approving
- Provide enough description for human moderators to understand without seeing the image

Analyze this image:
"""


@dataclass
class ImageAnalysisConfig:
    """Configuration for image analysis."""

    # Claude model for vision
    vision_model: str = "claude-sonnet-4-5-20250929"

    # Voyage model for multimodal embeddings
    embedding_model: str = "voyage-multimodal-3"
    embedding_dimensions: int = 1024

    # Analysis settings
    max_image_size_mb: int = 10
    supported_formats: tuple = ("png", "jpg", "jpeg", "gif", "webp")

    # Moderation thresholds
    nsfw_threshold: float = 0.7
    violence_threshold: float = 0.8
    require_human_review: float = 0.5


@dataclass
class AnalysisResult:
    """Result of image analysis."""

    description: str
    summary: str
    tags: list[str]
    detected_elements: dict
    observation_type: str
    embedding: list[float]
    file_hash: str


@dataclass
class ModerationResult:
    """Result of content moderation check."""

    is_safe: bool
    confidence: float
    flags: list[str]
    violation_type: Optional[str]
    description: str


class ImageAnalyzer:
    """Analyzes images using Claude Vision and generates Voyage embeddings."""

    def __init__(
        self,
        anthropic_client: AsyncAnthropic,
        voyage_client: Optional[voyageai.AsyncClient] = None,
        config: Optional[ImageAnalysisConfig] = None,
    ):
        self.anthropic = anthropic_client
        self.voyage = voyage_client or voyageai.AsyncClient()
        self.config = config or ImageAnalysisConfig()

    async def analyze(self, image_bytes: bytes, media_type: str) -> AnalysisResult:
        """
        Full analysis: description, tags, elements, embedding.

        Args:
            image_bytes: Raw image data
            media_type: MIME type (e.g., "image/png")

        Returns:
            AnalysisResult with all extracted information
        """
        # Generate file hash for deduplication
        file_hash = hashlib.sha256(image_bytes).hexdigest()

        # Get Claude vision analysis
        analysis = await self._get_vision_analysis(image_bytes, media_type)

        # Get Voyage multimodal embedding
        embedding = await self._get_embedding(image_bytes)

        return AnalysisResult(
            description=analysis.get("description", "No description available"),
            summary=analysis.get("summary", "Image observation"),
            tags=analysis.get("tags", []),
            detected_elements=analysis.get("detected_elements", {}),
            observation_type=analysis.get("observation_type", "unknown"),
            embedding=embedding,
            file_hash=file_hash,
        )

    async def moderate(self, image_bytes: bytes, media_type: str) -> ModerationResult:
        """
        Check image for policy violations.

        Args:
            image_bytes: Raw image data
            media_type: MIME type

        Returns:
            ModerationResult indicating safety status
        """
        base64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        response = await self.anthropic.messages.create(
            model=self.config.vision_model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image,
                            },
                        },
                        {"type": "text", "text": CONTENT_MODERATION_PROMPT},
                    ],
                }
            ],
        )

        result = self._parse_json_response(response.content[0].text)

        return ModerationResult(
            is_safe=result.get("is_safe", False),
            confidence=result.get("confidence", 0.0),
            flags=result.get("flags", []),
            violation_type=result.get("violation_type"),
            description=result.get("description", "Analysis failed"),
        )

    async def _get_vision_analysis(
        self, image_bytes: bytes, media_type: str
    ) -> dict:
        """Get structured analysis from Claude Vision."""
        base64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        response = await self.anthropic.messages.create(
            model=self.config.vision_model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image,
                            },
                        },
                        {"type": "text", "text": IMAGE_ANALYSIS_PROMPT},
                    ],
                }
            ],
        )

        return self._parse_json_response(response.content[0].text)

    async def _get_embedding(self, image_bytes: bytes) -> list[float]:
        """
        Get Voyage multimodal embedding for the image.

        Uses voyage-multimodal-3 which accepts base64 images directly.
        """
        base64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        result = await self.voyage.multimodal_embed(
            inputs=[[{"type": "image", "data": base64_image}]],
            model=self.config.embedding_model,
        )

        return result.embeddings[0]

    async def get_text_embedding(self, text: str, input_type: str = "document") -> list[float]:
        """
        Get text embedding using the text model.

        Used for querying image observations by text description.
        """
        result = await self.voyage.embed(
            [text],
            model="voyage-3.5-lite",
            input_type=input_type,
        )
        return result.embeddings[0]

    def _parse_json_response(self, response_text: str) -> dict:
        """Extract JSON from Claude response, handling markdown code blocks."""
        text = response_text.strip()

        # Handle markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
                # Remove language identifier if present
                if text.startswith(("\n", "json")):
                    lines = text.split("\n", 1)
                    text = lines[1] if len(lines) > 1 else ""

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            # Return empty dict on parse failure, caller handles defaults
            return {}
