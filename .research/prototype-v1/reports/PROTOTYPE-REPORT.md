# Department Hub｜隔離研究原型 v1 報告（供獨立審查）

- 卡片：`t_baf15a68`；狀態：**prototype complete，僅供獨立審查**。
- **這不是 P0 PASS、不是 M1 授權、不解除 `t_369a6f8c`、不放行 `t_a1c1b08b`。**
- 未修改任何既有研究／封存、正式 profile、正式 board、gateway、Hermes core、FreshTape；未安裝、未 push、未付費、未對外訊息。
- 受測 runtime：`/home/ubuntu/.hermes/hermes-agent`，`git rev-parse HEAD = f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`，venv Python 3.11.15。
- 模型：**deterministic synthetic model（無推理、無 provider、無外網）**；真 gateway／真 profile／真 Kanban 工具路徑。所有 live 證據明確標示 synthetic。

## 1. 目標與實際做到什麼

在全新 sandbox（user/mount/net/pid namespace）中建置並跑通一個可重跑的試驗程式 `deptproto`：

1. 受信任單一寫入者下的 **manager-only work DAG**；
2. **完全 detached 的 default report 卡**，部門／報告關聯存在 prototype 自己的 store（不是 board 依賴邊）；
3. task/event ID、精確 artifact（sha256）、獨立 review run/verdict、受控狀態機、receipt/outbox、idempotency、pending/failure/dead-letter 可視狀態、reconciliation；
4. 正常路徑：合成 review `REQUEST_CHANGES` → rework → 新 artifact → 最新獨立 `PASS` → manager acceptance → **單一有效 final**；
5. 負例全部 fail closed、不送 final；
6. effect/receipt 邊界的 crash 前後與重啟恢復（離線每個邊界 + live 真 board 一個邊界）；
7. fail-before-start harness：路徑未全部 resolved 在 lab 即拒絕啟動。

## 2. 關鍵設計

- **Store**：prototype 自有 SQLite（WAL、`BEGIN IMMEDIATE`、busy_timeout），表格含 departments/work_items/artifacts/review_requests/review_runs/verdicts/acceptances/events/event_rejections/board_events/deliveries/outbox/receipts/alerts/transitions/gate_rejections。
- **事件兩階段**：`admitted`（持久）→ `processed`（套用狀態後才寫 receipt）。事件 ID 唯一；每 work item 嚴格 `seq_no`；重複／跳號／落後一律 `duplicate_event` / `out_of_order` 拒絕並留下 `event_rejections`。**admission 不等于處理完成**。
- **狀態機**：`draft → artifact_ready → in_review → changes_requested → rework_ready → in_review → approved → accepted → reported`；非法轉移拒絕並留 `state_violation`/alert。
- **Review 綁定**：verdict 綁 review run 的 `artifact_sha`；request rev 必須等於目前 rev；reviewer 必須是 request 指定且 ≠ implementer；舊 rev 的 PASS 為 `stale_verdict`/`stale_pass`。
- **Acceptance gate**：manager 身分、acceptance 綁 exact PASS verdict + exact 目前 artifact sha + current rev；缺 acceptance／stale PASS／錯 artifact 一律不送。
- **Delivery**：唯一 `operation_key = final:<dept>:<acceptance_id>`；先寫 intent + outbox（commit），再 effect，再**權威 read-back**，再 receipt，再 confirmed。狀態：`intent/pending/effect_unknown/confirmed/dead_letter`；最多 3 次，unknown 會 surfaced，不會宣稱成功。
- **Board 邊界**：live 只用公開 CLI 讀 `kanban show --json`／`notify-list` 驗證結構，唯一寫入是最終 report comment（`kanban comment`，再 read-back）。**不用私有函式、不直接寫正式 SQLite。**
- **Parent 語意**：work/review/acceptance 卡的 parent 是**已完成 setup 依賴卡**；另有一張 pending manager root 卡存在但**從不作为 parent**；report 卡無任何 parent/child，關聯存在 prototype DB。

## 3. 正常路徑 live 證據（真 gateway + 3 profiles + synthetic model）

Session（各自 profile store 核對）：default `api-0b36333bf4394fee`、manager `api-9d018bfc598b4849`、reviewer `api-5e66804d3c85a299`。

| 步驟 | Board 卡 / 事件 | 結果 |
|---|---|---|
| setup 依賴 | `t_3c51d932` | 先完成，供子卡完成 |
| manager root（不作 parent） | `t_2f58d1b4` | 保持 pending，未出現在任何 child parent |
| work 卡 | `t_66a88d72` | `create --parent` 實際繼承 manager 訂閱（證據 `inheritance-observation.json`）；manager wake 執行 `kanban_show` + `kanban_comment`，board comment author=manager |
| review 1（`REQUEST_CHANGES`, sha `2e2b159a…`） | `t_d1701454` | reviewer profile 真 wake，comment 為結構化 `{"verdict":"REQUEST_CHANGES","artifact_sha":"2e2b159a…"}`；prototype 套用 → `changes_requested`，report 無 final |
| rework + review 2（`PASS`, sha `ef65e542…`） | `t_b574d38d` | reviewer wake 產生 fresh PASS，綁**新** artifact；prototype → `approved` |
| manager acceptance | `t_2d0d6acc` | manager wake 產生 `{"decision":"ACCEPT","artifact_sha":"ef65e542…"}`；prototype → `accepted` |
| 單一 final | detached report `t_ce23ba7a` | 交付 `dlv-15d4ce7e7d3c`、`acc-21849a6a066d`、`vrd-150678b9d1af`；report 上恰好 1 個 marker comment `[[deptproto-final:15d4ce7e7d3cadde]]`；重複 deliver → `already_delivered`，仍 1 個 |
| default 關聯 | report 完成 | default profile 真 wake 並留下 receipt comment；default 對 4 張 work-DAG 卡無任何 subscription/receipt |

Default 訊息中四個 child task id 的文字出現次數皆為 0（`default-child-exposure.json`）。**不得引伸為零 metadata 暴露**：shared board 的 `kanban_show` worker_context 仍可能帶 child 摘要（run 377 已證）。

## 4. 負例（不得送 final，全部成立）

| 負例 | 證據 | 結果 |
|---|---|---|
| 未接受（PASS 但無 acceptance） | unit `test_unaccepted_pass_never_sends_final` | `held/no_acceptance`，effect 0 |
| stale PASS（PASS 後出現新 artifact） | unit `test_stale_pass_on_older_artifact_never_sends_final` | `held`，effect 0 |
| 錯 artifact verdict | unit `test_verdict_for_wrong_artifact_is_rejected` | `rejected/wrong_artifact` |
| 錯 owner verdict | unit `test_wrong_owner_verdict_is_rejected` | `rejected/wrong_owner` |
| 重複事件 | unit `test_duplicate_event_is_rejected_and_state_unchanged` | 第二次 `duplicate_event`，只 1 次 final |
| 亂序事件 | unit `test_out_of_order_event_is_rejected` | `out_of_order`，狀態不變 |
| 錯 child parent（pending root） | unit `test_work_item_cannot_use_pending_manager_root_as_parent` | `wrong_parent_edge` |
| 錯 report 邊 | unit + live `dept-bad`（`t_6d83ba76` 被 link 到 `t_3fd2c66`… work 卡） | `held/wrong_report_edge`；錯 report 卡 0 comment |
| review 卡 = report 卡 | unit `test_review_card_must_not_be_report_card` | `wrong_report_edge` |
| 缺訂閱 | live `t_f444f075` | 無 wake；`dead_letter/missing_subscription` + alert |
| 錯 owner subscription（manager owner + default session） | live `t_034dc30c` | 無 wake；`dead_letter/wrong_owner` + alert |
| artifact 檔案 hash 不符 | unit `test_artifact_hash_mismatch_is_rejected` | `artifact_mismatch`，不記錄 artifact |

## 5. Crash / recovery

離線（真子程序 `os._exit(137)` + 重啟）：

| 邊界 | crash 前 effect 數 | 重啟後 |
|---|---|---|
| `after_intent` | 0 | apply 1 次 → confirmed |
| `before_effect` | 0 | apply 1 次 → confirmed |
| `after_effect_before_receipt` | 1 | read-back 確認、補 receipt、不重送 |
| `after_receipt_before_confirm` | 1 | 直接 confirmed、不重送 |
| `after_admit_before_process` | 0（event 已 admitted） | process 1 次、artifact 1 份 |

另測：兩個並行 deliver process → 只有 1 個 effect、1 個 receipt；永久 read-back 不可用 → `effect_unknown`→3 次後 `dead_letter`，receipt 0，不宣稱交付。

Live 真 board：`t_de718ca5`（dept-crash，delivery `dlv-b5bc425d37e1`，marker `[[deptproto-final:b5bc425d37e14434]]`）在 `after_effect_before_receipt` crash（rc 137），board 上已有 1 個 final comment；重啟 recovery → `confirmed`，comment 仍 1 個，`receipts=1`、`status=confirmed`。

## 6. 隔離與 fail-before-start

- `unshare --user --map-root-user --mount --net --pid --fork --kill-child --mount-proc` 實際成功；`/home/ubuntu` 換 tmpfs 後只 bind 回 prototype 樹（rw）、installed source（ro）、uv Python runtime（ro）；`/run`、`/tmp` tmpfs。
- 網路：只有 `lo`、`routes=[]`；無外網 provider。preflight 記錄 mount/net/pid/user namespace inode。
- `assert_lab_env` 要求 HOME/HERMES_HOME/HERMES_KANBAN_*/XDG_*/TMPDIR 等 12 個路徑全部 resolve 在 lab；拒絕 worker pins、SSH/D-Bus/proxy 變數與 lazy install（`HERMES_DISABLE_LAZY_INSTALLS=1`、`UV_OFFLINE=1`、`PIP_NO_INDEX=1`）。負向輸入（production DB、`/run/user/1000`、`HERMES_KANBAN_TASK`）皆 fail-before-start（`preflight.json` + `assertions.json` 前 3 項）。symlink 逃逸由 unit test 覆蓋。
- `unshare` 不可用時 launcher 直接 exit 78，不啟動任何 Hermes 程序（`sandbox.sh --probe` 於本機回 `unshare-ok`）。
- 清理：gateway SIGTERM rc=1（非 graceful exit 0，如實保留）、model rc=-15，全部 reaped；`ss -lntup` 只剩表頭；PID namespace 隨 launcher 結束。

## 7. 能力邊界與 fail-closed（不可誤報）

1. **受信任單寫入者不是不可繞過的 ACL。** prototype 的 `flock` + 單一 DB writer 只約束本程式；shared board 上任何 CLI/UI 仍可 create/link/decompose，`create --parent` 繼承（本次 live 再次觀察到）無法由 prototype 原子阻止。安全成立條件是「已知拓樸 + 單一寫入者」，不是「任意寫入者都被隔離」。
2. **Shared board metadata 不具保密性。** 即使 default 零 child wake，`kanban_show` 的 worker_context 仍可能含 child id/摘要；本輪 default 訊息文字計數為 0，但不宣稱零 metadata。
3. **不宣稱 exactly-once。** 對外效果只有「同一 marker 的冪等 apply + 權威 read-back + receipt」；若 read-back 不可用，狀態是 `effect_unknown` 或 `dead_letter`，不是成功。外部效果的原子性未證明。
4. **未使用私有函式、未直接修改正式 SQLite。** 公開介面仍缺（承 P0-R）：同交易 `inherit_notifications=false`/exact recipients、owner 驗證與拒絕回執、durable ACK/replay/dead-letter、scoped task query/ACL。遇到即 fail closed，不以 prototype receipt 冒充核心 ACK。
5. **未測**：真模型判斷品質、`review_requested`/`changes_requested` 事件種類的 live wake（本輪只用 proven 的 `completed` + result 內容承載 verdict）、真 Desktop/SDK/remote OAuth、多 board、production 部署。

## 8. 未知／未處理副作用（明列）

- gateway 啟動 console 可能有 relay／plugin 初始化 warning（本次 `gateway-console.log` 保留原文）；未安裝、未修 relay。
- gateway SIGTERM 回 rc=1，不是 0；已 wait/reap，listener 已消失。
- synthetic model 只回固定工具呼叫，不代表模型行為；`model.jsonl` 明確標記 `synthetic: true`。
- prototype 的最終 comment 由 orchestrator 以 `--author manager` 寫入；board 無法區分「manager 本人」與「持有 manager 名稱的單一寫入者」。
- 本次 live 只注入 final delivery 一個 crash 邊界；其他邊界以離線真程序死亡測試，不宣稱 live 全邊界。
- default 文字計數 0 只代表本次觀察窗與 `content` 欄位，不含 tool result 的結構化 metadata（已知 exposure 風險）。

## 9. Gate 狀態

`t_369a6f8c` 仍 blocked、`t_a1c1b08b` 仍待其 parent；本卡完成**只代表 prototype 可供獨立審查**。沒有 P0 PASS、沒有 M1 授權、沒有正式部署或 profile/board 變更。
