import json
import shutil
import tempfile
import unittest
from pathlib import Path

from trpg_platform.engine import ActionRejected, GameEngine
from trpg_platform.llm import FakeLlmClient


ROOT = Path(__file__).resolve().parents[1]


class JsonEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="trpg-json-")
        self.game_dir = Path(self.temp_dir) / "game"
        shutil.copytree(ROOT / "game", self.game_dir)
        self.protocol = ROOT / "GM_PROTOCOL.md"

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def engine_with(self, response):
        return GameEngine(self.game_dir, FakeLlmClient(responses=[response]), self.protocol)

    def test_player_view_isolated_but_npc_condition_is_shared(self):
        response = {
            "narration": "守衛隊長捂住傷口，怒視艾文。",
            "public_messages": ["北門守衛隊長受傷了。"],
            "private_messages": [
                {"player_id": "player-a", "content": "你知道是自己的攻擊造成傷勢。"}
            ],
            "changes": [
                {
                    "type": "npc_condition_added",
                    "npc_id": "guard-001",
                    "condition": {"type": "injured", "severity": 40, "visibility": "observable"},
                },
                {
                    "type": "npc_memory_added",
                    "npc_id": "guard-001",
                    "player_id": "player-a",
                    "content": "玩家 A 攻擊了我。",
                    "relationship_delta": {"trust": -30, "hostility": 20},
                },
            ],
            "requires_human_review": False,
            "review_reason": None,
        }
        engine = self.engine_with(response)
        result = engine.submit_action("player-a", "我攻擊守衛隊長")
        self.assertTrue(result["event_id"].startswith("event-"))

        a_view = engine.get_player_view("player-a")
        b_view = engine.get_player_view("player-b")
        self.assertEqual(a_view["npcs"][0]["state"]["conditions"][0]["type"], "injured")
        self.assertEqual(b_view["npcs"][0]["state"]["conditions"][0]["type"], "injured")
        self.assertIn("自己的攻擊", json.dumps(a_view, ensure_ascii=False))
        self.assertNotIn("自己的攻擊", json.dumps(b_view, ensure_ascii=False))
        # The actor may receive a private explanation, but never the NPC's
        # internal memory/relationship record itself.
        self.assertNotIn("玩家 A 攻擊了我", json.dumps(a_view, ensure_ascii=False))

        npc = json.loads((self.game_dir / "shared/npcs/guard-001.json").read_text())
        self.assertEqual(len(npc["private_memories"]["player-a"]), 1)
        self.assertNotIn("player-b", npc["private_memories"])

    def test_cross_player_transfer_is_explicit_and_atomic(self):
        response = {
            "narration": "艾文把治療藥水交給米拉。",
            "public_messages": ["玩家 A 將一瓶治療藥水交給玩家 B。"],
            "private_messages": [],
            "changes": [
                {
                    "type": "item_transfer",
                    "from_player_id": "player-a",
                    "to_player_id": "player-b",
                    "item_id": "healing-potion-003",
                    "quantity": 1,
                    "cross_player_interaction": True,
                }
            ],
        }
        engine = self.engine_with(response)
        engine.submit_action("player-a", "我把治療藥水交給玩家 B")
        a = engine.get_player_view("player-a")
        b = engine.get_player_view("player-b")
        self.assertEqual(a["self"]["inventory"]["items"], [])
        self.assertEqual(b["self"]["inventory"]["items"][0]["item_id"], "healing-potion-003")

    def test_unknown_change_is_rejected_without_state_change(self):
        response = {
            "narration": "",
            "public_messages": [],
            "private_messages": [],
            "changes": [{"type": "rewrite_the_world"}],
        }
        engine = self.engine_with(response)
        before = (self.game_dir / "manifest.json").read_text()
        with self.assertRaises(ActionRejected):
            engine.submit_action("player-a", "隨便做點什麼")
        self.assertEqual((self.game_dir / "manifest.json").read_text(), before)

    def test_context_contains_only_actor_private_data(self):
        llm = FakeLlmClient()
        engine = GameEngine(self.game_dir, llm, self.protocol)
        engine.submit_action("player-a", "觀察守衛")
        context = llm.contexts[0]
        self.assertEqual(context["actor"]["player_id"], "player-a")
        self.assertNotIn("player-b", json.dumps(context["actor"], ensure_ascii=False))
        self.assertIn("GM Protocol", context["protocol"])
        self.assertTrue(context["ai"]["must_follow_boot_sequence"])
        self.assertIn("game/campaign/character_creation.md", context["ai"]["loaded_documents"])
        self.assertIn("character_creation", context["campaign"])

    def test_character_creation_uses_whitelisted_fields_and_campaign_defaults(self):
        profile_path = self.game_dir / "players/player-a/profile.json"
        profile = json.loads(profile_path.read_text())
        profile.pop("character_name")
        profile["character_creation_status"] = "pending"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        response = {
            "narration": "角色建立完成。",
            "public_messages": [],
            "private_messages": [{"player_id": "player-a", "content": "你的角色已建立。"}],
            "changes": [
                {
                    "type": "character_created",
                    "player_id": "player-a",
                    "character": {
                        "character_name": "新角色",
                        "concept": "擅長觀察的旅行者",
                        "background_summary": "來自北方的旅人。",
                        "stats": {"observation": 3},
                    },
                }
            ],
        }
        engine = self.engine_with(response)
        engine.submit_action("player-a", "我要建立角色：新角色")
        created = json.loads(profile_path.read_text())
        self.assertEqual(created["character_creation_status"], "completed")
        self.assertEqual(created["character_name"], "新角色")
        self.assertEqual(created["stats"]["health"], 10)

    def test_missing_boot_document_blocks_generation(self):
        (ROOT / "AI_GM_BOOT.md").read_text(encoding="utf-8")
        engine = GameEngine(self.game_dir, FakeLlmClient(), self.protocol)
        engine.boot_path = self.game_dir / "missing-AI_GM_BOOT.md"
        with self.assertRaises(ActionRejected):
            engine.submit_action("player-a", "觀察守衛")


if __name__ == "__main__":
    unittest.main()
