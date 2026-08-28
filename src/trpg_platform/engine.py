from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import LocalLlmClient
from .store import JsonStore


class ActionRejected(ValueError):
    """Raised when an action or a proposed state change is unsafe."""


class GameEngine:
    """Authoritative room coordinator backed by JSON and JSONL files."""

    ALLOWED_CHANGE_TYPES = {
        "character_created",
        "npc_condition_added",
        "npc_condition_removed",
        "npc_memory_added",
        "npc_relationship_changed",
        "player_memory_added",
        "item_added",
        "item_removed",
        "item_transfer",
        "player_state_changed",
        "clue_discovered",
        "location_changed",
        "time_advanced",
    }

    def __init__(
        self,
        data_dir: str | Path,
        llm_client: LocalLlmClient,
        protocol_path: str | Path | None = None,
    ):
        self.store = JsonStore(data_dir)
        self.llm_client = llm_client
        self.protocol_path = Path(protocol_path) if protocol_path else self.store.root.parent / "GM_PROTOCOL.md"
        document_root = self.protocol_path.parent if protocol_path else self.store.root.parent
        self.boot_path = document_root / "AI_GM_BOOT.md"
        self.data_contract_path = document_root / "DATA_CONTRACT.md"
        self.content_lifecycle_path = document_root / "CONTENT_LIFECYCLE.md"
        self.api_capabilities_path = document_root / "API_CAPABILITIES.json"

    @property
    def manifest(self) -> dict[str, Any]:
        return self.store.read_json("manifest.json", {})

    def _player_ids(self) -> list[str]:
        directory = self.store.path("players")
        if not directory.exists():
            return []
        return sorted(
            path.name for path in directory.iterdir() if path.is_dir() and (path / "profile.json").exists()
        )

    def _require_player(self, player_id: str) -> None:
        if player_id not in self._player_ids():
            raise ActionRejected(f"unknown player: {player_id}")

    def validate_player_id(self, player_id: str) -> str:
        """Validate the lightweight LAN identity used by the HTTP layer."""
        self._require_player(player_id)
        return player_id

    def _player_file(self, player_id: str, name: str) -> str:
        self._require_player(player_id)
        return f"players/{player_id}/{name}.json"

    def _npc_ids(self) -> list[str]:
        directory = self.store.path("shared/npcs")
        if not directory.exists():
            return []
        return sorted(path.stem for path in directory.glob("*.json"))

    def _require_npc(self, npc_id: str) -> None:
        if npc_id not in self._npc_ids():
            raise ActionRejected(f"unknown npc: {npc_id}")

    def _npc_file(self, npc_id: str) -> str:
        self._require_npc(npc_id)
        return f"shared/npcs/{npc_id}.json"

    def _protocol(self) -> str:
        missing = [
            path
            for path in (
                self.boot_path,
                self.protocol_path,
                self.data_contract_path,
                self.content_lifecycle_path,
                self.api_capabilities_path,
            )
            if not path.exists()
        ]
        if missing:
            raise ActionRejected(
                "required AI documents are missing: " + ", ".join(str(path) for path in missing)
            )
        return "\n\n".join(
            [
                self.boot_path.read_text(encoding="utf-8"),
                self.protocol_path.read_text(encoding="utf-8"),
                self.data_contract_path.read_text(encoding="utf-8"),
                self.content_lifecycle_path.read_text(encoding="utf-8"),
                self.api_capabilities_path.read_text(encoding="utf-8"),
            ]
        )

    def _public_player_summary(self, player_id: str) -> dict[str, Any]:
        profile = self.store.read_json(self._player_file(player_id, "profile"), {})
        return {
            "player_id": profile.get("player_id", player_id),
            "display_name": profile.get("display_name"),
            "character_name": profile.get("character_name"),
            "location": profile.get("location"),
        }

    @staticmethod
    def _visible(value: dict[str, Any], player_id: str) -> bool:
        visibility = value.get("visibility", "public")
        if visibility in {"public", "room", "observable", "objective"}:
            return True
        if visibility == "player_private":
            return value.get("player_id") == player_id
        visible_to = value.get("visible_to", [])
        return player_id in visible_to

    def _public_npc(self, npc: dict[str, Any], player_id: str) -> dict[str, Any]:
        result = {
            "npc_id": npc.get("npc_id"),
            "name": npc.get("name"),
            "base_description": npc.get("base_description"),
            "state": copy.deepcopy(npc.get("state", {})),
        }
        conditions = result["state"].get("conditions", [])
        result["state"]["conditions"] = [item for item in conditions if self._visible(item, player_id)]
        return result

    def _campaign_context(self) -> dict[str, str]:
        required = [
            "campaign/gm_settings.json",
            "campaign/world.md",
            "campaign/rules.md",
            "campaign/character_creation.md",
            "campaign/content_index.json",
        ]
        missing = [relative for relative in required if not self.store.path(relative).exists()]
        if missing:
            raise ActionRejected("required campaign documents are missing: " + ", ".join(missing))
        return {
            "world": self._read_text("campaign/world.md"),
            "rules": self._read_text("campaign/rules.md"),
            "gm_settings": self._read_json_text("campaign/gm_settings.json"),
            "character_creation": self._read_text("campaign/character_creation.md"),
            "content_index": self._read_json_text("campaign/content_index.json"),
        }

    def _read_text(self, relative: str) -> str:
        path = self.store.path(relative)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _read_json_text(self, relative: str) -> str:
        path = self.store.path(relative)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def build_context(self, actor_id: str, action_text: str) -> dict[str, Any]:
        self._require_player(actor_id)
        profile = self.store.read_json(self._player_file(actor_id, "profile"), {})
        inventory = self.store.read_json(self._player_file(actor_id, "inventory"), {"items": []})
        clues = self.store.read_json(self._player_file(actor_id, "clues"), {"clues": []})
        player_memories = self.store.read_json(
            self._player_file(actor_id, "npc_memories"), {"memories": []}
        )
        npcs = [self.store.read_json(self._npc_file(npc_id), {}) for npc_id in self._npc_ids()]
        actor_npc_memories = {
            npc.get("npc_id"): copy.deepcopy(npc.get("private_memories", {}).get(actor_id, []))
            for npc in npcs
            if actor_id in npc.get("private_memories", {})
        }
        return {
            "ai": {
                "must_follow_boot_sequence": True,
                "read_order": [
                    "AI_GM_BOOT.md",
                    "GM_PROTOCOL.md",
                    "game/manifest.json",
                    "game/campaign/gm_settings.json",
                    "game/campaign/world.md",
                    "game/campaign/rules.md",
                    "game/campaign/character_creation.md",
                    "DATA_CONTRACT.md",
                    "CONTENT_LIFECYCLE.md",
                    "game/campaign/content_index.json",
                    "API_CAPABILITIES.json",
                    "relevant shared runtime state",
                    "actor private data",
                ],
                "loaded_documents": [
                    "AI_GM_BOOT.md",
                    "GM_PROTOCOL.md",
                    "game/manifest.json",
                    "game/campaign/gm_settings.json",
                    "game/campaign/world.md",
                    "game/campaign/rules.md",
                    "game/campaign/character_creation.md",
                    "DATA_CONTRACT.md",
                    "CONTENT_LIFECYCLE.md",
                    "game/campaign/content_index.json",
                    "API_CAPABILITIES.json",
                ],
            },
            "protocol": self._protocol(),
            "api_capabilities": self.api_capabilities_path.read_text(encoding="utf-8"),
            "manifest": self.manifest,
            "campaign": self._campaign_context(),
            "room": {
                "room_id": self.manifest.get("room_id", "main"),
                "scene": self.store.read_json("shared/world.json", {}).get("scene"),
                "state_version": self.manifest.get("state_version", 0),
            },
            "public_players": [self._public_player_summary(pid) for pid in self._player_ids()],
            "public_npcs": [self._public_npc(npc, actor_id) for npc in npcs],
            "actor": {
                "player_id": actor_id,
                "profile": profile,
                "inventory": inventory,
                "clues": clues,
                "npc_memories": player_memories,
            },
            "npc_memories_about_actor": actor_npc_memories,
            "action": {"text": action_text},
        }

    @classmethod
    def validate_llm_result(cls, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ActionRejected("LLM result must be a JSON object")
        for key in ("narration", "public_messages", "private_messages", "changes"):
            if key not in result:
                raise ActionRejected(f"LLM result missing field: {key}")
        if not isinstance(result["narration"], str):
            raise ActionRejected("narration must be a string")
        if not isinstance(result["public_messages"], list) or not all(
            isinstance(item, str) for item in result["public_messages"]
        ):
            raise ActionRejected("public_messages must be an array of strings")
        if not isinstance(result["private_messages"], list):
            raise ActionRejected("private_messages must be an array")
        for message in result["private_messages"]:
            if not isinstance(message, dict) or not isinstance(message.get("player_id"), str) or not isinstance(
                message.get("content"), str
            ):
                raise ActionRejected("private_messages entries need player_id and content")
        if not isinstance(result["changes"], list):
            raise ActionRejected("changes must be an array")
        for change in result["changes"]:
            if not isinstance(change, dict) or change.get("type") not in cls.ALLOWED_CHANGE_TYPES:
                raise ActionRejected(f"unsupported change type: {change.get('type') if isinstance(change, dict) else None}")
        normalized = copy.deepcopy(result)
        normalized.setdefault("requires_human_review", False)
        normalized.setdefault("review_reason", None)
        return normalized

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _find_item(items: list[dict[str, Any]], item_id: str) -> tuple[int, dict[str, Any]]:
        for index, item in enumerate(items):
            if item.get("item_id") == item_id:
                return index, item
        raise ActionRejected(f"item not found: {item_id}")

    def _apply_changes(self, actor_id: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        updates: dict[str, Any] = {}

        def load(relative: str, default: Any) -> Any:
            if relative not in updates:
                updates[relative] = copy.deepcopy(self.store.read_json(relative, default))
            return updates[relative]

        for change in changes:
            kind = change["type"]
            if kind == "character_created":
                target_player = change.get("player_id", actor_id)
                self._require_player(target_player)
                if target_player != actor_id:
                    raise ActionRejected("character creation can only affect the acting player")
                profile = load(self._player_file(target_player, "profile"), {})
                if profile.get("character_creation_status") == "completed":
                    raise ActionRejected("character creation is already completed")
                character = change.get("character", {})
                if not isinstance(character, dict):
                    raise ActionRejected("character_created needs character object")
                allowed = {"character_name", "concept", "background_summary", "stats"}
                unknown = set(character) - allowed
                if unknown:
                    raise ActionRejected("unsupported character fields: " + ", ".join(sorted(unknown)))
                name = character.get("character_name")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                    raise ActionRejected("character_name must be 1 to 40 characters")
                for player_id in self._player_ids():
                    if player_id == target_player:
                        continue
                    other = self.store.read_json(self._player_file(player_id, "profile"), {})
                    if other.get("character_name") == name.strip():
                        raise ActionRejected("character_name is already in use")
                concept = character.get("concept", "")
                if not isinstance(concept, str) or not 1 <= len(concept.strip()) <= 200:
                    raise ActionRejected("concept must be 1 to 200 characters")
                background = character.get("background_summary", "")
                if not isinstance(background, str) or len(background) > 1000:
                    raise ActionRejected("background_summary cannot exceed 1000 characters")
                stats = character.get("stats", {})
                if not isinstance(stats, dict):
                    raise ActionRejected("character stats must be an object")
                for key, value in stats.items():
                    if not isinstance(key, str) or not isinstance(value, (int, float)):
                        raise ActionRejected("character stats must contain numeric values")
                settings = self.store.read_json("campaign/gm_settings.json", {})
                if not isinstance(settings, dict):
                    raise ActionRejected("gm_settings must be an object")
                creation = settings.get("character_creation", {})
                profile["character_name"] = name.strip()
                profile["concept"] = concept.strip()
                profile["background_summary"] = background.strip()
                profile["stats"] = dict(stats)
                profile["stats"]["health"] = creation.get("starting_health", profile["stats"].get("health", 10))
                profile["location"] = creation.get("starting_location", profile.get("location"))
                profile["character_creation_status"] = "completed"
                inventory = load(self._player_file(target_player, "inventory"), {"items": []})
                inventory["items"] = copy.deepcopy(creation.get("starting_items", []))
                load(self._player_file(target_player, "clues"), {"clues": []})["clues"] = []
                load(self._player_file(target_player, "npc_memories"), {"memories": []})["memories"] = []
            elif kind == "npc_condition_added":
                npc_id = change.get("npc_id")
                self._require_npc(npc_id)
                condition = copy.deepcopy(change.get("condition"))
                if not isinstance(condition, dict) or not condition.get("type"):
                    raise ActionRejected("npc_condition_added needs condition.type")
                condition.setdefault("visibility", "observable")
                npc = load(self._npc_file(npc_id), {})
                npc.setdefault("state", {}).setdefault("conditions", []).append(condition)
                npc["state_version"] = int(npc.get("state_version", 0)) + 1
            elif kind == "npc_condition_removed":
                npc_id = change.get("npc_id")
                self._require_npc(npc_id)
                npc = load(self._npc_file(npc_id), {})
                conditions = npc.setdefault("state", {}).setdefault("conditions", [])
                condition_type = change.get("condition_type")
                npc["state"]["conditions"] = [item for item in conditions if item.get("type") != condition_type]
                npc["state_version"] = int(npc.get("state_version", 0)) + 1
            elif kind == "npc_memory_added":
                npc_id = change.get("npc_id")
                target_player = change.get("player_id")
                self._require_npc(npc_id)
                self._require_player(target_player)
                content = change.get("content") or change.get("summary")
                if not isinstance(content, str) or not content:
                    raise ActionRejected("npc_memory_added needs content")
                npc = load(self._npc_file(npc_id), {})
                memory = {"event_id": "pending", "content": content, "importance": change.get("importance", "normal")}
                npc.setdefault("private_memories", {}).setdefault(target_player, []).append(memory)
                if isinstance(change.get("relationship_delta"), dict):
                    relationship = npc.setdefault("private_relationships", {}).setdefault(target_player, {})
                    for key, delta in change["relationship_delta"].items():
                        if not isinstance(delta, (int, float)):
                            raise ActionRejected("relationship deltas must be numbers")
                        relationship[key] = relationship.get(key, 0) + delta
            elif kind == "npc_relationship_changed":
                npc_id = change.get("npc_id")
                target_player = change.get("player_id")
                self._require_npc(npc_id)
                self._require_player(target_player)
                delta = change.get("delta", {})
                if not isinstance(delta, dict):
                    raise ActionRejected("npc_relationship_changed needs delta object")
                npc = load(self._npc_file(npc_id), {})
                relationship = npc.setdefault("private_relationships", {}).setdefault(target_player, {})
                for key, value in delta.items():
                    if not isinstance(value, (int, float)):
                        raise ActionRejected("relationship deltas must be numbers")
                    relationship[key] = relationship.get(key, 0) + value
            elif kind == "player_memory_added":
                target_player = change.get("player_id", actor_id)
                self._require_player(target_player)
                if target_player != actor_id and not change.get("cross_player_interaction"):
                    raise ActionRejected("changing another player's memory needs cross_player_interaction")
                content = change.get("content")
                if not isinstance(content, str) or not content:
                    raise ActionRejected("player_memory_added needs content")
                memories = load(self._player_file(target_player, "npc_memories"), {"memories": []})
                memories.setdefault("memories", []).append(
                    {"event_id": "pending", "content": content, "npc_id": change.get("npc_id")}
                )
            elif kind in {"item_added", "item_removed", "item_transfer"}:
                if kind == "item_transfer":
                    source = change.get("from_player_id")
                    target = change.get("to_player_id")
                    if source != actor_id and not change.get("cross_player_interaction"):
                        raise ActionRejected("item transfer source must be actor unless marked interaction")
                    self._require_player(source)
                    self._require_player(target)
                    item_id = change.get("item_id")
                    source_inventory = load(self._player_file(source, "inventory"), {"items": []})
                    target_inventory = load(self._player_file(target, "inventory"), {"items": []})
                    source_items = source_inventory.setdefault("items", [])
                    target_items = target_inventory.setdefault("items", [])
                    index, item = self._find_item(source_items, item_id)
                    quantity = int(change.get("quantity", 1))
                    available = int(item.get("quantity", 1))
                    if quantity < 1 or available < quantity:
                        raise ActionRejected("insufficient item quantity")
                    if available == quantity:
                        source_items.pop(index)
                    else:
                        item["quantity"] = available - quantity
                    destination = next((entry for entry in target_items if entry.get("item_id") == item_id), None)
                    if destination:
                        destination["quantity"] = int(destination.get("quantity", 1)) + quantity
                    else:
                        received = {"item_id": item_id, "name": item.get("name", item_id), "quantity": quantity}
                        target_items.append(received)
                else:
                    target_player = change.get("player_id", actor_id)
                    self._require_player(target_player)
                    if target_player != actor_id and not change.get("cross_player_interaction"):
                        raise ActionRejected("changing another player's inventory needs cross_player_interaction")
                    inventory = load(self._player_file(target_player, "inventory"), {"items": []})
                    items = inventory.setdefault("items", [])
                    item_id = change.get("item_id")
                    if kind == "item_added":
                        new_item = copy.deepcopy(change.get("item", {"item_id": item_id, "name": item_id, "quantity": 1}))
                        if not new_item.get("item_id"):
                            raise ActionRejected("item_added needs item_id")
                        items.append(new_item)
                    else:
                        index, item = self._find_item(items, item_id)
                        quantity = int(change.get("quantity", 1))
                        available = int(item.get("quantity", 1))
                        if quantity < 1 or available < quantity:
                            raise ActionRejected("insufficient item quantity")
                        if available == quantity:
                            items.pop(index)
                        else:
                            item["quantity"] = available - quantity
            elif kind == "player_state_changed":
                target_player = change.get("player_id", actor_id)
                self._require_player(target_player)
                if target_player != actor_id and not change.get("cross_player_interaction"):
                    raise ActionRejected("changing another player's state needs cross_player_interaction")
                profile = load(self._player_file(target_player, "profile"), {})
                updates_to_apply = change.get("updates", {})
                if not isinstance(updates_to_apply, dict):
                    raise ActionRejected("player_state_changed needs updates object")
                for key, value in updates_to_apply.items():
                    if key not in {"health", "status", "location"}:
                        raise ActionRejected(f"unsupported player state field: {key}")
                    if key == "location":
                        profile["location"] = value
                    else:
                        profile.setdefault("stats", {})[key] = value
            elif kind == "clue_discovered":
                target_player = change.get("player_id", actor_id)
                self._require_player(target_player)
                if target_player != actor_id and not change.get("cross_player_interaction"):
                    raise ActionRejected("adding another player's clue needs cross_player_interaction")
                clue = copy.deepcopy(change.get("clue"))
                if not isinstance(clue, dict) or not clue.get("clue_id"):
                    raise ActionRejected("clue_discovered needs clue.clue_id")
                clues = load(self._player_file(target_player, "clues"), {"clues": []})
                if not any(item.get("clue_id") == clue["clue_id"] for item in clues.setdefault("clues", [])):
                    clues["clues"].append(clue)
            elif kind == "location_changed":
                entity_type = change.get("entity_type", "player")
                if entity_type == "player":
                    target_player = change.get("player_id", actor_id)
                    self._require_player(target_player)
                    if target_player != actor_id and not change.get("cross_player_interaction"):
                        raise ActionRejected("changing another player's location needs cross_player_interaction")
                    profile = load(self._player_file(target_player, "profile"), {})
                    profile["location"] = change.get("location")
                elif entity_type == "npc":
                    npc_id = change.get("npc_id")
                    self._require_npc(npc_id)
                    npc = load(self._npc_file(npc_id), {})
                    npc.setdefault("state", {})["location"] = change.get("location")
                    npc["state_version"] = int(npc.get("state_version", 0)) + 1
                else:
                    raise ActionRejected("location_changed entity_type must be player or npc")
            elif kind == "time_advanced":
                world = load("shared/world.json", {})
                world["time"] = change.get("to")
            else:  # pragma: no cover - guarded by validate_llm_result
                raise ActionRejected(f"unsupported change type: {kind}")

        return updates

    @staticmethod
    def _change_visibility(change: dict[str, Any]) -> str:
        if "visibility" in change:
            return change["visibility"]
        # Private by default for data that belongs to one player or to an NPC's
        # private mind. The model can explicitly promote observable effects.
        if change.get("type") in {
            "npc_memory_added",
            "npc_relationship_changed",
        }:
            return "gm_only"
        if change.get("type") in {
            "character_created",
            "player_memory_added",
            "item_added",
            "item_removed",
            "clue_discovered",
            "player_state_changed",
        }:
            return "player_private"
        return "public"

    def _visible_event(self, event: dict[str, Any], player_id: str) -> dict[str, Any] | None:
        is_actor = event.get("actor_id") == player_id
        result = event.get("result", {})
        private_messages = [
            message for message in result.get("private_messages", []) if message.get("player_id") == player_id
        ]
        visible_changes = []
        for change in result.get("changes", []):
            visibility = self._change_visibility(change)
            if visibility in {"public", "room", "observable", "objective"}:
                visible_changes.append(change)
            elif player_id in change.get("visible_to", []) or (
                visibility == "player_private" and change.get("player_id") == player_id
            ):
                visible_changes.append(change)
        if not result.get("public_messages") and not private_messages and not visible_changes and not is_actor:
            return None
        return {
            "event_id": event.get("event_id"),
            "actor_id": event.get("actor_id"),
            "narration": result.get("narration") if result.get("public_messages") or is_actor else None,
            "public_messages": result.get("public_messages", []),
            "private_messages": private_messages,
            "changes": visible_changes,
            "created_at": event.get("created_at"),
        }

    def get_player_view(self, player_id: str) -> dict[str, Any]:
        self._require_player(player_id)
        world = self.store.read_json("shared/world.json", {})
        npcs = [self.store.read_json(self._npc_file(npc_id), {}) for npc_id in self._npc_ids()]
        events = [self._visible_event(event, player_id) for event in self.store.read_jsonl("shared/events.jsonl")]
        return {
            "room": self.manifest,
            "world": world,
            "players": [self._public_player_summary(pid) for pid in self._player_ids()],
            "npcs": [self._public_npc(npc, player_id) for npc in npcs],
            "self": {
                "profile": self.store.read_json(self._player_file(player_id, "profile"), {}),
                "inventory": self.store.read_json(self._player_file(player_id, "inventory"), {"items": []}),
                "clues": self.store.read_json(self._player_file(player_id, "clues"), {"clues": []}),
                "npc_memories": self.store.read_json(
                    self._player_file(player_id, "npc_memories"), {"memories": []}
                ),
            },
            "chat": self.store.read_jsonl("shared/chat.jsonl")[-100:],
            "events": [event for event in events[-100:] if event is not None],
        }

    def submit_chat(self, player_id: str, text: str) -> dict[str, Any]:
        self._require_player(player_id)
        if not isinstance(text, str) or not text.strip():
            raise ActionRejected("chat text cannot be empty")
        message = {
            "message_id": f"chat-{uuid.uuid4().hex}",
            "player_id": player_id,
            "text": text.strip(),
            "created_at": self._now(),
        }
        with self.store.room_lock():
            self.store.append_jsonl("shared/chat.jsonl", message)
        return message

    def submit_action(self, actor_id: str, action_text: str) -> dict[str, Any]:
        self._require_player(actor_id)
        if not isinstance(action_text, str) or not action_text.strip():
            raise ActionRejected("action text cannot be empty")
        with self.store.room_lock():
            context = self.build_context(actor_id, action_text.strip())
            raw_result = self.llm_client.generate(context)
            result = self.validate_llm_result(raw_result)
            updates = self._apply_changes(actor_id, result["changes"])
            action_id = f"action-{uuid.uuid4().hex}"
            event_id = f"event-{uuid.uuid4().hex}"
            for change in result["changes"]:
                change.setdefault("source_action_id", action_id)
            for memory in result["private_messages"]:
                memory.setdefault("source_action_id", action_id)
            # Replace temporary source markers in derived snapshots so a
            # memory can always be traced to its immutable creating event.
            for relative, value in updates.items():
                serialized = json.dumps(value, ensure_ascii=False)
                serialized = serialized.replace(
                    '"event_id": "pending"', f'"event_id": {json.dumps(event_id)}'
                )
                updates[relative] = json.loads(serialized)
            event = {
                "event_id": event_id,
                "action_id": action_id,
                "actor_id": actor_id,
                "action_text": action_text.strip(),
                "result": result,
                "state_version_before": self.manifest.get("state_version", 0),
                "state_version_after": self.manifest.get("state_version", 0) + 1,
                "created_at": self._now(),
            }
            for relative, value in updates.items():
                self.store.write_json_atomic(relative, value)
            manifest = self.manifest
            manifest["state_version"] = event["state_version_after"]
            self.store.write_json_atomic("manifest.json", manifest)
            self.store.append_jsonl("shared/events.jsonl", event)
            return {"action_id": action_id, "event_id": event_id, "result": result}
