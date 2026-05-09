# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Process-level configuration for the proactive subsystem.

Per-persona policy lives in `personas/*.json` (parsed by persona_loader).
This module exposes the global env-driven kill switches and defaults.
"""

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class GlobalProactiveConfig:
    """Global gates that apply to every persona's scheduler."""
    enabled: bool                              # master kill switch (PROACTIVE_ENABLED)
    shadow_mode: bool                          # actor is no-op when true (PROACTIVE_SHADOW_MODE)
    heartbeat_interval_seconds: int            # PROACTIVE_HEARTBEAT_INTERVAL_SECONDS
    cross_persona_lockout_seconds: int         # PROACTIVE_CROSS_PERSONA_LOCKOUT_SECONDS
    decider_model_default: str                 # PROACTIVE_DECIDER_MODEL
    actor_model_default: str                   # PROACTIVE_ACTOR_MODEL

    @classmethod
    def from_env(cls) -> "GlobalProactiveConfig":
        return cls(
            enabled=_bool_env("PROACTIVE_ENABLED", False),
            shadow_mode=_bool_env("PROACTIVE_SHADOW_MODE", True),
            heartbeat_interval_seconds=_int_env("PROACTIVE_HEARTBEAT_INTERVAL_SECONDS", 3600),
            cross_persona_lockout_seconds=_int_env("PROACTIVE_CROSS_PERSONA_LOCKOUT_SECONDS", 5),
            decider_model_default=os.getenv(
                "PROACTIVE_DECIDER_MODEL", "claude-haiku-4-5-20251001"
            ),
            actor_model_default=os.getenv(
                "PROACTIVE_ACTOR_MODEL", "claude-sonnet-4-6"
            ),
        )
