import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from trpg_platform.engine import GameEngine
from trpg_platform.llm import FakeLlmClient
from trpg_platform.server import ApiHandler


ROOT = Path(__file__).resolve().parents[1]


class ServerConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="trpg-http-")
        self.game_dir = Path(self.temp_dir) / "game"
        shutil.copytree(ROOT / "game", self.game_dir)
        engine = GameEngine(self.game_dir, FakeLlmClient(), ROOT / "GM_PROTOCOL.md")
        handler = type(
            "ConfiguredApiHandler",
            (ApiHandler,),
            {"engine": engine, "player_guide_path": ROOT / "PLAYER_CLIENT_GUIDE.md"},
        )
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except PermissionError as error:
            self.skipTest(f"environment does not allow binding a test port: {error}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.temp_dir)

    def request(self, method, path, payload=None):
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_local_and_lan_style_requests_use_same_connection_route(self):
        status, health = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])

        status, connection = self.request(
            "GET", f"/rooms/main/connection?{urlencode({'player_id': 'player-a'})}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(connection["player_id"], "player-a")
        self.assertTrue(connection["connected"])

        status, view = self.request(
            "GET", f"/rooms/main/view?{urlencode({'player_id': 'player-a'})}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(view["self"]["profile"]["player_id"], "player-a")

    def test_bootstrap_works_without_conversation_history(self):
        status, payload = self.request(
            "GET", f"/rooms/main/bootstrap?{urlencode({'player_id': 'player-a'})}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["player_id"], "player-a")
        self.assertIn("/rooms/main/actions", payload["guide"])
        self.assertIn("七日誓約", payload["campaign_intro"])
        self.assertIn("創角", payload["character_creation"])
        self.assertEqual(payload["view"]["self"]["profile"]["player_id"], "player-a")

    def test_action_and_chat_use_player_id_from_same_http_boundary(self):
        status, message = self.request(
            "POST", "/rooms/main/chat", {"player_id": "player-b", "text": "我先觀察北門"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(message["player_id"], "player-b")

        status, result = self.request(
            "POST", "/rooms/main/actions", {"player_id": "player-b", "text": "我查看守衛"}
        )
        self.assertEqual(status, 201)
        self.assertTrue(result["action_id"].startswith("action-"))

    def test_unknown_player_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/rooms/main/connection?player_id=not-a-player")
        self.assertEqual(error.exception.code, 400)

    def test_player_id_header_and_query_must_not_conflict(self):
        request = Request(
            self.base_url + "/rooms/main/view?player_id=player-a",
            headers={"X-Player-Id": "player-b"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
