# Crypto_Robot

MAX 交易所 ETH/TWD 現貨，個人自用。使用者說繁體中文；對使用者的回覆、文件、程式註解都用繁體中文，commit message 用英文。

角色：使用者是**營運者**（決定規則、資金、時段，看推送），Claude 是**開發者**（改程式、診斷、維護測試與文件）。目標是乾淨、安全的環境。
**出問題先看 `docs/ARCHITECTURE.md` 的快速索引與流程圖**（第 2 節系統總覽、第 3 節 01:35 執行順序、第 5 節出錯流程），不要從頭讀程式。
**改了流程、新增 Agent、換檔案位置，同一個 commit 要更新這三張圖與對應表格**（2026-10-27 曾有圖落後於程式）。
**開發前先畫架構圖與流程圖**（mermaid，實際渲染檢查過），給營運者看、說沒問題才寫程式；改完同一個 commit 更新維護手冊 `docs/ARCHITECTURE.md`（2026-11-05 營運者要求；只有不改流程的小修正例外）。

## 現況（2026-10-28）

- **主模擬帳戶**（不是真單）：三資產 `rebal3-eth50-cashusdt75-band5`（ETH 50%、USDT 37.5%、台幣 12.5%，任何一項偏離 5 個百分點全部調回），6,000 台幣；2026-10-26 由原本的兩資產帳戶轉換過來（`--migrate`），狀態在 `state/paper_main.db`。舊的 `state/paper_rebalance.db` 只留作紀錄。
- 排程：01:35 執行 `paper_rebalance`（不推送，失敗每小時重試到 07:35）、00:00 執行 `kronos_forecast`（Kronos 多空預測，只記錄；模型在 `../_vendor/kronos_venv`，真單的 `.venv` 不裝 torch）、08:30 執行 `paper_report`（推送 Discord）。營運者每天用 `scripts/judge.py long|short|flat` 記判斷。
- **影子帳戶**（同一個排程、同樣 6,000 元，只記錄）：原本的兩資產 `rebal-eth50-band10`（ETH 50%、台幣 50%），2026-10-27 凌晨重新建倉；狀態在 `state/shadow_rebal2.db`。
- **第十七輪（2026-11-05，技術面與基本面 Agent，6 組）**：方向 5 組都沒過；**EWMA 波動預測研究期與考卷都通過**（可用來調合約部位大小，不能預測方向），考卷已用掉。
- **第十八輪（2026-11-06，Kronos 加資金費率、利率等 7 項資訊再訓練）**：研究期原版、再訓練、再訓練＋資訊都沒過（每筆仍虧損），不進考卷；程式已刪除（`git checkout 939e29f`）。
- **第十九輪（2026-11-06，自己訓練小模型 ridge、LightGBM，逐月重訓）**：研究期都沒過（`gbm` 每筆 +0.044%、t 1.58、後半賠錢），不進考卷；程式已刪除（`git checkout 7ed1ca8`）。
- **第二十輪（2026-11-06，波動目標持有，只做多＋EWMA 調部位，3 組）**：研究期都沒過（Sharpe 都低於一直持有，回撤也比同平均部位的固定持有深），不進考卷；程式已刪除（`git checkout 53e5706`）。EWMA 預測本身仍有效，但調部位沒有被證明能賺更多。
- **第二十一輪（2026-11-06，多幣排序，每週前 50 大、10 多 10 空，`mom`、`ridge`、`gbm`）**：研究期都沒過（最好的 `gbm` 每週 +0.34%、t 1.26、回撤 -30.8%）。營運者破例考 `gbm` 也沒過（每週 +0.58%、t 1.08、回撤 -49.8%），考卷已用掉；程式在 `lab/xsec.py`。
- 十六輪研究共 201 組（第十一輪是槓桿、第十二～十四輪是短線方向、跌破後做多、Binance 合約資金費率，第十五、十六輪是 Kronos 模型預測 3 小時／隔天，都沒過；第十四輪 `fund_streak` 只差 t、第十六輪 `d1_agree` 破例考卷也都沒過）：只有現貨比例再平衡留下（營運者仍可能找新規則；照 `/research-round` 先訂標準）。**ETH 考卷期（2024-01-01 起）已用掉**，結果在 `state/research/holdout.json`。
- **真單**：**2026-10-27 01:35 起開啟**（第一階段 6,000 元，上限 9,000 元），規則同主帳戶；計畫與 MAX 私有 API 查核在 `docs/DESIGN.md` 6.5。金鑰開查詢＋交易（沒開提領）。由 `.env` 的 `LIVE_TRADING=1`＋`LIVE_CONFIRM=I_UNDERSTAND` 開啟，每天 01:35 執行；預覽用 `scripts/live_trade.py --plan`、紀錄用 `--status`。跑滿 2 週（約 11/10）沒有 🔴 才討論加碼；金鑰絕不經過對話。
- USDT 開了 MAX「閒置資產收益」（2026-10-28，APR 1%，不鎖倉）：真單權益會略高於模擬帳戶。
- 每週一 08:30 報告後產生每週事實檢討（第一次 2026-11-02；後兩段是幣安資金費率做多、Kronos＋人工判斷多空的前瞻紀錄，只記錄，見 DESIGN 6.7）；主管（`../Cashflow_orchestrator`）每 3 小時檢查一次，跟真錢有關的 🔴 會自動建立暫停檔。
- **槓桿（借滿益）**：2026-10-27 訂下五個關卡（`docs/DESIGN.md` 6.6，開局 10,000 元、最多賠 10,000 元、目標 100,000 元）。2026-11-01 提前做的第 3 關回測沒通過（2 倍被強平、1.5 倍回撤 -86% 以上）→ 不做 MAX 槓桿。**2026-11-02 營運者決定以後槓桿走 Binance 永續合約（可多空），研究以 Binance 為主**（DESIGN 6.6；系統沒有 Binance 金鑰）。**2026-11-03 最終目的改成：在這台電腦 24 小時於 Binance 交易合約的 Agent 機器人**；五個關卡、最多賠 10,000 元、程式不能轉帳照舊，第 3 關找到通過的合約規則前不開發機器人。

## 不可違反的規則

- 自動交易時段：**2026-11-03 營運者取消「只能在台北 01:30～08:00」的限制，全部可以 24 小時**（之前是因為白天要用電腦）。現貨真單仍是每天 01:35 一次，程式裡的 `WINDOW`（01:30～08:00，`paper/rebalance_account.py`）還沒改；要改先問營運者。
- 價格、數量、金額一律 `Decimal`（`core/num.py`）；交易路徑不准出現 float。報告統計可以用 float。
- 新研究：規則組合與通過標準**先寫好、先 commit，才跑結果**；沒通過不放寬標準。ETH 研究期資料（2021–2023）已看過六次以上。
- `.env`（webhook 等同密碼）、`state/`、`data/`、`logs/`、`reports/` 不進 git；不要在輸出中印出 webhook 網址。
- repo 是公開的（github.com/ShanHugo/Crypto_agent），只有 `main` 分支：改完 → 測試通過 → commit → push。
- **commit 一律明確列出檔名，不用 `git add -A`／`git add .`**（2026-10-26 資料夾裡曾出現金鑰文字檔）；commit 內容不能有金鑰、webhook、使用者的公開 IP 或其他個資。
- `requirements.txt` 只能用 ASCII（中文 Windows 的 pip 讀不了 UTF-8 註解）。
- `scripts/register_tasks.ps1` 要存成 UTF-8 with BOM；建立排程需要系統管理員，交給使用者執行，不要自己跑。

## 要改什麼，改哪裡

| 想改 | 檔案 |
|---|---|
| 主帳戶／影子帳戶的規則、初始資金、手續費 | `scripts/paper_rebalance.py` 開頭的 `MAIN`、`SHADOW`、`INITIAL`、`TAKER_FEE` |
| 08:30 通知內容 | `scripts/paper_rebalance.py` 的 `push_text()`（推送：真單、真實帳戶、Kronos）；模擬帳戶的 `message()`、`morning_text()` 只寫紀錄 |
| 模擬成交、帳戶紀錄 | `src/crypto_robot/paper/multi_account.py`（`run_day`、`day_report`、`import_two_asset`）、`paper/fills.py`；交易時段 `WINDOW` 在 `paper/rebalance_account.py` |
| 再平衡判斷（研究與模擬共用） | `src/crypto_robot/agents/strategy/rebalance.py`；多資產是 `rebalance_multi.py` |
| 排程時間 | `scripts/register_tasks.ps1` 的 `$tasks`（改完請使用者以系統管理員重跑） |
| MAX 公開 API | `src/crypto_robot/exchange/max/public.py`（端點已實測，見 DESIGN.md 第 1 節） |
| 操作台「虛擬貨幣」分頁的資料、Agent 健康檢查 | `scripts/console_data.py`（只讀 JSON；Kronos／資金費率前瞻紀錄在 `forecast_data()`）、`src/crypto_robot/health.py`；畫面與 API 在 Cashflow_orchestrator 的 `src/console/templates/console.html`（子分頁：總覽、前瞻紀錄、健康與排程、紀錄；`loadCrypto`、`renderCryptoForecast`）、`src/console/app.py`（`/api/crypto`） |
| 每週事實檢討（第 2 關的成績單，每週一 08:30 報告後） | `src/crypto_robot/weekly.py`（計算與文字）、`scripts/weekly_review.py`（讀資料、存 `reports/weekly_*.md`、推送）；只列數字，不加建議 |
| 備份 | `src/crypto_robot/backup.py`、`scripts/backup.py`；08:30 報告推送後自動執行；目的地在 `.env` 的 `BACKUP_DIRS`（不要寫進 git） |
| 真單執行與安全檢查 | `src/crypto_robot/live/executor.py`（`check_guards`、`plan_orders`、`run_live`）、`scripts/live_trade.py`（開關、設定、`--plan`）；紀錄 `state/live/live.db` |
| 真實帳戶（唯讀）、IP 變動 | `src/crypto_robot/live/readonly.py`、`exchange/max/private.py` 的 `MaxPrivateClient`（只有查詢方法；會下單的是 `MaxTradingClient`）、`scripts/live_check.py`（`--accept-ip`）；紀錄在 `state/live/`（含公開 IP，絕不進 git） |
| 幣安合約 24 小時 Agent（骨架，還不能下單） | `src/crypto_robot/futures_agent/agent.py`（`tick`、`AgentConfig`、`NoStrategy`）；流程圖在 `docs/ARCHITECTURE.md` 第 9 節 |
| 研究 | `src/crypto_robot/lab/`（`rebalance_variants.py` 是事先訂定的組合與標準，看過結果後不得修改） |

改再平衡參數會讓模擬帳戶前後不一致：先問使用者要「接著用舊帳戶」還是「重開帳戶（備份 `state/paper_main.db` 後刪除）」。

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
- `docs/DESIGN.md` 有 600 多行，**不要整份讀**：用 Grep 找需要的章節（第 0 節：和原始規格的差異；第 1 節：MAX API 查核；第 5 節：下單安全（client_oid、訂單狀態、金鑰）；6.3：十六輪研究結果；6.4.1：模擬帳戶；6.5：真單；6.6：槓桿關卡；6.7：走勢預測與前瞻紀錄；6.8：技術面與基本面研究 Agent 草案；6.9～6.12：Kronos 再訓練、自己訓練小模型、波動目標持有、多幣排序）。兩個專案共用的主管在另一個 repo `../Cashflow_orchestrator`（設計在它的 `docs/DESIGN.md`）。
- 第 2～4 節大多是原始規格的常駐 Agent 與 EMA 規則，沒有實作，每節開頭有現況說明；改模擬帳戶或真單時不用看（2.4 目錄除外）。
- 沒通過的研究程式已刪除，要找回就看 `git show ff7425a --stat`（第 1、2、4 輪）、`git show b112076 --stat`（第 6～9 輪）、`git show c67c3b1 --stat`（第 10 輪）、`git show b846628 --stat`（第 11 輪槓桿）、`git checkout 98eae81 -- <檔名>`（第 12、13、15、16 輪：3 小時方向、跌破後做多、Kronos）。
- 常用流程用 skill：`/paper-status`（看模擬帳戶）、`/ship`（測試＋commit＋push）、`/research-round`（新一輪研究的固定步驟）。
