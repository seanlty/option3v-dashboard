# Data Processing Plan

Last updated: 2026-06-19

本文件獨立記錄 option dashboard 的資料處理規劃，方便後續追蹤「歷史資料重建」與「即時資料快取」兩條主線。本文只描述資料端方案，不代表目前 production code 已實作。

## 背景與目標

目前可用資料源：

- Fugle API 即時期貨選擇權資料。
- FinMind 加權指數歷史資料。
- 期交所歷史 `.rpt` 檔，用於重建期貨/選擇權日內歷史資料。

需要解決的問題：

1. T 字報表在收盤後、非交易日期、非交易時段不能留白，要能顯示最後一個有效截面資料。
2. 部位明細與自動化交易選擇權部位需要即時損益計算。
3. 歷史回朔需要可重建的歷史資料。

## 兩個大方向

### 方向 A：歷史資料重建

目的：

- 支援歷史回朔頁籤。
- 支援台指期貨每日早盤 1 分 K 圖。
- 支援自動化交易部位在過去合約月份的回放與估值。
- 在沒有即時快取時，作為非交易時段的 fallback。

資料來源分工：

- FinMind 加權指數歷史資料：
  - 加權指數日 K。
  - 長週期走勢圖背景資料。
- 期交所 `.rpt`：
  - 台指期貨日內資料重建。
  - TXO 歷史成交資料或報價資料重建。
  - 若 `.rpt` 只有成交資料，能重建 last/volume/1 分 K；若含委買委賣，才能重建歷史 bid/ask T 字表。
- Fugle live 過程落地：
  - 未來可變成自己的歷史截面資料。
  - 對於 live 之後的歷史資料，會比單純期交所日資料更接近畫面狀態。

建議 pipeline：

```text
TAIFEX .rpt raw files
        ↓
parser normalize
        ↓
canonical database tables
        ↓
1m bars / option snapshots / settlement calendar index
        ↓
/api/history/contracts
/api/history/tquote?contract=YYYYMM&date=...
/api/history/positions?contract=YYYYMM
```

歷史資料重建的第一階段建議先做：

1. 定義 `.rpt` 檔案類型與欄位 mapping。
2. 重建 TXF 1 分 K。
3. 驗證交易日、夜盤/日盤切分、合約月份對應是否正確。
4. 再處理 TXO option chain 與 T 字截面。

2026-06-19 已先新增工具側 Parquet demo：

- 設計文件：`docs/taifex-history-parquet-store.md`
- 建檔工具：`tools/taifex_history_store.py`
- 預設 demo store：`data/processed/taifex_history_parquet_demo/`

這一版先針對 `OptionsDaily` 成交資料做 TXO 月選擇權歷史資料庫：

- `/api/history/contracts` 可用的合約清單。
- `contract_month + trading_date` 可查的歷史 option chain。
- `mark_source = taifex_rebuild_last` 的歷史部位估值。
- `stale: true`、`source: taifex_rebuild` 的非交易時段 fallback。
- TXO option daily / 1m trade bars。

限制仍相同：目前 `.rpt` 是成交資料，不含 bid/ask book，因此不能從此資料源單獨重建歷史 bid/ask T 字表。

### 方向 B：即時資料快取

目的：

- 解決 T 字報表非交易時段不留白。
- 解決即時部位損益。
- 讓前端只讀「最新可用截面」，不用自己判斷 live 或 stale。

建議資料流：

```text
Fugle books + aggregates
        ↓
Live in-memory state
        ↓
latest_snapshot table / latest snapshot JSON
        ↓
/api/tquote?mode=live-or-last
        ↓
T 字表 / VIX / 部位即時損益
```

即時資料快取策略：

- Fugle `books` 更新 bid/ask、bid size、ask size。
- Fugle `aggregates` 更新 last、volume、change、last update。
- 每 N 秒落地完整 T 字截面。
- 收盤後保留最後一個有效截面，API 回傳：
  - `stale: true`
  - `source: "fugle_cache"`
  - `snapshot_at: 最後有效更新時間`
- 非交易日或非交易時段啟動 dashboard：
  - 優先讀 latest live cache。
  - 若沒有 latest cache，再讀歷史重建資料最近可用截面。
  - 都沒有才顯示無資料。

## 共用資料契約

建議先定一個統一 snapshot shape，讓 T 字表、VIX、部位損益與歷史回朔都吃同一種資料格式。

```text
quote_snapshot
- contract_month: 202607
- trading_date: 2026-06-19
- session: day / night / closed
- snapshot_at
- source: fugle_live / fugle_cache / taifex_rebuild
- stale: true / false
- underlying:
  - symbol
  - price
- rows:
  - strike
  - call:
      bid
      ask
      bid_size
      ask_size
      last
      volume
      iv
      delta
      gamma
      theta
      vega
  - put:
      bid
      ask
      bid_size
      ask_size
      last
      volume
      iv
      delta
      gamma
      theta
      vega
- vix
```

部位估值輸出建議：

```text
position_valuation
- position_id
- strategy_id
- contract_month
- symbol
- side
- qty
- entry_price
- mark_price
- mark_source: live_mid / live_last / cache_mid / historical_last / missing
- mark_at
- daily_pnl
- total_pnl
- delta
- gamma
- theta
- vega
```

## 部位即時損益計算

估值規則建議：

- Option 市價優先順序：
  1. mid = `(bid + ask) / 2`
  2. last
  3. 前一筆 cached mark
  4. missing
- Futures 市價優先順序：
  1. Fugle TXF/MXF/TMF live price
  2. latest cached futures snapshot
  3. historical close/last
  4. missing
- 每筆估值都要帶 `mark_source` 與 `mark_at`，避免 UI 看起來是即時價但其實是舊截面。

手動部位與自動化部位都應走同一套 valuation engine：

```text
positions
      +
quote_snapshot
      ↓
valuation engine
      ↓
manual position table
automation position table
history replay table
```

## 資料庫設計

### 推薦主資料庫：PostgreSQL

理由：

- 適合部署在 Zeabur。
- 可以同時存關聯資料、時間序列索引與 JSONB snapshot。
- 比 SQLite 更適合未來部署、多人使用、背景 job、歷史查詢與資料庫備份。
- 後續若資料量大，可以做 partition 或把 raw 檔案移到 object storage。

### 不建議直接用 SQLite 當 production 主資料庫

SQLite 可以保留為 local dev 或單機 demo，但 production 不建議作為主資料庫：

- Zeabur service 預設偏 stateless；SQLite 必須依賴 volume。
- SQLite 需要處理單檔備份、檔案鎖與多 worker 問題。
- 歷史日內資料量增加後，查詢與維護會逐漸吃力。

### 初版 schema 草案

```text
contracts
- id
- product
- contract_month
- settlement_date
- first_trade_date
- last_trade_date
- created_at

quote_snapshots
- id
- contract_month
- trading_date
- session
- snapshot_at
- source
- stale
- underlying_symbol
- underlying_price
- payload_jsonb
- created_at

option_quote_points
- id
- contract_month
- trading_date
- session
- timestamp
- symbol
- strike
- cp
- bid
- ask
- bid_size
- ask_size
- last
- volume
- source

intraday_bars
- id
- product
- symbol
- contract_month
- trading_date
- session
- minute
- open
- high
- low
- close
- volume
- source

position_events
- id
- strategy_id
- position_id
- contract_month
- opened_at
- closed_at
- symbol
- side
- qty
- entry_price
- source

position_valuations
- id
- position_id
- snapshot_at
- contract_month
- mark_price
- mark_source
- daily_pnl
- total_pnl
- delta
- gamma
- theta
- vega
```

建議索引：

```text
quote_snapshots(contract_month, snapshot_at desc)
quote_snapshots(trading_date, session)
option_quote_points(contract_month, trading_date, timestamp)
option_quote_points(symbol, timestamp)
intraday_bars(product, contract_month, trading_date, minute)
position_events(strategy_id, contract_month)
position_valuations(position_id, snapshot_at)
```

資料保留策略：

- `quote_snapshots`：
  - latest snapshot 永久保留每天收盤截面。
  - 日內高頻 snapshot 可先保留 30-90 天，之後壓縮或轉冷資料。
- `option_quote_points`：
  - 若資料量太大，先只保留 1 分鐘聚合或特定截面。
- `.rpt` raw file：
  - 不建議塞進 DB。
  - 建議放 object storage 或 Zeabur volume，再把解析後結果放 PostgreSQL。

## Zeabur 部署資料庫方案

Zeabur 官方文件目前顯示，在建立 service 時可以選 `Databases`，並快速部署 PostgreSQL、MySQL、MongoDB、Redis 等常見資料庫。Zeabur 也支援同一 project 內用 private networking 讓服務互相連線；官方文件用 PostgreSQL 當例子，hostname 類似 `postgresql.zeabur.internal`，port 可從 `POSTGRES_PORT` 環境變數取得，預設為 5432。

### 建議部署拓撲

```text
Zeabur Project
├─ app service
│  ├─ Python dashboard/API
│  ├─ Fugle live collector
│  ├─ TAIFEX rpt import job
│  └─ DATABASE_URL env
│
├─ PostgreSQL service
│  ├─ quote snapshots
│  ├─ historical bars
│  ├─ option quote points
│  └─ positions / valuations
│
└─ Redis service (optional)
   ├─ live latest quote cache
   └─ pub/sub for SSE fanout
```

推薦初期只部署：

- App service
- PostgreSQL service

Redis 可以等 SSE fanout、背景 worker、即時多連線壓力出現後再加。

### Zeabur PostgreSQL 部署步驟

1. 在 Zeabur 建立 project。
2. Deploy New Service。
3. 選 Databases。
4. 選 PostgreSQL。
5. 建立 app service。
6. 在 app service 設定環境變數：
   - `DATABASE_URL`
   - `FUGLE_TOKEN`
   - 其他必要 token
7. `DATABASE_URL` 使用 private networking 連線 PostgreSQL，例如：

```text
postgresql://USER:PASSWORD@postgresql.zeabur.internal:${POSTGRES_PORT}/DATABASE
```

實際 hostname、user、password、database name 要以 Zeabur dashboard 中該 PostgreSQL service 顯示的值為準。

### Volume 的角色

Zeabur 文件說明：service 預設是 stateless；若需要讓服務內某個目錄在重啟後保留資料，可以 mount Volumes。Volume 適合存：

- 原始 `.rpt` 檔暫存。
- import job 下載中的 raw files。
- 小型 local cache。

但要注意：

- 開啟 Volume 後，該 service restart 會有短暫 downtime，不能 zero-downtime restart。
- 第一次掛載 volume 到目錄時，該目錄既有資料會被清空，要先備份再掛載。
- Volume 不應取代 PostgreSQL 主資料庫。

### Raw `.rpt` 檔部署建議

初期：

- App service 掛 `/data` volume。
- `.rpt` raw files 放 `/data/raw/taifex/`。
- 解析後寫入 PostgreSQL。

中期：

- raw files 移到 object storage，例如 S3/R2 類型服務。
- PostgreSQL 只存 metadata、解析後資料與 snapshot。
- Zeabur app 只保留短期工作目錄，不依賴長期 volume。

## API 分層建議

```text
/api/live/tquote
- 回傳 live 或最後有效 T 字截面。

/api/live/positions
- 回傳目前手動/自動化部位即時估值。

/api/history/contracts
- 回傳可回朔合約月份。

/api/history/tquote
- 依 contract/date/session 回傳歷史 T 字截面。

/api/history/futures-1m
- 依 product/contract/date/session 回傳台指期 1 分 K。

/api/history/positions
- 依 contract_month 回傳自動化部位歷史回放。
```

## 建議實作順序

1. 定義 DB schema 與共用資料契約。
2. 建 PostgreSQL local dev DB，先不急著上 Zeabur。
3. 實作 Fugle live snapshot 落地：
   - latest snapshot
   - snapshot history
   - stale/live 標記
4. 前端 T 字表改吃 `/api/live/tquote`，解決非交易時段不留白。
5. 實作 valuation engine，讓手動部位與自動化部位都使用同一套估值。
6. 實作 TAIFEX `.rpt` parser，先重建 TXF 1 分 K。
7. 實作 TXO 歷史 option data 重建。
8. 接上歷史回朔 tab。
9. 部署到 Zeabur：
   - app service
   - PostgreSQL service
   - volume 或 object storage for raw `.rpt`
10. 補上 migration、backup、import job 與健康檢查。

## 需要先確認的問題

- 期交所 `.rpt` 檔案實際欄位是否包含 bid/ask，還是只有成交價量。
- 歷史回朔需要的最小粒度：
  - tick
  - 1 分鐘
  - 5 分鐘
  - 日結截面
- 自動化交易部位的資料來源：
  - 由策略每日產生 position events。
  - 還是由外部檔案/資料庫匯入。
- Zeabur production 是否需要 Redis：
  - 初期可以不用。
  - 若 SSE 多連線或 collector/app 分離，再加 Redis。
- raw `.rpt` 是否要永久保留在 Zeabur volume，或同步到外部 object storage。

## 參考資料

- Zeabur Create Service / Databases: https://zeabur.com/docs/en-US/deploy/create/create-service
- Zeabur Private Networking: https://zeabur.com/docs/en-US/deploy/networking/private-networking
- Zeabur Volumes: https://zeabur.com/docs/en-US/data-management/volumes
