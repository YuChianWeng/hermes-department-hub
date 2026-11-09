# Department Hub｜路由補證獨立審查與下一步

- 審查卡 `t_e68ef120` / run 381；對象 `t_e22ed05f` / run 377，原 P0-R `t_369a6f8c` / run 370。
- **結論：有限範圍的既有路由證據可信；整體 P0 未通過，P0-R 不解除，M1 不放行。** 這是證據審查，不是新的 live E2E 或產品驗收。
- 所稱兩類 child 是「建立時帶 parent」與「事後動態 link」**兩種建構路徑**；實際收到的都是 child `completed`，不是已測兩種不同事件種類。`review_requested`／`changes_requested` 未在此輪跑通。

## Findings（先列尚存阻擋）

1. **[P0 / 安全] 繼承會實際穿透；受控拓樸不是強制權限邊界。** 反例 event 21/25 都喚醒 default，並落地 default 工具 comment。現有證據只支持受控 DAG：default 不訂閱 work DAG，且 report detached。任意 CLI/UI 建卡、link、decompose 或 creator 繼承能否被不可繞過地約束尚無證據。事後移除訂閱有 race；shared board 不具 child metadata 保密。不得用該補證宣稱安全隔離或上線。
2. **[P0 / 完整性] acceptance 與 crash/retry 仍未驗證。** report `t_8d4c4eea` 是測試者以 CLI `complete --force` 完成（`final-stage/cli.jsonl:6`），並非 manager 驗證 fresh independent PASS 後的 final。錯 owner 事件維持 pending 不是明確拒絕、處理成功或恢復證明。wakeup admission、comment 和 cursor 都非 application completion ACK；不可推導 exactly-once。
3. **[P0 / 介面與驗收] 沒有真 Desktop、完整公開 adapter、版本下限與端到端授權證據。** 受限 prototype 可先於隔離環境開發測試；但任何部署及 P0 gate 仍需真 Desktop／SDK/backend、適用授權、必要的安全路由與資料完整性、獨立驗收。不能用 mock UI 視同真 Desktop，也不能以真 Desktop 尚未驗收作為所有隔離 prototype 禁令。
4. **[證據用語邊界，避免過度宣稱]** wrong-owner 最終 snapshot cursor=16、event=17 且無 wake／工具／comment，直接證明的是觀察終點尚未確認和未見處理；光憑最終 cursor 無法排除暫時 claim 後 rewind。固定 commit 的 `gateway/kanban_watchers_notifier.py:214–220,246–248,302–316` 在 session owner 不符合時設計為 claim 前跳過，與觀察一致，但沒有逐 tick 的 claim trace，因此「從未 claim」應視為 source 支持的解釋而非單由 snapshot 獨立證成。

沒有發現足以推翻這批**限定 completed routing** 核心結論的證據衝突；上列不成立或未測的更強主張一律不得引用為通過證據。

## 封存與審查方法

- `sha256sum .research/p0-route-followup-evidence.zip`：`c88d8891136a1f6e41b218d7786d61665a4e283c6623c8238fd9c2ba6ce0cc93`，與交接相同；`unzip -t` 全部 OK；59 entries。ZIP 內報告與外部報告 byte-identical。
- 封存內 manifest 列原 lab 152 項，ZIP 收錄其中 57 項且逐項 SHA-256 全相符；另外 ZIP 含報告及 manifest。其餘 95 項不在 ZIP，**不能聲稱整個原 lab 全量封存**。三個關鍵 DB 被省略的 WAL 在原 manifest 的大小均為 0，SHM 亦未封存；本次只以封存中的 DB 主檔與封存日誌核對，不憑此斷言未收錄檔案內容。本批 report/DB、model logs、CLI/error 和 audit 均在封存並匹配 manifest。
- 先閱讀 `scripts/audit.py`，確認它會寫 `audit.json` 和 manifest，因此只對 ZIP 在本卡 scratch 的合成副本執行：`env -i HOME="$HERMES_KANBAN_WORKSPACE/review-copy" PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I review-copy/route-lab-377/scripts/audit.py` → exit 0、38/38。這是**相同既有證據的離線核對**，不是 38 次 live 測試。副本 script 的 `/proc` 檢查按副本路徑比對，不能當作原 lab 現在無 process 的獨立證明；原先停機狀態以封存 `cleanup.json` 和原 audit 的當時記錄為限。
- 額外以獨立編寫之 scratch SQL 檢查，`sqlite3 mode=ro&immutable=1` 讀 ZIP 提取後的合成 DB，從 sessions、messages、task_events、kanban_notify_subs、task_comments 重建事件／session／工具／comment；另核對 HTTP JSONL 與 model wire 摘要，輸出 `INDEPENDENT CHECK OK`，exit 0。獨立檢查腳本位於本卡 scratch `independent_check.py`，非產品程式，未啟動 gateway、lab、模型服務或網路。
- 本機 source `git rev-parse HEAD` 為 `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`，和補證聲稱的固定 commit 一致；以 installed 文件作離線 source 對照，不代表最新版或最低相容版本。

## 原始資料逐項交叉核對

| 項目 | 我的核對結果與來源 | 限制 |
|---|---|---|
| 原 session 身分 | `evidence/http.jsonl:1–2` 兩個 bootstrap 200，各自 header 為 default `api-0cbeb59d02cdeb44` 與 manager `api-ce71a2ebe36fdb0d`；兩個獨立 profile 的 `state.db` sessions 各自記錄 `profile_name`、`api_server`，manager wake 及工具均在原 manager ID | 真 gateway/profile/session + deterministic synthetic model；不證真模型的獨立判斷。|
| create child | board event **8** `t_de5b37a8`；訂閱 owner `manager`、chat 原 manager ID、cursor 8；manager messages 11/13/14 為 wake、`kanban_show`、`kanban_comment`，board comment 2 author manager；`model.jsonl` 含 wake tool schemas 和 `final_after_tools` | 一次 bounded completed 觀察；comment 是合成 receipt，非 acceptance。|
| dynamic link child | event **12** `t_0bc551ad`；manager 原 session messages 16/18/19 對應 wake/show/comment；board comment 3 author manager；`final-stage/cli.jsonl:3–5` 確有 create、link、complete | 同上；不是保證全部建卡者／事件。|
| default 陰性窗與正控 | default DB 完整收集的 messages 對上述兩個 child 的 `[kanban] … completed` wake 為 0，JSON 結構化直接工具 target 也為 0；detached report event **14** `t_8d4c4eea` 在 default messages 7/9/10 出現 wake/show/comment，board comment 4 author default | 僅此二 child completed、此次保留的觀察窗；不等於資料保密或其他事件永不觸發。|
| 實際 metadata exposure | default message 9 的 `kanban_show(report)` tool result `worker_context` 的 Recent work 含兩個 safe child IDs `t_de5b37a8` 和 `t_0bc551ad`；是 report tool result 的內容，**不是** default 對這兩 child 的直接 `kanban_show` target | 已明確推翻「default 不看見任何 child metadata」，也不是證明全部機密已暴露。|
| default inheritance 反例 | event **21** `t_672b0bf7` 與 **25** `t_a0c1eac8`：各有 default-owned inherited subscription、default wake、show/comment，board comment 5/6 author default；CLI 顯示 create-parent 及後續 link | 使用合成資料、明確故意 default 訂閱 unsafe root。反證自然隔離，不代表每個安全拓樸都洩漏。|
| 錯 owner | `final-stage/cli.jsonl:8` 接受 `manager` owner + default-only `api-7ed08b54bf27e574`；event **17** `t_c5e38c84` 的最終 cursor **16**；兩個 store 均無該 task wake 或直接工具，board 無 comment | 在此觀察終點 pending，未提供 subscribe-time rejection／error receipt；claim 時序區分見 finding 4。|
| 鑑權負例 | `http.jsonl:3` 錯 key 401；`continuation/wrong-session-get.json` 對 default-only session 的 manager GET 404 | 不推廣為全部跨 profile 安全隔離。|
| raw session ID 反例 | `http.jsonl:4` 把 default raw ID 作 manager chat header 得 200；合成 DB 中兩個 profile 分別存在相同 raw ID，manager store 記錄 `negative-wrong-session` 而 default store 保留自己的 bootstrap | 初始拒絕 assertion 失敗，不等於 manager 成功讀取 default transcript。必須綁 `(profile, session)` 及來源，不能用 raw ID 當全域權限 token。|

兩種正例雖有 `model.jsonl` 的 tool result 回傳及真實 DB comment 互相佐證，仍只是 fixed synthetic model 在真 gateway 的工具 routing；沒有真模型推理、dispatcher-driven review/rework、最終使用者訊息或 acceptance。

## Live stage errors 與隔離證據的限定

1. 首次啟動前 Python symlink 被遮蔽 rc 127；加入已安裝 uv runtime 唯讀 bind 後才繼續。初始 N2 預期拒絕但 HTTP 200，`evidence/error.json` 留存 rc 1。不可把這個負向測試重標 PASS。
2. continuation 因 setup parent `t_f13b8b4c` 尚未完成，child `complete --force` 被拒 rc 1；證明 parent edge 是依賴，不可用 pending 主管卡當組織容器。補完 setup 後才繼續，未靠修改 DB 繞過。
3. final-stage 六個 routing assertions 均 true，但後續匯出 SQLite message BLOB 的 `json.dumps` TypeError，整段 rc 1；因此原來安排的正向 HTTP session metadata GET 根本沒跑。`final_stage.py:43–51` 與 `error.json` 可查；不可稱三段 live 均 exit 0 或 turnkey replay。
4. 離線 audit 首版將 report tool result 的 recent-work child 字串誤判成直接 target，後改解析結構化 target；必須保留真正的 metadata exposure，不得藉新比對邏輯宣稱無洩漏。
5. 保存的 preflight 為獨立 mount/net/pid namespace、only loopback、`routes=[]`、home masked、source read-only；保存的分段 cleanup 顯示 process reaped、listener 無資料列。gateway SIGTERM rc 1、model -15，不是 graceful exit 0；relay startup warning 存在。沒有獨立重跑 live、沒有以正式 board hash 證明零接觸。報告中首次 uv 綁定修正的 rc127 依原報告敘述，ZIP 未見獨立的首次 launcher console receipt，故不把這部分升格為獨立逐行確認。

## P0-R run 370 各 finding 精確變化

| 原 finding | 現在 | 仍缺什麼 |
|---|---|---|
| **1 真 manager profile wake** | 原「完全未證實」已被補上兩條 completed child 路徑：獨立 manager session 的真 wake、工具與 manager-authored board comment。這個狹義證據缺口已補。 | synthetic model，沒有真管理判斷或完整安全／acceptance；不能據此解除整體 gate。|
| **2 訂閱歸屬與失敗不上報** | create/link 的安全拓樸與 detached 正控獲有限實證；風險從 source 推論升級成 default 兩次真實 wake 的反例，且 report 的 worker_context 洩露 child metadata。 | 原子 no-inherit／跨任意 mutation 強制、review failure/rework/fresh PASS 前無 final、資料可見性均未證；此 blocker 保留。|
| **3 review/final 去重** | 本輪未補；report 被測試器直接 complete。 | 真 dispatcher、reject→rework→新 revision 獨立 PASS→manager acceptance→單一有效 final，含 stale verdict、duplicate、restart/競態。|
| **4 crash/recovery** | 多了錯 owner session pending 的具體反例及需區分 cursor/receipt 的邊界。 | 原 run 370 的連續錯誤後 subscription 刪除風險未解除；離線補證未故障注入，claim/admission/執行中 crash、重送／亂序、reconciliation、dead-letter 未證。|
| **5 公開 adapter/Desktop** | 固定 commit 的 profile multiplex、CLI subscription/create/link/complete、錯 key 與 foreign-session GET 等有窄實測。 | 正向 HTTP metadata GET 未執行；event-watch durability、原子 recipient policy、多 board、正式授權、版本下限、真 Desktop/SDK/remote OAuth 不可由此 PASS。|

## 最小可執行下一步（建議，不改 gate）

**選擇：先做隔離、一次性受限 research prototype；不是開始既有 gated M1。** 用新 scratch／新的 synthetic `HOME`、board、sessions 與無外網 namespace，先建立 fail-before-start 的環境檢查；只用合成資料、公開 CLI/API 和明確列出的能力，禁止正式資源、網路、push、產品安裝。現有 M1 `t_a1c1b08b` 的前提仍為 P0-R PASS，此研究工作不得繞過該依賴，也不暗示可部署。

1. **自主可做（開發／測試）：** 用一個可信單寫入者操作已知測試 DAG，固定 manager-only work 訂閱、完全 detached 的 default report，關聯另存在 prototype 自己的資料中；CLI 輸出記錄 exact IDs、source commit、session owner 與實際狀態。對 create/link/decompose、錯 owner、報告被誤連結的負例採 fail-closed：測試拓樸違規時停止，不宣稱可以攔截其他任意使用者操作。先驗證隔離及清理，再使用真 gateway + synthetic model 補測 completed/review_requested/changes_requested；多次完整 fresh rerun，保留 live stage exit 和所有失敗。
2. **自主可做（acceptance/recovery 實驗）：** 在 prototype 的獨立持久 store 建 run/artifact/verdict 綁定、唯一 operation key、receipt/outbox、reconciliation、有限 retry/可觀察停滯狀態；合成 worker/reviewer 在隔離環境跑 reject→rework→fresh independent PASS→manager 查權威狀態後接受→一個有效 final。對 crash 在 claim 前後、admission 後未執行、工具效果後 receipt 前、final effect 後本地 commit 前等點注入，另測 replay、亂序、stale PASS、錯 artifact、重複 request 和並發。明示哪些效果透過文件化的 idempotency key 可去重，哪些外部效果的原子性／未知結果尚不可證；不可把自建 receipt 等同核心 ACK，也不可空喊 exactly-once。失敗必須明確停在不可上報並保留證據。
3. **公開介面的硬邊界：** 固定 commit 的 `hermes_cli/kanban_db.py:1325,1364–1399,1589` 顯示 create/link 會繼承；`website/docs/user-guide/features/hooks.md:386–397` 的 Kanban lifecycle hook 在 board commit 後且 observer 回傳被忽略。依目前查得的公開介面，**沒有已證可用的同交易 veto/no-inherit/exact recipients，不能僅靠事後 observer／取消訂閱保證任意創建者永不穿透**；`creator_task_id` 還有獨立繼承路徑。也尚未證實能透過公開 durable ACK/replay 完成 crash 無遺失及外部副作用原子提交。這是對此固定 commit／已檢查路徑的能力邊界，不宣稱數學上排除所有替代架構。若 prototype 不能透過受支援介面提供所需安全性，對該保證停下並產出所需 API 合約，而非改核心私有函式或直接改正式 SQLite。
4. **需要 maintainer／負責人決策而非研發自行降規：** 正式威脅模型能否接受「單一受信任寫入者、shared board 可讀、無任意 CLI/UI 寫入保証」；zero child metadata 是否必需；是否允許提議／授權 Hermes core 的原子 recipient/no-inherit（涵蓋 create/link/decompose 及 creator inheritance）、owner 驗證與 observable undelivered／durable receipt/replay 或 scoped query/ACL；若不允許且需求仍要求對任意寫入者強制隔離，該產品承諾不得發布。隔離研究不預設這些縮減已獲批准。
5. **部署前不變：** 在已選定的正式 scope 上，補安全拓樸的負向與 race、資料完整性、crash/replay、失敗審查不上報及獨立 fresh PASS、真 Desktop unified package + SDK/backend round trip 與適用 local/remote OAuth，完成另一輪獨立複核；任何正式 profile／服務／board 更動、部署或公開推送另需相應授權。詳見原 `p0-review.md` 的原始 P0 條件。

## Gate 與操作界限

本審查時 `t_369a6f8c` 仍為 **blocked**；`t_a1c1b08b` 仍為 **todo**，有該 blocked parent。未更改它們；本審查卡的完成僅交付證據分析與建議，**不是 P0 PASS、不是 M1 授權**。本次未修改產品、正式 profiles、正式 gateway/看板或 FreshTape，未啟動 lab、未複製正式 sessions、未聯網、未推送。允許的 board 操作僅本審查卡的狀態交接與唯讀查詢。
