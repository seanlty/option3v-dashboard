# TAIFEX History Parquet Store Demo

Last updated: 2026-06-19

本文件記錄 `OptionsDaily_YYYY_MM_DD.rpt` 轉成可查詢 Parquet 歷史資料庫的初版設計。這一版先放在 `tools/`，用來驗證資料建檔、查詢與維護流程，不直接改 production API。

## 格式選擇

目前先選 Parquet，不選 HDF5：

- 本機環境已有 `pyarrow`，但沒有 PyTables。
- Parquet 適合依 `trading_date` 做分割與增量更新。
- 之後要搬到 object storage 或批次 job，比單一 HDF5 檔更容易維護。

工具：

```powershell
python tools\taifex_history_store.py --help
```

預設 store：

```text
data/processed/taifex_history_parquet/
```

## 資料表

```text
taifex_history_parquet/
├─ manifest.json
├─ contracts.parquet
├─ contract_strikes.parquet
├─ option_daily_series/
│  └─ year=YYYY/month=MM/data.parquet
└─ option_minute_bars/
   └─ year=YYYY/month=MM/data.parquet
```

### contracts.parquet

用途：支援 `/api/history/contracts`。

主要欄位：

- `product`
- `contract_month`
- `contract_kind`
- `first_trading_date`
- `last_trading_date`
- `trading_days`
- `strike_count`
- `option_series`
- `strike_min`
- `strike_max`
- `available_strikes_json`
- `total_volume`
- `total_trade_count`

### contract_strikes.parquet

用途：快速看每個合約月份有哪些 strikes，以及 Call/Put 成交量分布。

主要欄位：

- `product`
- `contract_month`
- `strike`
- `strike_text`
- `first_trading_date`
- `last_trading_date`
- `call_volume`
- `put_volume`
- `total_volume`
- `total_trade_count`

### option_daily_series

用途：支援 TXO 歷史 option chain、日 K、歷史部位估值、非交易時段 fallback。

粒度：

```text
trading_date + product + contract_month + strike + cp
```

主要欄位：

- `open`
- `high`
- `low`
- `close`
- `last`
- `volume`
- `trade_count`
- `first_trade_at`
- `last_trade_at`
- `source_files`

注意：`trading_date` 來自檔名，`first_trade_at` / `last_trade_at` 來自 RPT row 的實際成交日期時間，因此可保留夜盤跨日資訊。

### option_minute_bars

用途：支援 TXO 選擇權自己的 1 分 K。

粒度：

```text
trading_date + minute + product + contract_month + strike + cp
```

主要欄位與 daily series 相同，另外有：

- `session`: `day` / `night`
- `minute`: `YYYY-MM-DDTHH:MM:00`

## 五個可直接使用場景

### 1. 歷史回朔合約清單

Demo command：

```powershell
python tools\taifex_history_store.py contracts --store-dir data\processed\taifex_history_parquet --product TXO
```

對應資料：

- `contracts.parquet`
- `contract_strikes.parquet`

可直接提供：

- 有哪些 TXO 合約月份。
- 每個合約月份起訖交易日。
- 可用 strikes。
- 成交量與成交筆數。

### 2. TXO 歷史 option chain

Demo command：

```powershell
python tools\taifex_history_store.py chain --store-dir data\processed\taifex_history_parquet --contract-month 202607 --trading-date 2026-06-18
```

對應資料：

- `option_daily_series`

可直接提供：

- 每個履約價的 Call / Put。
- `open / high / low / close / last / volume / trade_count`。
- `first_trade_at / last_trade_at`。

限制：

- bid / ask / bid size / ask size 會是 `null`。
- `mark_source` 固定標成 `taifex_rebuild_last`。

### 3. 自動化部位歷史回放

Demo command：

```powershell
python tools\taifex_history_store.py positions --store-dir data\processed\taifex_history_parquet --contract-month 202607 --trading-date 2026-06-18
```

對應資料：

- `option_daily_series`

估值規則：

```text
mark.price = historical last = option_daily_series.last
mark.source = taifex_rebuild_last
mark.stale = true
```

這可以支援 `/api/history/positions`，但 UI 應清楚標示這是歷史成交價估值，不是 live mid。

### 4. 非交易時段 fallback

Demo command：

```powershell
python tools\taifex_history_store.py fallback --store-dir data\processed\taifex_history_parquet --contract-month 202607
```

對應資料：

- `option_daily_series`

回傳 payload 會帶：

```text
stale: true
source.type: taifex_rebuild
status: stale_fallback
```

用途：

- Fugle live cache 不存在時，顯示最近一個 TAIFEX rebuild 截面。
- 部位估值不留白。

### 5. 日 K / 分 K 類資料

日 K 來自 `option_daily_series`。

1 分 K 來自 `option_minute_bars`，建檔時需加 `--with-minute-bars`。

Demo command：

```powershell
python tools\taifex_history_store.py bars --store-dir data\processed\taifex_history_parquet --contract-month 202607 --trading-date 2026-06-18 --strike 22600 --cp C --freq 1m
```

## 建檔與維護

### 建單日 demo store

```powershell
python tools\taifex_history_store.py build `
  --year 2026 `
  --start-date 2026-06-18 `
  --through-date 2026-06-18 `
  --with-minute-bars `
  --store-dir data\processed\taifex_history_parquet_demo
```

### 建完整 2026 store

```powershell
python tools\taifex_history_store.py build `
  --year 2026 `
  --store-dir data\processed\taifex_history_parquet
```

若也要產生 1 分 K：

```powershell
python tools\taifex_history_store.py build `
  --year 2026 `
  --with-minute-bars `
  --store-dir data\processed\taifex_history_parquet
```

### 每日更新

假設新增一個 raw 檔：

```text
data/raw/taifex_rpt/OptionsDaily_2026_06_19.rpt
```

更新指令：

```powershell
python tools\taifex_history_store.py build `
  --year 2026 `
  --start-date 2026-06-19 `
  --through-date 2026-06-19 `
  --with-minute-bars `
  --store-dir data\processed\taifex_history_parquet
```

工具會重寫該交易日所在的 partition，並重新產生：

- `contracts.parquet`
- `contract_strikes.parquet`
- `manifest.json`

## 已知限制

- 這批 RPT 是成交資料，不是 order book。
- 無法從此資料源重建 bid / ask / bid size / ask size。
- IV / Greeks 若要做，只能用 last/close 近似，品質不等同 live mid IV。
- 目前先聚焦 TXO 月選擇權；週選可用 `--no-monthly-only` 另建。
