---
name: ship
description: 把目前的修改測試、更新文件、commit 並 push 到 main。使用者說「commit」「推上去」「收尾」或改完一個功能時使用。
---

1. 跑測試：`.venv/Scripts/python.exe -m pytest -q`。失敗就停下來修，不要 commit。
2. `git status --short` 確認範圍；不能出現 `.env`、`state/`、`data/`、`logs/`、`reports/`（被 .gitignore 擋掉才對）。
3. 文件同步（只改受影響的地方，用 Grep 定位，不要整份讀 DESIGN.md）：
   - 使用方式變了 → `README.md`
   - 行為或決策變了 → `docs/DESIGN.md` 對應章節（模擬帳戶是 6.4.1，研究結果是 6.3）
   - 檔案位置或規則變了 → `CLAUDE.md`
   - 流程、執行順序、Agent、檔案位置變了 → `docs/ARCHITECTURE.md` 的三張 mermaid 圖（系統總覽、01:35 執行順序、出錯流程）、快速索引、Agent 責任表、狀態檔地圖
4. `git add` 只列出這次改的檔案（不用 `-A`／`.`）；再看一次 `git diff --cached --stat`，不能有金鑰檔、公開 IP 或個資。
   commit：英文標題（祈使句，≤ 72 字元），內文說明「為什麼」，最後一行是系統提供的 Co-Authored-By。用 heredoc 傳訊息。
5. `git push -q`，回報 commit hash 與一句話摘要。
