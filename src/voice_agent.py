# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Standalone voice-capable agent runner.

Runs a single persona agent with voice channel support.
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

    # Start agents that have tokens
    tasks = []
    for name, persona in personas.items():
        token_env = f"AGENT_{name.upper()}_TOKEN"
        token = os.getenv(token_env)
        if not token:
            logger.info(f"Skipping '{name}': no {token_env} env var")
            continue

        client = AgentClient(persona)
        task = asyncio.create_task(client.start(token), name=f"voice-agent-{name}")
        tasks.append(task)
        logger.info(
            f"Started voice agent '{persona.display_name}' (agent_id={persona.memory.agent_id})"
        )

    if not tasks:
        logger.error("No agent tokens configured")
        sys.exit(1)

    logger.info(f"Voice agent(s) running. UDP voice support enabled.")

    # Wait for all tasks (they run forever until interrupted)
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutting down...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Voice agent stopped")
