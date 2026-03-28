# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Manages lifecycle of all agent Discord clients.
Loads personas, starts clients for those with bot tokens, handles shutdown.
"""

import asyncio
import logging
import os
from pathlib import Path

from agents.agent_client import AgentClient
from agents.persona_loader import PersonaConfig

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages lifecycle of all agent Discord clients."""

    def __init__(self, memory_manager=None):
        self.agents: dict[str, AgentClient] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.memory_manager = memory_manager

    async def start_all(self):
        """Load all personas from personas/ dir and start clients with tokens."""
        personas_dir = Path("personas")
        if not personas_dir.exists():
            logger.info("No personas/ directory found, skipping agent startup")
            return

        personas = PersonaConfig.load_all(personas_dir)
        if not personas:
            logger.info("No persona files found in personas/")
            return

        started = 0
        for name, persona in personas.items():
            token_env = f"AGENT_{name.upper()}_TOKEN"
            token = os.getenv(token_env)
            if not token:
                logger.info(f"Skipping agent '{name}': no {token_env} env var")
                continue

            client = AgentClient(persona, self.memory_manager)
            self.agents[name] = client
            self.tasks[name] = asyncio.create_task(
                client.start(token),
                name=f"agent-{name}",
            )
            started += 1
            logger.info(
                f"Started agent '{persona.display_name}' "
                f"(token: {token_env}, agent_id: {persona.memory.agent_id})"
            )

        if started > 0:
            logger.info(f"Started {started} agent bot(s)")
        else:
            logger.info("No agent tokens configured, no agent bots started")

    async def stop_all(self):
        """Gracefully stop all agent clients."""
        for name, client in self.agents.items():
            logger.info(f"Stopping agent '{name}'...")
            try:
                await client.close()
            except Exception as e:
                logger.error(f"Error stopping agent '{name}': {e}")
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        self.agents.clear()
        self.tasks.clear()
        logger.info("All agent bots stopped")
