from __future__ import annotations

import json
import select
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class LocalLlmClient(Protocol):
    """Adapter boundary for Ollama, LM Studio, llama.cpp, or a human GM."""

    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        ...


class CodexAppServerClient:
    """Use one local Codex app-server thread as the authoritative GM.

    The game server remains the only writer. Codex receives a complete,
    already-scoped context and must return the structured result consumed by
    ``GameEngine.validate_llm_result``. The app-server process is deliberately
    kept on the GM host and is never exposed as a LAN endpoint.
    """

    OUTPUT_SCHEMA: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "narration": {"type": "string"},
            "public_messages": {"type": "array", "items": {"type": "string"}},
            "private_messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "player_id": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["player_id", "content"],
                },
            },
            "changes": {"type": "array", "items": {"type": "object"}},
            "requires_human_review": {"type": "boolean"},
            "review_reason": {"type": ["string", "null"]},
        },
        "required": [
            "narration",
            "public_messages",
            "private_messages",
            "changes",
            "requires_human_review",
            "review_reason",
        ],
    }

    def __init__(
        self,
        model: str | None = None,
        codex_bin: str = "codex",
        cwd: str | Path | None = None,
        timeout_seconds: float = 180.0,
    ):
        self.model = model
        self.codex_bin = codex_bin
        self.cwd = str(cwd) if cwd else None
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._thread_id: str | None = None
        self._request_id = 0
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RuntimeError("GM Codex app-server is not running")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read(self, deadline: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("GM Codex app-server is not running")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for GM Codex")
            if process.poll() is not None:
                raise RuntimeError("GM Codex app-server exited")
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                raise TimeoutError("timed out waiting for GM Codex")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("GM Codex app-server closed its output")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Startup diagnostics should not corrupt the JSON-RPC stream.
                continue
            if isinstance(message, dict):
                return message

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id()
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            message = self._read(deadline)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                raise RuntimeError(str(error.get("message") or error))
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None and self._thread_id:
            return
        self.close()
        command = [self.codex_bin, "app-server", "--listen", "stdio://"]
        self._process = subprocess.Popen(
            command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "trpg_gm_bridge",
                    "title": "TRPG GM Bridge",
                    "version": "0.1.0",
                }
            },
        )
        self._send({"method": "initialized", "params": {}})
        params: dict[str, Any] = {}
        if self.model:
            params["model"] = self.model
        if self.cwd:
            params["cwd"] = self.cwd
        result = self._request("thread/start", params)
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("GM Codex did not return a thread id")
        self._thread_id = thread_id

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(CodexAppServerClient._text(item) for item in value)
        if isinstance(value, dict):
            for key in ("text", "delta", "output_text", "content"):
                if key in value:
                    text = CodexAppServerClient._text(value[key])
                    if text:
                        return text
        return ""

    def _turn(self, prompt: str) -> str:
        if not self._thread_id:
            raise RuntimeError("GM Codex thread is not initialized")
        request_id = self._next_id()
        self._send(
            {
                "method": "turn/start",
                "id": request_id,
                "params": {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly"},
                    "outputSchema": self.OUTPUT_SCHEMA,
                    "summary": "concise",
                },
            }
        )
        deadline = time.monotonic() + self.timeout_seconds
        chunks: list[str] = []
        while True:
            message = self._read(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    error = message.get("error") or {}
                    raise RuntimeError(str(error.get("message") or error))
                continue
            method = message.get("method")
            params = message.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = self._text(params.get("delta") or params.get("text"))
                if delta:
                    chunks.append(delta)
            elif method == "item/completed":
                item = params.get("item") or {}
                if isinstance(item, dict) and item.get("type") in {"agentMessage", "assistantMessage"}:
                    text = self._text(item)
                    if text:
                        chunks = [text]
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if isinstance(turn, dict) and turn.get("status") not in {None, "completed"}:
                    raise RuntimeError(f"GM Codex turn ended with status {turn.get('status')}")
                break
        return "".join(chunks).strip()

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("GM Codex returned non-JSON output")
            value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict):
            raise RuntimeError("GM Codex output must be a JSON object")
        return value

    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._start()
            prompt = (
                "你是這個 TRPG 房間的權威 GM 仲裁器。只能依照提供的上下文與規則判定本次玩家行動。"
                "不要讀取或修改任何檔案，不要呼叫工具，不要自行補充不存在的資料。"
                "請只輸出符合指定 JSON schema 的 JSON 物件，不要 markdown、解釋或前後文字。"
                "changes 只能使用上下文協議列出的變更類型；不確定時保留世界不變並將 requires_human_review 設為 true。\n\n"
                "本次上下文：\n"
                + json.dumps(context, ensure_ascii=False)
            )
            return self._parse_json(self._turn(prompt))

    def close(self) -> None:
        process, self._process = self._process, None
        self._thread_id = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


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
