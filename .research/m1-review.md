# M1-R 獨立驗收報告 — REVIEW: REQUEST_CHANGES

- 審查卡 `t_4be3305e` / run **455**；此結論不是 PASS，不放行依賴卡 `t_750e8a14`。
- 審查對象：`<repo>` 的 M1 核心，非 P0 的不同 v6 原型。此目錄沒有 Git repository；不可用 commit 當身分。凍結副本及逐檔 SHA-256 清單：`.research/m1-review-probes/snapshot/`、`snapshot.sha256`；清單本身 SHA-256 `ce009235de22d394d3d7f27c4215f5d1d55cded82bd12e1c1ddfc44368d06ae8`（46 files，含測試）。比對時 manifest、plugin 入口與凍結清單中的全部 M1 `department_lib/*.py` 均與被測副本一致。收尾時共享目錄新增 `department_lib/dashboard.py`、`department_lib/kanban_db_source.py`（不在此 M1 快照與驗收範圍），顯示 M2 並行期間樹已擴充；本結論不涵蓋新檔。`README.md` 當時另計 SHA-256 `838b105ded3d289427104fc4b0da81538e58e4e7c64f63c76bbcf1d2fd26c936`。此身分只指本輪檢查時的 bytes；M2 並行，不推論日後共享目錄未改。
- 強調：下述反例是**synthetic board/port + 隔離 SQLite**，不是 live gateway 或實際私人資料外洩。`SYNTHETIC_SECRET` 等均為合成字串。未變更產品程式、正式 board/profile/gateway、服務或 FreshTape；本輪只新增本報告及隔離測試／測試快照。

## 阻擋 findings（P1）

### 1. 拒絕審查被改寫為 PASS；run provenance 與 artifact 不充分綁定

- `department_lib/acceptance.py:85–121, 185–202` 選 request 後數字較大的任一 `outcome=completed` run，自動標為 PASS；完全不檢查 reviewer 明確的 `metadata.verdict=REQUEST_CHANGES` 或反向 summary，也未驗證該 run 從 review lane claim。`gate.py` 的 control 正常通過；`explicit_rejection` 仍顯示 `accept=releasable release=delivered`，輸出的 verdict 是 PASS。`not_a_review_run` 將 claimed event 的 source 改成 `ready` 仍同樣放行。`missing_request_run` 移除 request 對應 run 的不完整 snapshot 仍放行，需區別其正常生命週期是否可達，但 fail-closed 契約已被否定。
- 同一腳本 `wrong_handoff_artifact`：提交／request run metadata 為 SHA A，reviewer 卻宣告 SHA B，manager claim B，仍交付 B。這不同於已知「送出時不重算 bytes」：此處甚至沒有比對兩個已提供的身分。現有 request-review 工具可能不承載 artifact SHA；因此不能假裝已具備可靠來源，必須設計、公開並驗證相容的 submission/review identity 契約，缺失或相互矛盾均 held。也要以 `fullmatch` 拒絕 `64hex + newline`，本測試顯示目前會放行。
- 修復：明確且無衝突的當前 reviewer PASS（精確 structured verdict 或明確 canonical contract）、驗證正確 review request/run/claim provenance、獨立成員和 current artifact identity；同時測拒絕／混合訊息／缺 run／非 review／不同 SHA／malformed SHA 和正向控制。
- **會影響 M2 的介面變更：**若新增 worker 提交 artifact 宣告、必填 reviewer structured verdict、錯誤碼或 evidence 欄位，須由 orchestrator 與 M2 對齊 schema；不能僅改文字。判斷 review lane 與矛盾證據若可在現有欄位完成則屬內部修復。

### 2. wake owner 未指定亦未驗證；不屬於已知訂閱繼承限制

- `kanban_cli.py:132–138` 未帶公開 `--notifier-profile`；installed Hermes CLI 的預設是執行時 profile。`routing.py:49–52, 56–79` 僅匹配 platform/chat，不看 `notifier_profile`；`routing.py:114–117` 建立後亦用相同錯誤讀回。`routing.py` 隔離 probe 顯示期待 `dev-manager` 而實得 `default` 的新、舊 subscription 均回 `ok=True`。
- 先前 P0 的獨立 live 證據 `.research/p0-route-followup.md:10–16, 54–64` 證明錯 owner/session 可被 CLI 接受卻不被 claim、沒有 wake；不是本輪 M1 live 重跑。正確目標需校驗 `(profile, session, platform, mode)`，禁止宣稱 read-back 相同 chat 即證實 manager 醒來。修復後用隔離真 CLI read-back 和可行條件下安全獨立 runtime 反向驗證；現有工具若不能驗證 session ownership，應 held 或明確標未驗證。

### 3. 任務歸屬與 handoff root 在未知狀態 fail-open

- `orchestrator.py:68–74, 245–250` 對未快取 task 把 owner 視為未知後仍訂閱；probe 中 `ops-child -> ops-root` 被 dev manager 訂閱且回 OK。`:78–92` 對曾快取的 child 永遠信任舊 owner；模擬移到 ops 後仍歸 dev。`:224–234` ancestry 讀不到或不相連時，退回任意該部門第一個 root；probe 在 board error 下仍把 escalation 寫進 `dev-root`。此為產品造成的錯誤路由，不是已記錄的 core inheritance 穿透。
- 修復：對全部 targets 由目前可核實的 board topology/明確受信權威關聯建立唯一歸屬；移動或多 root/讀取錯誤應 fail closed 和可診斷，不得猜 root。注意 plan 指依賴邊和 membership 不必相同，故修復可以引入明確 association，不能盲目把所有 dependency 當 ownership。
- **會影響 M2 的介面變更：**如果為非 DAG 關聯新增或修改公開 binding/API、資料形狀、錯誤碼，須同步 M2；單純拒絕不明確 routing 的內部修復可維持現有介面。

### 4. 舊事件可撤銷新 acceptance，並使重試崩潰；衝突後仍可能套用

- `orchestrator.py:111–160` 遲到的歷史 `changes_requested` 無條件 supersede 所有 active acceptance；`store.py:428–450` duplicate acceptance 處理使該紀錄不能重新 active。`events.py` 先在 revision 2 PASS 時合法 accept，再 ingest 其歷史事件，輸出 `active=None; reaccept: TypeError 'NoneType' object is not subscriptable`。這是交付前真實狀態回退，不是已知 post-delivery correction 語意。
- 同 probe：一個 observed 無 receipt 的事件與異 payload 發生 conflict，原 payload 再到時居然 `applied stored_state=conflict ... receipt=True`。修復時必須將 event disposition/quarantine、具序 revision 的 supersession 和 recovery 納入原子狀態機；只對該次事件有權改動的 acceptance 生效，不能把已衝突當可恢復 duplicate。
- `events.py` 還模擬兩個不同 board `blocked` 事件在同秒同 run 無 ID 時碰撞，`applied, conflict`；來源 CLI 對外未提供穩定 event ID，需確認 public upstream 的可用性，不能僅加 payload hash 讓真正 identity-conflict 被誤當新事件。共享同一 Store 實例的受控雙執行緒也得到兩個 rounds、一個 receipt；插件目前每次工具呼叫建 service，這個同實例並行路徑的實際可達性尚未證實，應作條件性風險而非獨立量產主張。

### 5. 並行 final 可重複；不同 accepted identity 被回報 already delivered

- `delivery.py:109–120` 先查後 `INSERT OR IGNORE`，而不檢查是否取得唯一 claim；兩個獨立 SQLite connection 在首讀後同步，兩者均 `delivered`，synthetic port 記錄 **2 次 append**。這不是苛求外部 exactly-once：README:102–109 明確主張單一 effective handoff；至少一次不重複的併發 claim 必須有實證。若 upstream 不支援可靠冪等 effect，就必須安全地處理 in-flight unknown，而非宣稱已保證。
- `gate.py` 先送 run 6/SHA A，再接受 run 8/SHA B；second release 回 `already_delivered`，但唯一已送 body 仍為 run 6/A。已交付更正政策尚未決定是既知限制；即便不允許第二次傳送，也不能將舊 identity 當作已送出新 identity。應明確 held/衝突並展示既送 identity，待政策決議後再定更正。

### 6. 升級訊息將任意 detail 原文寫入共享 card

- 工具敘述聲稱不含 credentials/transcripts，但 `__init__.py:101–112,251–259` 只驗 dict；`delivery.py:160–175` 將 detail 逐字放進 comment。synthetic token/transcript probe 看到原文。這不是宣稱跨 ACL 竊取：是模型呼叫工具時產品對共享目的地的直接輸出未做承諾的最小化。修復為固定 reason code 與嚴格安全 allowlist；拒絕未核准欄位，測 nested/escaped/private sentinels。既知 shared-board exposure 仍不是本 finding 的替罪羊。
- **會影響 M2 的介面變更：**收緊 escalation detail schema/錯誤回應需與 M2 對齊，舊 caller 不應默默丟資料後回成功。

## 非阻擋／條件性觀察

- P2：unknown final 的 `Delivery.reconcile` 有實作，但未見對應已註冊 plugin tool（`__init__.py`）；普通 release 在已確認 absent 時仍停 `unknown`。需要可用受控恢復入口，恢復前重驗當前 acceptance；新增公開工具會影響 M2。
- P2：`kanban_effects.py:68–85` 對 malformed comment row 跳過後回 `absent`；此時 reconcile 可能誤判可重送。正常 CLI 是否產生 malformed row 未實證，不能稱日常一定發生。
- 從發現 child 的 ingest 並未自動 ensure wake；只對建立在正確 subscription 之後的繼承路徑可能有效，先前已存在的 child 未證實。

## 驗證與範圍

1. 我方在隔離快照執行既有 full suite：`PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/.hermes/hermes-agent/venv/bin/python -m pytest review-routing/snapshot/tests -q -p no:cacheprovider --basetemp=~/.hermes/kanban/workspaces/t_4be3305e/review-routing/pytest-rerun` → **160 passed in 8.78s, exit 0**。測試全綠不代表上列反例通過。
2. 可重現、凍結且不改正式系統（3 個 script 的 assert 是在**確認缺陷存在**，exit 0 不表示產品 PASS）：

```bash
D=<repo>/.research/m1-review-probes
(cd "$D" && sha256sum -c snapshot.sha256)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$D/snapshot" python3 "$D/gate.py"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$D/snapshot" python3 "$D/events.py"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$D/snapshot" python3 "$D/routing.py"
```

以上三個腳本本輪均 exit 0，輸出與 findings 對照；其中 events 的 fault-injection rollback/restart 正向控制為 `applied duplicate rounds=1 receipts=1`，board status 從 done 變 blocked 時 `held verdict_not_final`。routing probe 的副作用只有隔離 SQLite 與記憶體 fake port。未聲稱 OS 網路 namespace 隔離：這些測試不啟動 gateway，gate probe 額外擋 Python connect/DNS；其他 probe 不做網路呼叫。
3. 支援介面既有證據：`t_369a6f8c` run **444** 的 structured metadata `verdict=PASS, scope=routing_and_security_boundary_only, p0_pass=false`，是獨立 loopback-only 真 gateway + synthetic model 14/14，非本輪 M1 live；`t_54577d57` run 436 的 PASS 只屬另一 v6 狀態機，不可轉嫁至 M1 bytes。strict no-network 下相關 live `NOT FEASIBLE`（`t_549898c3` run 431）；未執行作者 loopback 變體，也不宣稱真 Desktop。已報告的 core subscription inheritance、shared-board metadata exposure、profile 非 ACL、惡意序號燒耗與 post-delivery correction 語意不當作本輪新 defect。
4. 作者宣稱的 isolated Hermes CLI contract 和乾淨 staging plugin validate 為上游證據，本輪未重新執行；不將之升格為本輪 live 或獨立 validation。

## 放行條件

修正 P1 後對**新凍結 bytes**另跑本報告 probes（將反例改為預期 held／正確狀態的回歸，保留舊版 RED）、完整 suite、實際 CLI contract、owner/target 反向案例以及同範圍可行 runtime 證據；重新獨立審查並以新 run 的 structured `verdict=PASS` 才可完成此卡並放行 M2-R。不得將此 REQUEST_CHANGES 以 complete 包裝。
