# slashAI - Title Progression Evaluator
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Title Progression Evaluator

Determines when players should be granted titles based on:
- Recognized build count
- Quality trajectory
- Style development
- Community impact
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .api import PlayerProfile
from .analyzer import BuildAnalysis

logger = logging.getLogger(__name__)


@dataclass
class TitleRecommendation:
    """Recommendation for title grant"""

    title_slug: str
    title_name: str
    confidence: float
    reason: str


# Title progression thresholds
BUILDING_TITLES = {
    "first-build": {
        "name": "First Build",
        "tier": "entry",
        "min_recognized": 1,
    },
    "apprentice-builder": {
        "name": "Apprentice Builder",
        "tier": "bronze",
        "min_recognized": 3,
    },
    "journeyman-builder": {
        "name": "Journeyman Builder",
        "tier": "silver",
        "min_recognized": 7,
        "requires_style": True,
    },
    "master-builder": {
        "name": "Master Builder",
        "tier": "gold",
        "min_recognized": 15,
        "requires_excellence": True,
    },
}


class TitleProgressionEvaluator:
    """Evaluates players for title grants based on progression"""

    def evaluate(
        self,
        player_profile: PlayerProfile,
        latest_analysis: Optional[BuildAnalysis] = None,
    ) -> Optional[TitleRecommendation]:
        """
        Evaluate if a player should receive a new title.

        Args:
            player_profile: Player's current recognition profile
            latest_analysis: Optional analysis of their latest submission

        Returns:
            TitleRecommendation if a new title should be granted, None otherwise
        """
        # Get current earned title slugs
        earned_slugs = {t.get("slug") for t in player_profile.earned_titles}

        # Check each title in order of prestige
        recognized = player_profile.recognized_builds

        for slug, requirements in BUILDING_TITLES.items():
            # Skip if already earned
            if slug in earned_slugs:
                continue

            # Check minimum recognized builds
            if recognized < requirements["min_recognized"]:
                continue

            # Check style development requirement
            if requirements.get("requires_style"):
                if not self._has_style_development(player_profile, latest_analysis):
                    logger.debug(
                        f"{player_profile.uuid}: Meets count for {slug} but "
                        "style development not yet evident"
                    )
                    continue

            # Check excellence requirement
            if requirements.get("requires_excellence"):
                if not self._has_excellence(player_profile, latest_analysis):
                    logger.debug(
                        f"{player_profile.uuid}: Meets count for {slug} but "
                        "excellence pattern not established"
                    )
                    continue

            # All requirements met
            return TitleRecommendation(
                title_slug=slug,
                title_name=requirements["name"],
                confidence=self._calculate_confidence(
                    recognized, requirements, latest_analysis
                ),
                reason=self._format_reason(recognized, requirements),
            )

        return None

    def _has_style_development(
        self,
        profile: PlayerProfile,
        analysis: Optional[BuildAnalysis],
    ) -> bool:
        """Check if player shows style development"""
        # For now, rely on the latest analysis style consistency note
        if analysis and analysis.style_consistency:
            # Style consistency noted = developing personal style
            return True

        # Could also check historical submissions for pattern
        # (Would require API to return submission history)

        return False

    def _has_excellence(
        self,
        profile: PlayerProfile,
        analysis: Optional[BuildAnalysis],
    ) -> bool:
        """Check if player shows consistent excellence"""
        # Require high technical score on recent submission
        if analysis and analysis.technical_score >= 0.8:
            return True

        # Could check average technical score across recent submissions
        # (Would require historical data)

        return False

    def _calculate_confidence(
        self,
        recognized: int,
        requirements: dict,
        analysis: Optional[BuildAnalysis],
    ) -> float:
        """Calculate confidence in the title grant"""
        base_confidence = 0.7

        # Boost if clearly above threshold
        min_req = requirements["min_recognized"]
        if recognized >= min_req * 1.5:
            base_confidence += 0.1

        # Boost if latest analysis is strong
        if analysis and analysis.technical_score >= 0.8:
            base_confidence += 0.1

        return min(base_confidence, 0.95)

    def _format_reason(self, recognized: int, requirements: dict) -> str:
        """Format human-readable reason for title grant"""
        min_req = requirements["min_recognized"]

        if requirements.get("requires_style"):
            return (
                f"Reached {recognized} recognized builds with developing "
                "personal style"
            )
        elif requirements.get("requires_excellence"):
            return (
                f"Reached {recognized} recognized builds with consistent "
                "excellence"
            )
        else:
            return f"Reached {recognized} recognized builds (threshold: {min_req})"


def evaluate_progression(
    player_profile: PlayerProfile,
    latest_analysis: Optional[BuildAnalysis] = None,
) -> Optional[TitleRecommendation]:
    """Convenience function to evaluate title progression"""
    evaluator = TitleProgressionEvaluator()
    return evaluator.evaluate(player_profile, latest_analysis)
