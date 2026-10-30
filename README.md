# 加密貨幣自動交易系統（地端，MAX ETH/TWD 現貨）

個人自用、只操作自己帳戶。延續 Stock_Robot 的做法：**先研究、再模擬、事先訂好照抄標準，達標才考慮真錢**。

- 目前狀態：九輪研究只留下比例再平衡；**模擬帳戶**（主帳戶＋影子，各 6,000 台幣）2026-10-26 開始，**真單**（同一套規則，6,000 台幣）2026-10-27 開始。進度見 [docs/DESIGN.md](docs/DESIGN.md) 第 8 節。
- 維運（Agent 分工、出錯流程圖、先看哪裡）：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 設計與規格：[docs/DESIGN.md](docs/DESIGN.md)（第 0 節列出與原始規格不同的地方，已確認）。
- 真單：`.env` 設 `LIVE_TRADING=1` 與 `LIVE_CONFIRM=I_UNDERSTAND` 才會下單（MAX 金鑰只開查詢＋交易，不開提領），沒設就只跑模擬。2026-10-27 起真單開啟，階段上限 9,000 元。

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

主帳戶 ETH 50%＋USDT 37.5%＋台幣 12.5%，任何一項偏離 5 個百分點就全部調回；用 MAX 當下盤口模擬成交。每天 01:35 由排程執行、失敗就每小時重試到 07:35（只在 01:30～08:00 動作），08:30 推送當天凌晨的結果到 Discord。
2026-10-26 起主帳戶是三資產（ETH 50%＋USDT 37.5%＋台幣 12.5%，偏離 5 個百分點調回），同一個排程另跑一個**影子帳戶**（原本的 ETH 50%＋台幣 50%），只記錄、和主帳戶比較。
```bash
.venv\Scripts\python.exe scripts/paper_rebalance.py --dry-run   # 只印出
.venv\Scripts\python.exe scripts/paper_rebalance.py --status    # 帳戶走勢與交易紀錄
.venv\Scripts\python.exe scripts/paper_rebalance.py --report --no-send   # 預覽 08:30 的通知
```
建立排程（以**系統管理員身分**開 PowerShell）：`powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1`（加 `-Disable` 停用）。
真實帳戶：`.env` 有 MAX 金鑰（查詢＋交易，不開提領）時，同一個排程每天讀一次真實餘額與公開 IP（真單開啟時在下單之後讀）；手動讀用 `scripts/live_check.py`，IP 變了更新 MAX 白名單後加 `--accept-ip`。
Stock_Robot 的本機操作台有「虛擬貨幣」分頁，顯示帳戶、Agent 健康、排程與紀錄（資料來自 `scripts/console_data.py`，只讀）。
備份：每天 08:30 報告推送後把 `state/` 與 `reports/` 壓縮到 `.env` 的 `BACKUP_DIRS`（不含 `.env`）；手動：`scripts\backup.py`。
要推送 Discord：複製 `.env.example` 為 `.env`，填 `PAPER_WEBHOOK_URL` 或 `DISCORD_WEBHOOK_URL`。暫停：建立 `state/paper_rebalance.paused`。

## 研究

```bash
.venv\Scripts\python.exe scripts/research.py --stage dev       # 比例再平衡研究期 2021～2023：12 組規則，報告寫到 reports/
.venv\Scripts\python.exe scripts/research.py --stage holdout   # 考卷期（已考過）：顯示當時的結果
```
九輪研究（均線交叉、日 K 趨勢、比例再平衡、凌晨買低賣高、三資產再平衡、每小時檢查、山寨幣突破、恐懼與貪婪指數、加入 BTC）的結果記在 docs/DESIGN.md 6.3；只有比例再平衡留下，沒通過的程式已刪除，留在 git 歷史。規則組合與通過標準在 `src/crypto_robot/lab/rebalance_variants.py`（看結果前訂定）。考卷結果存在 `state/research/holdout.json`，之後執行只顯示當時的結果。

## 測試

網路請求一律以假資料替代（`tests/fakes.py`，內容取自 MAX 真實回應），不會連線。

本專案僅供研究學習用途，非投資建議。
