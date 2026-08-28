# 分支與劇本分工

本專案採用「平台核心」與「劇本內容」分開的簡單方式：

| 分支 | 用途 |
| --- | --- |
| `main` | 通用平台、JSON 資料邊界、HTTP API、LLM 介面與測試。不要放特定劇本秘密。 |
| `trpg/7day_test` | 《七日誓約》專用內容、初始世界狀態、NPC／地區／任務與該劇本測試。 |

## 替換劇本

新增劇本時，以 `main` 為基礎建立新的劇本分支，例如 `trpg/new_campaign`，只替換或新增：

- `game/campaign/`：劇本文件與內容索引。
- `game/manifest.json`：`campaign_id`、名稱、版本與劇本文件入口。
- `game/shared/world.json`：該劇本的初始共享狀態。
- `game/players/`：該劇本要使用的玩家名單與初始角色狀態。
- `game/shared/npcs/`：該劇本的初始 NPC 狀態。

平台程式放在 `src/trpg_platform/`，除非真的新增通用能力，否則換劇本不需要修改。不要把一個劇本的執行中玩家資料或秘密內容合併回 `main`。

目前 `trpg/7day_test` 就是七日誓約的完整測試分支；未來換劇本時保留同樣的資料入口，玩家端 API 與啟動提示不必跟著重寫。
