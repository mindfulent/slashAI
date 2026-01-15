# slashAI Recognition Extension
# Core Curriculum integration for AI-assisted build reviews

"""
Recognition Extension for slashAI

This module provides build review capabilities for The Block Academy's
Core Curriculum recognition system. It handles:

1. Build Analysis - Vision-based analysis of submitted Minecraft screenshots
2. Feedback Generation - Constructive feedback for craft development
3. Title Progression - Evaluating players for title grants
4. Nomination Review - Anti-gaming checks for peer nominations

API Integration:
- Receives submissions from theblockacademy Recognition API
- Sends analysis results via webhook callbacks
- Polls for pending items if webhooks fail
"""

from .analyzer import BuildAnalyzer
from .feedback import FeedbackGenerator
from .progression import TitleProgressionEvaluator
from .nominations import NominationReviewer
from .api import RecognitionAPIClient
from .scheduler import RecognitionScheduler

__all__ = [
    "BuildAnalyzer",
    "FeedbackGenerator",
    "TitleProgressionEvaluator",
    "NominationReviewer",
    "RecognitionAPIClient",
    "RecognitionScheduler",
]

__version__ = "0.1.0"
