> **Historical / superseded:** 交付語義已由 [m1-v5-contract.md](m1-v5-contract.md) 取代。Final 現為 plugin-owned immutable report；下文 stock-port-always-held 與 `effect_fencing_unavailable` 僅是歷史。

# Department Hub — M1 對外契約（v4.1，歷史凍結版）

狀態：**v4.1 候選，待 `t_8a9f865b` 輕量複核後凍結**。介面相對 v4 **未變**：7 個工具、
參數、回傳欄位、狀態集與 artifact 語義都相同。v4.1 只做三件事：

1. **文件對齊**：README／operator 流程改寫成 v4 的 producer-only 契約（v4 README 仍寫 v3
   的雙邊 `metadata.artifact_sha256`、且沒有 `department_submit`，照它操作會讓
   `ready_to_deliver` 永遠不可達）。
2. **契約補 P2-5 limitation**（第 8 節）與 **M2 文案限制**（第 7 節）。
3. **P3 更正**：補 `readback_not_found`、移除憑空的 `event_at_*`、澄清 `not_attempted`
   名稱衝突、把 dashboard 健康碼移出 acceptance 分類、補「第二世代不可交付」。
4. **一個小程式修正**：`release` 在 gate held 時也套用與 `_accept` 相同的
   `strongest_hold` 合併（第 5 節）。不改 gate 結構，附回歸測試。

DB 升級為 `CREATE TABLE IF NOT EXISTS`；v3／v4 契約檔案保留為歷史。

## 1. 工具（7）

| 工具 | 權限 | 說明 |
|---|---|---|
| `department_status` | 任一 | 唯讀快照 |
| `department_ingest` | 任一 | 重讀卡片、冪等記錄事件 |
| `department_bind` | manager | 綁定 board 驗證過的 root |
| `department_submit` | manager 或 member | **v4 新增**：artifact producer（identity 唯一來源） |
| `department_accept` | manager | 驗證目前 revision 的 PASS 與 producer 記錄並記錄 acceptance |
| `department_deliver` | manager | 冪等釋出唯一 final handoff |
| `department_escalate` | manager | 每 reason code 一次 escalation |

## 2. Producer 輸入契約（`department_submit`）

輸入：`task_id`、`artifact_path`（**絕對路徑**的本地一般檔案；≤ 64 MiB）。

輸出（成功）：
```json
{"success": true, "action": "recorded"|"already_recorded", "task_id": "...",
 "revision": 1,
 "artifact": {"task_id": "...", "revision": 1, "sha256": "<64 hex>", "algorithm": "sha256",
              "path": "<呼叫端路徑>", "real_path": "<realpath>", "size_bytes": 123,
              "producer_profile": "worker-a", "computed_at": 1793...}}
```

規則：
1. 只有這個路徑會產生 artifact identity。**board 上的 `artifact_sha256`（submission run、
   review run、request payload）永遠不能取代 producer 記錄**；未經 producer 的提交維持
   `artifact_unbound`（fail-closed）。
2. identity 以 `(task_id, revision)` 為鍵；同一 revision 換一個不同 SHA 的檔案會被拒絕
   （`artifact_identity_conflict`），復原方式是新的 revision，不是覆寫。
3. board 上若另有 SHA 宣告且與 producer 不同 → `artifact_declaration_conflict`
   （宣告只能收緊，不能放寬）。
4. revision 只因**非本世代**事件而增加（例如重播的舊事件）時，沿用最近一筆較低 revision 的
   identity；只要該 task 有任何一筆可授權綁定的 rejection，就必須重新 submit。
5. **`department_submit` 必須早於該次 `department_accept`**。accept 會驗證該 revision 的
   producer 記錄，所以**在 review 其餘條件都成立的情況下**，先 accept（或根本沒 submit）只會得到
   `held/artifact_unbound`，且不寫入 acceptance。submit 晚於 review 是允許的（見第 8 節），但
   晚於**已記錄的 acceptance** 不具回溯
   效力——deliver 需要已記錄的 acceptance，因此會維持 `held/acceptance_missing` 直到重新 accept。
6. 拒絕碼：`artifact_path_invalid`（非絕對路徑）、`artifact_missing`、
   `artifact_not_regular`、`artifact_too_large`、`artifact_unreadable`、
   `artifact_changed_while_reading`、`caller_unverified`、`caller_not_permitted`、
   `artifact_identity_conflict`、`inactive_department`、`board_unknown`、`ownership_*`。

## 3. Verdict 權威（v4 語義，v4.1 未變）

| 輸入 | v3 | v4 / v4.1 |
|---|---|---|
| `outcome=completed` + `metadata.verdict="PASS"` | releasable | releasable（不變） |
| 無 structured verdict，summary `PASS` / `REVIEW: PASS`（行首、大小寫不敏感） | releasable | releasable |
| 無 structured verdict，summary 其他正向文字（`looks good`、`All checks passed`、`已通過`） | **releasable** | **held/`review_unproven`** |
| 無 structured verdict，summary 負向（`not approved`、`changes required`、`changes requested`、`REQUEST CHANGES`、`changes_requested`、`未通過`、`不通過`、`駁回`、`退回`、`拒絕`、`需修改`） | **delivered** | **held/`verdict_conflict`** |
| structured `verdict=PASS` + 任何負向字串 | held | held（不變，且中文也納入） |
| `outcome=changes_requested` 且可授權綁定 | held/`review_rejected` | held/`review_rejected`（不變） |
| `changes_requested` 無法綁定（`run_id=NULL`、run 不存在、非 reviewer、無 review claim、早於 request） | **revision+1 後舊 PASS delivered** | **held/`rejection_unbound`** |
| 舊世代（早於目前 request）的 `changes_requested` | 排除 | 排除（不 veto、不解除 artifact 沿用） |

`review_unproven`、`rejection_unbound`、`artifact_declaration_conflict` 是 v4 新增 held code。

## 4. 完整 held / final 狀態集

### 4.1 Acceptance gate held（`evaluate()` 依序回傳）

`inactive_department`、`manager_mismatch`、`review_rejected`、`repair_loop_exhausted`、
`reviewer_not_independent`、`reviewer_not_requested`、`implementer_not_member`、
`rejection_unbound`、`verdict_conflict`、`review_unproven`、`revision_mismatch`、
`no_pass_verdict`、`stale_verdict`、`verdict_not_final`、`artifact_declaration_conflict`、
`artifact_unbound`、`artifact_mismatch`。

### 4.2 Durable-authority held（`_accept` 與 gate reason 合併後可能額外出現）

`review_generation_stale`（board 落後於已觀測世代）、`review_rejected`（durable
`review_rejections` 記錄）、`event_quarantined`（snapshot 衝突或 durable quarantine）、
`acceptance_not_active`（同一個 acceptance_id 已被 superseded／revoked，需要新的 review）。
`department_accept` 在 board 讀取失敗時**拋出工具錯誤**（`{"success": false, "error":
"BoardError: …"}`），不是 held 值。

### 4.3 Delivery（`department_deliver` / `department_escalate`）結果集

`delivered`、`already_delivered`、`held`、`unknown`、`dead_letter`。

- `held`：4.1／4.2 全部 reason，外加 `ownership_changed`、`ownership_unknown`、
  `ownership_unverified`、`board_unknown`、`department_changed`、`effect_transaction_nested`、
  `effect_fencing_unavailable`、`delivery_identity_conflict`、`escalation_identity_conflict`、
  `acceptance_missing`、`acceptance_stale`、`no_delivery`（reconcile 無 final 列）。
- `unknown` 伴隨：`prior_effect_unknown`（先前狀態不明，不重送）、`in_flight_unknown`
  （意圖已被他人持有，或無法證明未送出）、`readback_unavailable`（read-back 本身不可用）、
  **`readback_not_found`（append 後 read-back 沒有 `found`：寫入可能已落地但無法確認，
  留在 `unknown`，不得重送）**。
- `dead_letter`：`retry_budget_exhausted`（`Attempts` 上限）、`effect_raised`（effect port
  拋例外時 delivery row 進入的狀態）。

### 4.4 Producer 拒絕碼（非 held，`success=false`）

見第 2 節第 6 條。

### 4.5 錯誤／例外（非 held）

`escalation_detail_invalid`、`escalation_reason_invalid`（`department_escalate` 參數錯誤）。

### 4.6 不是 M1 reason code 的三個名稱（M2 對齊時不要誤用）

| 名稱 | 實際是什麼 | 契約立場 |
|---|---|---|
| `event_at_*` | 不存在。`event_at` 是 **M2 dashboard payload 的欄位**（最後一個 durable event 的 `created_at` 整數），不是 reason code | v4 契約誤列為 acceptance held，v4.1 **移除** |
| `not_attempted` | M1 沒有任何 `not_attempted` 交付／acceptance 狀態。M2 dashboard 的 409 mutation refusal payload 有一個同名欄位 `not_attempted: ["config write", …]`，意思是「拒絕請求時刻意沒有執行的副作用清單」 | 名稱相同、語義不同；M2 不得把它映射成 release 狀態 |
| `board_unavailable`、`department_unreadable`、`department_missing` | **M2 dashboard 健康碼**（`/departments` health／HTTP 503），不是 acceptance held | v4 契約誤分類，v4.1 移出第 4 節 |

### 4.7 單一 final handoff：第二世代不可交付

一個 (department, task) 只有一筆 final delivery（`final_key`）。**一旦 delivered，後續新世代
即使 `department_accept` 回 `releasable`（`ready_to_deliver=true`），`department_deliver` 也只會
`held/delivery_identity_conflict`**（body 的 revision／verdict 與已確認的 delivery 不同）。這是
v1「唯一 final」設計，不是缺陷；M2 顯示時必須知道 accepted-readiness 與交付是兩件事，第二次
世代沒有第二個 handoff。

## 5. accept / deliver 同一狀態同一 reason

`_accept` 以 `strongest_hold(gate.reason, authority_hold)` 合併：
`repair_loop_exhausted`(60) > `review_rejected`/`review_generation_stale`(55) >
`verdict_conflict`(50) > 其他(0)。

v4.1 起，`release` 對**同一份 board snapshot** 的 held 決定也套用同一個合併（v4 只在 gate
held 時回 `gate.reason`，是 latent 分歧：弱 gate hold + 較強 durable hold 時 accept 回較強
reason、deliver 回較弱 reason）。因此 3 輪 rejection、`max_repair_rounds=2` 時，accept 與
deliver 都回 `repair_loop_exhausted`。回歸測試
`tests/test_m1_v4_regressions.py::test_accept_and_release_report_the_same_strongest_hold`
以人工注入的 durable hold 鎖住兩者一致。

`release` 的 `authorize()` 會在 effect 交易內**重讀 board**；那是另一個 snapshot（TOCTOU
窗口），其 reason 只在該窗口內有效，不屬於「同一狀態」的比較範圍。

## 6. P2-2 / P2-3 決定

- **P2-2（修好）**：`orchestrator.escalate` 先檢 `dept.active`，並在 delivery 交易的
  兩個階段各呼叫一次 `authorize()`；phase-2 失敗時放棄（刪除）尚未送出的 pending
  intent（`abandon_delivery`），不 dead-letter、不 append。inactive department 的
  escalation 一律 `held/inactive_department`、0 effect。
- **P2-3（修好）**：`normalize_source_id` 將 JSON scalar 正規化為文字（`9` 與 `"9"` 同
  identity；bool 保持可區分）。同 identity 同 payload 為 duplicate、不同 payload 為
  durable conflict（`event_quarantined`），不會重複計算 repair round。

## 7. M2 需要顯示的狀態集合（含文案限制）

- readiness：`accept.status ∈ {releasable, held}` 與 `ready_to_deliver`（= releasable）。
- held code：4.1＋4.2 全部（M2 不得自創或省略）。
- delivery：`delivered / already_delivered / held / unknown / dead_letter` 與
  `prior_effect_unknown / in_flight_unknown / readback_unavailable / readback_not_found /
  no_delivery`。
- artifact：`artifact.sha256 / algorithm / path / real_path / size_bytes /
  producer_profile / computed_at`；缺 producer 記錄即 `artifact_unbound`，M2 不可顯示為 ready。
- **文案限制（P2-5）**：`ready_to_deliver` 只表示「manager 已接受這個 revision／run／SHA 的
  exact evidence，`department_deliver` 會被授權」。**不得**寫成「reviewer 已驗證 artifact
  內容」、「bytes 已比對」、「reviewer 蓋章」或任何暗示 reviewer 看過／重算過檔案的文案。
  正確說法示例：`Accepted: reviewer PASS (run 123) + worker-declared artifact SHA-256 abc…`、
  `Ready to deliver (manager-accepted evidence; artifact hash supplied by the worker)`。
- 第二世代：已 delivered 的 task 再 accept 成 releasable 時，M2 必須顯示
  `delivery_identity_conflict`（不可交付），不得顯示成「可再次交付」。

## 8. 已知限制

- **artifact identity 是 worker 宣告的雜湊，不是 reviewer 對 bytes 的 attestation。**
  `department_submit` 由 manager 或 member 呼叫，`submit` **允許晚於 review**（真實 reviewer
  不會呼叫 plugin 工具），只要求早於該次 `department_accept`。因此 review 之後才 submit 的
  檔案會成為交付 body 裡的 `artifact_sha256`，而 reviewer 可能從未看過那些 bytes。這是
  **明列的 limitation，不是安全保證**；M2 文案限制見第 7 節。
- artifact 只在 submit 時計算／記錄；`department_deliver` 不重新讀取 bytes，因此
  「交付後的檔案內容可變」不在 M1 防護範圍（identity 描述的是送審當下的 bytes）。
- board run 的 `created_at` 只用於世代排序，不作時鐘 fences；跨時鐘比較不宣稱正確。
- 仍無 upstream 條件式 comment write：stock `CommentEffectPort` 下 final 一律
  `held/effect_fencing_unavailable`，人工交付需 fake/local port 或上游 fence（v1 範圍決策）。
- 無真實 Desktop rendering、無正式憑證／網路驗證；全部證據來自 synthetic board + 隔離 SQLite。

契約版本：`department-hub/m1-contract/v4.1`（v3／v4 檔案保留為歷史）。
