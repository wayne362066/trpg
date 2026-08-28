# 遊戲內容生命週期與內容 API 邊界

這份文件定義「新增地區、NPC、任務或修改既有劇本內容」的流程。它和 `AI_GM_BOOT.md`、`GM_PROTOCOL.md` 一樣，是 AI GM 的必讀系統規則。

## 四種資料層

### 1. 系統規則（runtime immutable）

以下文件是 AI 的邊界與操作協議，執行期間只能讀取，不能由 LLM 或玩家行動修改：

- `AI_GM_BOOT.md`
- `GM_PROTOCOL.md`
- `DATA_CONTRACT.md`
- `CONTENT_LIFECYCLE.md`

要改這些文件，必須由 GM／開發者在專案層修改並重新啟動或重新載入。不能提供一個遊戲內 API 讓 AI 自己改寫規則，否則下一次判定的邊界會漂移。

### 2. Campaign 內容定義（GM-owned content）

這些文件描述世界中「可以存在什麼」：

- `game/campaign/world.md`
- `game/campaign/rules.md`
- `game/campaign/gm_settings.json`
- `game/campaign/character_creation.md`
- `game/campaign/regions/<region-id>.json`
- `game/campaign/npcs/<npc-id>.json`
- `game/campaign/content_index.json`

新增或修改內容必須透過內容 API／GM 工具流程；不能由一般玩家行動直接寫入。

### 3. 執行期間共享狀態

例如 NPC 的傷勢、位置、時間與任務階段，寫在 `game/shared/`，由一般正式行動經仲裁層變更。這些不是重新定義劇本，而是劇本內容的當前狀態。

### 4. 玩家與 NPC 的個別認知

玩家情報、玩家對 NPC 的觀察，以及 NPC 對各玩家的記憶仍分開保存。新增公開 NPC 不代表所有玩家自動知道 NPC 的秘密記憶。

## 內容索引是同步入口

`game/campaign/content_index.json` 是每次 AI 讀取內容時的入口。任何內容變更都必須同時更新：

1. 實際內容檔案。
2. `content_index.json` 的項目與 `content_version`。
3. `manifest.json` 的 `content_version`。
4. 一筆追加式 `content_changed` 事件。

若只建立 `shared/npcs/new-npc.json` 而沒有更新索引，下一次 Context Builder 不保證會找到它，視為未完成的內容變更。

內容索引至少包含：

```json
{
  "content_version": 0,
  "regions": [
    {
      "id": "north-gate",
      "path": "campaign/regions/north-gate.json",
      "status": "active",
      "context_tags": ["north-gate"]
    }
  ],
  "npcs": [
    {
      "id": "guard-001",
      "runtime_path": "shared/npcs/guard-001.json",
      "definition_path": "campaign/npcs/guard-001.json",
      "status": "active",
      "context_tags": ["north-gate"]
    }
  ]
}
```

## 內容 API 的角色分工

內容 API 和玩家行動 API 是不同能力：

```text
玩家 Codex
  → chat / view / submit_action

GM Codex 或 GM 管理流程
  → propose_content
  → validate_content
  → apply_content
```

第一版可以先讓 API 由 GM 主機內部使用；即使區網可信任，也不要讓一般玩家的 `submit_action` 隱含內容建立權限。

### propose_content

AI 發現需要新地區或 NPC 時，先提出提案，不直接寫檔：

```json
{
  "content_type": "npc",
  "operation": "create",
  "id": "merchant-001",
  "definition": {
    "name": "流浪商人",
    "region_id": "north-gate",
    "base_description": "...",
    "public_information": [],
    "initial_relationships": {}
  },
  "reason": "玩家進入新地區，需要一名可互動 NPC",
  "source_event_id": "event-..."
}
```

### validate_content

仲裁層檢查：

- ID 是否唯一、格式是否正確。
- 參照的地區／NPC／任務是否存在。
- 欄位是否在該 `content_type` 白名單。
- 是否誤把 runtime 狀態、秘密或玩家私人資料塞入基礎定義。
- 是否需要 GM 人工確認。

### apply_content

只有驗證成功的提案才能套用，且必須在同一個內容變更操作中：

1. 寫入定義檔。
2. 更新 `content_index.json`。
3. 更新 `manifest.json` 版本。
4. 追加 `content_changed` 事件。
5. 重新載入 Context Builder 的內容索引。

任一步驟失敗都不能回報「新 NPC／新地區已正式存在」。

## 新 NPC 的三層同步

新增 NPC 時要分別處理：

1. `campaign/npcs/<id>.json`：基礎設定、公開資訊、秘密、初始關係規則。
2. `shared/npcs/<id>.json`：目前位置、傷勢、情緒、行為與事件版本。
3. `private_memories`／`private_relationships`：隨實際互動逐玩家新增。

建立 NPC 時可以建立第 1、2 層；第 3 層只在玩家與 NPC 互動後產生。不能把 NPC 對 A 的初始私人記憶自動放進 B 的上下文。

## 新地區的三層同步

新增地區時要分別處理：

1. `campaign/regions/<id>.json`：地區描述、規則、可見入口與可用 context tags。
2. `content_index.json`：讓 Context Builder 能找到地區。
3. 需要時建立共享 runtime 狀態，例如目前時間、門是否開啟、已觸發事件。

地區定義中的秘密預設 `gm_only`；玩家只有在角色觀察、事件揭露或情報分享後才取得相應情報。

## 內容發佈與可見性

新內容可以先是：

- `draft`：只有 GM 可見，不能出現在玩家視圖。
- `active`：已成為世界的一部分，但秘密仍依欄位過濾。
- `retired`：不再作為新上下文候選，但歷史事件仍保留。

「NPC 已建立」和「玩家已知道 NPC 存在」不是同一件事。AI 必須分別寫入內容狀態與玩家可見事件。

## AI 必須停止的情況

- 新內容 ID 與既有內容衝突。
- 不知道新內容應屬於哪個地區或可見範圍。
- 需要修改系統規則文件才能繼續。
- 內容變更無法同時更新索引、版本與事件。
- 玩家行動試圖呼叫 GM-only 的內容 API。

此時回傳 `requires_human_review: true`，而不是自行建立檔案或把新內容塞進敘事當成既成事實。
