# Quote Snapshot / Position Valuation Demo

Last updated: 2026-06-19

本文件是資料端第一步的初版 demo 契約。目標是在不改動 production code 的前提下，先把「即時或快取報價截面」與「部位估值結果」拆成兩個清楚的資料格式，讓前端 T 字表、VIX、手動部位、自動化交易部位與歷史回朔之後可以吃同一套 shape。

Demo 產生器：

```powershell
python tools\quote_position_valuation_demo.py
```

預設輸出：

```text
data/processed/quote_position_valuation_demo.json
```

## 設計原則

- `quote_snapshot` 只描述某一個時間點的市場截面，不放使用者部位。
- `position_valuation` 只描述用某一份 snapshot 估完的一批部位，不重新包完整 option chain。
- 所有時間使用 ISO-8601，保留 timezone，例如 `2026-06-19T13:29:45+08:00`。
- Option IV 在 leg 欄位中用 decimal，例如 `0.245` 表示 `24.5%`；前端顯示時再轉百分比。
- VIX 同時提供 `value_decimal` 與 `value_percent`，避免計算端和圖表端混淆。
- Greeks 分兩層：
  - `quote_snapshot.rows[].call/put.delta/gamma/theta/vega` 是單一選擇權、未乘口數與點值的模型值。
  - `position_valuation.positions[].position_greeks` 是已乘上買賣方向、口數與合約乘數後的部位曝險。
- Option Greeks 一律使用 `mid_price = (bid + ask) / 2` 推出的 `mid_iv` 計算。
- 部位估值 mark price 優先使用 live/cache 的 bid/ask mid，沒有 mid 才退回 last，再沒有才標記 missing。
- 每筆報價與估值都帶 `source` / `mark_source` / `stale` / `quality`，避免非交易時段看起來像即時價但其實是舊截面。

## quote_snapshot v0.1

用途：

- T 字表。
- VIX 速算 line chart。
- 部位即時損益計算。
- 非交易時段最後有效截面。
- 歷史回朔的某一個重建截面。

建議 shape：

```json
{
  "schema": "quote_snapshot",
  "schema_version": "0.1.0",
  "snapshot_id": "quote_snapshot:TXO:202607:2026-06-19T13:29:45+08:00",
  "exchange": "TAIFEX",
  "product": "TXO",
  "contract_month": "202607",
  "settlement_date": "2026-07-15",
  "trading_date": "2026-06-19",
  "session": "day",
  "snapshot_at": "2026-06-19T13:29:45+08:00",
  "received_at": "2026-06-19T13:29:46+08:00",
  "status": "ok",
  "stale": false,
  "source": {
    "type": "demo_static",
    "provider": "local_demo"
  },
  "underlying": {
    "product": "TXF",
    "symbol": "TXFG6",
    "price": 22642.0,
    "source": "demo_static",
    "updated_at": "2026-06-19T13:29:45+08:00"
  },
  "risk_model": {
    "model": "black76",
    "risk_free_rate": 0.015,
    "expiry_at": "2026-07-15T13:30:00+08:00",
    "time_to_expiry_years": 0.071234,
    "iv_basis": "mid_price"
  },
  "rows": [],
  "vix": {},
  "metadata": {}
}
```

### rows leg 欄位

每一列以履約價為單位，左右各一個 call / put leg。

```json
{
  "strike": 22600,
  "call": {
    "symbol": "TXO22600G6",
    "type": "call",
    "bid": 486.0,
    "ask": 498.0,
    "mid": 492.0,
    "bid_size": 22,
    "ask_size": 18,
    "last": 493.0,
    "volume": 1640,
    "change": 8.5,
    "change_percent": 0.0175,
    "quote_at": "2026-06-19T13:29:45+08:00",
    "aggregate_at": "2026-06-19T13:29:45+08:00",
    "bid_iv": 0.2381,
    "ask_iv": 0.2449,
    "mid_iv": 0.2415,
    "delta": 0.5182,
    "gamma": 0.00044,
    "theta": -8.91,
    "vega": 23.51,
    "greeks_source": "black76_mid_iv",
    "quality": {
      "status": "ok",
      "bid_ask_state": "normal",
      "stale": false,
      "age_seconds": 0,
      "warnings": []
    }
  },
  "put": {}
}
```

前端欄位對應：

| 畫面 | quote_snapshot 欄位 |
| --- | --- |
| T 字表成交量 | `rows[].call.volume`, `rows[].put.volume` |
| T 字表 Last | `last` |
| 買量 / 賣量 | `bid_size`, `ask_size` |
| 買價 / 賣價 | `bid`, `ask` |
| IV | `mid_iv` |
| Delta/Gamma/Theta/Vega | `delta`, `gamma`, `theta`, `vega` |
| VIX 卡片與 line chart | `vix.value_percent`, `vix_series` |

## position_valuation v0.1

用途：

- 手動部位明細。
- 自動化交易選擇權部位。
- 歷史回朔部位監控表。
- 策略層級曝險彙總。

建議 shape：

```json
{
  "schema": "position_valuation",
  "schema_version": "0.1.0",
  "valuation_id": "position_valuation:202607:2026-06-19T13:29:45+08:00",
  "snapshot_id": "quote_snapshot:TXO:202607:2026-06-19T13:29:45+08:00",
  "as_of": "2026-06-19T13:29:45+08:00",
  "contract_month": "202607",
  "currency": "TWD",
  "positions": [],
  "totals": {},
  "quality": {
    "status": "ok",
    "missing_position_count": 0,
    "stale_position_count": 0
  }
}
```

### position 欄位

```json
{
  "position_id": "manual-001",
  "book": "manual",
  "strategy_id": null,
  "instrument": "option",
  "product": "TXO",
  "contract_month": "202607",
  "symbol": "TXO22600G6",
  "option_type": "call",
  "strike": 22600,
  "side": "long",
  "qty": 2,
  "entry_price": 450.0,
  "multiplier": 50,
  "mark": {
    "price": 492.0,
    "source": "live_mid",
    "at": "2026-06-19T13:29:45+08:00",
    "stale": false
  },
  "pnl": {
    "points": 84.0,
    "unrealized_twd": 4200.0,
    "day_twd": null
  },
  "unit_greeks": {
    "iv": 0.2415,
    "delta": 0.5182,
    "gamma": 0.00044,
    "theta": -8.91,
    "vega": 23.51
  },
  "position_greeks": {
    "delta": 51.82,
    "gamma": 0.044,
    "theta": -891.0,
    "vega": 2351.0
  },
  "quality": {
    "status": "ok",
    "warnings": []
  }
}
```

前端欄位對應：

| 畫面 | position_valuation 欄位 |
| --- | --- |
| 類型 | `book` 或 `source` 對應實際/模擬/自動化 |
| 契約 | `product`, `contract_month`, `strike`, `option_type` |
| 買賣 | `side` |
| 口數 | `qty` |
| 建倉價 | `entry_price` |
| 市價 | `mark.price` |
| 損益 | `pnl.unrealized_twd` |
| Delta/Gamma/Theta/Vega | `position_greeks.*` |
| IV | `unit_greeks.iv` |
| 報價來源提示 | `mark.source`, `mark.at`, `mark.stale` |

## Mark Source 建議枚舉

```text
live_mid          Fugle live bid/ask mid
live_last         Fugle live aggregate last
cache_mid         最後有效快取 bid/ask mid
cache_last        最後有效快取 last
historical_mid    歷史重建 bid/ask mid
historical_last   歷史重建 last
entry             沒有市場價時暫用建倉價
missing           找不到可用報價
```

## 下一步銜接點

1. 先確認本文件欄位命名、單位與 mark priority。
2. 再把 Fugle live payload 轉成 `quote_snapshot`，但保留舊 endpoint 不動。
3. 建一個 valuation engine，讓手動部位與自動化部位都輸出 `position_valuation`。
4. 最後才把 production API / frontend 改吃新資料契約。
