# Department Hub｜隔離研究原型 v2 報告（P1 修復交付，供獨立複審）

- 卡片：`t_98ede0aa`（父卡：獨立審查 `t_e83d632f` / run 385，判定 REQUEST_CHANGES）。
- 狀態：**v2 修復完成，僅供獨立複審**；本卡完成**不是**獨立驗收、**不是** P0 PASS、**不是** M1 授權。
- **不解除 `t_369a6f8c`、不放行 `t_a1c1b08b`。** 未修改正式 board/profile/credentials/gateway/Hermes core/FreshTape；未安裝、未 push、未部署、未對外訊息。
- 受測 runtime：`/home/ubuntu/.hermes/hermes-agent`，HEAD `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`，venv Python 3.11.15。
- 模型：**deterministic synthetic model（無推理、無 provider、無外網）**；live 使用**真 gateway、真 profile、真 Kanban CLI 工具**。所有 live 證據明確標示 `synthetic_model: true`。
- 原 `prototype-v1/` 與既有證據**未被覆蓋**：manifest SHA-256 `26f6046b714f0289b9aafa6e9c6df360b27d30180629f7127899943d51645b97`，193/193 檔 `sha256sum -c` 通過（`evidence/v1-immutability.json`）。

## 1. 交付範圍

依獨立審查報告的 6 項 P1，在 `.research/prototype-v2/` 內修復並以可重跑測試與隔離重跑驗證：

1. recovery 不得繞過 acceptance／新 artifact／topology gate；
2. 未知外部效果不得盲目重送；
3. artifact/verdict/acceptance 必須精確綁定實際 immutable bytes；
4. 未知 session owner 不得被 ACK；
5. report 卡必須由權威 board 證明完全 detached；
6. domain mutation、receipt、event 狀態必須原子（單一 transaction）。

**不是**重寫 Hermes core，也**不是**把 prototype 的結論升格為平台安全性承諾。

## 2. 六項修復與實際行為

### F1 recovery stale gate

- v1：`recover()` 直接依 outbox 驅動 effect；gate 只在正常 `deliver_final` 跑。
- v2：`_drive_delivery()` 先 `probe` 既有效果，再決定；任何**新** effect 前重新執行完整 `_gate()`，並比對 intent 建立時保存的 `gate_fingerprint`。gate 失效或 fingerprint 不同 → `held_stale` / `superseded`，寫 `gate_rejections` 與 alert，**不發送**。
- 已送出但事後失效：`_confirm_effect()` 仍寫 receipt（歷史事實），狀態記為 `historical_confirmed`，回 `held/stale_after_effect`；不宣稱「目前已接受」，也不重送。
- 驗證：`tests/test_p1_regressions.py::Finding1RecoveryStaleGate`（2 個反例）、`test_v2_verification.py::ProcessLevelStaleEffectTests`（真子程序送 effect 後改 artifact，再 recovery）。

### F2 unknown effect 禁止盲目重送

- v1：`find() is None` 同時代表「確定不存在」與「讀不到」；read-back 隱藏時每次 recovery 都再 apply。
- v2：`effects.py` 改為 `probe()` 回傳 `FOUND` / `ABSENT`（權威地不存在）/ `UNKNOWN` / `CONFLICT`。
  - 只有 `ABSENT` 才允許新 effect；`UNKNOWN` 在任何 attempt 之後都不得再送。
  - 唯一例外：全新 intent 且 `attempts == 0`（attempt 標記先 commit，尚不可能已送出）可送一次。
  - `CONFLICT`（同 marker、body/author 不符）→ `effect_unknown` + alert，不確認、不重送。
  - read-back 驗證 exact task、author 與**完整 body**（`body_digest`），不是 marker 子字串。
- 驗證：`Finding2UnknownEffect`（2 個）、`OwnerAndEffectConflictTests::test_conflicting_existing_effect_is_never_confirmed_or_resent`。

### F3 exact artifact / verdict / acceptance

- v1：acceptance 不讀 `artifact_sha`、verdict 可省略 SHA、delivery 只比 DB 記錄不比檔案 bytes。
- v2：
  - `review.verdict` 必須帶合法 sha256，且等於 run 的 `artifact_sha`、等於目前 artifact 的**實際 bytes**；否則 `missing_artifact_sha` / `wrong_artifact`。
  - `manager.acceptance` 必須明示 `verdict_id`、`item_id`、`artifact_sha`、`rev`；逐項比對 item、request、run、verdict、latest artifact、actual bytes、item owner 與 reviewer 不同人；任一缺漏或不符 fail closed。
  - publish 時把 artifact bytes 快照到 content-addressed `blobs/<sha256>`；delivery gate 重驗 snapshot blob，而非可變的來源路徑。來源檔事後被覆寫不影響已核可內容；blob 缺失／損毀 → `artifact_corrupt`，不送。
- 驗證：`Finding3ExactArtifactBinding`（4 個，含跨 item 同 bytes 的 verdict）、`ArtifactImmutabilityTests`（2 個：來源變更不改變交付 bytes；blob 變更 fail closed）。

### F4 unknown owner

- v1：只有「已知 mapping 但不一致」才拒絕；未知 session 直接 admitted，之後寫 receipt/processed。
- v2：`ingest_board_event()` 在 **admission 之前**即 dead-letter：missing/empty `chat_id`、未映射 session、profile 不一致皆 `dead_letter/unknown_owner|wrong_owner`，不寫 `board_events`、不寫 observation、不寫 receipt。
- 處理階段 `_process_board_event()` 在同一 transaction 內重驗 mapping；session 在 admission 後被撤銷 → `dead_letter`，無 receipt。
- 驗證：`Finding4UnknownOwner`、`OwnerAndEffectConflictTests::test_revoked_session_at_processing_time_produces_no_receipt`；live 也直接驗證未映射 owner 無 admission/observation/receipt。

### F5 完全 detached report

- v1：只拒絕 report 邊與「已註冊非 report work IDs」的交集；setup／root／未知卡／間接中介未拒絕。
- v2：`_gate()` 以權威 board 讀 `task_state(report_task)`：
  - `parents` 或 `children` 任一非空 → `wrong_report_edge`（不論對方是 setup、root、未知卡、work、review）；
  - board 讀取錯誤 → `board_read_unknown`；卡不存在 → `report_missing`；皆 fail closed；
  - 另外把每張 work 卡在 board 上的實際 `parents` 與註冊資料交叉核對，不符 → `work_parent_mismatch`（本輪 live 曾正確攔下一個 seeding 錯誤）。
  - 上述檢查在**每次 effect 前**與 recovery 都執行。
- 驗證：`Finding5DetachedReport`（setup edge、unknown child）、`ReportTopologyTests`（board read error、missing report、work parent mismatch）；live 以 `kanban link` 真連結 work→report，`held/wrong_report_edge` 且 0 comment。

### F6 原子性

- v1：verdict 與 item status、acceptance 與 status、observation/receipt/processed 各自 commit；中途失敗留下半套資料，event 還可能被永久標 rejected。
- v2：`_process_row()` 以單一 `BEGIN IMMEDIATE` transaction 包住整個 handler；`crashpoint` 置於 transaction 內。verdict+status、acceptance+status、observation+receipt+processed 要嘛全部 commit、要嘛全部 rollback。retryable 失敗回 `retry`，達 `MAX_EVENT_ATTEMPTS` 才 dead-letter；重啟由 idempotent reconciliation 續跑。
- 驗證：`Finding6Atomicity`（注入 `set_item_status` 例外，verdict 必須不存在）、`AtomicMutationCrashTests`（每個 mutation boundary 以真子程序 kill 後 rollback 並只恢復一次；commit 後 crash 冪等；並行 recovery 只套用一次）。

## 3. Live 證據（真 gateway + synthetic model + 手動 harness 串接）

最終隔離重跑：`./scripts/sandbox.sh live` → **42/42 assertions、rc 0**（`live-lab/evidence/assertions.json`、`result.json`）。

- Sessions（各自 profile store 核對）：default `api-0b36333bf4394fee`、manager `api-9d018bfc598b4849`、reviewer `api-5e66804d3c85a299`；錯 profile key（default key 打 manager route）→ 401。
- 正常路徑：setup `t_a0115d1c` → work `t_dbe3d901`（manager wake 真 tool effect）→ review1 `t_547f6861`（REQUEST_CHANGES, sha `2e2b159a…`）→ rework rev2 `ef65e542…` → review2 `t_83f9b7fd`（fresh PASS）→ acceptance `t_a30198c4` → 單一 final（delivery `dlv-616ebf987241`、marker `[[deptproto-final:616ebf9872419a6c]]`）→ report `t_745499c7` 恰好 1 個 final comment；重複 deliver → `already_delivered`，仍 1 個。
- 負例（live）：缺訂閱 `t_0b12c2fa`、錯 owner `t_a61442fe`、未映射 session、錯誤 report edge `t_775c180f`，全部無 wake／無 final；未映射 owner 無 `board_events` row、無 observation、無 receipt。
- Crash：`live_deliver.py --crash-at after_effect_before_receipt` 以 `os._exit(137)` **真程序死亡**；crash 前 board 已有 1 個 final comment；重啟 `--recover` → `confirmed`，comment 仍 1 個，`receipts=1`、`status=confirmed`（`crash-run.json`、`crash-recovery.json`、`proto-crash/crash-evidence.json`）。

**這不是 autonomous E2E。** `live_stage.py` 是手動 harness：它先用真 gateway 讓 manager/reviewer/default profile 產生合成 comment，再由 harness 自己組固定字面 payload 呼叫 `ingest_board_event()`；report 完成也是 harness 以 CLI 執行。adapter 沒有從真實 comment 解析與驗證 payload，也沒有真 dispatcher review spawn、`review_requested`/`changes_requested` 事件種類、真模型判斷、Desktop/SDK/OAuth。此界線與 v1 審查一致，未因 42/42 而改變。

## 4. 隔離與 fail-before-start

- `unshare --user --map-root-user --mount --net --pid --fork --kill-child --mount-proc`；`/home/ubuntu`、`/run`、`/tmp` 換 tmpfs，只 bind 回 prototype 樹（rw）、installed source（ro）、uv Python runtime（ro）。
- 網路：只有 `lo`、`routes=[]`；本輪 preflight 記錄 user/mount/net/pid namespace inode 與 uid 0（`preflight.json`）。
- `assert_lab_env` 在任何清理**之前**檢查 12 個 HOME/HERMES/XDG/TMP 路徑全部 resolve 在 lab，拒絕 worker pins、proxy/socket 變數與 lazy install；負向輸入（production DB、`/run/user/1000`、`HERMES_KANBAN_TASK`）皆 fail-before-start。
- 清理：兩個程序 reaped（model rc=-15、gateway rc=1，如實保留非 graceful rc）；`ss -lntup` 只剩表頭。
- 本輪 `live_stage.py` 只改 seed 的 `setup_task` 參數（見 §6）；程式與測試邏輯未為 live 放寬。

## 5. 明確不可引伸的結論

1. **受信任單寫入者不是 ACL。** prototype 只約束自己的 DB writer；shared board 上任何 CLI/UI 仍可 create/link/decompose，`create --parent` 繼承仍存在。
2. **Shared board metadata 不保密。** default 在本輪 observation window 沒有 child 文字，但 `kanban_show` worker_context 仍可能帶 child id／摘要。
3. **不宣稱 exactly-once。** 外部 effect 若已落地但 read-back 不可用，v2 永久維持 `unknown` 並告警，等待人工 reconciliation；不會重送、也不會宣稱成功。
4. **併發 reconciler 未裁決。** 「fresh unattempted intent 可送一次」假設單一序列化寫入者；兩個並行 recovery 同時看到 attempts=0 的競態沒有分散式鎖證明。
5. **離線合成的部分**：unknown/conflict effect 用隱藏 read-back 與注入例外；多數 crash 邊界是本地真子程序 kill，不是真 board crash。live 只涵蓋 `after_effect_before_receipt` 一個邊界。
6. **未測**：真模型、Desktop/SDK/OAuth、真 dispatcher review spawn、多 board、production 部署、其他 crash 點的 live board 行為。

## 6. 本輪保留的錯誤與修正

- `evidence/historical-failed-live-run.json`：一次失敗的 live run。原因不是 gate 缺陷，而是 harness `seed_proto()` 把 work 卡的註冊 parent 寫成字面 `t_setup`，與真實 board 上的 setup id 不同；v2 的 `work_parent_mismatch` 正確地 fail closed。修正為傳入真實 `setup_task` 後重跑，42/42 通過。此檔如實保留，未重標為通過。
- `.research/p0-*` 等上游證據不在本卡範圍，未修改。

## 7. Gate 狀態

`t_369a6f8c` 仍 blocked、`t_a1c1b08b` 仍待其 parent；本卡只交付 v2 修復與證據，交由 orchestrator 另排獨立 reviewer（既有 child `t_8a0440e0`）複審 exact v2 manifest。**未有獨立審查 PASS 前，不推進 P0/M1。**
