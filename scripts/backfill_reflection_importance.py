# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Backfill reflection importance scores (Enhancement 015 / v0.16.5).

The heartbeat-time reflection job scores `proactive_actions.importance` in
small batches (default 20/tick) so it doesn't blow out a single heartbeat.
That's fine for steady-state, but on first deploy of band 4 there can be a
backlog of unscored rows. This script lets the operator score them in one
pass with explicit cost controls.

Usage:
    # Show what would happen, no API calls, no DB writes
    python scripts/backfill_reflection_importance.py --dry-run

    # Score every unscored row for slashai (default persona)
    python scripts/backfill_reflection_importance.py

    # Limit to 100 rows, all personas
    python scripts/backfill_reflection_importance.py --max-rows 100 --all-personas

    # Score for a specific persona
    python scripts/backfill_reflection_importance.py --persona lena

Environment:
    DATABASE_URL          required
    ANTHROPIC_API_KEY     required (unless --dry-run)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import asyncpg

# Allow `from proactive.reflection import ...` when run from repo root
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("backfill")


async def _count_unscored(pool: asyncpg.Pool, persona_id: Optional[str]) -> int:
    if persona_id:
        return await pool.fetchval(
            """
            SELECT COUNT(*)::INT FROM proactive_actions
            WHERE persona_id = $1 AND importance IS NULL AND decision != 'none'
            """,
            persona_id,
        )
    return await pool.fetchval(
        """
        SELECT COUNT(*)::INT FROM proactive_actions
        WHERE importance IS NULL AND decision != 'none'
        """
    )


async def _list_personas_with_unscored(pool: asyncpg.Pool) -> list[tuple[str, int]]:
    rows = await pool.fetch(
        """
        SELECT persona_id, COUNT(*)::INT AS n
        FROM proactive_actions
        WHERE importance IS NULL AND decision != 'none'
        GROUP BY persona_id
        ORDER BY n DESC
        """
    )
    return [(r["persona_id"], r["n"]) for r in rows]


async def main_async(args: argparse.Namespace) -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        return 1

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        # Resolve which personas to backfill
        if args.all_personas:
            personas = [p for p, _ in await _list_personas_with_unscored(pool)]
            if not personas:
                logger.info("No unscored proactive_actions across any persona.")
                return 0
        else:
            personas = [args.persona]

        # Pre-flight summary
        for p in personas:
            n = await _count_unscored(pool, p)
            logger.info(f"persona={p} unscored={n}")

        if args.dry_run:
            logger.info("--dry-run set; exiting before API calls.")
            return 0

        # Lazy import so --dry-run can run in environments without anthropic
        try:
            import anthropic
        except ImportError:
            logger.error("anthropic SDK not installed. pip install anthropic")
            return 1

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY not set")
            return 1

        from proactive.reflection import ReflectionEngine

        client = anthropic.AsyncAnthropic(api_key=api_key)
        engine = ReflectionEngine(pool)

        total_scored = 0
        for persona_id in personas:
            scored_for_persona = 0
            t0 = time.time()
            while True:
                if args.max_rows is not None and total_scored >= args.max_rows:
                    logger.info(f"Reached --max-rows={args.max_rows}; stopping.")
                    return 0

                # Bound batch by remaining max-rows budget
                remaining = (args.max_rows - total_scored) if args.max_rows is not None else args.batch_size
                batch_size = min(args.batch_size, remaining)
                if batch_size <= 0:
                    break

                n = await engine.score_unscored_actions(
                    persona_id, anthropic_client=client, batch_size=batch_size
                )
                if n == 0:
                    break

                total_scored += n
                scored_for_persona += n
                elapsed = time.time() - t0
                rate = scored_for_persona / elapsed if elapsed > 0 else 0
                logger.info(
                    f"persona={persona_id} scored_this_run={scored_for_persona} "
                    f"total={total_scored} rate={rate:.2f}/s"
                )

        logger.info(f"Done. Total scored: {total_scored}")
        return 0
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--persona", default="slashai",
        help="Persona to backfill (default: slashai). Ignored if --all-personas.",
    )
    parser.add_argument(
        "--all-personas", action="store_true",
        help="Backfill every persona that has unscored actions.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=20,
        help="Rows per Anthropic batch (default 20).",
    )
    parser.add_argument(
        "--max-rows", type=int, default=None,
        help="Stop after scoring this many rows total (across all personas).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Just count unscored rows; no API calls, no DB writes.",
    )
    args = parser.parse_args()

    rc = asyncio.run(main_async(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
