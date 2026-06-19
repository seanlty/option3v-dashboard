# 2026-06-19 Change Log

本文件記錄截至 2026-06-19 的 option dashboard 主要改動，重點是 Fugle 即時期權資料接入、近月 TXO T 字報價、VIX 速算、盤勢判別 UI、走勢圖區塊與自動化部位區塊調整。

## Fugle 即時期權資料

- 新增 Fugle 期貨選擇權 live demo 與 production service 雛形。
- 使用 Fugle WebSocket `books` 作為 live bid/ask 來源。
- 新增 Fugle `aggregates` 訂閱，用於取得 last price、累計成交量、漲跌與最後更新時間。
- `.env` 中的 Fugle token 僅由程式讀取，文件不記錄 token 內容。
- Production API 新增：
  - `/api/fugle-tquote`
  - `/api/fugle-tquote-events`
- `requirements.txt` 新增 `websockets`。

## TXO T 字報價與 Greeks

- 原 T 字表改為 Fugle live T 字報價表。
- 表格欄位新增：
  - 成交量
  - Last
  - bid/ask size
  - bid/ask price
  - IV
  - Delta
  - Gamma
  - Theta
  - Vega
- bid/ask 仍由 Fugle `books` 負責。
- last price、累計成交量與更新資訊由 Fugle `aggregates` 補上。
- IV 使用本機 Black-76 反推：
  - bid IV
  - ask IV
  - mid IV
- Greeks 統一使用 `mid_price = (bid_price + ask_price) / 2` 對應的 Mid IV 計算：
  - Delta
  - Gamma
  - Theta
  - Vega

## 台指選擇權 VIX 速算

- 在 T 字表上方加入台指選擇權波動率 VIX 速算。
- 計算方式：
  - 取 ATM 附近 4 個 Call IV
  - 取 ATM 附近 4 個 Put IV
  - 以距離 ATM 的權重做加權平均
- 前端新增 VIX line chart。
- VIX 序列會保留到 demo/server thread 結束為止。

## 盤勢判別建議

- 3V 策略表已合併進「盤勢判別建議」區塊。
- 區塊右上角新增 tab：
  - 盤勢判別
  - 3V 策略表
- 原本盤勢判別上方的舊摘要 chip 已移除：
  - 淨 Delta
  - Gamma
  - Theta
  - Vega
  - ATM IV
  - Put Skew
- 底部 AI 輔助判斷總結保留。
- 盤勢判別上方改為三欄水平排列：
  - VIEW
  - VOLATILITY
  - TIME VALUE
- `TIME VALUE` 會依目前最後交易日在結算行事曆上的位置顯示：
  - P1（買方天堂）
  - P2（賣方天堂）
  - P3（收割期）
- `VOLATILITY` 目前保留 placeholder：
  - 波動上升
  - 波動持平
  - 波動下降
- `VIEW` 改為三列：
  - H2
  - PK
  - RD
- 每列再左右切分為：
  - H：過熱K
  - 2：2Q
  - P：樞紐點
  - K：關鍵K
  - R：修正比例
  - D：道式防線

## 走勢圖區塊

- 「加權指數日線」標題改為「走勢圖」。
- 區塊右上角新增 tab：
  - 加權指數
  - 台指期
- 台指期 tab 目前是 placeholder，後續放台指期貨每日早盤 1 分 K 圖與歷史回朔資料。
- 加權指數日期預設：
  - 開始日：2025 最後一個結算日，目前為 `2025-12-17`
  - 結束日：最新日期
- 圖表載入資料後初始視窗會 fit 滿畫面。
- 移除先前過多右側留白，讓 K 線圖畫面更集中。
- 日期選擇控制列高度已壓低，減少圖表上方 UI 佔用空間。

## 自動化交易選擇權部位

- 「自動化交易選擇權部位」區塊新增 tab：
  - 本期部位
  - 歷史回朔
- 移除「同步報價快取」按鈕。
- 即時報價後續改為直接串 Fugle API 計算自動化部位損益。
- 歷史回朔頁籤新增：
  - 歷史合約月份下拉選單
  - 與本期部位相同欄位的部位監控表格
- 歷史回朔資料載入邏輯目前保留 placeholder，後續補上。

## 主要檔案

- `src/fugle_live.py`
  - Fugle live T 字報價 service、Black-76 IV/Greeks、VIX 速算。
- `src/main.py`
  - Fugle service 啟動與 API/SSE endpoints。
- `app.js`
  - Fugle live T 字表渲染、VIX chart、盤勢判別 UI、走勢圖 tab、自動化部位 tab。
- `index.html`
  - T 字表欄位、VIX 區塊、盤勢判別 tab、走勢圖 tab、自動化部位歷史回朔 tab。
- `styles.css`
  - T 字表、VIX、盤勢三欄、VIEW 六格、自動化部位歷史頁籤與 responsive layout。
- `tools/fugle_live_tquote_demo.py`
  - Fugle live T 字報價 demo。
- `tests/test_fugle_futopt_live.py`
  - Fugle 文件/連線測試雛形。
- `tests/test_fugle_black76_model.py`
  - Black-76 與 IV/Greeks 測試。

## 已驗證

- Python syntax check：
  - `python -c "import ast, pathlib; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in pathlib.Path('src').glob('*.py')]; print('syntax ok')"`
- 目標測試：
  - `python -m pytest tests\test_main.py tests\test_fugle_black76_model.py -q`
  - 結果：`5 passed`
- Browser smoke check 已確認：
  - T 字表與 VIX chart 可渲染。
  - 盤勢判別 tab 可切換。
  - 3V 策略表已合併進盤勢區塊。
  - VIEW/VOLATILITY/TIME VALUE 三欄等分。
  - VIEW 內 H/2/P/K/R/D 六格正確。
  - 走勢圖 tab 文案與預設日期正確。
  - 自動化部位「本期部位 / 歷史回朔」tab 可切換。
  - 歷史回朔合約月份下拉選單與 placeholder 表格正常。
  - console error 為 0。

## 待補項目

- `VIEW` 的 H/2/P/K/R/D 實際判斷邏輯。
- `VOLATILITY` 的波動上升/持平/下降判斷邏輯。
- 台指期貨每日早盤 1 分 K 圖與歷史資料回朔。
- 自動化交易部位直接串 Fugle API 的即時損益計算。
- 自動化交易歷史合約月份的回朔資料載入與表格填值。
