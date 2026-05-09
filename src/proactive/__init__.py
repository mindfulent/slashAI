# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Proactive interaction subsystem (Enhancement 015).

Each persona runs its own ProactiveScheduler that decides — on heartbeat ticks
and on inbound messages — whether to react, reply, start a topic, or engage
another persona. All decisions are logged to `proactive_actions` whether or
not action is taken; the actor is gated by PROACTIVE_SHADOW_MODE in v0.14.0.
"""

from .scheduler import ProactiveScheduler

__all__ = ["ProactiveScheduler"]
