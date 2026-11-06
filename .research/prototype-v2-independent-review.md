# Department Hub｜prototype v2 六項 P1 獨立複審

**REVIEW: REQUEST_CHANGES** — `t_8a0440e0` / run 388；僅針對 research prototype v2，不是 P0 PASS，也不是 M1 授權。`t_369a6f8c` 經唯讀查詢仍 blocked，`t_a1c1b08b` 為 todo，parent 未滿足；本審查不得解除或繞過它們。

已先讀 v1 獨立報告、原 reviewer driver 及 v2 兩份 handoff 報告，凍結原樹 manifest；在 reviewer-owned 副本的獨立 OS sandbox 中執行。既有 focused/full/live 綠燈可重現，但不能涵蓋下列獨立反例。各反例均使用合成資料及可觀察真實 prototype 邏輯；不聲稱已在真 gateway 造成同樣故障。

## Blocking findings

### 1. [P1] 權威讀取缺欄被轉成空 topology，未 detached 仍可 confirmed

- **來源** `deptproto/board.py:122–148` 對成功 JSON 的 `parents`、`children` 均用 `data.get(...) or []`，未驗證欄位存在、型別、task identity 與完整性；`orchestrator.py:444–458` 隨後將其視作權威的空邊。
- **獨立反例** `test_incomplete_authoritative_topology_must_be_unknown_not_detached`：底層合成 board 的 report 明確有 parent `t_setup`；以合成 CLI stdout 模擬 rc=0、有效 JSON 但省略 report parents/children；透過**真正的 CliBoard 解析及 gate**，結果 `confirmed`、journal effect 1。期望 unknown/held、effect 0。這不是對真 gateway 回傳缺欄的實測，而是測其回應不完整時 fail-closed contract。
- **修復／複驗**：嚴格驗證回應結構、task id、完整且類型正確的 parents/children；缺、null、不符、截斷或不可信均 UNKNOWN，不可預設 detached。以真 sandbox CLI 正向回應及各種合成缺欄/錯型/timeout、未知 setup/root/child 邊、recovery 前改邊重測；保持未受信任外部 writer 仍可競態連邊的限制。

### 2. [P1] 缺少 comments 的讀取被當作「權威不存在」，已送 effect 可重送

- **來源** `board.py:141–144` 缺 comments 預設空 list；`effects.py:133–144` 無 marker 即 ABSENT；`orchestrator.py:617–645,648–688` 將其當權威不存在而重新 apply。這直接破壞未知結果不可盲重送的宣告。
- **獨立反例** `test_incomplete_authoritative_comments_must_not_resend_after_timeout`：合成 CLI adapter + 真 `BoardEffectPort`，第一個 effect 已進合成 board、在 receipt 前以例外中斷；接著 CLI 成功 JSON 省略 comments，recovery 仍追加一次，底層實際 **2** 個相同 effect；返回 `unknown/effect_unknown` 並不能消除第二次寫入。此反例為合成 CLI／同步中斷，非真 board timeout。當完整讀回明確 FOUND、或明確 UNKNOWN 時的正常行為另見複驗。
- **修復／複驗**：只對經驗證的完整、權威、無截斷 comments 取得 ABSENT；缺欄/非 list/不完整/pagination 未確認/timeout 均 UNKNOWN；先保留已送未知而不是重送。以真隔離 board 做可行 fault injection，加測寫成功但 CLI timeout、讀取無法取得及恢復，核對實際 comment count 與 receipt。

### 3. [P1] 矛盾的新 verdict 被 ACK 為舊 PASS，仍可 acceptance + final

- **來源** `orchestrator.py:309–315` 在驗證新事件 verdict 與 SHA 前，只要 run 已有 verdict 就回 `applied/already=True`；`:181–186` 接著為新 event 寫 receipt 並 processed。
- **獨立反例** `test_conflicting_verdict_replay_must_not_ack_as_existing_pass`：先有 PASS(v)，再送**不同 event ID、新 seq** 的 REQUEST_CHANGES 與不同 SHA；結果新事件 `applied`、receipt=true，manager acceptance `applied`、final `confirmed`、effect 1。這不是相同 event ID 的重放，而是衝突的新事件被錯誤去重。這違反顯式驗證 exact verdict/bytes 及衝突 payload 不得冒充成功的 contract。
- **修復／複驗**：只對完整身份與 payload 完全一致的重放作冪等成功；衝突 verdict/SHA/task/reviewer 均可觀察地拒絕或隔離，不能寫成功 receipt，並明定後續 acceptance 失效／升級策略。測相同 ID 不同 payload、不同 ID 相同／衝突 payload、亂序、crash/retry 與 zero final。

### 4. [P1] 內容定址 blob 仍可變：驗證與 effect 間變更仍已對外發 final

- **來源** `orchestrator.py:99–112` 以普通可寫檔案及 `os.replace` 建 blob；`:648–688` gate 驗 hash 後才呼叫外部 effect。再次驗證只發生 effect 之後 `:717–749`。內容定址命名與 fsync 本身不代表 immutable，也不能關閉 TOCTOU。final body 傳的是 SHA/metadata，**沒有直接傳 artifact bytes**。
- **獨立反例** `test_blob_change_at_effect_boundary_is_not_immutable`：通過 PASS/acceptance 後，合成 effect port 在真正 append final 前改寫 blob（模擬另一個有寫入權限的程序在 check/use 窗口動它）；結果回 `held/stale_after_effect/artifact_corrupt`，但 journal **已經有 1 個 final**，blob 實際為不同 bytes。這不是證明任意遠端攻擊者可寫 blob；受信任單一 DB writer 也不自動保護普通檔案免於其他本機寫入。事後標歷史是有價值的補救，但不等於出效前的不變性。
- **修復／複驗**：明確定義並實作可驗證的 immutable artifact store／權限與信任邊界，或讓 effect 以經驗證且不可被替換的同一 bytes/handle 發送，處理整個 check-use 區間；不可只把普通可寫檔命名為 hash。測別的 process 在 gate 後、effect 前與 effect 中覆寫、刪除、symlink/replace，以及最終取得的實際 bytes 是否與審查、接收完全相同。若只交付摘要而不傳 bytes，請縮窄 contract 並明確說明可取得內容及不變性機制；本卡不能自行降低已要求的 contract。

## 六項原 P1 的複驗矩陣

| 原項 | 本輪證據與判定 |
|---|---|
| F1 新 effect 前重新 gate | 真子程序 `os._exit(137)` 在 `after_intent` 後 publish 新未審 artifact，recovery `held/no_acceptance`、0 effect；`before_effect` 真子程序退出後新增未知 report child，recovery `held/wrong_report_edge`、0 effect。全套既有測試覆蓋已發 effect 後 stale 歷史確認。以上不等於原 §7 全排列（acceptance 撤銷、新增未接受 work 等）均已獨立實測；此外 finding 1 表明不完整權威資料仍可繞 gate。 |
| F2 unknown effect | 合成 effect 寫成功再 timeout + 讀取 UNKNOWN，重試維持 1 筆；恢復讀取後 confirmed、仍 1 筆。finding 2 顯示缺 comments 被誤當 ABSENT 可寫出第 2 筆；未在真 gateway 注入 CLI timeout。 |
| F3 artifact／verdict／acceptance | 相同 bytes 跨 item verdict 被拒，刪除 blob 後 held、0 effect；原 focused tests 涵蓋不符／缺 SHA、verdict、revision 和靜態 blob 覆寫。finding 3、4 使此項仍不通過；真正在 effect 窗口改 blob 為合成 port 注入。 |
| F4 owner | 未映射、空 session、profile 不符、缺 subscription 各重送兩次均 dead-letter、無 board event row/receipt；既有測試涵蓋處理階段 mapping 撤銷。隔離 live 有真 gateway + synthetic model 的錯 owner/未映射負例。未證跨 board 同 raw ID 的完整實際隔離或真上游 ACK semantics。 |
| F5 detached report | 未知 child 明確連邊 held、0 effect；live 真 board work→report held、0 comment；既有 tests 含 read error、missing 和 work-parent mismatch。finding 1 表明缺欄不 fail closed；未對 setup/root/全部中介拓撲逐一真 board 實測。 |
| F6 domain/receipt/event | SQLite `BEFORE INSERT ON receipts ... RAISE(ABORT)` 真 DB 故障令 verdict rollback、event pending；撤 trigger 後恢復一次。另在 `after_verdict_insert` 以測試 hook 停住**真子程序**，父程序送 SIGKILL，returncode **-9**；重開 DB `verdicts=0,status=in_review,receipts=2,pending=1`；恢復後 verdict=1、approved、receipts=3、pending=0。既有 suite 另驗多個 os._exit(137) 邊界，不等同每個 persistence 邊界都做 SIGKILL／SQLite failure。finding 3 的新衝突事件被錯誤 ACK 亦需修復；並行 writer 與全部亂序情況未裁決。 |

## 精確產物、命令與重跑結果

- 原 v1 manifest SHA-256 `26f6046b714f0289b9aafa6e9c6df360b27d30180629f7127899943d51645b97`，193/193 唯一項、0 missing/unlisted/mismatch；原 v2 manifest SHA-256 `3c915b9cb97aa12ce345ea2b98cf113ee4de6dd302769988a6e426d9a58e18e3`，213/213 唯一項、0 missing/unlisted/mismatch。審查後原兩樹再雜湊依然全部吻合；v1 review report SHA-256 `c3e76fa703675277691f95d9e6ce89c8b920868b80fb8b0d33359b642f63fce0`。執行的 reviewer copy 中 `deptproto/`、`tests/`、`scripts/` 共 21 項與 v2 manifest 全相符；live lab 只在副本重置，副本整份原 manifest 之後自然不再是原 lab 證據。原樹未執行、未清理。
- 預檢 `prototype-v2/scripts/sandbox.sh --probe` → `unshare-ok`、rc 0；reviewer 的 `sandbox-review.sh` 僅修改副本 launcher：在 unit/review/live 每次先做獨立 isolation check 才執行模式。驗證 user/mount/net/pid namespace 與 host 不同、production board/profile/socket/vault 路徑遮蔽、12 個 HOME/board/XDG/tmp 在副本 lab、介面只有 `lo`、IPv4 routes `[]`。使用 user/mount/net/pid namespace，source 與 Python runtime 唯讀、只允許 sandbox loopback；若前置檢查失敗便停止，不能直接在 host 執行測試或原 live launcher。
- 執行位置 `$HERMES_KANBAN_WORKSPACE=/home/ubuntu/.hermes/kanban/workspaces/t_8a0440e0`：
  1. `./v2-copy/reviewer/sandbox-review.sh unit` → **57 passed、3 subtests、rc 0**，`unit-review-v2.log`。
  2. `./v2-copy/reviewer/sandbox-review.sh live` → **42/42、rc 0**，真隔離 Hermes gateway + deterministic synthetic model；report 的流程由人寫的 harness 拼接事件，**不是 autonomous E2E**；真模型未測。live 原生 crash 使用 `os._exit(137)` 不是 SIGKILL，effect 後重啟仍恰 1 comment/receipt；cleanup gateway rc 1、model -15，非 graceful rc 0。獨立 live 的 `assertions.json` SHA-256 `903441b22c8b47557696a1f29d9bbfbb1909e25fb1148a8233f73347151e4c76`。
  3. `./v2-copy/reviewer/sandbox-review.sh review` → runner rc 0 **僅代表收集完成，不代表測試通過**。內部 exact commands：在 `reviewer/red` 以凍結 v1 copy 執行 `python -m pytest tests/test_p1_regressions.py -q -p no:cacheprovider` → rc 1、**12 failed，逐項為行為 AssertionError，無 import/collection error**；在 v2 copy 執行 `python -m pytest tests/test_p1_regressions.py tests/test_v2_verification.py -q -p no:cacheprovider` → rc 0、**23 passed、3 subtests**；執行 `python -m pytest reviewer/test_independent.py -q -s -p no:cacheprovider` → rc 1、**4 failed、8 passed**，四個 failure 分別對應上述四項 P1。
- 可下載的 `prototype-v2-review-evidence.tar.gz` 包含 copy 的程式／測試／reviewer launcher、v1 RED copy、上述 logs、live assertions/preflight/cleanup；SHA-256 `df29bd6237955408b29ec24c4472616909916cfafb276dec66737fd4af85f4e4`。壓縮包故意不含原完整 live lab，不能用內含 manifest 對整包執行全樹驗證；原始 manifest 須在原 v2 樹驗。重跑只能解壓至專屬 `/home/ubuntu/.hermes/kanban/workspaces/t_8a0440e0`，先檢查該路徑沒有其他人的檔案；重新產生 `v2-copy/reviewer/host-namespaces.json`（各 namespace 的 host `/proc/self/ns/{user,mnt,net,pid}` readlink JSON），再以 reviewer launcher 啟動；launcher 固定依賴本機 `/home/ubuntu/.hermes/hermes-agent` 與其 venv。嚴禁在正式環境直接跑測試或清理 live lab。
- 主要 SHA-256：`test_independent.py` `50586933fb99d7482285ab79b5ca342142427d54949aa831089301b5e8277871`；`independent-v2.log` `ef6ccf5fa2247a900c99fece3c77ff6fe0b6bd7bfbab1c549139ba001c6b824a`；`red-v1.log` `7346999cfd10ea1acc46909e4ffb7fdb86a5041a4cb085d7e1fbb1949ef4e648`；`focused-v2.log` `010303832ab95b85ab2cd048cb1100ecd85da2a6549b5b46267bf6e67efb77d4`；`independent-run-final.log` `d2abcfe07d9779f47b619418724750ed2dadea9fc02ba5541c1f1f7c88ae4522`；`unit-review-v2.log` `dec5aefdeed9717d07203409e42f9cbbabb3d141d1be428611aa322c5e7dd1b3`；`live-review-v2.log` `a8ed52e349dba48324c7ef4416d70b6cbd3fe8864c96a769915c686962cce18a`；`sandbox-review.sh` `61d1d5ebe9ff9347b60f433c57f542c8d479275a69226973c120e3dc4eb9a1c2`。

## 範圍與剩餘限制

本次無真模型推理、Desktop/SDK/OAuth、真 dispatcher review spawn、真上游 event ACK、live 讀取 timeout 注入、全面 SIGKILL/SQLite fault 矩陣或多 reconciler 證據。受信任單寫入者不是不可繞過 ACL；shared board metadata 不保密；不宣稱 exactly-once 或任意外部 link 的原子防護。原 P0 gate 與 M1 的證據要求獨立存在。本次沒有使用正式看板作測試板、修改正式 profile/credentials/gateway/Hermes core/FreshTape，沒有公開 push 或部署；必要 Kanban lifecycle 查詢／操作除外。測試皆在副本 lab，未聲稱对所有正式狀態做 hash 對照。
