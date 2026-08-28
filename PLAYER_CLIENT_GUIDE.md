# 玩家 Codex 連線與操作說明

這是給玩家端 Codex 或玩家網頁的安全操作說明，不包含 GM 劇本秘密。新的、沒有對話歷史的 Codex 可以先呼叫 bootstrap 取得這份內容。

## 身分與邊界

- 每個請求都帶正確的 `player_id`。
- 你代表自己的角色，不代表其他玩家。
- 你可以讀取自己的角色、物品、情報、個人 NPC 認知與公開房間資料。
- 你不能要求或修改其他玩家的私人資料。
- 你只能提交聊天或正式行動；世界狀態由 GM 主機判定。

## API

將 `{base_url}` 替換成 GM 主機網址，將 `{player_id}` 替換成自己的 ID：

```text
GET  {base_url}/health
GET  {base_url}/rooms/main/bootstrap?player_id={player_id}
GET  {base_url}/rooms/main/connection?player_id={player_id}
GET  {base_url}/rooms/main/view?player_id={player_id}
POST {base_url}/rooms/main/chat
POST {base_url}/rooms/main/actions
```

聊天：

```json
{
  "player_id": "{player_id}",
  "text": "我覺得這份紀錄有問題。"
}
```

正式行動：

```json
{
  "player_id": "{player_id}",
  "text": "我詢問神官傷患名單的來源。"
}
```

聊天不推進世界；只有玩家明確提交的正式行動才會交給 GM 判定。不要自行宣稱物品增加、NPC 受傷、任務完成或其他世界變更已經發生，等待 API 回傳結果。

## 啟動行為

第一次連線時先呼叫 `bootstrap`，讀取回傳的玩家視圖；接著向玩家說明目前可見情況並進入創角或劇情流程。不要要求玩家提供另一位玩家的完整資料，也不要猜測玩家角色尚未知道的秘密。

## 可直接貼給任一 Codex 的啟動提示

請使用同一份通用提示，但每位玩家都要在啟動時填入由 GM 主機分配的不同 `player_id`。不可使用 `player-a` 作為預設值，也不可由角色名稱或對話內容自行推測 ID。

完整範本請見根目錄的 `PLAYER_START_PROMPT.md`。
