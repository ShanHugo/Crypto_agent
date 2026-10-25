---
name: research-round
description: 新一輪交易規則研究的固定流程（事先訂標準 → commit → 跑研究期 → 記錄結果）。使用者想測試新的交易規則、新參數或新策略時使用。
---

這個流程照 Stock_Robot 的教訓，順序不能換：

1. **先跟使用者講定**規則組合（建議 ≤ 20 組）、基準、通過標準、成交與成本假設。提醒：
   - 研究期 2021–2023 已經看過四次，新結果的可信度會打折；
   - 考卷期（2024 起）已經用掉，沒有乾淨的歷史資料可以驗證，只能靠模擬帳戶往後累積。
2. 寫程式：規則是純函式（`src/crypto_robot/agents/strategy/`，研究與模擬共用）；回測、組合與標準放 `src/crypto_robot/lab/<名稱>_*.py`。參考 `rebalance_backtest.py`、`rebalance_variants.py`、`rebalance_research.py` 的寫法，共用工具在 `lab/common.py`、`lab/metrics.py`。
3. 寫測試（手算的小例子），`pytest -q` 通過。
4. 在 `docs/DESIGN.md` 6.3 記下組合與標準，**commit 並 push，之後才跑結果**。
5. 跑研究期，報告寫到 `reports/`，用 SendUserFile 傳給使用者。
6. 把結果（含沒通過的）記進 DESIGN.md 6.3，commit。沒通過就不放寬標準。
7. 若使用者要用在模擬帳戶，另開模擬帳戶或問清楚是否取代現有帳戶。
