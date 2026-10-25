# 加密貨幣自動交易系統（地端，MAX ETH/TWD 現貨）

個人自用、只操作自己帳戶。延續 Stock_Robot 的做法：**先研究、再模擬、事先訂好照抄標準，達標才考慮真錢**。

- 目前狀態：**MAX 公開資料層與資料品質檢查**完成；還沒有策略、模擬帳戶與下單。進度見 [docs/DESIGN.md](docs/DESIGN.md) 第 8 節。
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

## 測試

網路請求一律以假資料替代（`tests/fakes.py`，內容取自 MAX 真實回應），不會連線。

本專案僅供研究學習用途，非投資建議。
