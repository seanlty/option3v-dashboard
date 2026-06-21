# Fugle Live Snapshot Cache Demo

Last updated: 2026-06-19

本文件記錄第一版 Fugle live snapshot 落地快取 demo。這一步不改 production code，只用 `tools/` 內的獨立工具驗證：

1. 非交易時段 Fugle REST API 還能拿到哪些欄位。
2. 能否把 API 回傳轉成 `quote_snapshot` v0.1。
3. 能否落地成 latest cache，讓 T 字表之後可以從最後有效截面回補，不留白。
4. 能否用同一份 snapshot 建出 demo `position_valuation`。

## Demo 指令

```powershell
python tools\fugle_live_snapshot_cache_demo.py --contract 202607 --strikes 9
```

強制模擬非交易時段 stale cache 回讀：

```powershell
python tools\fugle_live_snapshot_cache_demo.py --contract 202607 --strikes 9 --force-stale-readback
```

輸出位置：

```text
data/processed/fugle_live_snapshot_cache/
├─ latest_quote_snapshot.json
├─ latest_position_valuation.json
├─ latest_payload.json
├─ fugle_live_snapshot_cache_demo.json
└─ history/
   ├─ *_quote_snapshot.json
   └─ *_position_valuation.json
```

`data/processed/*` 目前在 `.gitignore` 中，因此這些落地資料預設只留在本機，不進 git。

## Demo 資料流

```text
Fugle REST intraday quote/tickers
        ↓
select TXO 202607 strikes around TXF center price
        ↓
quote_snapshot v0.1
        ↓
write latest cache + history file
        ↓
read latest cache back
        ↓
position_valuation v0.1
```

## 非交易時段不留白的核心邏輯

production 之後可以沿用這個決策順序：

1. 有 Fugle live / REST 可用資料時，更新 `latest_quote_snapshot`。
2. API 沒資料或不是交易時段時，讀 `latest_quote_snapshot`。
3. 若 cache 超過 TTL，仍回傳同一份資料，但標記：
   - `stale: true`
   - `source.type: fugle_cache`
   - `source.cache_age_seconds`
4. 前端 T 字表照樣渲染 rows，但狀態列顯示這是最後有效截面。
5. 部位估值使用同一份 snapshot，mark source 改成：
   - `cache_mid`
   - `cache_last`

這樣畫面不會空白，而且能明確知道目前不是 live。

## API Probe 摘要欄位

demo 輸出的 `api_probe` 會記錄：

```json
{
  "future_symbol": "TXFG6",
  "selected_symbols": [],
  "api_quote_count": 18,
  "api_bid_ask_count": 18,
  "api_last_count": 18,
  "quote_key_samples": {},
  "errors": []
}
```

重點是 `api_bid_ask_count` 與 `api_last_count`：

- `api_bid_ask_count > 0`：可以算 mid IV / Greeks。
- `api_bid_ask_count = 0` 但 `api_last_count > 0`：T 字表仍可顯示 Last/volume，但 IV/Greeks 先留空。
- 兩者都為 0：只能保留履約價骨架或退回前一份 cache。

## 和 quote_snapshot / position_valuation 的銜接

`quote_snapshot` 會保留：

- `rows[].call/put.bid`
- `rows[].call/put.ask`
- `rows[].call/put.mid`
- `rows[].call/put.last`
- `rows[].call/put.volume`
- `rows[].call/put.mid_iv`
- `rows[].call/put.delta/gamma/theta/vega`
- `vix.value_percent`
- `stale`
- `source`

`position_valuation` 會用同一份 snapshot 產生：

- `mark.price`
- `mark.source`
- `mark.stale`
- `pnl.unrealized_twd`
- `unit_greeks`
- `position_greeks`
- `totals`

Option Greeks 仍維持前面確認過的原則：只用 bid/ask mid 推出的 `mid_iv` 計算。沒有 bid/ask mid 時，該 leg 的 IV/Greeks 會是 null，不用 last 亂算。

## 下一步

1. 跑 demo 觀察非交易時段 Fugle REST 實際可回傳的欄位。
2. 確認 `latest_quote_snapshot.json` 是否足以讓 T 字表完整渲染。
3. 再設計 production 版 cache writer：
   - SSE live loop 寫入最新 snapshot。
   - REST probe 作為補救。
   - API endpoint 回傳 live-or-last snapshot。
4. 最後才把前端 T 字表 endpoint 從 `/api/fugle-tquote` 改成新的 `/api/live/tquote`。
