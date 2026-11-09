# Department Hub｜隔離研究原型 v1 獨立審查

- 審查 `t_e83d632f` / run 385；受測 `t_baf15a68` / run 383，**REVIEW: REQUEST_CHANGES**。
- 此判定只針對 research prototype v1，**不是 P0 最終驗收、不是 M1 授權**；不得解除 `t_369a6f8c`，不得放行 `t_a1c1b08b`。
- 已先讀原 `p0-report.md`、`p0-review.md`、`p0-route-followup.md`、`p0-route-independent-review.md`、本 prototype 的 README、兩份報告、上游 task 全部 handoff、程式與測試。保留原報告的限制，沒有把上游宣稱自行升格。

## 阻擋 findings（各項均在受信任單寫入者、合成資料的 prototype 範圍；不是要求修改 Hermes core）

### 1. [P1] recovery 繞過 acceptance / 新 artifact / topology gate

- **反例**：先完成 PASS + acceptance；在 `after_intent` 用例外中斷（只模擬該中斷點，非真 kill）；接著發佈新、尚未審查的 artifact。`deliver_final` 正確回 `held/stale_pass`，但 `recover` 隨即回 `confirmed` 並新增 1 個舊 final，item 仍是 `rework_ready`。見獨立 `unit-review.log` 的 `CASE stale_recover`。
- **原因／影響**：`deptproto/orchestrator.py:345–362` 正常 delivery 會 `_gate`，但 `:418–473` recovery 直接根據舊 outbox 驅動 effect，未重新核對 current rev、acceptance、report 邊及原始 payload。重啟不應使陳舊／未接受內容重新可送；真 crash 也能到同一 durable intent 狀態，但此**特定反例只做例外注入**。
- **修法**：reconcile 分離「已存在的舊 effect 查明」與「准許產生新 effect」：所有新 effect 前，在同一序列化單寫入者保護下重新驗 current gate、精確綁定 intent 的全部 work/artifact/acceptance/topology；失效則標示 held/superseded，不能發送或稱目前已接受。已發出但後來失效的 effect 須明確標示歷史事實及撤回／升級狀態，不假稱沒有發生。
- **複驗**：真 subprocess 在 intent 前後各 crash，重啟前引入新 artifact、撤銷／變更 acceptance、錯 report edge、額外未接受 work；零新 final、具體可觀察狀態；再測既有 effect 後 crash 可以 read-back 而不重送。

### 2. [P1] effect unknown 被重送，造成重複外部效果

- **反例**：`FileJournalPort(hide_readback=True)`，一次 delivery + 兩次 recovery：回 `unknown → unknown → dead_letter`，但 journal 已有 **3 筆**同 marker effect；不是只一筆。`CASE unknown_effect_retries`。這是離線合成 effect，不是宣稱已在真 board 重現三次。
- **原因／影響**：`effects.py:45–55,60–62,79–96` 的 apply 依自己的 find 判斷是否重複；當 read-back 不可用，find 返回 `None` 與「確定不存在」不可區別。`orchestrator.py:387–404,426–455` 因而再次 apply。BoardEffectPort 同樣在 `find(None)` 後 post comment；其 check-then-post 不具原子 idempotency。dead letter 不代表未發送；真 board 可能已有多個外部 final。
- **修法**：read 結果明確區分 FOUND / ABSENT-authoritatively / UNKNOWN；一旦 apply 結果未知，不可再次發送，除非外部公開 API 提供經證實的原子 idempotency 或權威 reconciliation 確認不存在；無法確認就維持 unknown / 人工處置。read-back 同時核驗 exact task、完整 body/identity，而非僅 marker 子字串。
- **複驗**：effect 真已落地但讀取暫時／永久不可用、CLI timeout 後實際成功、read-back 恢復；assert 發送次數不增加、receipt 與已知效果相符，死信不掩蓋不確定性；在隔離真 board 執行可行的故障注入。

### 3. [P1] acceptance 忽略申報 artifact；檔案內容事後變更亦照送

- **反例 A**：對 artifact A 取得 PASS，manager acceptance payload 指向不同 artifact B，`accept` 回 `applied`，接著 final `confirmed` 且 effect=1。`CASE wrong_acceptance_artifact`。
- **反例 B**：A 已 PASS/accepted 後，直接改寫同一路徑的檔案為不同 bytes（未發佈新 revision），delivery 仍 `confirmed`、effect=1；實際 SHA `c1f225e9238a2031d06ffeb38f51fe89bf3d82b7625b51621f9c1bdec80d5c96`，原核可 SHA `af72250d489ae165bf74a26d181af5117879339ffbb1725e2fb09c82c5cf3441`。`CASE mutable_artifact`。
- **原因／影響**：`orchestrator.py:234–274` 不讀 `p['artifact_sha']`，還允許省略 verdict ID 後自動選最近 PASS；`:300–327` 只比較資料庫紀錄而不驗 artifact bytes。`:217` 也允許 verdict 缺少 SHA。若檔案路徑不保證不可變，不能稱實際交付 artifact 與審查精確綁定。不能僅憑呼叫者聲明 manager 或 verdict 屬於同一 item；應核實整條權威關聯。
- **修法**：強制 acceptance 明確提供 verdict/run、item、revision 與 SHA，全部相等且 verdict 屬當前 item 和獨立 reviewer；強制 verdict 明確提供 exact SHA。用不可變 content-addressed snapshot，或 delivery 前安全重驗 actual bytes 並處理 TOCTOU，任一缺失／不一致 fail closed。
- **複驗**：錯誤與省略的 SHA/verdict、跨 item verdict（含內容 SHA 相同）、覆寫／刪除 artifact、hash 和讀取錯誤，均不得送；對同一 immutable bytes 的 fresh PASS + manager acceptance 才能送。

### 4. [P1] 未知 session owner 被當有效並完成 receipt

- **反例**：只提供 `notifier_profile=manager`，`chat_id=unmapped-session`，不在 `session_profiles`；`ingest_board_event` 返回 `admitted`，`process_pending` 後 `processed=true`。`CASE unmapped_owner`。
- **原因／影響**：`orchestrator.py:507–516` 只有已知 mapping 且不一致才拒絕；unknown 卻通過。`:523–530` 不做業務處理即寫 receipt/processed。不能把「未能驗證 owner」當成功，也不能拿 receipt 當應用程式已完成處理。
- **修法**：未映射／缺失 session、profile/scope 不一致一律可觀察地 fail closed；只在經驗證 `(board, profile, session, task, event)` 與實際對應業務處理持久完成後記 completion receipt；如果尚不支援 mapping，標為未處理，別冒充 ACK。
- **複驗**：已知錯 owner、未知／空 session、跨 profile 同 raw ID、missing subscription、重送與 recovery 均無不正確 receipt／final；正常 owner 要看到真實處理後 receipt，而非只測 admission。

### 5. [P1] 未完全 detached 的 report 仍可發 final

- **反例**：合成板把 setup 依賴連至 report (`t_setup → t_report`)，report 的 `parents=['t_setup']`；仍 `confirmed` 並有 1 effect。`CASE non_detached_setup_edge`。
- **原因／影響**：`orchestrator.py:276–290` 只拒絕 report 邊與已註冊非 report work IDs 的交集；setup 未註冊時、其他中介點或未知邊並未被拒。此違反本 prototype 明確的「完全 detached、沒有任何 parent/child」前提，可能令訂閱繼承穿透；這不是聲稱能強制限制任意外部 writer。
- **修法**：對權威 board read-back 驗證 report 的 parent 與 child 均為空，讀取錯誤／未知結構 fail closed；在效應前和 recovery 檢查。將 work 的真實 parent 與註冊資料交叉核對，不以 caller 提供的 parent list 單獨代替權威 board。
- **複驗**：report 連到 setup、root、未知卡、間接中介、work、review、未註冊子卡及讀取失敗；全部不送。沒有任何邊的 report 正向通過；另列出無法原子防止外部任意 link 的限制。

### 6. [P1] domain transition、receipt 與事件狀態非原子；失敗可留下半套資料且無 pending

- **反例**：PASS verdict 寫入後、變更 item status 前，注入 `set_item_status` 例外；結果事件 `rejected/apply_error`、`pending=0`，但 PASS verdict 已持久存在，item 還在 `in_review`。見 `CASE partial_verdict_failure`；此反例為同步錯誤注入，非該位置的真 process death。
- **原因／影響**：`orchestrator.py:102–123,224–231` 與 `store.py:269–278,378–390,495–499` 分多個各自 commit 的交易；同樣 request/run、artifact/status、acceptance/status、event receipt/processed 也有多個 commit。單寫入者不等於跨多次 transaction 原子化；重啟可留下不一致並卡住，錯誤 catch 還把 event 永久標 rejected。
- **修法**：單一 DB transaction 中完成經驗證的 domain mutation、狀態轉換、receipt 與 event processed（並對唯一性、順序和 transition 做真正的 transaction 內檢查）；可恢復的部分結果由 idempotent reconciliation 處理，不得把未知部分直接標成功或丟失。
- **複驗**：在每次持久 mutation 及 receipt 前後注入真 subprocess kill / SQLite 失敗，重啟要求要嘛全 rollback 要嘛完整恢復；重播相同與衝突 payload、不同行為的亂序及並行 writer 測可觀察結果與零錯誤 final。現有 `after_admit_before_process` 只覆蓋 handler 進入前，不涵蓋這些中間提交。

## 審查邊界及其他證據缺口

- `scripts/live_stage.py:441–485` 先觀察 reviewer/manager 合成 comment，但接著是 harness 自己組裝固定字面 payload 呼叫 `ingest`，不是 adapter 從實際 comment 解析與驗證 payload 後驅動；完成 report 也是測試器在 `:500–502` 手動 CLI complete。實測支持「真 gateway + synthetic model 的有限 wake/tool 與手動串接的 prototype」，不支持 autonomous event-to-acceptance end-to-end 或真模型判斷。`review_requested` / `changes_requested` 實際事件種類、真 dispatcher review spawn、Desktop/SDK/OAuth、真模型、外部原子 ACK/replay、其他 crash 點的真 board 行為均未驗證。
- 使用者要求 fail-before-start；重跑使用上游 OS namespace launcher 的**scratch 副本**，只變更 `PROTO` 為副本路徑，讓作者 lab/evidence 不被 reset。原腳本 `scripts/sandbox.sh:13` 硬編碼原路徑；不能當真正路徑可攜 harness。`live_stage.py:247–263` 亦先 reset 後 preflight；建議將前置檢查移至任何清理前，並拒絕缺少可信 namespace 證明的內部入口。此處是 harness 硬化／重跑品質，不以本次成功隔離推導對所有呼叫安全。
- 先前 fail-first `evidence/unit-tests-red.log` 為 `ModuleNotFoundError` 造成的 3 個 collection error，並非執行到產品行為 assertion 的有效 RED；需對上述反例先有能跑到 assertion 的 failing test，再修復、focused/full rerun。作者 live staging `error-stage-2.json` 的 reviewer exact-SHA assertion 曾失敗（保留的合成 SHA 與期待不同），不能重標為通過；後續成功為另一個 run。
- `process_pending` 所測 board event receipt 的實作僅記錄 receipt，無實際 verdict/acceptance 處理；記錄的 `dead_letter` 多為 alert/返回值，不應宣稱有上游 durable ACK 或完整可靠事件消費器。
- 受信任單一寫入者是操作假設，不是不可繞過 ACL：原 Hermes create/link 繼承通知可穿透。shared board `kanban_show` worker_context 可暴露 child metadata（run 377 已證）；本次 default child wake 陰性不是資料保密。對任意寫入者／工具的隔離、exactly-once 與跨 profile 保密均未證。

## 固定產物、測試與環境

- 原版 `MANIFEST.sha256` 的 SHA-256：`26f6046b714f0289b9aafa6e9c6df360b27d30180629f7127899943d51645b97`；`sha256sum -c` exit 0，精確 193 行、193 不重複檔，與實際非 cache/manifest 檔集合一致，無 missing / unlisted。上游兩份報告 hash 分別 `fd5c70f6c32940a4ca1a387270f29d5a7a6ecadc4aafeff2cdbede25946f6237`、`81fe8e0dbeb7d59ac54e06f27cf9c2c3a5c082bd16743c4ed2e7588fa1de28d2`，均吻合。兩個 scratch 副本在修改 launcher 前也各自整份 manifest 全過；僅 reviewer 自製 launcher 路徑／測試 driver 有變更，受測程式與測試 bytes 未改。
- `./unit-copy/scripts/sandbox.sh`：預檢 lab 路徑、禁止 pins/lazy install、網路只有 lo 且 IPv4 routes `[]`、正式 board 被遮蔽後，以 exact 複製測試執行 pytest：**34 passed / rc 0**；再做上述 7 個獨立合成反例。`unit-review.log` SHA-256 `812fb511f42e0612a2d3aa8f0fa2cdea771544cba38c563635fd511bf7a21386`；driver `eafd49825d76df95b862df18c49ff62977caded28741a48db4e17971392a85df`。綠測只代表既有測試，未覆蓋上述反例。
- `./review-copy/scripts/sandbox.sh --probe` → `unshare-ok`；完整 `./review-copy/scripts/sandbox.sh` → **rc 0、40/40**，受測 Hermes source HEAD `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`；合成三 profile + 真 gateway/Kanban tools。新 synthetic cards：work `t_8fbdcc35`、review `t_3e0867aa`/`t_6e0dfc03`、report `t_a8702c92`、crash report `t_d612633f`；一次 live post-effect/pre-receipt crash rc 137，recover 後有一 comment/receipt。`live-review.log` SHA-256 `3ddc912e783af28676879b57d3bf193c593df7da26fe934237edfdf66f7e37a9`。這不消除上列反例。
- `preflight.json` 記錄 user/mount/net/pid namespace、僅 lo、routes `[]`、所有 12 個 HOME/board/XDG/temp resolved 到副本 lab；覆蓋 `$HOME`、`/run`、`/tmp`，installed source 與 Python runtime 唯讀；三個污染輸入被拒。無外部網路 route；測試 loopback 僅 sandbox 內合成服務。cleanup 記錄兩程序均 reaped、namespace 內無 listener；gateway SIGTERM rc 1、model -15，不是 graceful rc 0。未以讀取正式 board/profile/credentials 做 hash comparison；隔離證據不等於外部全部狀態已獨立核對。任務必要的看板 lifecycle 與唯讀 gate 查詢不等於把正式 board 當測試板。未啟動 FreshTape、未改正式 source/profile/gateway/board、未安裝、未推送或外發訊息。
- 本次審查只寫本報告與 scratch 的可丟棄測試／日誌；附帶的 reviewer driver/log 供查核，應依同一嚴格隔離條件執行，不可在正式環境直接執行原 unit/live 腳本。

## Gate

審查前唯讀查證 `t_369a6f8c=blocked`，`t_a1c1b08b=todo` 且其 parent 未滿足。此卡完成只交付 REQUEST_CHANGES，不構成解除原 gate 的原因或授權。修復後必須重新獨立審查 exact 新 manifest，既有 34/34、40/40 不能替代反例回歸；P0 與 M1 維持原有獨立 gate。
