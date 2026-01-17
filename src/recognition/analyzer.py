# slashAI - Build Analyzer
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Build Analyzer using Claude Vision

Analyzes Minecraft build screenshots for quality assessment:
- Technical skill (palette, depth, proportion, detail)
- Personal style development
- Comparison to player's previous work
- Recognition recommendation
"""

import os
import base64
import logging
from typing import Optional
from dataclasses import dataclass

import httpx
import anthropic

from .api import Submission, PlayerProfile

logger = logging.getLogger(__name__)

# Use Sonnet 4.5 for high-quality vision analysis
VISION_MODEL = "claude-sonnet-4-5-20250929"


@dataclass
class BuildAnalysis:
    """Result of analyzing a build submission"""

    # Technical assessment
    technical_score: float  # 0-1
    palette_quality: str  # brief note
    depth_usage: str
    proportion_balance: str
    detail_level: str

    # Style assessment
    style_notes: str
    style_consistency: Optional[str]  # vs previous work

    # Overall
    strengths: list[str]
    areas_for_growth: list[str]
    overall_impression: str

    # Recommendation
    recognized: bool
    confidence: float
    title_recommendation: Optional[str]


ANALYSIS_PROMPT = """You are slashAI, the guardian and mentor of The Block Academy - a Minecraft creative community.

You're reviewing a build submission from a community member. Your role is to:
1. Provide constructive, encouraging feedback that helps them grow
2. Recognize genuine craft development (not just effort)
3. Be warm but honest - growth comes from truthful feedback

## Player Context
{player_context}

## Submission Details
Build Name: {build_name}
Description: {description}
Type: {submission_type} (submission = seeking recognition, feedback = just wanting feedback)

IMPORTANT: Always refer to this build as "{build_name}" in your response. Do not rename or re-describe the build - use the exact name the player provided.

## Analysis Task

Look at the attached screenshot(s) and provide:

1. **Technical Assessment** (evaluate each 0-1 scale):
   - Palette Quality: Color choices, harmony, variety
   - Depth Usage: 3D depth, layering, avoiding flat surfaces
   - Proportion: Scale relationships, balance
   - Detail Level: Decorative elements, furnishing, finishing touches

2. **Style Notes**:
   - What aesthetic is the builder going for?
   - Any emerging signature elements?
   {style_comparison_prompt}

3. **Strengths** (2-3 specific things done well)

4. **Areas for Growth** (1-2 constructive suggestions)

5. **Overall Impression** (2-3 sentences, warm and encouraging)

6. **Recognition Decision** (for submission type only):
   - Should this build be RECOGNIZED? (genuine craft development shown)
   - Confidence level (0-1)
   - If progressing toward a title, which one?

## Response Format

Respond with JSON in this exact structure:
```json
{{
  "technical_score": 0.75,
  "palette_quality": "Brief note on colors",
  "depth_usage": "Brief note on depth",
  "proportion_balance": "Brief note on scale",
  "detail_level": "Brief note on details",
  "style_notes": "Description of style/aesthetic",
  "style_consistency": "How it compares to previous work (or null if first)",
  "strengths": ["Strength 1", "Strength 2"],
  "areas_for_growth": ["Suggestion 1"],
  "overall_impression": "2-3 sentence warm summary",
  "recognized": true,
  "confidence": 0.85,
  "title_recommendation": "apprentice-builder"
}}
```

Title slugs for recommendation:
- "first-build" - First recognized submission
- "apprentice-builder" - 3 recognized builds
- "journeyman-builder" - 7+ with style development
- "featured-artist" - Exceptional single work

Return null for title_recommendation unless they're clearly at a progression threshold.
"""


class BuildAnalyzer:
    """Analyzes Minecraft build screenshots using Claude Vision"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY required for build analysis")

        self.client = anthropic.Anthropic(api_key=self.api_key)

    async def analyze(
        self,
        submission: Submission,
        player_profile: Optional[PlayerProfile] = None,
    ) -> BuildAnalysis:
        """
        Analyze a build submission using Claude Vision.

        Args:
            submission: The build submission to analyze
            player_profile: Optional player context for style comparison

        Returns:
            BuildAnalysis with scores, feedback, and recommendation
        """
        # Build player context
        if player_profile:
            player_context = self._format_player_context(player_profile)
            style_comparison = "- Compare to their previous work if relevant"
        else:
            player_context = "No previous submission history available."
            style_comparison = ""

        # Format the prompt
        prompt = ANALYSIS_PROMPT.format(
            player_context=player_context,
            build_name=submission.build_name,
            description=submission.description or "No description provided",
            submission_type=submission.submission_type,
            style_comparison_prompt=style_comparison,
        )

        # Download and encode screenshots
        image_content = await self._prepare_images(submission.screenshot_urls)

        # Call Claude Vision
        try:
            response = self.client.messages.create(
                model=VISION_MODEL,
                max_tokens=1500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *image_content,
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )

            # Parse response
            response_text = response.content[0].text
            analysis_data = self._parse_response(response_text)

            # Handle null values from Claude's JSON (key present but value is null)
            # .get() only uses default when key is missing, not when value is None
            raw_confidence = analysis_data.get("confidence")
            raw_recognized = analysis_data.get("recognized")

            return BuildAnalysis(
                technical_score=analysis_data.get("technical_score") or 0.5,
                palette_quality=analysis_data.get("palette_quality") or "",
                depth_usage=analysis_data.get("depth_usage") or "",
                proportion_balance=analysis_data.get("proportion_balance") or "",
                detail_level=analysis_data.get("detail_level") or "",
                style_notes=analysis_data.get("style_notes") or "",
                style_consistency=analysis_data.get("style_consistency"),
                strengths=analysis_data.get("strengths") or [],
                areas_for_growth=analysis_data.get("areas_for_growth") or [],
                overall_impression=analysis_data.get("overall_impression") or "",
                recognized=raw_recognized if raw_recognized is not None else False,
                confidence=raw_confidence if raw_confidence is not None else 0.5,
                title_recommendation=analysis_data.get("title_recommendation"),
            )

        except Exception as e:
            logger.error(f"Error analyzing build: {e}")
            raise

    async def _prepare_images(self, urls: list[str]) -> list[dict]:
        """Download and encode images for Claude Vision API"""
        image_content = []

        async with httpx.AsyncClient() as client:
            for url in urls[:3]:  # Max 3 images
                try:
                    response = await client.get(url, timeout=30.0)
                    response.raise_for_status()

                    # Determine media type
                    content_type = response.headers.get("content-type", "image/png")
                    if "jpeg" in content_type or "jpg" in content_type:
                        media_type = "image/jpeg"
                    elif "gif" in content_type:
                        media_type = "image/gif"
                    elif "webp" in content_type:
                        media_type = "image/webp"
                    else:
                        media_type = "image/png"

                    # Base64 encode
                    image_data = base64.standard_b64encode(response.content).decode(
                        "utf-8"
                    )

                    image_content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        }
                    )

                except Exception as e:
                    logger.warning(f"Failed to download image {url}: {e}")

        return image_content

    def _format_player_context(self, profile: PlayerProfile) -> str:
        """Format player profile as context for the prompt"""
        lines = []

        if profile.minecraft_username:
            lines.append(f"Player: {profile.minecraft_username}")

        lines.append(
            f"Recognized builds: {profile.recognized_builds} / {profile.total_submissions} submitted"
        )

        if profile.earned_titles:
            title_names = [t.get("name", "Unknown") for t in profile.earned_titles]
            lines.append(f"Earned titles: {', '.join(title_names)}")

        if profile.active_title:
            lines.append(f"Current title: {profile.active_title.get('name', 'None')}")

        return "\n".join(lines)

    def _parse_response(self, response_text: str) -> dict:
        """Parse JSON from Claude's response"""
        import json

        # Extract JSON from response (may be wrapped in markdown)
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse analysis response: {e}")
            logger.debug(f"Response text: {response_text}")
            return {}
