from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class LocalLlmClient(Protocol):
    """Adapter boundary for Ollama, LM Studio, llama.cpp, or a human GM."""

    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        ...


class FakeLlmClient:
    """Deterministic adapter used by the prototype and its tests."""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self._responses = list(responses or [])
        self._responder = responder
        self.contexts: list[dict[str, Any]] = []

    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        self.contexts.append(context)
        if self._responder is not None:
            return self._responder(context)
        if self._responses:
            return self._responses.pop(0)
        return {
            "narration": "GM 尚未接上本機模型，這是一個可重試的測試回應。",
            "public_messages": [],
            "private_messages": [],
            "changes": [],
            "requires_human_review": True,
            "review_reason": "FakeLlmClient 沒有實際判定能力",
        }
