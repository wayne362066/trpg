from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import ActionRejected, GameEngine
from .llm import FakeLlmClient


class AccessDenied(ValueError):
    """The request supplied conflicting player identities."""


class ApiHandler(BaseHTTPRequestHandler):
    engine: GameEngine
    player_guide_path: Path | None = None

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _player_id(self, parsed, body: dict | None = None) -> str:
        """Read the same plain player_id convention for local and LAN calls."""
        query_id = parse_qs(parsed.query).get("player_id", [None])[0]
        header_id = self.headers.get("X-Player-Id")
        body_id = body.get("player_id") if body else None
        supplied = [value for value in (query_id, header_id, body_id) if value]
        if not supplied:
            raise ActionRejected("player_id is required")
        if len(set(supplied)) != 1:
            raise AccessDenied("player_id values do not match")
        player_id = supplied[0]
        self.engine.validate_player_id(player_id)
        return player_id

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ActionRejected("request body too large")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            raise ActionRejected("request body must be JSON") from error
        if not isinstance(value, dict):
            raise ActionRejected("request body must be an object")
        return value

    def _player_guide(self) -> str:
        if self.player_guide_path and self.player_guide_path.exists():
            return self.player_guide_path.read_text(encoding="utf-8")
        return "請使用 /rooms/main/view 讀取自己的視圖，並使用 /chat 或 /actions 交互。"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "room_id": self.engine.manifest.get("room_id", "main"),
                    "api_version": "0.1",
                },
            )
            return
        if parsed.path == "/rooms/main/connection":
            try:
                player_id = self._player_id(parsed)
                self._send(
                    200,
                    {
                        "connected": True,
                        "room_id": self.engine.manifest.get("room_id", "main"),
                        "player_id": player_id,
                        "capabilities": ["view", "chat", "submit_action"],
                    },
                )
            except ActionRejected as error:
                self._send(400, {"error": str(error)})
            return
        if parsed.path == "/rooms/main/bootstrap":
            try:
                player_id = self._player_id(parsed)
                self._send(
                    200,
                    {
                        "ready": True,
                        "room_id": self.engine.manifest.get("room_id", "main"),
                        "player_id": player_id,
                        "guide": self._player_guide(),
                        "campaign_intro": self.engine.store.path("campaign/player_intro.md").read_text(
                            encoding="utf-8"
                        ),
                        "character_creation": self.engine.store.path(
                            "campaign/character_creation.md"
                        ).read_text(encoding="utf-8"),
                        "view": self.engine.get_player_view(player_id),
                    },
                )
            except ActionRejected as error:
                self._send(400, {"error": str(error)})
            except AccessDenied as error:
                self._send(403, {"error": str(error)})
            return
        if parsed.path == "/rooms/main/view":
            try:
                player_id = self._player_id(parsed)
                self._send(200, self.engine.get_player_view(player_id))
            except ActionRejected as error:
                self._send(400, {"error": str(error)})
            except AccessDenied as error:
                self._send(403, {"error": str(error)})
            return
        self._send(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Player-Id, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            parsed = urlparse(self.path)
            if parsed.path in {"/rooms/main/chat", "/rooms/main/actions"}:
                player_id = self._player_id(parsed, body)
            if parsed.path == "/rooms/main/chat":
                self._send(201, self.engine.submit_chat(player_id, body.get("text", "")))
                return
            if parsed.path == "/rooms/main/actions":
                self._send(
                    201,
                    self.engine.submit_action(player_id, body.get("text", "")),
                )
                return
            self._send(404, {"error": "not found"})
        except AccessDenied as error:
            self._send(403, {"error": str(error)})
        except ActionRejected as error:
            self._send(400, {"error": str(error)})
        except Exception as error:  # keep prototype API errors JSON-shaped
            self._send(500, {"error": str(error)})

    def log_message(self, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="JSON-backed TRPG GM prototype")
    parser.add_argument("--data-dir", default="game")
    parser.add_argument("--protocol", default=None)
    parser.add_argument("--guide", default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    engine = GameEngine(args.data_dir, FakeLlmClient(), args.protocol)
    guide_path = Path(args.guide) if args.guide else engine.store.root.parent / "PLAYER_CLIENT_GUIDE.md"
    handler = type(
        "ConfiguredApiHandler",
        (ApiHandler,),
        {"engine": engine, "player_guide_path": guide_path},
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"TRPG JSON server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
