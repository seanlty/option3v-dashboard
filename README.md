# 台指月選擇權部位監控 Dashboard

第一版是純前端靜態頁，可以直接用本機 HTTP server 開啟。內建示範資料，輸入 FinMind token 後可抓取加權指數與台指選擇權日資料。

## 專案結構

```text
quant-assistant/
├─ src/
│  └─ main.py
├─ data/
│  ├─ raw/
│  └─ processed/
├─ notebooks/
├─ docs/
├─ tests/
│  └─ test_main.py
├─ index.html
├─ app.js
├─ styles.css
├─ README.md
├─ requirements.txt
└─ .gitignore
```

`data/raw/` 與 `data/processed/` 預設不納入 Git，只保留 `.gitkeep` 讓資料夾存在。API key 請放在 `.env`，不要直接提交到 repo。

## Python 開發環境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src\main.py --smoke
pytest
```

## 開啟方式

```powershell
python src\main.py
```

然後開啟：

```text
http://127.0.0.1:8765/index.html
```

`src/main.py` 會同時提供靜態頁面與 `/api/latest-quotes`，後端每 30 秒抓一次 FinMind `taiwan_futures_snapshot` 與 `taiwan_options_snapshot` 並快取，前端只讀本機快取。

## 已完成

- 部位明細表
- 新增、刪除、複製模擬部位
- 總部位 payoff 圖
- Black-Scholes 理論價、IV 反推、Greeks
- 風險摘要與盤勢判別建議
- FinMind 加權指數日資料搭配最新交易日 `TaiwanVariousIndicators5Seconds` 重建當日 K
- FinMind `TaiwanOptionDaily` 選擇權日資料
- 交易行事曆與到期提醒
- 選擇權 IV / Skew 監控表
- 當前月選擇權 T 型報價，使用近月台指期貨為中心顯示上下 3000 點內實際掛出的 TXO 履約價
- 加權指數 K 線下方 OP Score 每日評分副圖
- OP Score 日變化副圖，顯示今日評分與前一日評分差值
- 主圖下方事件列以 `S` 標記 2026 月選擇權已發生結算日與下一個即將到來結算日
- 月選擇權結算日紀錄於 `data/settlement_dates.json`，可持續擴充年份供歷史回顧使用

## FinMind 資料限制

- 加權指數歷史 K 線優先使用日資料端點，並用 `TaiwanStockTradingDate` 過濾休市日；最新交易日再用 `TaiwanVariousIndicators5Seconds` 重建當日 K。
- 選擇權即時資料 `taiwan_options_snapshot` 與期貨即時資料 `taiwan_futures_snapshot` 文件標示為 sponsor 會員功能；T 型報價由本機後端帶 `.env` 的 token 抓取，避免 token 暴露在瀏覽器。
- Token 是選填，但 sponsor 即時報價 endpoint 需要對應權限。

## 下一步建議

- 用 SQLite 儲存實際部位、風險快照與每日行情。
- 將即時選擇權報價抽象成 provider，後續可換 TAIFEX、券商 API 或 FinMind sponsor endpoint。
