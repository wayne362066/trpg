# JSON 資料交互契約

這份文件定義 AI GM、仲裁層與玩家視圖如何使用檔案。它是機器流程的邊界說明，不是劇本內容。

## 檔案所有權

```text
game/manifest.json                 房間與版本（仲裁層）
game/campaign/content_index.json   可用地區／NPC／任務與內容版本（仲裁層）
game/campaign/regions/<id>.json    地區基礎定義（GM 內容 API）
game/campaign/npcs/<id>.json       NPC 基礎定義（GM 內容 API）
game/shared/world.json             共享客觀世界（仲裁層）
game/shared/npcs/<npc-id>.json     NPC 基礎描述、客觀狀態、私人記憶（仲裁層）
game/shared/events.jsonl           正式事件歷史，只追加（仲裁層）
game/shared/chat.jsonl             公開聊天，只追加
game/players/<id>/profile.json     該玩家角色狀態
game/players/<id>/inventory.json   該玩家物品
game/players/<id>/clues.json       該玩家知道的情報
game/players/<id>/npc_memories.json 該玩家對 NPC 的觀察與認知
game/players/<id>/events.jsonl     該玩家可見的私人事件索引
game/interactions/events.jsonl     跨玩家互動的索引，只追加
```

原始劇本文件是規則輸入，不是執行期間的寫入目標。玩家 Codex 不應直接開啟上述原始目錄；它只取得仲裁層產生的玩家視圖。

系統規則文件（`AI_GM_BOOT.md`、`GM_PROTOCOL.md`、`DATA_CONTRACT.md`、`CONTENT_LIFECYCLE.md`、`API_CAPABILITIES.json`）也不是遊戲內資料，不能由內容 API 或一般正式行動修改。

## 讀取範圍

### 玩家行動的上下文

玩家 A 的一次行動最多包含：

- 共享世界當前狀態。
- 與當前場景相關的 NPC 公開狀態。
- 所有玩家的公開摘要（ID、角色名、位置）。
- A 的完整角色、物品、情報與 NPC 認知。
- NPC 對 A 的相關記憶（只提供給 GM 判定，不直接展示給 A）。
- 與本次行動必要的近期事件摘要。

不包含 B 的完整背包、秘密情報、私人記憶或 NPC 對 B 的內心記錄。只有當行動明確涉及 B，仲裁層才提供完成判定所需的最小事實，例如「B 是否在場」或「物品是否可接收」。

## 變更提案外框

```json
{
  "type": "item_transfer",
  "source_action_id": "action-...",
  "visibility": "public",
  "cross_player_interaction": true
}
```

允許的 `type`：

- `character_created`
- `npc_condition_added`
- `npc_condition_removed`
- `npc_memory_added`
- `npc_relationship_changed`
- `player_memory_added`
- `item_added`
- `item_removed`
- `item_transfer`
- `player_state_changed`
- `clue_discovered`
- `location_changed`
- `time_advanced`

內容建立／修改不是一般玩家變更類型。`propose_content`、`validate_content`、`apply_content` 屬於 GM-only 內容 API，必須依 `CONTENT_LIFECYCLE.md` 同步更新定義檔、索引、版本與事件。

未知類型拒絕。AI 不得自訂 `type` 或在 JSON 中引入未定義的狀態欄位。

## 主要變更規則

### character_created

只能影響創角流程中的目標玩家，欄位限制在 `character_creation.md`；不能藉此建立 NPC、任務或未核准物品。角色建立後應標記完成，不能以同一變更重置既有角色。

### npc_condition_added / removed

更新 NPC 共享客觀狀態。傷勢、位置與可觀察行為是否公開，必須在變更中明確標記；受傷原因仍然是另一個可見性判定。

### npc_memory_added / npc_relationship_changed

更新 NPC 對指定玩家的內在資料，預設 `gm_only`。不得因為 A 能看到自己的事件，就把 NPC 對 A 的內心記憶直接展示給 A。

### item_transfer

必須同時指出 `from_player_id`、`to_player_id`、`item_id` 與數量。來源玩家必須持有足夠數量；移除與加入必須視為同一筆完整變更。

### clue_discovered / player_memory_added

預設只加入指定玩家的私人資料。若要讓多人知道，應建立明確的分享或共同發現事件，不應直接把私人資料改成公開。

## 事件欄位

每筆正式事件至少包含：

```json
{
  "event_id": "event-...",
  "action_id": "action-...",
  "actor_id": "player-a",
  "action_text": "...",
  "result": {},
  "state_version_before": 0,
  "state_version_after": 1,
  "created_at": "..."
}
```

事件不能刪除或覆寫。GM 的修正、否決或重試都建立新的管理事件，並以 `source_event_id` 指向原事件。
