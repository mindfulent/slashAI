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
from typing import TYPE_CHECKING, Optional

from agents.agent_client import AgentClient
from agents.persona_loader import PersonaConfig

if TYPE_CHECKING:
    import anthropic
    import asyncpg

    from proactive.config import GlobalProactiveConfig

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages lifecycle of all agent Discord clients."""

    def __init__(
        self,
        memory_manager=None,
        db_pool: Optional["asyncpg.Pool"] = None,
        anthropic_client: Optional["anthropic.AsyncAnthropic"] = None,
        global_proactive_config: Optional["GlobalProactiveConfig"] = None,
        primary_bot=None,
    ):
        self.agents: dict[str, AgentClient] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.memory_manager = memory_manager
        self.db_pool = db_pool
        self.anthropic_client = anthropic_client
        self.global_proactive_config = global_proactive_config
        # Reference to the primary @slashAI bot so resolve_persona_user_id can
        # look up `slashai`'s Discord user.id (the primary isn't a key in
        # self.agents). Optional for graceful degradation.
        self.primary_bot = primary_bot

    def resolve_persona_user_id(self, persona_id: str) -> Optional[int]:
        """Look up a persona's Discord bot user.id, or None if not connected."""
        if persona_id == "slashai" and self.primary_bot is not None:
            user = getattr(self.primary_bot, "user", None)
            return int(user.id) if user is not None else None
        client = self.agents.get(persona_id)
        if client is not None and client.user is not None:
            return int(client.user.id)
        return None

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

        # All loaded persona names (used by ProactiveScheduler so each persona
        # knows who else exists in the server). Includes 'slashai' if a config
        # exists for the primary; that's expected — the primary's scheduler is
        # built separately, here we just enumerate names for context.
        all_persona_names = list(personas.keys())

        started = 0
        for name, persona in personas.items():
            token_env = f"AGENT_{name.upper()}_TOKEN"
            token = os.getenv(token_env)
            if not token:
                logger.info(f"Skipping agent '{name}': no {token_env} env var")
                continue

            client = AgentClient(persona, self.memory_manager)
            self.agents[name] = client

            # Attach proactive scheduler (Enhancement 015) if infra is available.
            self._attach_proactive(client, persona, all_persona_names)

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

    def _attach_proactive(
        self,
        client: AgentClient,
        persona: PersonaConfig,
        all_persona_names: list[str],
    ) -> None:
        """Wire up a ProactiveScheduler on the agent client if infra is ready."""
        if (
            self.db_pool is None
            or self.anthropic_client is None
            or self.global_proactive_config is None
        ):
            logger.debug(
                f"Proactive infra not available; '{persona.name}' will run reactive-only"
            )
            return

        try:
            from proactive.scheduler import ProactiveScheduler

            scheduler = ProactiveScheduler(
                persona=persona,
                bot=client,
                anthropic_client=self.anthropic_client,
                memory_manager=self.memory_manager,
                db_pool=self.db_pool,
                global_config=self.global_proactive_config,
                all_persona_names=all_persona_names,
                resolve_persona_user_id=self.resolve_persona_user_id,
            )
            client.proactive_scheduler = scheduler
            logger.info(
                f"[{persona.name}] proactive scheduler attached "
                f"(persona.proactive.enabled={persona.proactive.enabled})"
            )
        except Exception as e:
            logger.error(
                f"Failed to attach proactive scheduler for '{persona.name}': {e}",
                exc_info=True,
            )

    async def stop_all(self):
        """Gracefully stop all agent clients."""
        for name, client in self.agents.items():
            logger.info(f"Stopping agent '{name}'...")
            try:
                if client.proactive_scheduler is not None:
                    client.proactive_scheduler.stop()
            except Exception as e:
                logger.error(f"Error stopping proactive for '{name}': {e}")
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
