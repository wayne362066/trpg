# 區網多人 AI GM TRPG（JSON 核心原型）

這是第一個可運作的最小骨架，先驗證資料邊界與遊戲事件流程，不引入 Laravel、資料庫或 Redis。

## 目前的設計

- `game/shared/`：所有玩家共用的客觀世界狀態與事件歷史。
- `game/players/<player-id>/`：每位玩家獨立的角色、物品、情報、NPC 記憶與私人事件。
- `game/interactions/`：跨玩家互動的追加式紀錄。
- `GM_PROTOCOL.md`：GM 每次生成前必須讀取的協議，定義資料隔離與可見性。
- `PLAYER_START_PROMPT.md`：可直接貼給任一玩家 Codex 的通用啟動提示；每位玩家填入自己的 `player_id`。
- `CAMPAIGN_BRANCHING.md`：說明 `main` 平台核心與劇本分支的分工。
- `src/trpg_platform/`：極簡仲裁層；玩家只能提交行動，只有仲裁層能寫入正式狀態。

`FakeLlmClient` 只用於測試與示範；正式判定可使用 `--gm-backend codex` 啟動本機 GM Codex。未來也可替換成 Ollama、LM Studio、llama.cpp 或其他 GM adapter。

## 執行測試

```bash
python3 -m unittest discover -s tests -v
```

## 啟動區網 HTTP 原型

```bash
PYTHONPATH=src python3 -m trpg_platform.server --data-dir game --host 0.0.0.0 --port 8787
```

上面的指令使用 `FakeLlmClient`，只適合測試連線與資料驗證。要讓 GM Codex 自動判定，請在 GM 主機使用 `codex` CLI 已登入的環境啟動背景判定端：

```bash
PYTHONPATH=src python3 -m trpg_platform.server \
  --data-dir game \
  --host 0.0.0.0 \
  --port 8787 \
  --gm-backend codex
```

`--gm-backend codex` 會由遊戲伺服器在本機啟動一個 GM Codex app-server 工作階段；玩家端不需要、也不應該直接連到這個工作階段。省略 `--gm-model` 時使用 Codex 預設模型；若模型服務無法使用，正式行動會被拒絕或要求重試，不會寫入半套狀態。

目前端點：

- `GET /health`
- `GET /rooms/main/bootstrap?player_id={player_id}`
- `GET /rooms/main/view?player_id={player_id}`
- `POST /rooms/main/actions`，JSON body：`{"player_id":"{player_id}","text":"..."}`

這一輪刻意只提供最小入口，尚未處理登入、WebSocket 或完整前端。`--gm-backend codex` 是本機 GM Codex 的最小連線方式。

## 重要限制

玩家端不能直接取得 `game/` 原始目錄。正式整合時，玩家 Codex 應透過 API／MCP 取得自己的視圖與提交行動；GM 仲裁層才可以讀取完整資料。

## 區網連線

原型階段不使用 Token。每個請求帶上正確的 `player_id` 即可；這是刻意針對可信任區網的簡化。玩家可以把 ID 放在 query、JSON body，或 `X-Player-Id` header。

```bash
# 主機端，讓區網裝置可以連入
PYTHONPATH=src python3 -m trpg_platform.server --host 0.0.0.0 --port 8787

# 用主機自己的位址或區網位址測試，兩者走完全相同的 HTTP 路徑
curl 'http://127.0.0.1:8787/rooms/main/connection?player_id={player_id}'
curl 'http://192.168.1.50:8787/rooms/main/bootstrap?player_id={player_id}'
curl 'http://192.168.1.50:8787/rooms/main/connection?player_id={player_id}'
curl 'http://192.168.1.50:8787/rooms/main/view?player_id={player_id}'
```

目前的連線檢查只驗證「服務可達」與「玩家 ID 存在」，不提供對抗惡意區網使用者的身分安全性；若未來需要，再把身分層替換成 Token，不必改變 LLM 或資料仲裁流程。
