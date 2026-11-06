# Department Hub — M2 Desktop 管理介面與後端

狀態（2026-11-06）：**M1 v5.1 + M2 v5 已整合進 repo（`014f372`），並於 2026-11-06 07:41–07:46 UTC
在本機手動安裝過（config 備份 + gateway restart，安裝檔與 repo byte-identical）。**
`t_e3d284ae` 的整合驗收對程式／parity 給 PASS、只對文件與 repo 衛生給 REQUEST_CHANGES；
本文件與 README 是該文件修正。**真實 Desktop 畫面（渲染、native grant／OAuth、實際載入）
仍未經獨立驗證**，不得宣稱 Desktop 已驗收。測試基準：Hermes Agent **v0.23.0（2026.9.24）**。

安裝以 **§7** 為準（含 Windows 路徑、遠端 Desktop、rollback）。本文件其餘部分是 M2 的
交接與限制說明，不是驗收通過證明。

- 專案：`/home/ubuntu/projects/hermes-department-orchestrator/`
- 計畫：`/home/ubuntu/.hermes/plans/2026-11-05_132450-hermes-department-orchestrator-hackathon.md`
- 前置：M1 v5（plugin-owned immutable final report）已由 `t_090d5884` 獨立
  **REVIEW: PASS**（P1-1 durable-conflict fence 修復版，product 55 檔 sha256 驗證）。
  契約為 `department-hub/m1-contract/v5`；v4.1 以前的交付語義是歷史。
  M2 本輪只做唯讀對齊，不宣稱 M1 的營運部署或真實 Desktop 已驗證。

---

## 1. 這次交付了什麼

| 檔案 | 用途 |
|---|---|
| `desktop/department-hub/plugin.js` | Desktop 正式頁面（公開 `@hermes/plugin-sdk` only）：route、sidebar、palette、en/zh-hant、鍵盤與文字狀態 |
| `dashboard/plugin_api.py` | FastAPI router，掛在 `/api/plugins/department-hub/`；授權驗證、最小資料、唯讀 |
| `dashboard/manifest.json` | Desktop backend manifest（`api: plugin_api.py`） |
| `department_lib/dashboard.py` | M2 對 M1 的**唯一薄層**：Kanban 狀態來源、證據、bounded/TTL cache |
| `department_lib/kanban_db_source.py` | 唯讀 Kanban SQLite 適配器；不 import Hermes 私有 Python 模組；schema 驗證失敗即 fail closed |
| `tests/test_dashboard.py` | 讀模型：狀態、阻塞原因、審查證據、acceptance/delivery、cache、無個資 |
| `tests/test_dashboard_api.py` | FastAPI：401/404/409/503、mutation 零寫入、最小資料、standalone 載入 |
| `tests/test_kanban_db_source.py` | 真 SQLite board 的唯讀讀取與非 board 檔拒絕 |
| `tests/test_desktop_plugin.py` | ESM import allow-list、無硬色碼、manifest、`node --check`、SDK mock |
| `tests/js/` | SDK / React / jsx-runtime mock 與元件邏輯測試 |
| `.research/m2-e2e.py` | 隔離真實全鏈 E2E（可重跑） |
| `.research/m2-e2e.log` | 本次 E2E 實際輸出（44 checks PASS） |
| `.research/m2-plugin-validate.json` | `hermes plugins validate` 的 JSON 證據 |
| `.research/m2-standalone-import.log` | 以 gateway 方式 by-path 載入 `plugin_api.py` 的證據 |
| `.research/m2-node-check.log`、`.research/m2-js-mock.log` | Node 語法與 SDK mock 證據 |

沒有修改 M1 既有的 `plugin.yaml`、`__init__.py`、`department_lib/{models,config,store,kanban_cli,board,routing,acceptance,delivery,kanban_effects,orchestrator}.py`，避免與並行的 M1-R 漂移。

---

## 2. Desktop 頁面

- Route：`/department-hub`（`ROUTES_AREA`）
- Sidebar：`SIDEBAR_NAV_AREA`，label 由 `ctx.i18n` 提供
- Command palette：`department-hub.open` → `host.navigate('/department-hub')`
- 預設關閉：`defaultEnabled: false`，由使用者在 Settings → Plugins 開啟
- Import 只允許：`@hermes/plugin-sdk`、`react`、`react/jsx-runtime`
- 版面：左側部門 listbox（`role="listbox"`、`aria-activedescendant`、方向鍵/Home/End/Enter、`aria-selected`），右側詳情
- 狀態一律有文字（`status.blocked` / `done` / `review` / `running` / `ready` / `todo` / `triage` / `scheduled` / `archived` / `unknown`），不以顏色單獨表達
- 資料：`ctx.rest('/departments', …)`、`ctx.rest('/departments/{id}', …)`、`ctx.rest('/departments/plan', …)`；`refetchInterval = 10_000 ms`、`staleTime = 5_000 ms`
- Refresh（使用者手動）：一次點擊以 `queryClient.refetchQueries({ queryKey: ['department-hub'] })` 同時重抓部門清單與目前開啟的部門詳情（含事件清單），按鈕在兩者都完成前維持 `Refreshing…`／`disabled`／`aria-busy="true"` 且至少 600 ms，完成後 header 的 snapshot 旁顯示 `Updated HH:MM:SS` 約 3 秒後消失、失敗則顯示 `Refresh failed — showing the last snapshot`；10 秒背景輪詢不會觸發這兩種提示。
- 錯誤：清單與詳情各自有 `ErrorState`；後端沒有回應時顯示「後端沒有回應：…」，**不會**顯示空部門清單
- 誠實標示：
  - `Read-only view.`
  - `A Hermes profile is a routing/identity binding, not a security sandbox.`
  - `Server snapshot: <time>. Status is an observed snapshot; it is not a live push.`
- 建立部門：表單可填 id/name/manager/members/reviewers；`POST /departments/plan` 只做真實驗證，`POST /departments` 會收到 409 `unsafe_mutation_blocked` 與 operator steps。頁面不會假裝已寫入 config。
- 未提供 socket push；OAuth remote 的 `ctx.socket` 限制不影響此頁（輪詢 fallback 是唯一路徑）。

### 視覺驗收状态

**尚未實際看到 Desktop UI 畫面。** 本次只測到：
1. `node --check`（ESM 語法）通過；
2. SDK mock 把 import 換成 stub 後執行 registration/i18n/元件輸出，`ALL PASS`；
3. 後端 payload 的欄位與文字由 Python 測試驗證。

真實像素、native grant/OAuth、實際 Desktop 載入與互動，屬 **M2-R 的獨立驗收**；未看到畫面之前不得宣稱「Desktop 視覺渲染通過」。

---

## 3. 後端 API

全部路由掛在 `/api/plugins/department-hub/`：

| Method | Path | 行為 |
|---|---|---|
| GET | `/health` | read-only、caller kind、board probe、部門數、refresh 上限 |
| GET | `/departments` | 部門清單 + 工作 rollup + board health + degraded |
| GET | `/departments/{id}` | 部門詳情 + tasks + review/blocking/acceptance/delivery + durable events |
| GET | `/departments/{id}/events?limit=1..50` | M1 store 的 durable event 尾巴 |
| POST | `/departments/plan` | 以 M1 `Department` 驗證，回 config snippet + operator steps；**零寫入** |
| POST | `/departments` | 409 `unsafe_mutation_blocked`；零寫入 |
| PATCH | `/departments/{id}` | 409 `unsafe_mutation_blocked`；零寫入 |
| DELETE | `/departments/{id}` | 409 `unsafe_mutation_blocked`；零寫入 |

授權：

- `_principal(request)` 只從 gateway 的 `request.state` 蓋章讀（共 3 種，依序）：
  - `native_http_principal` → `{"kind":"native","profile_bound":true}`
  - `token_authenticated` → `{"kind":"token","profile_bound":true}`
  - `session`（登入牆驗證過的 Session：bearer 原生 app 或 cookie；`dashboard_auth/middleware.py` L199/L258/L303）→ `{"kind":"session","profile_bound":false}`
  - 否則若 `app.state.auth_required` → **401**（`session` 為 `None`/`False`/空值同樣不算，fail-closed）
  - 否則 → `{"kind":"loopback","profile_bound":false}`（誠實標示 loopback，不假裝 profile 身分）
- **不**從 header/query/body 取 caller；測試 `test_principal_never_comes_from_the_caller`、`test_caller_supplied_credentials_never_authorize`。
- 所有 handler 是 sync `def`，FastAPI 會丟到 threadpool，不阻塞 gateway event loop。

資料最小化：

- 不輸出 `chat_id`、`platform`、`workspace_path`、絕對路徑、transcript、comment body、event payload、run summary。
- 事件只輸出 `event_id/task_id/kind/run_id/at/state/detail`，detail 截斷 300 字。
- 回應 `Cache-Control: no-store`。

錯誤碼：

| 情況 | HTTP | code |
|---|---|---|
| 未授權（auth_required） | 401 | `authentication required` |
| 未知部門 | 404 | `unknown_department` |
| 建立/編輯/刪除 | 409 | `unsafe_mutation_blocked` |
| 宣告驗證失敗 | 400 | `invalid_department` |
| board 讀取失敗 | 503 | `board_unavailable`（含 `BoardError.kind`） |
| M1 store 失敗 | 503 | `state_unavailable` |
| config 失敗 | 503 | `config_invalid` |
| 未預期例外 | 500 | `internal_error`（只回 type name，不回 traceback/path） |

Bounded refresh：

- `LIMITS`：20 departments、50 tasks/department、50 events、300 字、snapshot TTL 5 s。
- 清單/詳情有 TTL cache；`generated_at` 與 `generated_at_iso` 是伺服器快照時間。
- 前端每 10 秒輪詢；`staleTime` 5 秒，避免重複請求。

工作狀態 rollup：

- 以 `role != "root"` 的 work cards 計算；root 是部門報告容器，**不會**因為 root 仍 open 而把完成的部門顯示成 queued。
- 只有 root、沒有 work card 時才用 root 自己當 rollup（root-only 部門仍顯示其狀態）。
- 回應含 `scope: "work_tasks" | "all_tasks"`、`items_considered`，讓 UI/審查者知道口徑。

---

## 4. M1 依賴介面清單（M1-R 若變更，只需改薄層）

M2 對 M1 的依賴集中在 `department_lib/dashboard.py` 與 `dashboard/plugin_api.py`：

### 4.1 設定 / 部門

- `department_lib.config.load_departments({"departments": [...]}) -> dict[str, Department]`
- `Department` 欄位：`dept_id`、`name`、`active`、`manager_profile`、`members`、`reviewers`、`max_repair_rounds`
- `Department.from_mapping(candidate)`（`plan_department` 使用）
- `ConfigError`（驗證失敗）

### 4.2 Store（M1 durable SQLite）

- `Store(path)`
- `register_department(dept)`
- `departments() -> list[str]`（上限由 M2 套用）
- `get_department(dept_id) -> Department | None`
- `tasks_for_dept(dept_id) -> list[(task_id, role)]`（role：`root` / `work`）
- `root_task(dept_id) -> str | None`
- `active_acceptance(dept_id, task_id) -> AcceptanceRow | None`
  - `revision`、`artifact_sha256`、`manager_profile`
- `deliveries_for_dept(dept_id) -> list[DeliveryRow]`
  - `task_id`、`kind`（`final` / escalation）、`state`（`pending` / `confirmed` / `unknown` / `dead_letter`）、`delivery_key`、`detail`、`attempts`
- `snapshot() -> dict`（departments/tasks/events/receipts/deliveries/escalations 計數）
- `StoreError`
- `_durable_events()` 直接讀 `events` 表：`event_id, dept_id, task_id, kind, run_id, created_at, state, detail`
  - 這是 M2 唯一直接依賴 M1 store schema 的地方；schema 變更時改 `dashboard.py` 一處。

### 4.3 審查證據 / gate（v5 未改）

- `department_lib.acceptance.evidence_from_board(dept, BoardTask, *, artifact_lookup=store.artifact_identity) -> ReviewEvidence`
  - `revision`（repair rounds + 1）
  - `verdict.kind`（`PASS` / `REQUEST_CHANGES`）、`run_id`、`reviewer_profile`、`artifact_sha256`、`note`
  - `artifact_sha256`、`conflict`、`repair_rounds`、`implementer_profile`、`requested_reviewer`
  - `provenance_reason`、`prose_unproven`、`unbound_rejection`、`declaration_conflict`
- `department_lib.acceptance.evaluate(dept, BoardTask, AcceptanceClaim, artifact_lookup)` → `GateResult`
  - 薄層以「board 上實際存在的 evidence」合成 claim 呼叫它，取得 `department_accept` 會回的
    同一個 reason code；`evaluate()` 是純函式，這是唯讀呼叫，不寫入、不接受任何東西。
  - 因此 claim-dependent 的 `revision_mismatch` / `stale_verdict` / `artifact_mismatch`
    不會出現在讀模型（它們描述 manager 送錯 claim，不是 board 狀態）。

### 4.4 Board 形狀

- `department_lib.board.BoardTask`：`task_id`、`status`、`assignee`、`parents`、`children`、`comments`、`events`、`runs`
- `BoardEvent`：`task_id`、`kind`、`run_id`、`created_at`（int）、`payload`（dict）
- `BoardRun`：`id`、`profile`、`status`、`outcome`、`summary`、`metadata`
- `department_lib.kanban_cli.BoardError`：`kind in {unavailable, timeout, invalid, not_found}`

### 4.5 錯誤碼

- `BoardError.kind` → API `board_unavailable` 503
- `ConfigError` → `config_invalid` 503（plan 時 `invalid_department` 400）
- `StoreError` → `state_unavailable` 503
- `KeyError` → 404 `not_found` / `unknown_department`
- `MUTATION_CODE = "unsafe_mutation_blocked"`（409）

> **若 M1-R 修改上述任何欄位/錯誤碼**：只改 `department_lib/dashboard.py` 與 `dashboard/plugin_api.py` 的錯誤映射；`desktop/department-hub/plugin.js` 只認 JSON shape，不 import M1。

### 4.6 M2 對齊 M1 v5（plugin 持久報告；本輪）

> 依 `t_090d5884` PASS 的 M1 v5 product 與 `docs/m1-v5-contract.md`、清單
> `M2-v5-ALIGNMENT.md`（sha256 `4b2df347…8cecf`）。逐項對照見交付的
> `ALIGNMENT-M2-v5.md`。

v5 改變的是**交付終點**：`department_deliver` 不再 append board comment，
而是把結構化 final report 寫入 plugin 自有 SQLite 的 `deliveries.body_json`。

| 項目 | v4.1 舊語義 | v5 對齊後 |
|---|---|---|
| 契約版本 | `department-hub/m1-contract/v4.1` | **`department-hub/m1-contract/v5`** |
| 交付位置 | root card 的 comment（remote effect） | **plugin SQLite `deliveries.body_json`**；不讀檔案、不讀 board comment |
| delivered 可達性 | final 走 remote effect（歷史語義） | **stock port 即可 delivered**；final 完全不呼叫 append/find，交付內容直接寫入 plugin DB |
| `effect_fencing_unavailable` | deliver held 碼之一 | **移除**（程式與文件都不再出現） |
| `report_too_large` | 無 | 新增 final held 碼；報告 >65,536 UTF-8 bytes 或 summary >4,096 字 |
| `effect_raised` | 列為 dead-letter | **unknown**（遠端 escalation effect 可能已發生） |
| `not_delivered` | 可能被當 release/held 碼 | **status-only reason**；M2 永不當 mutation action 或 readiness code |
| delivery identity | nested `{verdict,state,root,…}` | **canonical 四欄 `task_id/revision/verdict_run_id/artifact_sha256`** |
| 報告投影 | 只有 delivery state/detail | 從 `deliveries.body_json` 投影 summary／delivered_at／schema／generation／accepted identity；上限 65,536 bytes／summary 4,096 字 |
| 第二世代 | 只顯示 conflict 布林 | current identity 與 **stored identity 分開**顯示；歷史報告仍可讀 |
| board 不可用 | 只能顯示 final state | 部門層 **Delivered（plugin 持久報告）** 清單仍可讀（來源是 plugin DB） |
| `already_delivered` 文案 | 「already delivered · identity」 | **Already delivered — existing report; no duplicate created**／**已交付過，沿用既有報告，未重複建立**（附實際 stored identity） |
| `delivery_identity_conflict` 文案 | 「identity conflict」 | **A different identity was already delivered; original report preserved**／**已有不同版本交付，原報告保留，不可覆寫**（附實際 stored identity） |

已保留：mutation 一律 409 零寫入、唯讀、bounded refresh、無 delivery/escalation
UI mutation 入口、`Ready to deliver` 不是已交付、新世代不可顯示「可再次交付」、
ownership 三態、`ensured` ≠ 已醒、free-text negative 與 producer-only SHA。

以下 v4.1 段落保留為歷史（第一輪 v2、第二輪 v4.1 的對齊紀錄）。

第一輪（`t_255f16e5`）是依 v2 盤點，v4.1 針對下列語義變更重新對齊：

| 項目 | v2 第一輪假設 | v4.1 凍結 | 本輪結果 |
|---|---|---|---|
| 拒絕權威 | structured/canonical verdict 或 `verdict=None` | **`outcome=changes_requested` + 授權 review claim**；structured verdict 只能是額外約束 | 已改：讀模型改呼叫 `evaluate()`，不再自行由 verdict 字串推論 |
| `review_unproven` | 無 | 無 structured verdict、summary 非 `PASS`/`REVIEW: PASS` 開頭 → held | 新增：API 與 UI 都顯示此碼 |
| `rejection_unbound` | 無 | 未綁定 run 的 `changes_requested` 不得放行舊 PASS | 新增：同上 |
| `artifact_declaration_conflict` | 以 `artifact_mismatch` 表達 | board 宣告與 producer 不一致 → declaration conflict；無 producer → `artifact_unbound` | 已改：`artifact_sha256` 只取 producer 值 |
| `ownership_changed` | M2 自創 `membership_changed` | 凍結碼是 `ownership_changed` | 已改：讀模型與 UI 改用凍結碼 |
| `delivery_identity_conflict` | 只看 `deliveries.detail` 字串 | 第二世代即使 gate releasable，deliver 仍 held；prior identity 在 delivered body | 已改：由 confirmed row 的 body identity 與目前 evidence 比較後計算 |
| `escalation_detail_conflict`（卡身用語） | — | **v4.1 沒有這個碼**；實際是 `escalation_identity_conflict`（held）與 `escalation_detail_invalid`（參數錯誤） | 文件更正；M2 無 escalation 入口，不適用 |
| artifact identity 來源 | reviewer 宣告 SHA | 只來自 `department_submit`，且是 worker 宣告值 | 已改：新增 `artifact_source` 與 UI 文案限制 |

第一輪逐項盤點（保留為歷史）：

| M1 v2 變更 | M2 原況 | 本次候選處理 |
|---|---|---|
| `acceptance.py` held 碼：`no_pass_verdict`、`review_rejected`、`verdict_conflict`、`artifact_unbound`/`artifact_mismatch` | `review` 只有 verdict/conflict，無 held 概念；`NONE` 與「未審」在 UI 不可區分 | **需修改，已補**：`review.state`（`pass`/`rejected`/`held`/`none`）、`review.held`、`review.held_code`；`accepted` 仍只來自 M1 store，且舊 revision 的 acceptance 標為 `current=false`；UI 以文字顯示 held，不以 PASS 呈現 |
| `routing.py`：`ownership_unknown`/`session_owner_unverified`；`ok` 不再因同 chat 為 true；`ensured` ≠ 已醒 | 只顯示 `manager` profile 字串，沒有主管狀態 | **需修改，已補**：`manager_status` 三項獨立欄位：`subscription`（唯讀 `kanban_notify_subs`）、`owner`（固定 `unverified/session_owner_unverified`）、`wake`（固定 `unconfirmed/ensured_not_awake`）；`verified` 永遠 false，UI 三段文字，不是單一綠燈 |
| `orchestrator.py`：`department_bind.task_ids` 明示 membership；`ownership_unverified`/`ownership_unknown`/`membership_changed` | 只顯示 store bound tasks，沒有 per-task 綁定狀態 | **需修改，已補**：每張卡 `binding.source`（`explicit_membership`/`legacy_bind_task`/`root_binding`）與 `binding.state`；父拓撲與記錄不符 → `membership_changed` + `rebind_required`；部門層 `membership` 彙總；UI 顯示「需重新綁定」橫幅 |
| `delivery.py`/`__init__.py`：`delivered_identity`、`delivery_identity_conflict`、`in_flight_unknown` | 只顯示 final delivery 的 `state`/`detail`；aggregate 把 `unknown` 變成 `pending` | **需修改，已補**：`final_report.delivered_identity`（實際 revision/verdict/root）、`identity_conflict`；aggregate 保留 `unknown`（優先序 unknown > dead_letter > pending > delivered），新增 `unknown_count`/`identity_conflicts` |
| `__init__.py`：escalation 固定 `reason_code` enum + `detail={round:int 0..1000}` | M2 沒有任何 escalation 入口 | **不適用（無入口）**：M2 只有唯讀事件尾巴；契約測試斷言 router 沒有 escalation/delivery mutation route。未來若加入入口，必須同步 enum/錯誤碼 |
| `board.py`/`models.py`：optional `events[].id` → `source_id`；拒 duplicate run IDs | SQLite adapter 讀了 `task_events.id` 但建 `BoardEvent` 時丟棄；沒有 duplicate run 檢查 | **需修改，已補**：`source_id` 在 M1 v2 `BoardEvent` 有欄位時原樣帶入；重複 run id 或非法 source id 型別 → `BoardError(kind='invalid')` |

M2 既有性質維持：mutation 一律 409 零寫入、只讀 M1 store/board、不 import Hermes 私有模組、中英標籤、鍵盤可達、狀態非僅顏色、10 秒 bounded refresh。

**讀模型碼彙總**：`review.held_code` 只會是 `ACCEPT_HELD_CODES`（20）之一；
`delivery_readiness.code` 只會是 accept/deliver held 或 unknown/dead-letter 的凍結碼；
`not_attempted` / `event_at` / `board_unavailable` / `department_unreadable` /
`department_missing` 永不當作 release 狀態（契約 §4.6）。payload 另帶
`contract.version = department-hub/m1-contract/v5` 與 `read_only: true`。

### 4.7 M2 自己額外寫入的 store 綁定（僅 E2E/維運）

- `store.bind_task(dept_id, task_id, role)`：legacy 顯式關聯，E2E 在沒有 parent link 的圖上仍可用。
  `department_bind(task_ids=...)` 會寫入 `membership_bindings`（root + parents fingerprint）；
  dependency edge 本身不再自動認領，legacy `bind_task` 每次讀取都會重新驗證 topology/root。
  M2 只讀取這些綁定並顯示 `ownership_changed`，不會替 operator 重新綁定。

---

## 5. 真理事實規則（不得由 UI 自行推斷）

1. **Kanban 是任務狀態唯一來源**：`status/assignee/last_event/blocking reason/review evidence` 都來自 board 讀取；讀不到就是 `unknown_tasks`，不是空清單。
2. **accepted / delivered 只來自 M1 store**：UI 不從「有 PASS」自行推論 accepted，也不從「有 acceptance」自行推論 delivered；`active_acceptance` 與 `deliveries` 分開顯示。
3. **M2 沒有任何寫入路徑**：不寫 Kanban、不寫 profile、不寫 config.yaml、不重啟 gateway；建立/編輯/刪除一律 409 + 零寫入。
4. **profile 不是沙箱**：頁面明示 profile 綁定只是 routing/identity，不是 ACL/security boundary。
5. **P0 已知限制不修**：default 訂閱繼承穿透、共享 board child metadata exposure、identity 不是認證、序號可被燒耗、已交付後更正語意未定。M2 不宣稱隔離或認證。
6. **沒有 live data 假造**：E2E 使用隔離 board 的實際 SQLite 與實際 CLI 讀取；沒有 gateway/model 的 live wake，因此不宣稱 live wake。
7. **`ready_to_deliver` 只代表 manager 已接受 exact evidence**：不得寫成「reviewer 已驗證 artifact
   bytes／內容已比對／reviewer 蓋章」。artifact SHA 是 worker 透過 `department_submit` 宣告的
   雜湊，reviewer 可能從未看過那些 bytes（M1 契約 §7、§8）。UI 顯示
   `Ready to deliver — manager-accepted evidence: revision / review run / worker-declared SHA-256`
   並附「不是 reviewer byte attestation」的說明。
8. **第二世代沒有第二個 handoff**：task 一旦 delivered，後續世代即使 accept 成 `releasable`，
   `department_deliver` 也只回 `held/delivery_identity_conflict`；UI 顯示
   `delivery held (delivery_identity_conflict)` 與 prior delivered identity，**不得**顯示成
   「可再次交付」或 `ready`。
9. **`ensured` 不是「主管已醒」**：M2 完全不顯示 `department_bind` 的 `ensured` 欄位；
   `manager_status.wake` 固定 `unconfirmed / ensured_not_awake`，`subscription.wake_mode` 只是
   路由訂閱事實，不是喚醒證據。

---

## 6. 實際測試證據

### 6.1 Python 測試

```bash
cd /home/ubuntu/projects/hermes-department-orchestrator
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

本次結果（`t_68287415`，v5 候選）：**409 passed**（完整輸出見交付的 `logs-full-pytest.txt`；契約測試 `tests/test_m2_v2_contract.py` 53 passed）。
舊 v4.1 候選的 377 個 nodeid 全數保留（missing=0），本輪只新增 v5 報告投影／離線歷史／超限／pure-read 測試。
第一輪 v2 契約檔的 23 個 test 名稱全數保留，其中 4 個 fixture/assertion 依凍結語義更新
（見 `ALIGNMENT-M2-v41.md` §3）。

### 6.2 Node 語法與 SDK mock

```bash
cp desktop/department-hub/plugin.js /tmp/plugin.mjs
node --check /tmp/plugin.mjs          # rc 0
node tests/js/run.mjs                # ALL PASS (rc 0)
```

證據：`.research/m2-node-check.log`、`.research/m2-js-mock.log`。
SDK mock 驗證的是 registration surface、i18n key 覆蓋、listbox 鍵盤、狀態文字、錯誤狀態、snapshot 標示；**不是**真實渲染。
i18n key 覆蓋不是用自製查表：`tests/js/mocks/sdk.mjs` 的 `usePluginI18n` 與 `ctx.i18n.t` 都走 `tests/js/mocks/i18n-resolver.mjs`，那是從 Desktop v0.23.0 `apps/desktop/src/i18n/runtime.ts`（`resolvePath` / `render` / `translateFrom`，加 `@hermes/shared/i18n` 的 `isRecord` 與 `DEFAULT_LOCALE`）**逐字抽出的真實 resolver**，來源 sha256 釘在測試裡：`runtime.ts`／`plugin-i18n.ts` 釘**整檔**（未被切出的 `createPluginI18n.t`、`usePluginI18n` 正是測試替身重新實作的路徑，任何變動都要重審）；`languages.ts`（僅 L15 `DEFAULT_LOCALE`）與 `apps/shared/src/i18n.ts`（僅 L17–19 `isRecord`）只釘**被切出的行範圍**（上游新增語言不會誤報，切片內任何變動仍會紅）。守衛語意是 fail-closed：來源存在但 hash／行範圍不符 → **紅**（exit 非 0，訊息要求重新抽取、重審語意，不得改 pin 讓它變綠）；來源不存在（`HERMES_APP_ROOT` 未設或空字串，且預設路徑也沒有 `apps/desktop/src/i18n/runtime.ts`）→ **可見 skip**（exit 0，訊息列出實際探測的路徑）；`HERMES_APP_ROOT` 明確指定但該路徑無效 → **紅**（誤設定，不 silent fallback 到預設路徑）。每次結尾固定印一行 `guard(b) provenance: PASS | SKIPPED (source not found) | FAILED`。因此頁面字串必須是**巢狀物件**（`subscription: { missing: … }`）；扁平點號鍵（`'subscription.missing'`）會被 resolver 當成 miss 而回傳原始鍵名。

### 6.3 Standalone backend import

```bash
cd /tmp
PYTHONPATH= /home/ubuntu/.hermes/hermes-agent/venv/bin/python - <<'PY'
# by-path import，模擬 gateway dashboard loader
PY
```

結果：`PLUGIN_ID=department-hub`、8 條 route 全部載入、`STANDALONE IMPORT: PASS`。
證據：`.research/m2-standalone-import.log`。

### 6.4 plugin validate

```bash
hermes plugins validate <clean staging copy excluding .research> --json
```

本次結果：`ok: true`、16 checks 全過、**0 warnings**、`desktop surface` 通過、`isolation.verdict=host`。
證據：`.research/m2-plugin-validate.json`。

> 注意：直接對 repo root 跑 validate 會掃到 `.research` 的 P0 lab（824 MB / 28k files），因此驗證對象是排除 `.research` 的 shipping package staging copy；這與 M1 的做法一致。

### 6.5 隔離真實全鏈 E2E（v5 harness）

> `.research/m2-e2e.py` 原本是 v4.1 語義的 harness（沒有 `department_submit`、期待 root card
> 有 final comment），對 v5 **照做必失敗**。2026-11-06 已更新為 v5：先 `department_submit`、
> 不再要求 final comment、不以 `report.ok` 判定 bind。舊的 44-check 輸出是歷史，不是現況；
> 現行指令與結果見 §6.5.1。

```bash
PYTHONPATH=/home/ubuntu/.hermes/hermes-agent:. \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python \
  .research/m2-e2e.py <scratch>/m2-e2e-run
```

v4.1 時代的結果是 **RESULT: PASS（44 checks）**。v5 重跑結果見 §6.5.1。實際使用：

- 真 `hermes kanban` CLI：`init/create/show/notify-subscribe/notify-list/comment`；
- 真 `hermes_cli.kanban_db`：`claim_task` / `claim_review_task` / `request_changes` / `complete_task`（dispatcher 實際使用的 API；沒有 CLI 的 review-claim 動詞）；
- 真 M1 `DepartmentService`（Board CLI adapter、durable Store、acceptance gate、delivery）；
- 真 M2 `DashboardService` + 唯讀 `KanbanDbBoard`。

鏈路：

1. root card 指派 `dept-manager`，work card 指派 `worker-a`；
2. `department_bind` 驗證 root 在 board 上的 assignee，建立 manager wake subscription 並讀回；
3. worker 第一次 claim → `request-review(reviewer-a)` → M1 ingest；
4. reviewer claim → `request-changes("widget is not fixed yet")` → M1 ingest；
5. board evidence = `REQUEST_CHANGES`、revision 1；`accept` 被 gate 擋下 `review_rejected`；
6. M2 頁面顯示 `REQUEST_CHANGES`、repair_rounds=1、尚無 final report；
7. worker 修正 → 第二次 `request-review` → 獨立 reviewer claim → `complete` 帶 `PASS`（**不宣告 SHA**）；
   artifact identity 由 worker 先呼叫 `department_submit` 對真實本機檔案算 SHA-256 產生
   （v5 producer-only；見 `docs/m1-v5-contract.md`）。
8. worker 先 `department_submit`（對真實檔案算 SHA-256）→ M1 ingest → revision 2、latest verdict
   `PASS`（run 4、reviewer-a）；identity 來自 producer，不是 reviewer 宣告；
9. `release` 在未 accept 時 held `acceptance_missing`；`accept` 後 releasable；`release` → `delivered`；
10. 第二次 `release` → `already_delivered`；root card **沒有** final comment——報告已在 plugin DB `deliveries.body_json`（v4.1 時代的單則 comment 描述是歷史，v5 已改）；
11. M2 重新開 store + 唯讀 board 讀回：work_state `done`、last activity、PASS run id、manager acceptance、final report `confirmed`、department rollup `delivered`、0 unknown、durable events 包含 `changes_requested`/`completed`。

隔離保證：`HERMES_HOME` / `HERMES_KANBAN_HOME` 都在 task scratch；E2E 在連線前清除所有 `HERMES_KANBAN_*` 並 assert `kanban_db_path()` 必須位於 scratch，否則拒絕執行。開發除錯時曾有一次因未清除 dispatcher 注入的 `HERMES_KANBAN_DB` 而解析到 production DB；當時使用的兩個隨機 task id 在 production board 不存在（`claim_task` 回 `None`），事後以唯讀查詢確認兩個 id 都不存在、當下 task=331 / task_runs=432，無任何列變更證據。正式腳本已加 guard，後續 E2E 全程只碰 scratch board。

### 6.5.1 v5 重跑（2026-11-06）

```bash
cd /home/ubuntu/projects/hermes-department-orchestrator
PYTHONPATH=/home/ubuntu/.hermes/hermes-agent:. \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python \
  .research/m2-e2e.py /tmp/m2-e2e-v5
```

harness 已改成 v5：reviewer 只給 PASS（**不宣告 SHA**）；worker 先 `department_submit` 對真實
檔案算 SHA-256（並驗證 caller 宣告的 SHA 會 `held/artifact_unbound`）；bind 不以 `report.ok`
判定（`session_owner_unverified` 是預期答案，root 與 work card 各一筆）；不期待 root final
comment，改讀 plugin SQLite `deliveries.body_json` 的 `department-hub/final/v2` 報告與
canonical 四欄 identity。

結果：**RESULT: PASS（53 checks，0 FAIL）**、rc=0。`kanban_db_path()` 在 scratch
（`<scratch>/kanban-home/kanban.db`）；`root_comments=0`、`delivery=delivered`、
`delivery_repeat=already_delivered`、`report_schema=department-hub/final/v2`、報告 identity 的
`artifact_sha256` 等於真實檔案 SHA。`session_owner_unverified` 如實回報，沒有偽造 wake。
原始 log 附在 `t_00130130` 的交付證據。

### 6.6 未證明的項目

- **真實 Desktop 視覺渲染 / 實際載入 / native grant / OAuth**：未看到畫面，未驗證。M2-R 需以截圖或實際試用獨立驗收。
- **gateway 載入 `plugin_api.py` 並以真實 auth token 呼叫**：本次只做 by-path standalone import 與 FastAPI TestClient；沒有重啟 gateway。
- **live model / manager wake**：E2E 用 synthetic board 事件與 DB transitions，沒有 gateway+model 喚醒。
- **真實 Kanban `show --json` 之外的 CLI 寫入路徑**（除了 E2E 的 CLI 讀取與 notify/comment）。
- **profile 隔離/ACL**：不宣稱。

---

## 7. 安裝步驟（2026-11-06 實測；v0.23.0）

> 本節步驟在 2026-11-06 07:41–07:46 UTC 於本機**實際執行過**：config 備份
> `~/.hermes/config.yaml.pre-department-hub.20261106-074114`（含 `.sha256`）、agent half 與
> desktop half 複製、`hermes -p default gateway restart --yes`。安裝後的檔案與 repo
> **byte-identical**。這是**安裝足跡，不是驗收**：真實 Desktop 畫面仍未經獨立驗證。
> 測試基準 Hermes Agent **v0.23.0（2026.9.24）**，伺服器 Linux、Desktop Windows。
> 本套件**沒有 installer**，以下全是 operator 手動步驟，不是原子安裝。

### 7.1 Agent half（跑 gateway 的機器）

```bash
cd /home/ubuntu/projects/hermes-department-orchestrator
install -d ~/.hermes/plugins/department-hub
cp -a plugin.yaml __init__.py department_lib dashboard \
      ~/.hermes/plugins/department-hub/
```

不要複製 `tests/`、`docs/`、`desktop/`、`.research/` 到 plugin 目錄。確認：

```bash
test -f ~/.hermes/plugins/department-hub/plugin.yaml
test -f ~/.hermes/plugins/department-hub/__init__.py
test -f ~/.hermes/plugins/department-hub/dashboard/plugin_api.py
test -f ~/.hermes/plugins/department-hub/department_lib/artifacts.py
```

### 7.2 Desktop half（跑 Desktop 的機器）

只複製 `desktop/department-hub/plugin.js`；資料夾名必須等於 plugin id `department-hub`。
根目錄是 `<HERMES_HOME>/desktop-plugins`：

| 主機 | 路徑 |
|---|---|
| Linux / macOS（預設） | `~/.hermes/desktop-plugins/department-hub/plugin.js` |
| Windows（預設，未設 `HERMES_HOME`） | `%LOCALAPPDATA%\hermes\desktop-plugins\department-hub\plugin.js` |
| 任何 OS 有設 `HERMES_HOME` | `$HERMES_HOME/desktop-plugins/department-hub/plugin.js` |

Windows 預設會展開成
`C:\Users\<你>\AppData\Local\hermes\desktop-plugins\department-hub\plugin.js`，
**不是** `%USERPROFILE%\.hermes\...`。放錯根目錄不會報錯，app 只是永遠看不到這個 plugin
（2026-11-06 實測：`plugin.js` 51,158 bytes、與 repo 一致，放進
`C:\Users\weng1\.hermes\desktop-plugins\department-hub\` 時 Desktop 看不到）。

兩個容易誤解的點：

- **遠端 Desktop 讀自己的磁碟**：Electron 的 `desktopPluginsRoot` 讀本機 disk。把
  `plugin.js` 放到伺服器的 `~/.hermes/desktop-plugins/` 對另一台機器上的 Desktop 無效；
  必須放到**那一台**的 desktop-plugins 根目錄。agent half（`plugins/department-hub`）才裝在伺服器。
- **瀏覽器版 `hermes dashboard`（預設 9119）不載入 Desktop 外掛**：dashboard surface 的
  `desktop_plugins=False`，只有 `webapp`（Desktop app）surface 為 true。不要用 9119 驗證
  Desktop half。

v0.23.0 載入器對「手放檔案」的實際行為（`runtime-loader.ts` L76–362 原始碼 + 對本 `plugin.js`
實測）：

- **它會是一張獨立的 Desktop 卡**（`desktop:department-hub`，名稱＝plugin 的 `name`
  "Department Hub"，徽章 "Desktop"），**不會**跟 agent 卡（`department-hub`、徽章 "Agent"）
  合併。合併鍵是 `.hermes-package.json` 的 `packageName`，手放的檔案沒有這個標記；遠端模式下
  unified 半邊**永遠不會自動複製到本機**。Agent 卡的 Desktop 欄顯示「—」是**正常**（agent 套件
  沒有 `desktop/` 目錄），不是故障。**不要**替手放的檔案補 `.hermes-package.json`：
  Electron 的 reconcile 會在本機來源不存在時 prune 有標記的半邊。
- **Opt-in 開關**：plugin 宣告 `defaultEnabled: false`，載入器對這種 plugin「只盤點、不呼叫
  `register()`」，直到使用者在 Capabilities → Plugins 打開**那張 Desktop 卡**的開關（Agent 卡
  的開關不影響它）。打開後立即註冊：路由 `/department-hub`、側邊欄 nav 列（codicon
  organization，標籤 Departments／部門）、`Ctrl+K` 指令；**不需重啟**。載入失敗才會有 toast
  與紅色 failed 卡。
- **Windows 重新掃描**：`Ctrl+K` →「Reload desktop plugins」。Desktop 讀檔上限 512 KiB
  （本 `plugin.js` 51,158 bytes，安全）；資料夾名必須等於 plugin id `department-hub`。

以上是**載入器層級證據，不是 UI 驗收**：unsupported imports `[]`、3 個 specifier 被改寫、
改寫後 `node --check` OK、替身模組載入後 default export 合法
（`id`/`name`/`defaultEnabled:false`/`register` 為函式）、`register(ctx)` 產生 3 筆
（page／nav／palette）、SDK 14 個 import 名稱在 v0.23.0 `sdk/index.ts` 全部存在。
**畫面仍未驗證（UNVERIFIED）**。

### 7.3 `config.yaml`（gateway 機器）

在 `plugins.enabled` 加 `department-hub`，並建立／合併
`plugins.entries.department-hub.settings`（`entries` 這個 key 可能原本不存在）：

```yaml
plugins:
  enabled:
    - department-hub        # ← 新增
  disabled: []
  entries:                  # ← 這個 key 原本可能不存在
    department-hub:
      settings:
        state_path: ~/.hermes/department-hub/state.db
        departments:
          - id: dev
            name: Development
            manager:
              profile: default          # 必須存在並啟用 `kanban` toolset
              platform: api_server      # 佔位；真實 wake 需 manager 實際 platform/chat_id
              chat_id: api-dev-session
            members: [normal-worker]
            reviewers: [reviewer]
            max_repair_rounds: 2
```

- 用空白縮排，不要 tabs；YAML 縮排錯會讓 gateway 起不來（備份已於 §7.4 建立）。
- `manager.profile` 必須存在並已啟用 `kanban` toolset。若 manager 不是 `default`，把 agent half
  也裝到 `~/.hermes/profiles/<manager>/plugins/department-hub/`，並在該 profile 的 `config.yaml`
  加同樣的 `plugins.enabled` / `plugins.entries`，`state_path` 指到**同一個絕對路徑**。
  會呼叫 `department_submit` / `department_ingest` 的 member／reviewer profile 同理。
- `platform` / `chat_id` 是 wake subscription 的比對值。佔位值會讓 `department_bind` 回
  `session_owner_unverified`、UI 顯示 `unconfirmed (ensured_not_awake)`——這是正確行為，
  不是安裝失敗。要測真實 wake 請填 manager 實際值，**不要猜 credentials**。

### 7.4 備份、重啟、驗證

```bash
CFG="$HOME/.hermes/config.yaml"
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a "$CFG" "$CFG.pre-department-hub.$STAMP"
sha256sum "$CFG" > "$CFG.pre-department-hub.$STAMP.sha256"

hermes -p default gateway restart --yes     # 會中斷正在跑的 worker；請在 idle 時段執行
hermes -p default gateway status
hermes plugins doctor ~/.hermes/plugins/department-hub --ci   # 7 tool(s), 2 hook(s)
```

- plugin HTTP 路由由 dashboard surface 提供，需要已驗證的 principal。
  `curl http://127.0.0.1:8644/api/plugins/department-hub/health` 在本機回 **404**：
  8644 是 OpenAI-compatible API，不是 dashboard。對 dashboard 路由的未驗證請求回 **401**
  （正確拒絕，不是安裝壞掉）。請用 Desktop app 或已驗證的 dashboard session。
- Desktop：Settings → Plugins 開啟 Department Hub，開 `/department-hub`。

### 7.5 Rollback

```bash
CFG="$HOME/.hermes/config.yaml"
STAMP=<§7.4 印出的 stamp>
hermes -p default gateway stop
cp -a "$CFG.pre-department-hub.$STAMP" "$CFG"
sha256sum -c "$CFG.pre-department-hub.$STAMP.sha256"
rm -rf "$HOME/.hermes/plugins/department-hub"
hermes -p default gateway start
```

Desktop 機器另外刪 `~/.hermes/desktop-plugins/department-hub/`（Windows 為
`%LOCALAPPDATA%\hermes\desktop-plugins\department-hub\`）。
`~/.hermes/department-hub/state.db` **刻意保留**：它保存不可變的 final reports；只有確定要
丟掉交付歷史時才刪。

---

## 8. 六項 installer P1 的狀態（不得誤解）

前一則 orchestrator 留言列的六項（安裝原子性、journal 世代、TOCTOU、rollback 所有權、config 語意、隔離入口不可偽造）是 **Desktop spike 安裝器的缺陷**。**M2 沒有提供安裝/移除/隔離入口**，因此：

- 這六項**沒有被 M2 行使，也沒有被 M2 滿足**；
- 本文件不宣稱它們已修復；
- 若後續要交付一鍵安裝器，必須先補上那六項回歸測試與獨立複審，才能給 Oneal；
- M2 的建立/編輯功能是**明確阻擋**（409 `unsafe_mutation_blocked`），不是 installer。

---

## 9. 已知限制

1. **程式／整合已 PASS，Desktop 畫面未驗收**：`t_090d5884` 對 M1 v5 修復版給 **REVIEW: PASS**
   （direct-boundary durable-conflict fence 已修）；`t_e3d284ae` 對 M1 v5.1 + M2 v5 整合樹給
   「程式與 parity PASS、文件／repo 衛生 REQUEST_CHANGES」。2026-11-06 07:41–07:46 已有手動安裝
   足跡（檔案 byte-identical、config 已備份），但**真實 Desktop 畫面、native grant／OAuth
   仍未經獨立驗證**。
2. **P0 已知限制**：default 訂閱繼承穿透、共享 board metadata exposure、identity 不是認證、序號可被燒耗、已交付後更正語意未定。M2 只如實顯示，不修、不宣稱隔離。
3. **Kanban SQLite schema 依賴**：M2 以唯讀 URI 直接讀 `tasks/task_events/task_runs/task_links`；schema 缺失/漂移會 fail closed（503/unknown），不會回假資料。這是為了 bounded refresh 與避免私有 Python import 的取捨；M2-R 應把 schema 變更視為需要對齊的介面。
4. **沒有寫入路徑**：create/edit/delete 一律 409；department 設定只能由 operator 改 `config.yaml`。這是刻意的最小權限設計，不是功能缺漏。
5. **refresh 上限**：10 秒輪詢、20 departments、50 tasks/department；超過會截斷並標示 `tasks_truncated`。
6. **artifact SHA 是 worker 宣告值**：M1 v4 起 identity 只來自 `department_submit`（worker/manager
   對本地檔案算 SHA-256），reviewer 不宣告、也不 attest bytes；gate 不在 delivery 時重新 hash
   bytes（`docs/m1-v4-contract.md` §2、§8）。因此 `ready_to_deliver` 文案不得暗示 reviewer 已
   驗證 artifact 內容（同契約 §7）。
7. **沒有 git commit/push/deploy**：本次只改本地工作樹。
8. **M1 schema 已凍結（v5）**：候選程式對齊 `department-hub/m1-contract/v5`；這不是
   M2-R PASS，也不是已安裝到 Desktop。真實 Desktop 驗證仍待 Oneal 手動執行。
9. **manager_status 的新 schema 依賴**：`subscription` 直接唯讀 `kanban_notify_subs`（platform/chat/profile/mode）；表不存在或讀取失敗時回 `unknown`，不會假裝已訂閱。owner 永遠 `unverified`，wake 永遠 `unconfirmed`，這是契約限制，不是 bug。
10. **delivery identity conflict 與歷史報告的可見性**：M2 以 confirmed delivery row 的
    canonical 四欄 identity 與目前 board evidence 比較後計算「第二世代不可交付」，並分開顯示
    current／stored identity；報告本身從 `deliveries.body_json` 讀取，因此 board 不可用或
    之後出現新 rejection 時仍可讀。artifact SHA 仍是宣告值，delivery 不重新 hash bytes。
11. **identity 不是認證**：artifact SHA 與交付 identity 都是 worker 宣告值；profile 只是
    routing/identity binding，不是 credential。沒有任何東西證明 reviewer 真的看過那些 bytes。
12. **Desktop half 必須手動安裝，真實畫面未獨立驗證**：`plugin.js` 必須放到 Desktop 機器自己的
    `<HERMES_HOME>/desktop-plugins/department-hub/`（Windows 預設
    `%LOCALAPPDATA%\hermes\desktop-plugins\department-hub\`，不是 `%USERPROFILE%\.hermes\`）。
    目前沒有截圖或 hands-on 記錄，渲染、native grant、OAuth 都未驗證。
13. **沒有 profile ACL**：manager 檢查是 `ctx.profile_name` 比對，不是對抗惡意 runtime／database
    owner 的 ACL；plugin store 與 board 沒有 per-profile 存取控制。

---

## 10. 下一步（M2-R）

M2-R 應獨立：

1. 重跑 `pytest tests/ -q`（本輪 409 passed）與 `node tests/js/run.mjs`；
2. 重跑 `hermes plugins validate` 於排除 `.research` 的 staging copy（ok=true, 16 checks, 0 warnings）；
3. 以 by-path 載入 `dashboard/plugin_api.py`，確認 standalone import；
4. 重跑 `.research/m2-e2e.py`（**v5 harness**，見 §6.5／§6.5.1）：`RESULT: PASS`，且
   `kanban_db_path()` 在 scratch 內；
5. **實際開 Desktop 看 `/department-hub`**（截圖或 hands-on），確認 route/sidebar/palette、鍵盤、zh-TW/en、錯誤與空狀態、read-only 標示；未看到畫面不得給 PASS；
6. 核對 M1-R 是否已 current-run PASS；若 M1 API/欄位/錯誤碼有變，檢查 `dashboard.py` 是否需對齊；
7. 檢查六項 installer P1 是否被錯誤宣稱已滿足（本 M2 沒有 installer，應標為 not applicable / open）。

---

## 11. Supersedes：`.research/M2-DESKTOP-MANUAL-ACCEPTANCE.md`

那份文件保留為歷史證據，**不可再當作安裝指南**：

- 它的安裝來源是 v4.1 時代的 `t_c410652a/candidate`（bundle sha256
  `cdd15ad3…6ef6`），不是本 repo。
- 它開頭寫**「不要安裝 `/home/ubuntu/projects/hermes-department-orchestrator/`」**，理由是該目錄
  仍是 v1；這句話在 2026-11-06 整合後已 **false**，而且 07:41 的手動安裝（config 備份名
  `config.yaml.pre-department-hub.20261106-074114` 與該文件的 `$STAMP` 格式一致）顯示有人正是
  照它操作。
- **正確的安裝來源是 repo root**，步驟以本文 §7 為準（含 Windows 路徑、遠端 Desktop、
  rollback）。該文件的 config 備份／還原模式仍有效，已收進 §7.4–§7.5。

同理，`.research/m2-e2e.py` 的舊 44-check 輸出是 v4.1 歷史證據；現行 harness 已更新為 v5
（§6.5）。
