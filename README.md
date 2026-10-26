# 加密貨幣自動交易系統（地端，MAX ETH/TWD 現貨）

個人自用、只操作自己帳戶。延續 Stock_Robot 的做法：**先研究、再模擬、事先訂好照抄標準，達標才考慮真錢**。

- 目前狀態：四輪研究在研究期都未通過；**比例再平衡模擬帳戶**（6,000 台幣、不下真單）2026-10-26 開始運作。進度見 [docs/DESIGN.md](docs/DESIGN.md) 第 8 節。
- 設計與規格：[docs/DESIGN.md](docs/DESIGN.md)（第 0 節列出與原始規格不同的地方，已確認）。
- 預設 `DRY_RUN=1`（模擬帳戶），不會下真單。目前只用到 MAX 公開 API，**不需要 API Key**。

## 安裝

需要 Windows、Python 3.12。在 PowerShell 執行：
```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pytest -q
```

## K 線資料

```bash
.venv\Scripts\python.exe scripts/fetch_bars.py              # 補 ethtwd 1h K 線到 data/bars.db（第一次從 2018 年起，約 1 分鐘）
.venv\Scripts\python.exe scripts/fetch_bars.py --no-fetch   # 只看快取的資料品質與每年流動性
```

## 比例再平衡模擬帳戶

ETH 目標 50%，偏離 10 個百分點就調回；用 MAX 當下盤口模擬成交，不下真單。每天 01:35 由排程執行（只在 01:30～07:30 動作），08:30 推送當天凌晨的結果到 Discord。
```bash
.venv\Scripts\python.exe scripts/paper_rebalance.py --dry-run   # 只印出
.venv\Scripts\python.exe scripts/paper_rebalance.py --status    # 帳戶走勢與交易紀錄
.venv\Scripts\python.exe scripts/paper_rebalance.py --report --no-send   # 預覽 08:30 的通知
```
建立排程（以**系統管理員身分**開 PowerShell）：`powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1`（加 `-Disable` 停用）。
Stock_Robot 的本機操作台有「虛擬貨幣」分頁，顯示帳戶、Agent 健康、排程與紀錄（資料來自 `scripts/console_data.py`，只讀）。
要推送 Discord：複製 `.env.example` 為 `.env`，填 `PAPER_WEBHOOK_URL` 或 `DISCORD_WEBHOOK_URL`。暫停：建立 `state/paper_rebalance.paused`。

## 研究

```bash
.venv\Scripts\python.exe scripts/research.py --stage dev       # 比例再平衡研究期 2021～2023：12 組規則，報告寫到 reports/
.venv\Scripts\python.exe scripts/research.py --stage holdout   # 考卷期（已考過）：顯示當時的結果
```
四輪研究（1h 均線交叉、日 K 趨勢／買低賣高、比例再平衡、凌晨時段買低賣高）的結果記在 docs/DESIGN.md 6.3；沒通過的三輪程式已刪除，留在 git 歷史。規則組合與通過標準在 `src/crypto_robot/lab/rebalance_variants.py`（看結果前訂定）。考卷結果存在 `state/research/holdout.json`，之後執行只顯示當時的結果。

## 測試

網路請求一律以假資料替代（`tests/fakes.py`，內容取自 MAX 真實回應），不會連線。

本專案僅供研究學習用途，非投資建議。
