# P0 報告｜部門主管制 Hermes Plugin — 事件路由與 Desktop 可行性實測

- **任務**：Kanban `t_b0f90986`（P0，能力實測；不建產品）
- **日期**：2026-11-05（Asia/Taipei）
- **受測 Hermes**：v0.22.6 (2026.9.24) stable，git `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`（2026-11-02），install `/home/ubuntu/.hermes/hermes-agent`，**未修改任何 core 檔**
- **隔離根**：`/home/ubuntu/projects/hermes-department-orchestrator/.research/lab/`
- **結論**：**核心路徑實證可行，不需 patch core，可進入建置**。但「主管可動作」不是預設行為——必須在主管 profile 啟用 `kanban` toolset，否則主管只收到文字通知、無法處理（本輪抓到的真缺口）。Desktop 僅 source-derived，未 runtime 驗證（本機無桌面 App）。

---

## 0. TL;DR

| 能力 | 判定 | 依據 |
|---|---|---|
| 子卡完成事件送達主管 | ✅ 真實 runtime | gateway log + SQLite 事件資料 |
| 主管被「真的喚醒」並跑一輪 agent（非僅訊息） | ✅ 真實 runtime | `gateway.wake` + stub 收到新請求（2 次 API call） |
| 主管能**讀取／處理**子卡結果 | ✅ 真實 runtime | `kanban_show` / `kanban_comment` / `kanban_create` 都落地 |
| 主管預設就有 kanban 工具 | ❌ 預設沒有（14 個工具全缺） | 第一次 wake 的 tool schema 只有 18 個工具 |
| 以 config 開啟後可用 | ✅ 真實 runtime | tool schema 18 → 32，含 14 個 `kanban_*` |
| 事件去重（不重複喚醒） | ✅ 真實 runtime | 每張卡恰好 1 次 wake；cursor 持久化 |
| Gateway 重啟後訂閱保留、不重播 | ✅ 真實 runtime | 重啟後無重複 wake；cursor 未回退 |
| 獨立驗收 gate（主管核可才算完成） | 🟡 source-derived（未跑） | review lane + `kanban_request_review` 存在且文件化 |
| 失敗審查（退回重做） | 🟡 source-derived（未跑） | `kanban_request_changes` → `changes_requested` wake |
| Desktop 管理介面 | 🟡 source-derived（未跑） | SDK 文件 + 本機無 Desktop App |
| Plugin hook 能否「攔截／否決」完成 | ❌ 不行（設計如此） | `VALID_HOOKS` 全部 observer-only |

---

## 1. 隔離環境與真實邊界

**真實**（未修改的 Hermes 本體）：
- Gateway 製程：`hermes gateway run`，只開 `api_server`，綁 `127.0.0.1:18122`。
- Kanban：獨立 board `p0`，DB 在 `lab/kanban/kanban/boards/p0/kanban.db`（**與正式 `/home/ubuntu/.hermes/kanban.db` 不同檔**）。
- Notifier / wake / kanban tools / CLI 全部走 Hermes 原始碼路徑。

**合成（lab 測試替身，報告中一律視為 mock）**：
- 模型端點 `lab/stub/stub_model.py`：不是真模型，只回固定 tool_calls 並記錄 gateway 實際送出的 schema。所有「LLM 判斷」都不是真的。
- 卡片、profile 名稱 `worker` / `manager` 皆為合成，無真實 profile 設定。

**隔離驗證**：
```
$ tr '\0' '\n' < /proc/<gateway-pid>/environ | grep -E 'HERMES_(HOME|KANBAN_HOME|KANBAN_BOARD|KANBAN_DB|DELEGATED|PROFILE)='
HERMES_KANBAN_BOARD=p0
HERMES_KANBAN_HOME=/home/ubuntu/projects/hermes-department-orchestrator/.research/lab/kanban
HERMES_HOME=/home/ubuntu/projects/hermes-department-orchestrator/.research/lab/home
```
（正式 pins `HERMES_KANBAN_DB` / `HERMES_DELEGATED_CHILD_CONTEXT` / `HERMES_PROFILE` 均已 unset。）

**已知偏差（必須一起讀）**：
1. 新的 `HERMES_HOME` 無法自建 venv（`python-olm` 需 `clang++`），因此用 install 既有的 venv 直譯器 `…/hermes-agent/venv/bin/python` 驅動 CLI。**每次 CLI 啟動都會先嘗試 `uv sync` 失敗再 fallback，多花约 10 秒，不影響正確性；未修改 install。**
2. 本 worker session 本身是 Kanban worker，env 帶正式 pins。lab 啟動腳本必須 `unset` 這些變數，否則 CLI 會拒絕 mutation（`delegate_task child contexts cannot mutate Kanban tasks`）。已在 `scripts/hk.sh`、`scripts/run-gateway.sh` 內處理。

---

## 2. 關鍵發現 A：主管「被喚醒」是真的（真實 runtime）

- 訂閱：`hermes kanban notify-subscribe <task> --platform api_server --chat-id api-809a09cd543bf09e --delivery-mode wake`
- 路徑：`gateway/kanban_watchers_notifier.py::wake()` → `gateway/wake.py::deliver_wake()`（**只有在 live gateway 內才會 fire**）

Gateway log（最後一次驗證，`t_6a10edd4`）：
```
2026-11-05 05:55:53,031 INFO gateway.run: inbound message: platform=api_server user=api chat=api-809a09cd543bf09e
  msg='[kanban] Task t_6a10edd4 completed. Title: P0 verify child 1793858120 Assignee: ...'
2026-11-05 05:55:53,326 INFO gateway.run: kanban notifier: woke agent for t_6a10edd4
  on api_server/api-809a09cd543bf09e profile=default events={'completed'}
```
stub 端同時收到**新的 completion 請求**（不是原本那輪），且 messages 內含 `role=tool`，證明 agent 真的跑完工具回合，不只是把字串貼進對話。

---

## 3. 關鍵發現 B：主管預設「有耳朵沒有手」（本輪最重要的缺口）

第一次 wake（`t_ce2ab2e9`，run 367 遺留證據）時，wake 輪送出的 tool schema 是 **18 個工具、0 個 `kanban_*`**。模型依設計呼叫 `kanban_create`，Hermes 回工具不存在，該輪只以 24 字作結，**沒有任何卡片被建立**（board 上只有 root + child 兩張）。

根因（source）：
- `toolsets.py` 的 `kanban` toolset 與 `tools/kanban_tools.py::_visible()`：一般 session 只有在 **profile 啟用 `kanban` toolset** 時才看得到 kanban 工具；dispatcher worker 則是靠 `HERMES_KANBAN_TASK` 自動獲得。
- 官方文件已明講：`website/docs/user-guide/features/kanban.md` §437-451「orchestrator profiles can also enable the `kanban` toolset explicitly」、§523-525「Zero schema footprint on normal sessions… unless the active profile explicitly enables it」。

**修法（不需改 core）**：lab `config.yaml`
```yaml
platform_toolsets:
  api_server:
    - hermes-api-server
    - kanban
```
驗證（`tools_config._get_platform_tools`）：`kanban enabled: True`。
開啟後同一個 wake 輪的 schema：**32 個工具、14 個 `kanban_*`**
（`kanban_show/list/create/link/comment/complete/block/heartbeat/request_review/request_changes/attach/attach_url/attachments/unblock`）。

> 產品含意：**「主管 profile 必須顯式啟用 kanban toolset」是 P1 建置的硬性前置條件**，不是可選優化。文件也建議主管 profile 只給 `kanban`/`gateway`/`memory` 這類工具，避免主管自己去執行實作（§765）。

---

## 4. 關鍵發現 C：主管能真的處理結果（真實 runtime）

開啟 toolset 後，wake 輪模型發出三個 tool_calls，全部落地：

| 動作 | 結果 |
|---|---|
| `kanban_show(child)` | 成功（後續回合帶回 child 內容） |
| `kanban_comment(child, "Manager (synthetic) reviewed…")` | `task_comments` 新增一列，author=`default` |
| `kanban_create(follow-up, parents=[child])` | 新卡 `t_aab5b8f5`，狀態 `ready`，`task_links` 有 parent→child 邊 |

Board 終態（節錄）：
```
t_6a10edd4  done   worker   P0 verify child 1793858120
t_aab5b8f5  ready  worker   P0 manager follow-up after t_6a10edd4
```

**附帶機制（重要）**：`kanban_db._inherit_notify_subs()`（`kanban_db.py:1325`）會讓子卡**繼承父卡的訂閱**。實測中主管建立的 follow-up 卡自動帶了同一 chat 的 `wake` 訂閱（即使 `kanban.auto_subscribe_on_create: false`），所以「主管接手後續」這條鏈會自動延續。
→ **設計副作用**：若 default 訂閱了主管的母卡，之後所有子孫卡完成都會喚醒 default，等於繞過部門層。此點列為待解設計問題 #1（見 §8）。

---

## 5. 去重與重啟恢復

- 去重是**持久化 cursor**：`kanban_notify_subs.last_event_id`，由 `claim_unseen_events_for_sub()` 在 `BEGIN IMMEDIATE` 內以 CAS 前移（`hermes_cli/kanban_db_notify.py:352-389`），投遞失敗才 `rewind`。
- 實測：每張卡在 gateway log 恰好 1 次 `woke agent for <id>`；重跑測試後舊卡沒有第二次喚醒。
- 重啟：gateway SIGTERM → 重新啟動後，訂閱仍在（DB 列），且**沒有重播已 claim 的事件**（cursor 保留）。run 367 也提供了跨重啟樣本：訂閱建立於 05:39，gateway 05:39:47 重啟、05:40:08 起來，05:40:25 的事件照常送達。
- 未驗證：**gateway 停機期間**產生的事件，重啟後是否補送（cursor 會停在舊位置，理論上會補，但本輪沒跑這個案例）。

---

## 6. 驗收 gate 與往上游回報（source-derived，未跑）

**Hook 不能當 gate**：`hermes_cli/plugins.py::VALID_HOOKS` 的 kanban 家族（`kanban_task_completed`、`kanban_task_blocked`、`on_kanban_worker_spawned`…）**全部是 observer-only，回傳值被忽略**（hooks.md §489-496）。所以「主管核可才放行」不可能用 hook 否決實作。

**可用的 gate 是看板語意（review lane）**：
- worker 端改用 `kanban_request_review(reviewer=<主管 profile>)` 而非 `kanban_complete()` → 卡片進 `review`，dispatcher 以主管 profile 起一個 review worker（**是真的 spawn，不是喚醒**）。
- 主管用 `kanban_complete()` 核可，或 `kanban_request_changes()` 退回；notifier 的 `_WAKE_KINDS` 包含 `review_requested` / `changes_requested`，會喚醒 origin。
- 這條路徑在程式與文件中都完整（`kanban.md`、`tools/kanban_tools.py`、`kanban_watchers_notifier.py:41-47`），但**本輪 lab 關閉 dispatcher，沒有實跑 spawn**。

**建議組合**：review lane 當硬 gate（未核可不得 done），wake 當「主管接手後續／回報」的軟迴路。

---

## 7. Desktop 邊界（source-derived，未跑）

- Desktop plugin 是 `$HERMES_HOME/desktop-plugins/<id>/plugin.js`（ESM，無 build），唯一 import surface `@hermes/plugin-sdk`；`ctx.register({area:'panes'|PALETTE_AREA|ROUTES_AREA|SIDEBAR_NAV_AREA})` 可掛面板／頁面。
- 讀資料兩條路：`host.request(method, params)`（gateway JSON-RPC）與 `host.onEvent`；需要後端時 ship `plugin_api.py`，以 `ctx.rest('/path')` 打自己的 `/api/plugins/<id>` 命名空間。
- **關鍵限制**：gateway 對 desktop 幾乎沒有 kanban RPC —— `tui_gateway/contracts/canonical.py` 只有 `kanban.run`（「admit the dispatcher's current claim」），沒有 `kanban.list/get/board.*`。所以 **Desktop 管理介面必須自帶 `plugin_api.py` 後端**去讀 `kanban.db`（或呼叫 CLI），不能只靠 `host.request`。
- 本機是 headless server，**沒有 Desktop App 可載入 plugin**，因此「面板真的長出來」未驗證；這是 Desktop 部分唯一的空白。

---

## 8. 風險與待解設計問題

1. **子卡訂閱繼承會穿透部門層**（§4）。若 default 訂閱主管母卡，所有子孫完成都會叫醒 default。
   最小替代：default 只在主管**分解完成後**才訂閱母卡；或主管用「回報卡」明示上報，不靠母卡訂閱。
2. **wake 需要 live gateway**。gateway 沒開就沒有喚醒（事件留在 cursor，下次啟動才可能補）。產品需要「主管常駐 session」或明確的離線語意。
3. **主管工具面過大**：若照抄 `hermes-api-server`，主管也能 `terminal`/`write_file` 自己動手。建議 restricts 到 `kanban`+`memory`(+`gateway`)。
4. **同一 session 歷史累積**：每次 wake 都往同一 session 疊訊息（stub 觀察到 messages 已 22 筆）。長期需靠壓縮/每日新 session。
5. **hook 無 veto**：任何「完成前攔截」需求都只能靠 review lane 或 completion contract，plugin 無法擋。
6. **本輪 near-miss（誠實記錄）**：第一次重啟 lab gateway 時沿用了 worker 的 `HERMES_KANBAN_DB`（指向**正式**看板）。發現後立即停止該 process；檢查正式看板 `kanban_notify_subs` 仍為 129 列、log 無任何 delivery/GC 紀錄，**未觀測到副作用**。之後改為 sanitize 版本並在 /proc 驗證。此類 pin 洩漏應列為 lab 腳本的固定檢查。

---

## 9. Catalog 與外部 repo 重疊（唯讀查核）

**Hermes plugin catalog**（`plugin-catalog/*.yaml`，community/official 分級）已有大量 kanban 周邊，但都**不是**部門主管事件接手：

| 條目 | 做的事 | 與本案差異 |
|---|---|---|
| `kanban-gantt` / `kanban-graph` / `kanban-live-gantt` | 看板視覺化（時間軸／依賴圖） | 純 UI，無事件接手 |
| `kanban-stream` | 把 worker 活動即時推到 Telegram | 單向通知，不處理結果 |
| `kanban-task-threads` | 每張卡一個 Discord thread | 通道鏡像，非主管決策 |
| `hermes-worker-monitor` | Desktop footer 顯示 worker 健康 | 監控，無驗收 |
| `hermes-teammates` | 命名 subagent 團隊（assign/check/wait） | 走 `delegate_task`，非看板，無獨立驗收 gate |
| `duty-board` | 待處理對話／未回覆總覽 | 與部門無關 |
| `advanced-delegation`（official） | `delegate_task` 的 model_map / 額外模式 | 不改 kanban 生命週期 |

**外部 repo（以 GitHub API + README 實查，非搜尋摘要）**：

- `GieshBuilds/hierarchical-agents` — MIT，建立 2026-04-08、最後 push 2026-04-09（**已 7 個月未動**），18 stars。它是**在 Hermes 之上另建一套協調層**：org chart、SQLite IPC message bus、delegation chain、分層記憶、worker registry、自己的 8080/8081 UI。教訓可參考，但**不是 Hermes plugin、不走 native kanban、沒有 review gate**；直接採用等於第二套狀態源。
- `Fruxano/fruvisi` — MIT，建立與最後 push 皆 2026-07-20（**當天來回，之後未動**），5 stars。Hermes **dashboard** plugin（React Flow org chart、areas/groups、preset 切換、拖放分派 kanban 任務、per-agent OpenRouter fallback）。它的「kanban integration」是**分派與路由訊號**（preset 會把 active/INACTIVE 寫進 profile 描述供 decomposer 讀），README roadmap 才把「卡片上的即時活動（lifecycle hooks）」列為未來項。**沒有主管事件接手、沒有獨立驗收、沒有往上游回報**。
- 兩者都**不在** Hermes plugin catalog 內。

**差異總結**：本專案的獨特組合是「native kanban 事件 → 喚醒部門主管 → 主管以 kanban 工具獨立驗收 → 才回報 default」。catalog 現有項目最多做到視覺化/通知/分派，沒有這個閉環。

---

## 10. 建議架構（v1，全部用 supported API）

```
default ──kanban_create(assignee=dept-manager)──▶ 主管卡（部門）
                                                   │ 分解
                                    kanban_create(parents=[主管卡], assignee=worker-*)
                                                   ▼
                                             worker 卡（可多張）
                                                   │ 完成時
                    ┌──────────────────────────────┴───────────────────────┐
                    │ 硬 gate：worker 用 request_review(reviewer=主管)      │
                    │   → review lane → dispatcher spawn 主管審查           │
                    │   → 主管 complete（核可）/ request_changes（退回）    │
                    └──────────────────────────────┬───────────────────────┘
                                                   ▼
                     主管核可後 kanban_complete(主管卡) → 喚醒 default（回報）
```

- 每個主管 profile：`platform_toolsets.<platform>: [kanban, memory]`（**刻意不給 terminal/file**）。
- 主管只負責：讀子卡、寫 comment、建 follow-up、核可/退回、對 default 回報；不實作。
- 回報：主管完成自己的卡 → `completed` 事件 → default 的訂閱被喚醒（注意 §8.1 的繼承問題）。
- Desktop：`desktop-plugins/dept-hub/plugin.js`（org chart + 卡片狀態 pane）＋ `plugins/dept-hub/dashboard/plugin_api.py`（讀 `kanban.db` 的 `/dept/tree`、`/dept/tasks`），因 gateway 沒有 kanban 查詢 RPC。

---

## 11. 未驗證清單（交付 reviewer 判斷是否補測）

1. review lane 的真實 spawn（需開 dispatcher + 真模型或 stub 擴充）。
2. Gateway 停機期間事件的補送。
3. Desktop plugin 實際載入與 pane 呈現。
4. 多部門／多 board／併發 tick 的 cursor 行為。
5. 非 `api_server` 平台（Telegram/Discord）的 wake 路由。
6. 真模型下主管的判斷品質（本輪全部是合成模型）。

---

## 12. 重跑方式（固定、可重現）

```bash
LAB=/home/ubuntu/projects/hermes-department-orchestrator/.research/lab
cd "$LAB"
./scripts/run-stub.sh            # 前景或背景：合成模型 127.0.0.1:18121
./scripts/run-gateway.sh         # 背景：隔離 gateway 127.0.0.1:18122
bash scripts/p0-e2e-test.sh      # 建卡→訂閱→完成→斷言；exit 0 = 全過
```
最後一次執行（`evidence/p0-e2e-test.log`）：
```
PASS  child reached done
PASS  manager created a linked follow-up card
PASS  follow-up exists and names the child
PASS  manager commented on the child (review note)
PASS  exactly one wake for this child (no duplicate delivery)
PASS  woken turn carried the kanban toolset            [tools=32]
PASS  kanban tools present by name                     [14 tools]
PASS  subscription cursor claimed the completed event  [cursor=18, completed_ev=18]
RESULT: PASS
EXIT_STATUS=0
```

**產物**
- `lab/scripts/p0-e2e-test.sh`（可重跑斷言，exit 0/1）
- `lab/scripts/run-gateway.sh`、`lab/scripts/run-stub.sh`、`lab/scripts/hk.sh`（皆已 sanitize 正式 pins）
- `lab/stub/stub_model.py`（合成模型；會記錄 gateway 實際送出的 tool schema）
- `lab/logs/stub-requests.jsonl`、`lab/home/logs/gateway.log`
- `lab/kanban/kanban/boards/p0/kanban.db`
- `evidence/p0-e2e-test.log`

**未動到**：`/home/ubuntu/.hermes`（正式 board/gateway/settings/profiles）、`hermes-freshtape`、任何 credentials、任何對外服務。
