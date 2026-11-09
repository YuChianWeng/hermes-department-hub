# P0-R 獨立複驗｜routing & security boundary only：PASS（run 444）

**判定：`verdict=PASS`、`scope=routing_and_security_boundary_only`、`desktop_real_ui=deferred_to_M2R`、`p0_pass=false`。這不是整體 P0 PASS，不是部署、安全隔離或完整 E2E 的批准。** 此次依 maintainer 決策 A 之新卡 body 重新判準；下方 run 370 保留為歷史紀錄，不再代表本次縮限範圍的結論。

## Findings 與限制（不是本次重割後的 blocker）

1. **安全限制 — 繼承穿透與 metadata exposure 是實測事實，不是已修好。** 在受控 DAG，default 不訂閱工作圖、回報卡 detached 時，觀察到的兩條 child completed 路徑不喚醒 default；但若 default 訂閱 root，create/link 兩條路徑皆繼承 default 並真實喚醒、執行工具。default 的 report `kanban_show` 結果還可包含 child IDs。這只支持可信單寫入者與受控拓樸；不是任意 CLI/UI 操作的 ACL、保密邊界或不可繞過的保證。舊分析 `.research/p0-route-followup.md:66–89`、`.research/p0-route-independent-review.md:7–14,28–36` 已記錄；本輪亦重現，見下表。
2. **語意限制 — 驗收前不可 final 是已審查的 synthetic v6 domain invariant，不是已實測的真 gateway/真模型端到端強制。** 引用 `t_54577d57` run 436、`~/.hermes/kanban/attachments/t_54577d57/REPORT.md`：identity matrix、衝突、crash、fuzz 等對固定 v6 bytes 通過；採 trusted adapter/single writer 前提。具 exact-current identity 的偽造仍可能 fence；ordered hostile rejection 可消耗序號。已 delivered 後的合法 conflict 無法撤回外部 effect，亦無 delivery↔item reconciliation。真實 reject→rework→fresh PASS→manager acceptance→default final 尚未端到端驗證；本輪 detached report 是測試者直接 complete 的 routing 正控，不是已通過 review 的 final。
3. **重複派工／恢復不得過度宣稱。** 原 run 377 的兩張 child 在有限觀察窗各有一次 wake；本輪亦各一。這不是跨重啟、claim 後 crash 或任意重播的 exactly-once/no duplicate dispatch 證明；尤其 wake admission 不是處理完成 receipt。v6 獨立審查的 4 個 SIGKILL（真 returncode -9）、2 個 SQLite ABORT、120 條 fuzz traces 與相應 synthetic/domain effects 支持**測過的**持久化及去重／恢復行為，非 gateway 層或任意外部系統的原子保證。run 370 的錯 session 持續錯誤後訂閱被刪仍是風險，不是本輪已修復。
4. **Desktop 和公開介面只獲有限可行性。** `t_30ed8424` 的 SDK 原始碼／mock 與後端證據，加 `t_0c0290f6` 的獨立原始碼核對及隔離 ASGI/TestClient，支持公開 SDK sidebar/route、scoped REST 的有限可行性；不證真 Desktop、OAuth/native grant、正式資料整合或完整公開 event/ACK adapter。`t_0c0290f6` 與 `t_394237b5` 均 **REQUEST_CHANGES**：原 i18n mock 不忠實（v2 該項修復已獨立確認）、安裝/回復和隔離手冊仍有 P1；既有安裝器／手冊已 withdrawn，**不要執行**。原研究所稱 live 6/6 的 raw logs 並未隨附件提供，不能稱已獨立重現該 live 結果。
5. **現行 no-network gate 的 v5/v6 gateway live 是 NOT FEASIBLE**：`t_549898c3` run 431，lo DOWN、localhost fixture connect errno 101。這不等同所有隔離 live 一律不可行，也不是本卡重割後的 blocker。本輪以下 run 444 採另一個私有 network namespace、lo UP 的 routing-only 代表性測試；不是在上述嚴格 no-network gate 重跑 v6 live，亦不代表產品 E2E。

## 已證：本次獨立 fresh routing 重跑

- 固定 installed Hermes source commit `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`；在本卡全新 disposable lab，重新建立合成 default/manager profiles、合成 sessions、board、synthetic API keys；沒有複製 run 377 session 或 board。新的 bootstrap 回傳 raw session ID 恰與 run 377 相同；原因未另驗證，勿據此推論 session 全域唯一。此次是在空 lab 中新建，**並非沿用舊 DB**。真 gateway、真 Kanban CLI/tools，模型是 deterministic synthetic，dispatcher disabled，沒有真模型管理判斷。
- 以 run 377 `runner.py`/`sandbox.sh` 複本為基礎：移除已知失敗的 raw-session N2 前提；先完成 setup root 才完成 child；避開 BLOB JSON 匯出，另加入 reviewer 的 session/message SQL 檢查。保留原訂閱繼承反例；非原 script 不修改重跑。`bash fresh-route/scripts/sandbox.sh` **exit 0，14/14 assertions PASS**。
- 隔離：`unshare` user/mount/net/pid，正式 `$HOME`、`/run`、`/tmp` 遮蔽；僅 fresh lab 與 read-only installed runtime/source bind 回；乾淨 env，禁 lazy install/offline。preflight 測試拒絕正式 board pin 與正式 XDG runtime 路徑；紀錄顯示唯一 interface 為私有 lo、`ip -j route=[]`；這是 loopback-only routing lab，不是 VM 或通用安全沙箱。gateway/model 都 wait/reaped；gateway shutdown rc=1、model=-15；私有 namespace 最終 listener 僅表頭，**不是宣稱 graceful exit 0**。

| 受測合成物件 | 實測 | 範圍 |
|---|---|---|
| child create `t_470fe6fe` | manager 原 session `api-ce71a2ebe36fdb0d` 1 次 completed wake；真 `kanban_show`/`kanban_comment`，comment author `manager`；default 同事件 wake 0 | 受控拓樸、單次有界觀察 |
| child link `t_ae7c12c3` | 同 manager session 1 次 wake 與工具；default 同事件 wake 0 | 兩種建卡路徑，同一 completed 事件種類 |
| detached report `t_bcec0bc7` | default 真 wake/show/comment | 正控，不是 final acceptance |
| unsafe create `t_e8384d99` / link `t_ed4ee121` | default 訂閱 root 後，二者各真實喚醒 default 並落地工具 comment | 反證自然隔離 |
| wrong owner `t_9732f551` | manager owner 配 default-only session：觀察窗內無 comment | 不是訂閱時拒絕或持久恢復的證明 |
| credential 負控 | default synthetic key 打 manager endpoint HTTP 401 | 非一般 ACL 安全證明 |

以合成 SQLite `mode=ro` 額外交叉核對：兩 store 中 profile/session 實際對應；各 session 皆有真 `kanban_show` / `kanban_comment` tool rows；default 4 個 show 的部分結果提到兩個安全拓樸 child ID，重現 metadata exposure。此是 shared board context 暴露，**不是** default 對該兩 child 直接 show 或直接收到 child wake。model log 18 requests、7 wake、7 tool-return rounds；synthetic receipts 不等於真正管理審查。

## 已證／未證及證據重現界線

- **已證：** 上表的固定 commit、受控設定和觀察窗內的 manager-profile completed routing；受控拓樸的 default 陰性及 detached report 正控；create/link 繼承反例及 shared-board metadata exposure。run 377 原始證據另經 `t_e68ef120` run 381 獨立離線 SQL 重建，`.research/p0-route-independent-review.md:16–46`。
- **已證（引用既有獨立驗收，非本輪重做）：** v6 狹義 acceptance/identity/domain crash-recovery，見 `t_54577d57` run 436。公開 SDK 有條件可行及兩輪拒收見 `t_30ed8424`、`t_0c0290f6`、`t_394237b5`。對 Hermes 的正式依賴限文件化 CLI/API/SDK 及 profile configuration；本輪唯讀 SQL 是**合成測試證據核對**，不是插件依賴、產品 API 或直接操作正式 DB。未證完整公開 durable ACK/replay、原子 no-inherit hook、任意建立者 enforcement 或所有多 board 操作。
- **未證：** 真 Desktop loader/render/焦點/可用性、真 OAuth/native grant 與完整 Desktop/backend round trip（移交 M2-R）；真模型自主決策、正式環境 live、真 dispatcher review/rework/final 閉環；claim/admission 與外部 side effect 跨 crash 的通用 exactly-once、任意多 writer、電源／磁碟故障。live under strict no-network 為 NOT FEASIBLE，不冒稱本輪做過。
- **證據附件：** 本次 `route-review-evidence.tar.gz` SHA-256 `22c99251249d1e53ee67b57a23b79ceeeb05d3ef09a7ecbfc811dfb2cd1ddbe0`；含本次實際執行腳本、全部原始 synthetic logs、isolated board/default/manager 三份 SQLite backup 與完整 console。25 archive members 經逐檔 byte hash roundtrip；不含真憑證。`runner.py` SHA-256 `6fa542cc776bf3ec1c387e2d37e1c1ab7a284cccbc16d56dafd91517484e6db4`，`sandbox.sh` `481ad6078cf81f0704e484848a61246d244590414391a396b5b6669c4b85edcd`。stdout 包含合成資料；logs/DB 不代表正式狀態。
- **重跑限制：** archive 內 launcher 寫死本輪 scratch 的 LAB，該路徑已含資料，**不得直接在既有 lab 重跑**；需在全新空 workspace 複製 scripts，分別建立空 `evidence`、`os-home/.hermes`、`board`、`runtime`、`tmp` 及 `xdg/{config,cache,data,state}`，將 sandbox 的 LAB 指到該全新目錄並重新 pin hash，確認安裝 source/uv runtime 只讀與 `unshare` 可用，再 `bash <fresh>/scripts/sandbox.sh`；任何 isolation/preflight 失敗均先停止，不放寬或改指正式系統。原命令本輪工作目錄 `~/.hermes/kanban/workspaces/t_369a6f8c`，完整輸出在附件。清理、profile 及 board 都限測試環境；未修改產品、正式 board/profile/gateway/通知或 FreshTape。

---

# 歷史紀錄：P0-R run 370 原始全範圍 BLOCKED（已被本次範圍裁決取代）

- 審查任務 `t_369a6f8c`，本次 run 370；上游 `t_b0f90986`；受測 source `~/.hermes/hermes-agent`，`git rev-parse HEAD` = `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`。
- 對照計畫 `~/.hermes/plans/2026-11-05_132450-hermes-department-orchestrator-hackathon.md` §3、§6、§33–39，及 `.research/p0-report.md`。本報告覆蓋上游「核心路徑實證可行，可進入建置」的判斷：**P0 尚未通過；已實測的是 default profile 加合成模型的單一路徑，不是已證實的部門主管閉環**。下游 `t_a1c1b08b` 必須保持依賴等待，不能藉 complete 或 REQUEST_CHANGES 後 complete 放行。

## Findings（全部是 P0 blocker，按風險排序）

1. **[P0] 主管 profile 根本未被證實喚醒。** 上游 gateway log `.research/p0-report.md:60–65` 明示 `profile=default`、`.research/p0-report.md:100–102` comment author 是 `default`。我在新 scratch gateway 重跑後，session SQLite 的 `profile_name='default'`、訂閱 `notifier_profile='default'`、comment author `default`、gateway log `profile=default`；卡片名或 `model_name: p0-manager` 不是 Hermes profile。硬編碼模型假裝主管發出工具呼叫，也不能證明真模型做了任何判斷。**補證**：隔離 HERMES_HOME 建立真正不同的 `manager` 與 `default` profile、各自有獨立可核對 session；在受支援 profile 路由啟動真 gateway，記錄訂閱 owner、wake session/profile、可用工具與管理行為，並斷言 default 無子工作 wake。測不到就維持 block，不可把 default 重命名為主管。

2. **[P0] 子工作訊息歸屬／審查拒絕時不上報沒有驗證，現有提案有明確穿透風險。** `hermes_cli/kanban_db.py:1323–1325,1364–1399,1583–1589` 在建立／link 時複製父卡通知訂閱；上游 `.research/p0-report.md:110–111,148–149` 亦確認。不僅 completed，notifier `gateway/kanban_watchers_notifier.py:404–428,472–484` 對 `review_requested` / `changes_requested` 也有格式化與 wake 路徑，故 root 的 coordinator 訂閱可能把未接受子卡事件向上游喚醒／暴露；這不等同證實已發 final report，但足以否定「自然只由主管接收」。既有建議「分解後再訂閱 root」只對當時已有子卡有可能有效，之後建立或 link 的子卡仍可能繼承；「回報卡」也只是尚未測試的候選方案。**補證**：在隔離板以真 default 與 manager 兩個收件端跑動態建卡、link、失敗 review、重做、PASS；斷言每個 child/review 事件僅到指定 owner，default 在 PASS + 特定 artifact/run + manager 接受之前零 final，失敗與陳舊 PASS 均不能放行；不禁用既有正式安全警報。若無支援介面能保證，停下並提出縮減範圍選項。

3. **[P0] review gate、最終回報與防重派工尚無端到端實證。** 上游 `.research/p0-report.md:124–133,207–214` 明列 dispatcher 關閉，review spawn、rejection 和 fresh PASS 未執行；所以 source 裡存在 `kanban_request_review` / `kanban_request_changes` 不能證明獨立審查與 manager 最終接受。所謂 8 項測試只數某一次 wake 與一張 follow-up，沒有重播同一事件造成的重派工、真實最終報告或接受檢查。現有同卡 review 單元測試能驗證 SQLite 狀態轉換，但不能取代真 dispatcher + manager + coordinator 的驗收。**補證**：跑 failed review → rework → 新 revision 獨立 PASS → 主管核對 run／artifact → 單一有效 final，以及失敗／舊 verdict／重放／重啟／競態的負向斷言；用穩定的事件和工作 idempotency key、持久 receipt 與權威卡 read-back 驗證，切勿承諾嚴格 exactly-once。

4. **[P0] 故障恢復未證實；一次錯誤投遞可導致訂閱被刪。** 原 8 項沒有測 gateway 停機期間產生事件，或在 cursor claim 後但處理完成前 crash。`gateway/kanban_watchers_notifier.py:302–323` 先 claim；`:491–496` 明言 wake admission 不是執行或最終回應 receipt；`:544–554,802–816` 一般連續失敗達上限會 unsubscribe（與 `WakeNotAccepted` 的持續 rewind 不同）。我的第一輪複本試驗因複製 session 造成 `HTTP 409 profile_mismatch`（環境建置問題，不歸因為產品跨 profile 缺陷），在真 gateway 重試 12 次後 log 顯示 `dropping subscription`，SQLite 查詢該卡 subscription 數為 0；證明不能把 cursor 或「沒重複醒來」當成完成處理保證。**補證**：以正確建立的 profile/session 測停機期間 event、claim 後 kill、admission 後 manager 未完成、執行中 kill、重複與亂序；檢查持久 receipt、獨立補償／dead-letter、有限重試與有界警示。任何被刪的訂閱都不得被誤認已處理。

5. **[P0] Desktop 與公開 Kanban adapter 路徑未被 P0 證實。** 上游承認 Desktop 未載入，且建議 `plugin_api.py` 直接讀 `kanban.db` 或 CLI（`.research/p0-report.md:137–142,203`），但沒有證明 DB schema 是公開穩定契約或插件透過受支援 API 可執行全部必要讀／寫／事件／權限操作；不應將內部 `_inherit_notify_subs`、`claim_unseen_events_for_sub`、直接引用 `hermes_cli.kanban_db*` 或 DB schema 推定為公開 plugin API。`host.request` 缺少查詢 RPC 的 source 調查不是已驗證的替代實作。**補證**：逐項列官方公開介面、確切輸入輸出、最小版本與權限，隔離跑通 task 查詢、profile 綁定、事件與 acceptance；以公開 CLI 或其他明確文件化介面為候選，但須先驗證隔離、多 board、原子性與授權，不能因 CLI 存在就判定可行。另在代表性的真 Desktop 載入 unified package，用 SDK 呼叫真 backend route 並呈現結果，區分 local、remote OAuth；不可拿 SDK mock 當真 Desktop。若缺受支援 API 或沒有 Desktop 測試能力，維持 block，請求適當環境／上游 API，或由 maintainer 決定縮減需求，不 patch core。

## 可重複檢查：已證明什麼、沒有證明什麼

### 獨立隔離 E2E（第二輪，PASS 但範圍有限）

在本卡 scratch 的 `review-lab-2` 複製上游 scripts、stub、config、board metadata 與僅 P0 合成板的 SQLite 快照；新建空 session DB 與隨機 API key。啟動前檢查 18121/18122 可 bind，`env -i` 類最小環境、`HOME`/`XDG`/`TMPDIR` 指向 scratch，設 `HERMES_DISABLE_LAZY_INSTALLS=1`、`UV_OFFLINE=1`。以 loopback 的真 gateway `/v1/chat/completions` 建立新 session，不使用任何正式 profile 或憑證；在 `/proc/<pid>/environ` 驗證 `HERMES_HOME`、`HERMES_KANBAN_HOME`、board，且 `HERMES_KANBAN_DB`、`HERMES_PROFILE`、`HERMES_KANBAN_TASK`、`HERMES_DELEGATED_CHILD_CONTEXT` 不存在。腳本 `bash scripts/p0-e2e-test.sh` 輸出 exit 0、8/8 PASS：

```text
CREATED LOCAL SESSION api-def162d3fa5911c3
child=t_447d3cec
PASS child reached done
PASS manager created a linked follow-up card [t_d43b56a1]
PASS follow-up exists and names the child
PASS manager commented on the child
PASS exactly one wake for this child [count=1]
PASS woken turn carried the kanban toolset [tools=32]
PASS kanban tools present by name [14 tools]
PASS subscription cursor claimed the completed event [cursor=22, completed_ev=22]
RESULT: PASS; E2E_EXIT 0
```

額外對受測 SQLite 唯讀核對：session `('api-def162d3fa5911c3','default',None,'api_server')`；comment author `default`；訂閱 `('default','wake',22)`。gateway log 確有 `inbound message … [kanban] Task t_447d3cec completed`、`gateway.wake: wake self-post delivered` 和 `woke agent … profile=default`，stub 請求含 `wake_tool_calls` 及後續 3 個 `role=tool`。這是**真的 default gateway wake 與工具落地，合成模型的固定回應**；不是 manager profile、真模型審查或去重／恢復完整證據。腳本只是在觀察時點數一次 wake，並立即退出；不是長時間無重播或 crash 注入測試。dispatcher 明確為 disabled。結束時兩個 loopback listener 均已關閉。

### 第一輪重跑的侷限與安全記錄

第一輪只複製 SQLite 但遺漏 board metadata；CLI 寫入 scratch 的 `kanban/kanban.db`，原測試卻查 `kanban/kanban/boards/p0/kanban.db`，因而報 6 項 FAIL（兩項條件式 assertion 未列），不應解讀為受測產品功能全部失敗。複製舊 session 也觸發 `profile_mismatch`，因此第二輪改為新建 session。另初次啟動雖設 `UV_OFFLINE=1`，Hermes 的 lazy dependency preparation 仍嘗試建立環境，console 印出多個 `Downloaded …`，最後因缺 `clang++` 的 `python-olm` build 失敗後 fallback；相關目的地在 scratch，但**無法聲稱第一次沒有外部套件網路活動**。`unshare -n` 在此環境被拒，沒有 OS 級網路隔離。第二輪另外設 `HERMES_DISABLE_LAZY_INSTALLS=1`；未見同樣 dependency preparation。上游曾報自己的正式 board pin near-miss（`.research/p0-report.md:153–154`）；上游稱未觀測副作用，不等同可證明未接觸正式 board。我本輪未故意讀寫或重新配置正式 board、正式 gateway/profile、FreshTape 或正式通知；未推送、未發送對外訊息。

### 獨立 focused tests

使用 source venv 的 `python -m pytest -q -p no:cacheprovider`，`env -i`、scratch `HERMES_HOME/HERMES_KANBAN_HOME/TMPDIR`、禁 bytecode、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，指定三個 exact nodes：

```text
tests/hermes_cli/test_kanban_core_functionality.py::test_notify_claim_is_single_owner_and_rewindable
tests/hermes_cli/test_kanban_notify.py::test_create_with_parents_inherits_delivery_metadata
tests/hermes_cli/test_kanban_review_lifecycle_complete.py::test_same_card_review_supports_changes_and_approval_without_block_loop
3 passed, 11 warnings in 3.79s; exit 0
```

11 warnings 為 pytest asyncio marker 未註冊（禁 autoload）；所選三項是同步測試。這些測試直接呼叫 kernel/SQLite（不是正式 plugin 公開介面），各自支持 cursor claim/rewind、訂閱繼承、同卡 review/rework/approval 狀態機，**不證明真 dispatcher spawn、跨 profile routing、Desktop 或 final delivery**。

## 僅文件／source 支持的介面地圖（候選，不是驗收通過清單）

- CLI 文檔 `website/docs/user-guide/features/kanban.md:988–996` 有 `hermes kanban notify-subscribe <id> --platform <name> --chat-id <id> [--thread-id <id>] [--user-id <id>] [--chat-type dm|group|channel|thread] [--delivery-mode notify|notify+wake|wake]`、`notify-list`、`notify-unsubscribe`。上游及複驗中實際只用 isolated `api_server` 的 `wake`，不代表跨 profile 或其他平台可用。
- 此 lab 的實測配置 `platform_toolsets.api_server: [hermes-api-server, kanban]` 對 default session 得到 32 工具、14 個 kanban；不是 manager profile 的最小授權證明，亦不可據此宣稱不含 terminal/file。profile 不構成安全邊界。
- `website/docs/developer-guide/desktop-plugin-sdk.md:2124–2206,2234–2274,2323–2345` 文件化一包 `plugin.yaml` + `desktop/plugin.js` + `dashboard/manifest.json`（`api: plugin_api.py`）+ FastAPI router；`ctx.rest('/board')` 對應 `GET /api/plugins/<id>/board`。Python backend 需獨立列入 `plugins.enabled`；Desktop 端啟用與後端啟用分開；`ctx.socket` 在 OAuth remote 是 no-op，需 bounded polling fallback。Remote backend 還需在 Desktop 本機安裝 desktop half（同文 :2208–2216）。這些是 source/doc，未 runtime 試用或證明可取得 Kanban 資料。
- `website/docs/user-guide/features/plugins.md:1080–1105` 文件化 injection 限 plugin 所在 profile，須授權／已知 session，並非可任意跨 profile 投遞；候選介面需隔離驗證，不可宣稱能直接叫醒另一 profile。
- 以上觀測只針對固定的本機 Hermes commit；**最低相容版本尚未證明**。

## 解除阻擋所需證據

提供可重跑、完整環境隔離且可審查的 P0 補測；列明 manager/default 身分、source／版本、清楚標示 mock 與真實流程；對五項 blocker 的正反案例各提供 ID、關鍵輸出及清理結果。優先採「真 manager profile + 控制子卡訂閱路由 + review/rework/fresh PASS + 單一有效回報 + restart/crash/duplicate + 真 Desktop 公開 SDK」單一端到端。不能安全測或公開介面不存在時維持 fail-closed，提出取得測試環境／請上游增加穩定介面／經使用者決定縮減功能三個選項，而不是降級門檻。