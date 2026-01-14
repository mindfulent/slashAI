# slashAI - Nomination Reviewer
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Nomination Reviewer

Reviews peer nominations for anti-gaming patterns:
- Reciprocal nomination detection
- Vague or low-effort reasons
- Nomination brigading
- Genuine community recognition
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from .api import Nomination, RecognitionAPIClient

logger = logging.getLogger(__name__)

# Use Haiku for cost-effective text analysis
TEXT_MODEL = "claude-3-5-haiku-20241022"


@dataclass
class NominationReview:
    """Result of reviewing a nomination"""

    decision: str  # 'approved', 'flagged', 'rejected'
    notes: str
    confidence: float
    flags: list[str]  # Specific concerns identified


REVIEW_PROMPT = """You are slashAI, reviewing a peer nomination for The Block Academy recognition system.

## Nomination Details
- **Category:** {category}
- **Reason provided:** {reason}
- **Anonymous:** {anonymous}

## Context
- Nominator has made {nominator_recent_count} nominations recently
- Nominee has received {nominee_total_count} nominations total
- Is reciprocal nomination: {is_reciprocal}

## Review Task

Evaluate this nomination for:

1. **Genuineness** - Does the reason reflect authentic recognition?
2. **Specificity** - Are concrete examples or situations mentioned?
3. **Gaming patterns** - Any red flags for abuse?

## Decision Criteria

**APPROVE** if:
- Reason is specific and genuine
- No gaming patterns detected
- Aligns with the nomination category

**FLAG** if:
- Reason is vague but seems genuine (needs human review)
- Possible reciprocal nomination pattern
- Unusual patterns but not clearly abusive

**REJECT** if:
- Reason is completely generic or obviously fake
- Clear gaming pattern (brigading, coordinated)
- Doesn't match the nomination category

## Response Format

Respond with JSON:
```json
{{
  "decision": "approved",
  "notes": "Brief explanation for audit log",
  "confidence": 0.85,
  "flags": []
}}
```

For flags, use these identifiers:
- "vague_reason" - Reason lacks specificity
- "reciprocal" - Appears to be reciprocal nomination
- "low_effort" - Minimal effort in reason
- "category_mismatch" - Reason doesn't match category
- "brigading_suspected" - Pattern suggests coordinated nominations
"""


class NominationReviewer:
    """Reviews peer nominations for gaming patterns"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY required for nomination review")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.api_client: Optional[RecognitionAPIClient] = None

    async def review(
        self,
        nomination: Nomination,
        nominator_recent_count: int = 0,
        nominee_total_count: int = 0,
        is_reciprocal: bool = False,
    ) -> NominationReview:
        """
        Review a peer nomination.

        Args:
            nomination: The nomination to review
            nominator_recent_count: How many nominations the nominator made recently
            nominee_total_count: Total nominations the nominee has received
            is_reciprocal: Whether the nominee has recently nominated the nominator

        Returns:
            NominationReview with decision and notes
        """
        # Quick reject for obviously problematic cases
        if is_reciprocal:
            # Reciprocal nominations within 7 days are flagged
            return NominationReview(
                decision="flagged",
                notes="Reciprocal nomination detected - requires human review",
                confidence=0.9,
                flags=["reciprocal"],
            )

        if len(nomination.reason) < 30:
            return NominationReview(
                decision="flagged",
                notes="Reason is very brief - may lack specificity",
                confidence=0.7,
                flags=["vague_reason", "low_effort"],
            )

        # Use Claude for nuanced review
        prompt = REVIEW_PROMPT.format(
            category=nomination.category,
            reason=nomination.reason,
            anonymous="Yes" if nomination.anonymous else "No",
            nominator_recent_count=nominator_recent_count,
            nominee_total_count=nominee_total_count,
            is_reciprocal="Yes" if is_reciprocal else "No",
        )

        try:
            response = self.client.messages.create(
                model=TEXT_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = response.content[0].text
            review_data = self._parse_response(response_text)

            return NominationReview(
                decision=review_data.get("decision", "flagged"),
                notes=review_data.get("notes", "Review could not be completed"),
                confidence=review_data.get("confidence", 0.5),
                flags=review_data.get("flags", []),
            )

        except Exception as e:
            logger.error(f"Error reviewing nomination: {e}")
            # Default to flagging for human review on errors
            return NominationReview(
                decision="flagged",
                notes=f"Automated review failed: {str(e)}",
                confidence=0.0,
                flags=["review_error"],
            )

    def _parse_response(self, response_text: str) -> dict:
        """Parse JSON from Claude's response"""
        import json

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
            logger.error(f"Failed to parse nomination review: {e}")
            return {"decision": "flagged", "notes": "Parse error", "flags": ["parse_error"]}


# Convenience function
async def review_nomination(
    nomination: Nomination,
    nominator_recent_count: int = 0,
    nominee_total_count: int = 0,
    is_reciprocal: bool = False,
) -> NominationReview:
    """Review a peer nomination"""
    reviewer = NominationReviewer()
    return await reviewer.review(
        nomination,
        nominator_recent_count,
        nominee_total_count,
        is_reciprocal,
    )
