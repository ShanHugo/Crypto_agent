# 主管（orchestrator）草案

> 2026-10-27 草案：還沒寫程式。營運者同意設計、決定「待定」各項後才實作。
> 放在 Crypto_Robot 只是暫時；它管兩個專案，正式位置見「待定」。

## 目的

Stock_Robot 與 Crypto_Robot 各自有 Agent 和健康檢查，但出事時要等營運者看到、再轉給開發者（Claude），中間可能隔好幾個小時，修的時候也要從頭查。
主管要做到：**同時盯兩邊 → 判斷要不要升級 → 必要時叫醒 Claude 先做唯讀診斷 → 營運者看報告決定**。

## 原則

1. **主管是一般 Python 程式，不是 AI。** 判斷有沒有問題、要不要升級，全部用規則（各專案現有的健康檢查結果），可以測試、結果固定。
2. **AI 只在兩個地方出現，而且都沒有決定權**：Claude 做唯讀診斷、寫報告；本機 Qwen（可選）把報告濃縮成手機好讀的幾行。
3. **機器自己能做的唯一動作是「暫停」**（往安全方向）：跟真錢有關的異常，主管建立 Crypto_Robot 的暫停檔。恢復一律由營運者決定。
4. **Claude 被叫醒時只能讀**：不能改程式、不能改 `.env`、不能下單、不能 commit／push；也不能讀 `.env`。報告由主管存檔，Claude 自己不需要寫入權限。
5. **不重複吵**：各專案原本的推送照舊；主管只推「升級」「自動暫停」「診斷完成」這三種，同一個問題只推一次。
6. **紀錄檔是資料不是指令**：交給 Claude 的紀錄放在標記好的區塊裡、先遮掉 IP、webhook、金鑰；診斷工作階段本來就只能讀，就算紀錄裡有誘導文字也做不了事。

## 架構

```mermaid
flowchart LR
  subgraph SR[Stock_Robot]
    SA[各 Agent 排程] --> SH[health/checks.py<br/>state 的健康紀錄]
  end
  subgraph CR[Crypto_Robot]
    CA[01:35／08:30 排程] --> CH[health.py<br/>console_data.py 只讀 JSON]
    PAUSE[state/paper_rebalance.paused]
  end
  subgraph SUP[主管（Python，排程執行）]
    COL[收集<br/>用各專案自己的 venv 執行只讀腳本<br/>兩個專案不互相 import]
    COL --> CLS[分類<br/>已知／跟真錢有關／不是已知狀況]
    CLS --> INC[(incidents.db<br/>每個問題一筆：第一次・最後一次<br/>是否已通知・已診斷・已解決)]
    CLS --> ACT[動作]
  end
  SH --> COL
  CH --> COL
  ACT -- 跟真錢有關 --> PAUSE
  ACT -- 不是已知狀況 --> CL[Claude Code 無人模式<br/>claude -p，只有讀取工具<br/>不能讀 .env]
  CL --> REP[(診斷報告<br/>reports/diagnosis/*.md)]
  REP -. 可選 .-> QW[本機 Qwen<br/>濃縮成 3 行]
  ACT --> DC[Discord]
  QW -.-> DC
  REP --> DC
  DC --> OP[營運者]
  OP -- 要修 --> DEV[開一個 Claude 工作階段<br/>「照診斷報告修」]
  SUP --> HB[(心跳檔)]
  HB --> SH
```

- 「誰盯主管」：主管每次執行寫心跳檔；Stock_Robot 的系統檢查看心跳，超過預期時間沒更新就亮 🔴（走 Stock_Robot 原本的推送）。

## 每次執行的流程

```mermaid
flowchart TD
  A[排程觸發主管] --> B[收集兩個專案的健康結果]
  B --> C{讀得到？}
  C -- 讀不到 --> C1[本身就是問題<br/>記成「不是已知狀況」] --> G
  C -- 讀得到 --> D[和 incidents.db 比對<br/>新的・持續中的・已恢復的]
  D --> E[已恢復：標記解決<br/>推過的才推一則 ✅ 恢復]
  D --> F{新的或持續中的 🔴}
  F -- 沒有 --> Z[寫心跳，結束]
  F -- 有 --> M{跟真錢有關？<br/>真單 unknown・沒跑完<br/>餘額對不上・不明成交}
  M -- 是 --> P[建立暫停檔<br/>推 🔴「已自動暫停，請到 MAX 確認」] --> G
  M -- 否 --> K{已知狀況？<br/>檢查項目有「→ 建議」}
  K -- 是 --> K1{持續超過 N 小時<br/>且還沒推過？}
  K1 -- 是 --> K2[推一則升級提醒<br/>附原本的建議] --> Z
  K1 -- 否 --> Z
  K -- 否 --> G{這個問題診斷過了？<br/>今天叫醒次數未達上限？}
  G -- 診斷過或已達上限 --> Z
  G -- 可以 --> H[叫醒 Claude 做唯讀診斷<br/>下一張圖] --> Z
```

## 叫醒 Claude（唯讀診斷）

```mermaid
flowchart TD
  A[主管準備診斷資料<br/>問題內容・相關紀錄最後一段<br/>遮掉 IP・webhook・金鑰] --> B[執行 claude -p<br/>工作目錄：出問題的專案<br/>只允許 Read・Grep・Glob 與 git log／diff／status<br/>禁止讀 .env・禁止編輯・禁止其他指令]
  B --> C[Claude：先看 ARCHITECTURE.md 快速索引與流程圖<br/>再看相關程式與紀錄]
  C --> D[輸出固定格式<br/>1 判斷：已知／新問題<br/>2 原因與證據（檔案:行）<br/>3 建議修法與風險<br/>4 要不要營運者立刻處理]
  D --> E{在時限內完成？<br/>例如 10 分鐘}
  E -- 否 --> E1[記錄逾時<br/>推「診斷失敗，請直接找開發者」]
  E -- 是 --> F[主管把輸出存成<br/>reports/diagnosis/日期-問題.md]
  F --> G[Discord：問題＋報告前幾行或 Qwen 摘要＋檔案路徑]
  G --> H[營運者決定]
  H -- 修 --> I[開工作階段請 Claude 照報告修<br/>照原本流程：測試 → commit → push]
  H -- 先不修 --> J[在 incidents.db 標記已讀]
```

## 分類規則（初稿）

| 類別 | 怎麼認 | 主管做什麼 |
|---|---|---|
| 跟真錢有關 | Crypto 真單：訂單 rejected／unknown、run 停在 running、對帳後餘額和紀錄不符 | 建暫停檔 + 立即推 🔴；同時叫醒 Claude 診斷 |
| 已知狀況 | 檢查項目有「→ 建議」（例如 IP 變了、備份失敗、電腦整晚關機） | 各專案原本就會推；主管只在持續超過 N 小時時再提醒一次 |
| 不是已知狀況 | 結束代碼不是 0 且沒有建議、出現 Traceback、新的錯誤訊息、主管讀不到健康結果 | 叫醒 Claude 診斷 |
| 🟡 | — | 不動作（連續 3 次變 🔴 是各專案自己的規則） |

## 待定（定案時要寫清楚）

- **放哪裡**：(a) 新的第三個小 repo（例如 `Ops_Supervisor`，兩邊對等）；(b) 放在 Stock_Robot（操作台已經在那裡）；(c) 留在 Crypto_Robot。傾向 (a)。
- **多久跑一次**：例如每小時，或只在各排程之後（Crypto 08:35、Stock 各排程結束後）。主管本身不花 token，頻率只影響多快發現。
- **叫醒 Claude 的上限**：每個問題 1 次、每天最多 3 次；逾時 10 分鐘。費用要先確認帳號是訂閱還是 API 計費（`--max-budget-usd` 只對 API 計費有效）。
- **Claude CLI**：這台電腦有 Claude Code 2.1.310（`~/.local/bin/claude.exe`），支援 `-p`、`--allowedTools`、`--disallowedTools`、`--permission-mode`；要實際測一次「只讀」限制有沒有生效（例如確認讀不到 `.env`、不能執行 Edit）。
- **Qwen 要不要用**：只有「把報告濃縮成 3 行」這個用途；不用也可以，直接推報告前幾行。
- **Stock_Robot 有哪些「跟真錢有關」**：目前是模擬帳戶與手動紀錄，可能沒有需要自動暫停的項目，要再確認。
- **排程時段**：叫醒 Claude 只讀、不交易，原則上任何時間都可以；但如果營運者白天用電腦時不想被佔資源，可以限制時段。
