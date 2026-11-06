# Department Hub｜P0 路由補證（調查結束，**不是 P0 PASS**）

- 卡 `t_e22ed05f` / run **377**；對照 `p0-review.md` Finding 1/2/4/5、`p0-report.md` 與計畫 §24–39。
- Installed Hermes source **`f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`**；既有 venv Python **3.11.15**。版本標籤沿用前報告 v0.22.6，但本次相容性依據是 exact commit，未證最低版本。
- 新 lab：`/home/ubuntu/projects/hermes-department-orchestrator/.research/route-lab-377`（下稱 `$LAB`）。未重用舊 lab、未複製正式 session/config/credentials。
- **結論：真正 manager profile 的 child event wake 與 Kanban 工具執行已補證；訂閱繼承的產品級政策、故障恢復、acceptance 與 Desktop 仍有缺口。保留 `t_369a6f8c` blocked、`t_a1c1b08b` todo 等待。**

## 1. 本輪已驗證與未驗證

### 已驗證：真 gateway + 真 profile scope + 真工具，模型為 synthetic

1. 單一真 `hermes gateway run` multiplex `default` / `manager`，模型服務僅回確定性的工具呼叫；沒有真模型判斷、沒有 provider 花費。
2. 透過正式 `/v1/chat/completions` 與 `/p/manager/v1/chat/completions` 各自建立 session，profile 各自獨立 `state.db`；不是把模型名稱叫 manager。
3. `create --parent` 與後續 `link` 都能將 manager 訂閱繼承給 child；child completed 後，原 manager session 真的執行 `kanban_show`、`kanban_comment`。實際模型請求包含 tools，下一輪帶回成功 tool results；comment author 是 manager。
4. 這兩張 child 的 wake transcript 與直接 target child 的 tool results 在 default store 為零；default 接收 detached report 的正向控制成功，因此不是 default endpoint 沒有運作。
5. 錯 profile key HTTP 401；manager 對只存在 default 的 session metadata 查詢 HTTP 404。另錯 subscription owner/session 的事件未被 claim、沒有任何 wake/tool/comment，事件留待處理，**不是已執行成功或有 explicit error receipt**。
6. 反例實證：default 訂閱父卡後，新建 child 及動態 link child 都會真的喚醒 default 並留下 default 工具 comment。`auto_subscribe_on_create: false` 並不阻止 inheritance。

### 不得據此宣稱

- 主管獨立判斷品質、完整 reject → rework → fresh PASS → manager acceptance → final。
- claim 後 crash、admission 後未完成、執行中 kill、重送/亂序、持久 ACK／dead-letter／訂閱被刪的修復。
- 對任意建卡者或任意 link 都有原子、不可繞過的子事件隔離政策。
- 完全不向 default 暴露子卡資料：`kanban_show(report)` 的 `worker_context` 會包含 manager 的 recent-work 摘要，實際 default tool response 有 safe child ID。**零 child wake 不等於零 child metadata。** Profile/shared board 不是 ACL 邊界。
- 真 Desktop package 載入、SDK/backend round-trip、OAuth remote 或多 board 原子性。
- 所有 live runner exit 0：本輪有三個保留的 stage error，見 §6。後續 **38/38 是離線交叉核對，不是 38 個新 live 測試**。

## 2. 身分、owner 與事件 ID（全部合成資料）

| 對象 | exact ID / scope | 可核對證據 |
|---|---|---|
| default 原始 session | `api-0cbeb59d02cdeb44` / `profile_name=default` | `evidence/session-identities.json`、`http.jsonl` response header、`evidence/audit.json` |
| manager 原始 session | `api-ce71a2ebe36fdb0d` / `profile_name=manager` | 同上；自己的 `os-home/.hermes/profiles/manager/state.db` |
| default-only 負向 target | `api-7ed08b54bf27e574` | `evidence/continuation/http.jsonl`；不在 manager store |
| setup/work dependency root | `t_f13b8b4c` | manager wake / receipt；非保持 running 的主管容器卡 |
| P1 新建 child | `t_de5b37a8`；completed event **8** | owner `manager`，chat `api-ce71a2ebe36fdb0d`；manager comment **2** |
| P2 動態 link child | `t_0bc551ad`；completed event **12** | owner `manager`，同一原始 manager session；manager comment **3** |
| detached report control | `t_8d4c4eea`；completed event **14** | owner `default`，default comment **4** |
| 錯 owner/session subscription | `t_c5e38c84`；completed event **17** | owner `manager` / target default-only session；cursor 未涵蓋 event，無執行 |
| unsafe create 反例 | `t_672b0bf7`；completed event **21** | inherited default owner，default 真 wake + comment |
| unsafe link 反例 | `t_a0c1eac8`；completed event **25** | inherited default owner，default 真 wake + comment |

完整事件、owner、cursor、wake message ID、tool_call_id、tool result、comment 與 session 身分放在 **`$LAB/evidence/audit.json`**；原始事件快照在 `evidence/final-stage/final.json`；原始模型 wire-request 摘要及真正 tool returns 在 `evidence/model.jsonl`。工具落地不僅以 synthetic 的 `SYNTHETIC_ACK` 字串認定。

P1/P2 的原 session 各收到恰一個相關 completed wake 是觀察窗內事實；不是跨 crash/replay 的 exactly-once 保證。Default 陰性觀察含後續 report、unsafe 控制測試期間；audit 的 target 匹配解析 JSON 的 `task.id` / `task_id`，不是粗糙字串搜尋。

## 3. 正反案例

| 案例 | 動作與真實結果 | 判定 |
|---|---|---|
| 正 P1 | manager-owned root 已建立訂閱；create child with parent；完成 setup root 後完成 child；event 8 → 原 manager session → show/comment；default 無該 child wake | routing verified |
| 正 P2 | 新 child 後動態 link 到同 root；event 12 → 同 manager session → show/comment；default 無該 child wake | routing verified |
| 正控制 | detached report event 14 → default → show/comment | endpoint verified；非 acceptance verified |
| 反 N1 | default synthetic API key 呼叫 `/p/manager/v1/chat/completions` | **401** `gateway_auth_failed`，沒有模型執行 |
| 反 N2b | manager key GET `/p/manager/api/sessions/api-7ed08b54bf27e574` | **404** `session_not_found` |
| 反 N3 | CLI 訂閱 owner=manager，但 session 僅屬 default；完成 event 17 | CLI **接受訂閱**，notifier 不 claim、無 wake/tool；保留待處理，**不是 subscribe-time rejection** |
| 穿透反例 create/link | default 訂閱 unsafe root；新建或動態 link child；event 21 / 25 | default 真的執行工具；否定「自然只由主管接收」 |
| 失敗的初始 N2 | manager key + `X-Hermes-Session-Id: api-0cbeb59d02cdeb44`（當時該 raw ID 只在 default） | **200**，在 manager store 建立同 raw ID 的 manager session；原始預期「拒絕」的 assertion **FAIL，保留** |

### Raw session ID 的重要限制

初始 N2 不能重標為 PASS，也不能直接稱為跨 profile 讀取成功：read-back 顯示兩個 profile 各自擁有同 raw ID，manager 的新 session 沒有搬走 default row。對照 `gateway/platforms/api_server_openai_routes.py:899–936`，顯式 header 可指定 profile-scoped ID，並非全機唯一授權 token。不得把 subscription 的 raw chat ID 當成全域唯一 session authority；需要 `(profile, session)` 配對及原 session 建立來源。N2b 故意使用另一個 fresh default-only session，且沒有再將其拿去對 manager 的 chat POST，避免污染負向樣本。

本輪沒有驗證所有 malformed owner/session 組合都會明確拒絕；已證 owner 不合的 subscription 可以被 CLI 建立但不執行。若產品要求「建立時拒絕錯 owner」而非「持續 undelivered」，現有公開 CLI 路徑仍缺此契約。

## 4. 訂閱繼承：實證範圍與最小選項

### 可運作的窄 routing topology（已測，但不是完整安全產品）

- Work DAG 僅 manager 訂閱；default 僅訂閱**完全無依賴邊連向 Work DAG**的 detached report 卡。
- Work child create/link 都繼承 manager，default 不接 child wake；report 完成時 default 可被喚醒。本輪 P1/P2 + control 支持這一窄命題。
- `root` 的 parent edge 是**前置依賴**而不是部門包含關係。需完成 setup root 才能完成 child，不能拿「等待 manager 最終接受的 pending 主管卡」直接作所有工作卡的 parent。本輪首次 child complete（即使 --force）被 kernel 拒絕，證據保留。部門 membership / report association 應由插件自己的關聯資料表示，不偽裝成依賴邊。

### 為何仍是缺口

1. detached report 若未來被錯誤接到 DAG，上游 default 訂閱仍會繼承。本輪只證正常拓樸，沒有公開原子 veto／no-inherit 屬性或 plugin enforcement。不能「建立後刪 default 訂閱」當安全方案：建立與刪除間存在 notifier 競態，dynamic link 也同樣有風險。
2. 全域 shared board 的 `kanban_show` 會把 recent-work 摘要放進 report 的 worker_context；即使沒有 child event wake，default 仍可能看到 child metadata。
3. 本輪 report 是測試器直接完成的 synthetic control，**不是 manager 核對 fresh independent PASS 後才完成的 final**。因此「只能上報已接受結果」尚無實證。

**建議最小可行選項（待 Oneal／orchestrator 決策，未自行縮減需求）：**採「單一部門、受信任單寫入者、detached report、部門關聯另存插件、共享 board 可讀」的 routing-only slice；明列不保證任意 CLI/UI link 的隔離，先補 acceptance/recovery gate 再談產品。若要求對任意建卡者也保證不穿透，停止建置並請上游提供下列正式 hook。

**仍缺的公開契約／hook：**

- 建卡／link 同交易的 per-edge/per-task `inherit_notifications=false` 或原子指定 exact recipients；需同時覆蓋 create、link、decompose，不能只有事後 observer。
- 對訂閱 `(board, task, profile, session)` 的公開驗證／拒絕回應、owner 轉移與可稽核 route refusal/undelivered 狀態；不能把 silent pending 當處理成功。
- 穩定公開事件 identity、application completion ACK／重放／補償／dead-letter 面，支援 plugin receipts 對帳；wake admission 不等於處理完成。
- 若 zero child metadata 也是需求：需 scoped task query / context redaction 或正式 task ACL；目前 profile/shared board 不是此安全邊界。

不建議任何正式 SQLite UPDATE、不引用 `_inherit_notify_subs` / collector / 私有 core function 作產品 API。本輪 SQLite 僅 `mode=ro` 交叉核對 lab 產物；mutation 全經 CLI／gateway Kanban 工具。

## 5. 文件化公開介面與限制（exact installed source docs，離線）

本輪禁止外網，沒有 fetch 最新線上版本；使用 installed commit 的官方文件快照。權威線上入口仍是 https://hermes-agent.nousresearch.com/docs 。相容性僅承諾本 commit 的觀察。

| 公開介面 | 已用輸入 / 輸出 | 文件 / 限制 |
|---|---|---|
| config profile home | lab `$HOME/.hermes` + `profiles/manager/{config.yaml,.env,SOUL.md}`；`profile list` 確認不同 profile | `website/docs/user-guide/profiles.md` identity files、§How it works；未複製別的 profile |
| multiplex API | `/v1/chat/completions`、`/p/manager/v1/chat/completions`；distinct keys；HTTP 200 + `X-Hermes-Session-Id` | `features/api-server.md:795–817`；不同 profile key 401 verified |
| metadata GET | `/p/manager/api/sessions/<default-only-id>` → 404 | 同文 `:719–729`；正向 session GET 因最後匯出 error **未執行**，不可宣稱已 read back |
| Kanban CLI | init、create --parent --assignee --json、show/notify-list（工具 show 亦落地）、link、complete --force --result | `features/kanban.md:937–998`；parent 未完成時 --force 也被拒 |
| subscription CLI | `notify-subscribe <task> --platform api_server --chat-id <sid> --notifier-profile <owner> --delivery-mode wake` | owner routing 文檔同文 `:1297–1333`；`--notifier-profile` 的 exact help 已在 `evidence/cli.jsonl` 記錄；錯 target 不保證 subscribe-time reject |
| allowed toolset | `platform_toolsets.api_server: [kanban]`；實際 wake schema 有 show/comment；原工具執行成功 | 同文 orchestrator toolset 章；模型 wire log 是實際 schema，不依 model 自稱 |
| event watch | `hermes kanban watch --kinds completed,blocked,...` 文件化 | 同文 `:980–982`；本輪未實跑 event watch stream，不把它當可靠 ACK/replay API |
| plugin hook | kanban lifecycle observers | `features/hooks.md` 與先前 P0 source map；observer 回傳值不是 create/link veto，本輪未加插件 |
| Desktop unified plugin | plugin.yaml + desktop/plugin.js + dashboard/manifest + backend router；ctx.rest | `developer-guide/desktop-plugin-sdk.md`，沿用 P0-R 邊界；**本輪沒有真 Desktop runtime，仍獨立 gate** |

API synthetic key 與 synthetic model placeholder 都是 lab 新建，不是 provider 切換或正式憑證。沒有執行 install、插件 lazy dependencies、push、對外訊息、FreshTape 修改。

## 6. 有界探勘與失敗紀錄（不掩蓋、不重試同失敗輸入）

本輪只有一個新 lab、一個 routing 方案探勘；過程有 setup 修正及分段續跑，**不是一次乾淨 exit-0 的 turnkey E2E**：

1. `sandbox.sh` 首次因遮蔽 HOME 後 venv symlink 的 uv interpreter 不可見，`env ... python: No such file or directory`，rc **127**，尚未啟動 Python/gateway。只增加 uv 的已安裝 Python runtime read-only bind。
2. 首段建立真 session 後，初始 N2 header 預期拒絕但得到 200；rc **1**。保留 `evidence/error.json`、`assertions.json`、`error-snapshot.json`。停止並 reaped gateway/model；沒有重送這個相同負向輸入。
3. `sandbox-continuation.sh` 只繼續未跑案例；N2b GET 404 成功，首個 child complete 因 parent 仍 ready 被拒，rc **1**。保留 `evidence/continuation/error.json` 與 CLI stderr；沒有改 SQLite／force 繞過 dependency。
4. `sandbox-final.sh` 從既有 lab 卡續測：先完成 setup dependency，再跑尚未完成的 child wake、link、report、錯 owner 與兩種穿透反例。六項 routing assertions 全成功；最後 `SELECT * messages` 匯出碰到 BLOB `display_identity`，`json.dumps` TypeError，rc **1**。因此後面的正向 HTTP metadata read-back 未執行。保留 `evidence/final-stage/error.json`。
5. 停止全部 live 實驗，不再啟動 gateway。離線 `scripts/audit.py` 選取可序列化 transcript 欄位；首次把 recent-work 中偶然提及 child 的字串誤算為直接 child tool target，assertion fail。檢視實際 JSON 確認 target 是 report / unsafe child 後改用 structured target 比對，並在本報告保留 metadata exposure 發現。最終 **38/38 audit checks，exit 0**；這是對同一批真 gateway/tool 證據的核對，不新增 live 測試。

### 實際執行命令／重現入口

所有呼叫限定 lab，完整參數、rc、stdout/stderr 在各段 `cli.jsonl`；HTTP inputs/status 在 `http.jsonl`。以下為**本輪原始執行順序**，不是對既有 lab 可重複寫入的 idempotent 指令：

```bash
LAB=/home/ubuntu/projects/hermes-department-orchestrator/.research/route-lab-377
"$LAB/scripts/sandbox.sh"                # 原始 stage：N2 mismatch → exit 1（保留）
"$LAB/scripts/sandbox-continuation.sh"   # N2b + 建卡，parent guard → exit 1（保留）
"$LAB/scripts/sandbox-final.sh"          # route assertions 成功；BLOB export → exit 1（保留）
/home/ubuntu/.hermes/hermes-agent/venv/bin/python "$LAB/scripts/audit.py" # offline 38/38, exit 0
```

**不要直接重跑現有 lab 的 live scripts**：final stage 刻意綁定保留的 synthetic IDs，且 runner 會新建 session/card；不是 fresh-portable harness。新 reviewer 應以新目錄重建同配置／namespace，從公開 CLI `--json` 捕捉新 task IDs，不複製 session DB。可重現的核心 CLI 序列如下（`hk` 必須是在 namespace、通過 preflight 的 `scripts/cli.py` wrapper；不可在正式環境照貼）：

```bash
hk kanban init
hk profile list
# 先用各 profile 自己的 synthetic key，POST 上述兩個 /v1/chat/completions endpoint，捕捉各 response 的 session header。
hk kanban create manager-work-root --assignee manager --json  # 捕捉 ROOT
hk kanban notify-subscribe "$ROOT" --platform api_server --chat-id "$MANAGER_SESSION" --notifier-profile manager --delivery-mode wake
hk kanban create created-child --assignee manager --parent "$ROOT" --json # 捕捉 C1
hk kanban complete "$ROOT" --force --result SYNTHETIC_ONLY
hk kanban complete "$C1" --force --result SYNTHETIC_ONLY
hk kanban create linked-child --assignee manager --json # 捕捉 C2
hk kanban link "$ROOT" "$C2"
hk kanban complete "$C2" --force --result SYNTHETIC_ONLY
hk kanban notify-list "$C1" --json
hk kanban show "$C1" --json
```

沒有另外宣稱「fresh-clone 單指令 replay 已驗證」。可攜重播 harness 是後續驗收品質缺口，不為湊 exit 0 超出本輪有界探勘繼續啟動。

## 7. Fail-before-start / OS 隔離與清理

- `unshare --user --map-root-user --mount --net --pid --fork --kill-child --mount-proc` **實際成功**。Namespace 內 mount private；把正式 `/home/ubuntu` 換成 tmpfs，只重新掛回新 lab 與 read-only installed source / uv Python runtime。
- `/run`、`/tmp` 各自 tmpfs；正式家目錄的 profiles、board、credentials、gateway sockets 不可見。source/venv 是 read-only dependency，不是正式 HERMES_HOME。
- network namespace 只有 **lo**、`ip -j route` **[]**，只開 `127.0.0.1:18231/18232`；外網沒有路由，沒有調用 web/remote provider。此輪不依賴 Python monkeypatch 作網路安全邊界。
- `env -i` allowlist；啟動每個 Hermes CLI / gateway / model 前檢查 HOME、HERMES_HOME、HERMES_KANBAN_HOME/DB、所有 XDG、TMPDIR/TMP/TEMP 的 resolved path 均在 lab；排除 task/run/session/delegated pins、SSH socket、D-Bus、HTTP proxy。socket direct path 與長路徑 fallback 的 XDG runtime / TMPDIR 均在 lab，且正式 /run 不可見。
- 設 `HERMES_DISABLE_LAZY_INSTALLS=1`、`UV_OFFLINE=1`、`PIP_NO_INDEX=1`、禁 bytecode。兩個故意污染的 preflight inputs：正式 DB path、正式 XDG runtime path，均 fail-before-start（沒有為負向檢查啟動子 process）。
- 檔案 `preflight.json`、各段 `process-env.json` 保留 actual child env；`cleanup.json` 全部 reaped，`ss -lntup` 只有表頭無 listener；offline audit 再查 host `/proc`：lab runtime process **[]**。
- gateway SIGTERM returncode 為 1（console 有 shutdown warning），model -15；不能把 gateway rc=1 說 graceful exit 0，但確實已 wait/reaped、PID namespace 隨 launcher 結束清除所有後代。
- Installed relay 在 gateway startup 有 `initialize() got an unexpected keyword argument 'additional_plugins_toml'` warning；保留原 log。沒有嘗試安裝、更新或修 relay，不影響已觀測的 Kanban routing，亦不推定其他功能健康。
- 沒有發現 lab 指向正式 board 的 pin，因此未觸發 stop-on-production-contact。不能用正式 board hash/no-churn 佐證（本輪沒有讀正式 DB）；隔離證據是遮蔽 mount + allowlist paths + namespace。協調面的 kanban_show/heartbeat/comment/complete 是本任務授權之 board lifecycle，**不是拿正式 board 當 lab**。

## 8. 交接與 gate

- 產物：本報告、`route-lab-377/scripts/`、分段原始 error／stdout／CLI／HTTP／model tool logs、synthetic board/session DB、`evidence/audit.json`、`evidence/sha256-manifest.json`、合成證據封存。
- **Finding 1：有實質補證**（真 manager original session + tools）。Finding 2：兩種 inheritance 與 routing topology 已實測，但 atomic/enforced ownership、metadata scoping、acceptance 尚缺。Finding 4：僅錯 owner 不被 claim 的負向結果，不是 crash/retry 修復。Finding 5：公開 CLI/API 一部分已實測，Desktop／最低版本／完整授權仍未過。
- 不修改／解除 P0-R，不開始 M1。本調查卡完成只代表有可核對結果；由既有獨立 reviewer 評估補證，不能把本報告當 `REVIEW: PASS`。
