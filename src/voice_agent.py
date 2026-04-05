# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Standalone voice-capable agent runner.

Runs a single persona agent with voice channel support and memory.
Designed for deployment on infrastructure that supports UDP (e.g., DO Droplet),
separate from the main slashAI bot on App Platform (which lacks UDP).

Usage:
    python src/voice_agent.py              # Loads from personas/ + AGENT_*_TOKEN
    PERSONA=lena python src/voice_agent.py # Run a specific persona
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ensure src/ is on the path for imports
sys.path.insert(0, str(Path(__file__).parent))

from agents.agent_client import AgentClient
from agents.persona_loader import PersonaConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voice_agent")


async def _init_memory():
    """Initialize memory system if DATABASE_URL and VOYAGE_API_KEY are set."""
    db_url = os.getenv("DATABASE_URL")
    voyage_key = os.getenv("VOYAGE_API_KEY")
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not (db_url and voyage_key and api_key):
        missing = []
        if not db_url:
            missing.append("DATABASE_URL")
        if not voyage_key:
            missing.append("VOYAGE_API_KEY")
        if not api_key:
            missing.append("ANTHROPIC_API_KEY")
        logger.info(f"Memory disabled (missing: {', '.join(missing)})")
        return None, None

    try:
        import asyncpg
        from anthropic import AsyncAnthropic
        from memory.manager import MemoryManager

        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
        anthropic_client = AsyncAnthropic(api_key=api_key)
        memory_manager = MemoryManager(pool, anthropic_client)
        logger.info("Memory system initialized for voice agent")
        return memory_manager, pool
    except Exception as e:
        logger.error(f"Failed to initialize memory: {e}")
        return None, None


async def main():
    persona_name = os.getenv("PERSONA", "").lower()
    personas_dir = Path(__file__).parent.parent / "personas"

    if not personas_dir.exists():
        logger.error(f"Personas directory not found: {personas_dir}")
        sys.exit(1)

    # Load personas
    all_personas = PersonaConfig.load_all(personas_dir)
    if not all_personas:
        logger.error("No persona files found")
        sys.exit(1)

    # Filter to specific persona if requested
    if persona_name:
        if persona_name not in all_personas:
            logger.error(
                f"Persona '{persona_name}' not found. "
                f"Available: {', '.join(all_personas.keys())}"
            )
            sys.exit(1)
        personas = {persona_name: all_personas[persona_name]}
    else:
        personas = all_personas

    # Initialize memory system
    memory_manager, db_pool = await _init_memory()

    # Start agents that have tokens
    tasks = []
    for name, persona in personas.items():
        token_env = f"AGENT_{name.upper()}_TOKEN"
        token = os.getenv(token_env)
        if not token:
            logger.info(f"Skipping '{name}': no {token_env} env var")
            continue

        client = AgentClient(persona, memory_manager=memory_manager)
        task = asyncio.create_task(client.start(token), name=f"voice-agent-{name}")
        tasks.append(task)
        logger.info(
            f"Started voice agent '{persona.display_name}' "
            f"(agent_id={persona.memory.agent_id}, memory={'enabled' if memory_manager else 'disabled'})"
        )

    if not tasks:
        logger.error("No agent tokens configured")
        sys.exit(1)

    logger.info("Voice agent(s) running. UDP voice support enabled.")

    # Wait for all tasks (they run forever until interrupted)
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    finally:
        if db_pool:
            await db_pool.close()
            logger.info("Database pool closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Voice agent stopped")
