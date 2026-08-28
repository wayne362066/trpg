"""Minimal JSON-backed multiplayer TRPG GM core."""

from .engine import GameEngine
from .llm import FakeLlmClient, LocalLlmClient

__all__ = ["GameEngine", "FakeLlmClient", "LocalLlmClient"]
