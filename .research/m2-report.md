# M2 交接報告 — Desktop 管理介面與端到端整合

- 任務：`t_6914a4a2`（normal-worker）
- 狀態：**實作完成；不是驗收 PASS。** M2-R 仍被 M1-R 的 `REQUEST_CHANGES` 卡住。
- 產物：`/home/ubuntu/projects/hermes-department-orchestrator/`
- 詳細文件：`docs/m2-desktop.md`

## 1. 前置現況（開工前已核對）

- M1 `t_a1c1b08b`：done，handoff 說 160 tests + plugin validate + isolated CLI contract PASS。
- **M1-R `t_4be3305e`：`REQUEST_CHANGES`（run 455），不是 PASS。** 報告 `.research/m1-review.md`。
  阻塞：明確 REQUEST_CHANGES 被當 PASS 交付、wake owner 未設/核對、未知/過期 ownership 與 ancestry 失敗仍錯路由、遲到 review event 撤銷新 acceptance 且重試崩潰、兩條獨立連線重複 append、escalation 原文輸出敏感 detail。
- P0-R `t_369a6f8c`：僅 `routing_and_security_boundary_only` 的 scoped PASS；default 訂閱繼承穿透與共享 board metadata exposure 未修。
- Desktop SDK `t_30ed8424`：有條件可行；spike installer 已 withdrawn，未執行。
- 因此 M2 **沒有**假設 M1 的安全性質成立，也不宣稱 profile 隔離是沙箱。

## 2. 本次新增/修改

新增：

- `desktop/department-hub/plugin.js`（由 `desktop/plugin.js` 移到 folder=id，符合 Desktop SDK 規則）
- `dashboard/plugin_api.py`
- `dashboard/manifest.json`
- `department_lib/dashboard.py`
- `department_lib/kanban_db_source.py`
- `tests/test_dashboard.py`
- `tests/test_dashboard_api.py`
- `tests/test_kanban_db_source.py`
- `tests/test_desktop_plugin.py`
- `tests/js/{run.mjs,mocks/*}`
- `docs/m2-desktop.md`
- `.research/m2-e2e.py` / `.log`、`.research/m2-plugin-validate.json`、`.research/m2-standalone-import.log`、`.research/m2-node-check.log`、`.research/m2-js-mock.log`

修改（M2 薄層/測試，未改 M1 既有檔案）：

- `department_lib/dashboard.py` 的 work rollup：root 是報告容器；有 work cards 時只 roll up work cards，避免 open root 讓完成部門顯示 queued。新增 `scope` / `items_considered`。
- `department_lib/kanban_db_source.py` 改為**不 import 任何 Hermes Python 模組**，以唯讀 SQLite URI 讀 board 並驗證 schema；schema 缺失/漂移 fail closed。

未修改：`plugin.yaml`、`__init__.py`、M1 的 `department_lib/*`（除上述兩個 M2 新檔）、`README.md`。

## 3. 實際測試證據（本次 run）

| 檢查 | 指令 | 結果 |
|---|---|---|
| Python 全套 | `pytest tests/ -q -p no:cacheprovider` | **202 passed in 11.71s** |
| Node 語法 | `node --check plugin.mjs` | **rc 0** |
| SDK mock | `node tests/js/run.mjs` | **ALL PASS (rc 0)** |
| Standalone backend import | by-path `importlib` from `/tmp` | **STANDALONE IMPORT: PASS**（8 routes） |
| plugin validate | clean staging copy, `--json` | **ok=true, 16 checks, 0 warnings, desktop surface ok** |
| 隔離真實全鏈 E2E | `.research/m2-e2e.py` | **RESULT: PASS（44 checks）** |

E2E 使用真 `hermes kanban` CLI（init/create/show/notify-subscribe/notify-list/comment）、真 `hermes_cli.kanban_db` 的 dispatcher transitions（claim / claim_review_task / request_changes / complete_task）、真 M1 `DepartmentService`、真 M2 `DashboardService` + 唯讀 `KanbanDbBoard`。鏈路：root 指派 manager → bind + wake subscription 讀回 → worker claim → review v1 → `REQUEST_CHANGES` → gate `review_rejected` → M2 顯示失敗與 repair round → 修正 → 第二次 review → 獨立 reviewer PASS（SHA bound）→ 未 accept 時 `acceptance_missing` → accept → `delivered` → 第二次 `already_delivered` → root 只 1 則 final comment → M2 讀回 done/PASS/acceptance/final delivered/0 unknown/durable events。

隔離：E2E 在連線前清除所有 `HERMES_KANBAN_*`，assert board path 在 task scratch 內，否則拒絕執行。開發除錯時曾有一次未清除 dispatcher 的 `HERMES_KANBAN_DB` 而解析到 production DB；隨機 task id 不存在、`claim_task` 回 None，事後唯讀確認 production 無該 ids（task=331 / task_runs=432），無列變更證據；正式腳本已加 guard。

## 4. M1 依賴介面（供 M2-R 核對漂移）

完整清單見 `docs/m2-desktop.md` §4。摘要：

- `department_lib.config.load_departments({"departments": [...]}) -> dict[str, Department]`
- `Department`：`dept_id/name/active/manager_profile/members/reviewers/max_repair_rounds`
- `department_lib.store.Store`：`register_department`、`departments()`、`get_department`、`tasks_for_dept() -> [(task_id, role)]`、`root_task`、`active_acceptance() -> {revision, artifact_sha256, manager_profile}`、`deliveries_for_dept() -> {task_id, kind, state, delivery_key, detail, attempts}`、`snapshot()`、`StoreError`
- `department_lib.acceptance.evidence_from_board(dept, BoardTask)`：`revision`、`verdict.{kind,run_id,reviewer_profile,artifact_sha256,note}`、`artifact_sha256`、`conflict`、`repair_rounds`、`implementer_profile`、`requested_reviewer`
- `department_lib.board.BoardTask` / `BoardEvent` / `BoardRun` 欄位見文件
- `department_lib.kanban_cli.BoardError.kind in {unavailable,timeout,invalid,not_found}`
- 錯誤映射：`board_unavailable` 503、`state_unavailable` 503、`config_invalid` 503、`unknown_department` 404、`invalid_department` 400、`unsafe_mutation_blocked` 409、未授權 401、未預期 500
- `dashboard.py` 直接讀 M1 `events` 表的 `event_id,dept_id,task_id,kind,run_id,created_at,state,detail`

若 M1-R 改動任何上述形狀，只需改 `dashboard.py` / `plugin_api.py`；`plugin.js` 只認 JSON。

## 5. 非宣稱 / 未證明

- **沒有看到真實 Desktop 畫面**；視覺渲染、實際載入、native grant/OAuth 都未驗證，M2-R 需另做 screenshot 或 hands-on。
- **沒有重啟 gateway / 沒有載入真實 backend**；只做 standalone by-path import 與 FastAPI TestClient。
- **沒有 live model / 真實 manager wake**；E2E 是隔離 board + DB transitions。
- **profile 不是安全沙箱 / 不是 ACL**：頁面明示；M2 不提供認證或隔離。
- **沒有 install/uninstall/isolation entrypoint**：六項 spike installer P1（安裝原子性、journal 世代、TOCTOU、rollback 所有權、config 語意、隔離入口不可偽造）**未被行使、未被滿足、未宣稱修復**。`docs/m2-desktop.md` §7 的安裝步驟是草案，未執行、未經獨立複審。
- **沒有 push/deploy/公開發文/支出**；沒有改任何現有 profile/board/gateway/通知/FreshTape。
- artifact SHA 是 reviewer 宣告的 metadata，delivery 不重新 hash bytes（M1 已知限制）。

## 6. 給 M2-R 的重跑清單

1. `pytest tests/ -q` → 202 passed
2. `node tests/js/run.mjs` → ALL PASS；`node --check` → rc 0
3. `hermes plugins validate <staging excluding .research> --json` → ok=true, 16 checks, 0 warnings
4. by-path import `dashboard/plugin_api.py` → 8 routes
5. `.research/m2-e2e.py` → 44 checks PASS，board path 在 scratch
6. **實際開 Desktop `/department-hub`**（截圖/hands-on）；未看到 UI 不得給 PASS
7. 核對 M1-R 是否 current-run PASS；若 M1 API 漂移，檢查薄層對齊
8. 六項 installer P1 仍為 open/not applicable（M2 無 installer），不可被誤記為已滿足
