# Crypto_Robot

MAX 交易所 ETH/TWD 現貨，個人自用。使用者說繁體中文；對使用者的回覆、文件、程式註解都用繁體中文，commit message 用英文。

角色：使用者是**營運者**（決定規則、資金、時段，看推送），Claude 是**開發者**（改程式、診斷、維護測試與文件）。目標是乾淨、安全的環境。
**出問題先看 `docs/ARCHITECTURE.md` 的快速索引與第 5 節流程圖**，不要從頭讀程式。

## 現況（2026-10-26）

- 唯一在跑的是**比例再平衡模擬帳戶**（不是真單）：`rebal-eth50-band10`（ETH 目標 50%，偏離 10 個百分點調回），6,000 台幣。
- 排程：01:35 執行 `paper_rebalance`（不推送）、08:30 執行 `paper_report`（推送 Discord）。
- 四輪研究共 118 組：只有比例再平衡留下。**考卷期（2024-01-01 起）已用掉**，結果在 `state/research/holdout.json`。
- **真單**：使用者有資金時會主動說；在那之前不要提議接真單、不要碰 MAX 私有 API 或金鑰。

## 不可違反的規則

- 自動交易只能在**台北時間 01:30～07:30**（使用者白天要用電腦）。
- 價格、數量、金額一律 `Decimal`（`core/num.py`）；交易路徑不准出現 float。報告統計可以用 float。
- 新研究：規則組合與通過標準**先寫好、先 commit，才跑結果**；沒通過不放寬標準。研究期資料（2021–2023）已看過四次。
- `.env`（webhook 等同密碼）、`state/`、`data/`、`logs/`、`reports/` 不進 git；不要在輸出中印出 webhook 網址。
- repo 是公開的（github.com/ShanHugo/Crypto_agent），只有 `main` 分支：改完 → 測試通過 → commit → push。
- `requirements.txt` 只能用 ASCII（中文 Windows 的 pip 讀不了 UTF-8 註解）。
- `scripts/register_tasks.ps1` 要存成 UTF-8 with BOM；建立排程需要系統管理員，交給使用者執行，不要自己跑。

## 要改什麼，改哪裡

| 想改 | 檔案 |
|---|---|
| 再平衡規則參數、初始資金、手續費 | `scripts/paper_rebalance.py` 開頭的 `PARAMS`、`INITIAL`、`TAKER_FEE` |
| 08:30 通知內容 | `scripts/paper_rebalance.py` 的 `message()`、`morning_text()` |
| 模擬成交、交易時段、帳戶紀錄 | `src/crypto_robot/paper/rebalance_account.py`（`WINDOW`、`run_day`、`day_report`）、`paper/fills.py` |
| 再平衡判斷（研究與模擬共用） | `src/crypto_robot/agents/strategy/rebalance.py` |
| 排程時間 | `scripts/register_tasks.ps1` 的 `$tasks`（改完請使用者以系統管理員重跑） |
| MAX 公開 API | `src/crypto_robot/exchange/max/public.py`（端點已實測，見 DESIGN.md 第 1 節） |
| 操作台「虛擬貨幣」分頁的資料、Agent 健康檢查 | `scripts/console_data.py`（只讀 JSON）、`src/crypto_robot/health.py`；畫面與 API 在 Stock_Robot 的 `src/web/templates/console.html`、`src/web/console.py`（`/api/crypto`） |
| 研究 | `src/crypto_robot/lab/`（`rebalance_variants.py` 是事先訂定的組合與標準，看過結果後不得修改） |

改再平衡參數會讓模擬帳戶前後不一致：先問使用者要「接著用舊帳戶」還是「重開帳戶（備份 `state/paper_rebalance.db` 後刪除）」。

## 常用指令（PowerShell，在專案根目錄）

```
.venv\Scripts\python.exe -m pytest -q                                   # 測試（網路全部用假資料）
.venv\Scripts\python.exe scripts/paper_rebalance.py --status            # 帳戶走勢與交易紀錄
.venv\Scripts\python.exe scripts/paper_rebalance.py --report --no-send  # 預覽 08:30 通知
.venv\Scripts\python.exe scripts/paper_rebalance.py --dry-run --ignore-window   # 試跑，不寫入
.venv\Scripts\python.exe scripts/fetch_bars.py                          # 補 1h K 線到 data/bars.db
.venv\Scripts\python.exe scripts/research.py --stage dev                # 再平衡研究期報告
```

在 Git Bash 執行 Python 時加 `PYTHONIOENCODING=utf-8`，否則中文輸出會亂碼。排程紀錄在 `logs/paper_rebalance.log`、`logs/paper_report.log`。

## 省 token

- 維運問題（漏跑、沒推送、燈號）看 `docs/ARCHITECTURE.md`；它有快速索引、Agent 責任表、出錯流程圖。
- `docs/DESIGN.md` 有 400 多行，**不要整份讀**：用 Grep 找需要的章節（第 0 節：和原始規格的差異；第 1 節：MAX API 查核；6.3：四輪研究結果；6.4.1：模擬帳戶）。
- 第 2～5 節是原始規格的常駐 Agent 架構，目前沒有實作，改模擬帳戶時不用看。
- 沒通過的研究程式已刪除，要找回就看 `git show ff7425a --stat`。
- 常用流程用 skill：`/paper-status`（看模擬帳戶）、`/ship`（測試＋commit＋push）、`/research-round`（新一輪研究的固定步驟）。
