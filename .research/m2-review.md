# Department Hub M2-R — 獨立整合與管理介面驗收（v5）

- 審查卡：`t_750e8a14`（reviewer，唯讀）
- 審查時間：2026-11-06 06:17–06:45 UTC（Asia/Taipei 14:17–14:45）
- 受審候選：`~/.hermes/kanban/workspaces/t_68287415/candidate`（56 檔）
- 封裝：`~/.hermes/kanban/workspaces/t_750e8a14/department-hub-m2-v5-candidate.tar.gz`
  - sha256 `e5cba34c638ecdd516df24937925a853326a541cac7d0c3f904dbf31ce885faa`
- 逐檔 manifest：`candidate-manifest.sha256`
  - sha256 `5280ab3e578529a01f9c15a55891845ba204f5eba903d3fab2d64968acf56247`
  - 審查前後皆 **56/56 OK**
- 對齊報告：`t_68287415/ALIGNMENT-M2-v5.md`
  - sha256 `6347e9bc1e8b66d9e3f0834431134a912f89dfbe2358560d67d7360031c12f8a`
- 證據包：`~/.hermes/kanban/workspaces/t_750e8a14/m2r-v5-evidence.tar.gz`
  （sha256 見 block metadata 與 `verify-v5/evidence-bundle.sha256`；內容含本報告、
  封裝、手動驗收包與全部 probe log）
- 手動驗收包：`~/.hermes/kanban/workspaces/t_750e8a14/M2-DESKTOP-MANUAL-ACCEPTANCE-v5.md`

---

## 0. 結論

| 範圍 | 結果 |
|---|---|
| 前置 M1 v5（schema 凍結＋P1-1 修復） | **PASS**（`t_090d5884`；我用受審 product 的 `delivery.py`／`plugin.yaml` hash 與我的候選逐位核對） |
| candidate 完整性（56 檔 manifest） | **PASS**（56/56 OK，審查前後不變） |
| 離線測試／契約／SDK mock／plugin validation | **PASS**（409 / 53 / 136 checks / 16 checks 0 warnings） |
| 獨立 runtime probe（真實 service＋SQLite，假 board） | **PASS**（36/36） |
| 獨立隔離 E2E（真實 kanban CLI＋dispatcher transition＋真實 service＋真實 M2 dashboard） | **PASS**（48/48） |
| 獨立 UI contract（SDK mock，非畫面） | **PASS**（60/60） |
| 後端授權／資料最小化 | **PASS**（auth 8/8 401；mutation 3/3 409 且不建 service；payload 無 chat id/platform） |
| 任務 body 指名的「整合產物」`<repo>/` | **FAIL（P1-H，仍是 stale pre-alignment 專案）** |
| 真實 Desktop rendering／gateway 載入／live wake | **UNVERIFIED — 沒有任何人在本次審查中看過畫面** |

**Overall：不能給 M2-R PASS。**
離線 candidate 是 PASS；阻擋的是任務指名的整合目錄仍 stale，以及真實 Desktop 完全沒有被觀察。
本卡以 `needs_input` block，等 maintainer 依手動驗收包安裝並提供畫面；在手動畫面出現前不得宣稱 M2-R PASS。

---

## 1. 前置驗收（M1 v5）

任務要求「確認前置驗收 PASS」。我沒有只引用 parent metadata，而是核對受審 product 本身：

- `t_090d5884` 的 `REVIEW-M1-v5-rereview.md` 結論是 **REVIEW: PASS**；它受審的是
  `t_fb24c666/product`（55 檔），manifest sha256
  `12f4392e9e78bdec244ed7b65a17e9d84846372c474c72c952f7947f47b4c5d5`。
- 我的候選 `department_lib/delivery.py` sha256
  `85e22203335f6756a93bd074fbf77c0678fb3039fc337355b0ff78c0439effb7`
  與該受審 product 逐位相同（含 P1-1 direct-boundary durable-conflict fence）。
- 我的候選 `plugin.yaml` sha256
  `f0c6328c49f34efa4a9c251a2f92e1a79013184d20053f715f4acc030a64a5d9`，版本 `0.5.0`，
  與該受審 product 相同。
- 逐檔比對 `t_fb24c666/product`（55 檔）與候選（56 檔）：
  - 46 檔位元相同（所有 M1 產品檔，含 `__init__.py`、`acceptance.py`、`store.py`、
    `delivery.py`、`kanban_effects.py`、`orchestrator.py`、`docs/m1-v5-contract.md` 等）；
  - 9 檔不同，全部是 M2 擁有的檔案：`README.md`、`dashboard/manifest.json`、
    `department_lib/dashboard.py`、`department_lib/kanban_db_source.py`、
    `desktop/department-hub/plugin.js`、`docs/m2-desktop.md`、`tests/support.py`、
    `tests/js/run.mjs`、`tests/js/.tmp/plugin.mjs`；
  - 1 檔新增：`tests/test_m2_v2_contract.py`。
- 因此 candidate 不是「另一個 M1 分支」；它就是受審 PASS 的 M1 v5 product 加上
  M2 唯讀層／UI／文件／契約測試。

---

## 2. Candidate 完整性

```text
$ sha256sum -c candidate-manifest.sha256
... 56 行全部 OK
rc=0
```

- 56 個 manifest 檔，無 `__pycache__`／`.pyc`／`.pytest_cache`（審查後已清除測試副作用，
  再驗仍 56/56 OK）。
- `tests/js/.tmp/plugin.mjs` 是 `run.mjs` 的 generated 檔；我用 source 的 import rewrite
  重新生成，與 manifest 內容逐位相同，無 stale。
- `docs/m1-v5-contract.md` 的 `plugin.yaml` 0.5.0、`delivery.py` hash 與 M1 v5 受審
  product 一致。

---

## 3. 離線測試（全部由我重跑）

環境：`~/.hermes/hermes-agent/venv/bin/python`（3.11.15）、
`PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`-p no:cacheprovider`；
Node 使用 `~/.hermes/tools/node-26.12.1-linux-arm64/bin/node`。
**注意**：系統預設 `/usr/bin/node` 是 v10，會把 ESM import 判成 syntax error；
用 v10 得到的 `node --check` 失敗不是產品缺陷。用 v26 的結果如下。

| 檢查 | 結果 | 證據 |
|---|---|---|
| 全套 pytest | **409 passed**（rc0，44.97s） | `verify-v5/full-pytest.log` |
| M2 契約測試 | **53 passed**（rc0，6.39s） | `verify-v5/contract.log` |
| pytest collection nodeid | **409**；v4.1 的 377 個 nodeid **missing=0**；新增 32 | `nodeids-v5.txt`、`nodeids-v41.txt`、`nodeids-missing.txt` |
| `node --check tests/js/.tmp/plugin.mjs` | rc0 | `node-check.log` |
| 作者 SDK mock（`node tests/js/run.mjs`） | **136 ok / 0 FAIL / ALL PASS** | `js-mock.log` |
| `hermes plugins validate`（clean staging） | **ok=true，16 checks，0 warnings** | `plugin-validate.json` |
| `hermes plugins doctor --ci` | rc0；7 tools / 2 hooks | `plugin-doctor.log` |
| standalone backend import | 由 plugin validate/doctor 的 capability probe 涵蓋；API TestClient probes 見 §5 | — |

---

## 4. 獨立 runtime probe（真實 service／SQLite）

我自寫 `verify-v5/m2_v5_probe.py`，不 import 作者測試、不呼叫作者 E2E。它使用：

- 真實 `Store`（temp SQLite）、真實 `DepartmentService`、真實 `Delivery`；
- 一個會記錄 append 的 fake `EffectPort`（v5 final 不得使用它）；
- 真實 `DashboardService` 讀模型；
- 假 board CLI（形狀與公開 `hermes kanban show --json` 一致）；
- 額外用 stock `CommentEffectPort` 重跑「delivered 是否真的可達」。

結果：**36/36 blocking checks PASS**（1 個非阻擋 observation，見 §7）。

關鍵重現：

1. **主管事件處理／防重**：bind → ingest revision 2（2 receipts）→ 同卡再 ingest
   0 新 receipt／0 applied；repair 事件只套用 2 個新 receipt。
2. **失敗審查 fail closed**：`changes_requested` 後 `department_accept` 回
   `held/review_rejected`，`department_deliver` 也回 `held/review_rejected`，
   `deliveries` 0 row、0 append、0 board comment。
3. **修正＋獨立 PASS 後單次報告**：repair → 獨立 PASS run 8 → accept `releasable`
   → release `delivered`：
   - `deliveries` 恰 1 row，state `confirmed`，schema `department-hub/final/v2`，
     generation 7，summary 是 reviewer note（含 HTML sentinel 原字）；
   - fake port `append` **0 次**、board comment **0 次**；
   - identity 恰為 `task_id/revision/verdict_run_id/artifact_sha256` 四欄。
4. **重播／重啟**：同 identity release → `already_delivered`，仍 1 row、0 append；
   關閉 Store 重開後 re-ingest 不新增 effect，release 仍 `already_delivered`，
   讀模型仍顯示 delivered report。
5. **第二世代**：新 PASS run 10（同 revision、不同 verdict run）→
   `held/delivery_identity_conflict`，`ready=false`，`stored_identity.verdict_run_id=8`
   與 `current_identity.verdict_run_id=10` 分開；原報告仍在 history。
6. **board 不可用**：讓 board 對該 task raise `BoardError('unavailable')`；task row 是
   explicit unknown，但 `department.final_report.history` 仍回 summary 與 identity；
   M1 `department_status` 也直接從 store 回 report body。
7. **v5 新邊界**：
   - `report_too_large`：DB trigger 先拒絕 >65,536 的 confirmed v2 insert；在 temp DB
     模擬 foreign/宿主 DB 移除 trigger 後插入超長 row，dashboard 回
     `report=null`、`report_state/code=report_too_large`，整個 payload 不含超長內容，
     history 只列 code；同一 row 的 readiness 因 identity 不匹配而
     `held/delivery_identity_conflict`（fail closed）。
   - `effect_raised` legacy dead-letter row → `unknown`，不是 dead-letter。
   - `not_delivered` 只在 `STATUS_ONLY_REASONS`，不在 accept/deliver/unknown/dead
     任一 mutation code set。
   - `report_too_large` 的 writer 路徑（release 時 >65,536）正常 producer 不可達
     （summary 上限 4,096、其他欄位有界）；它是 defense-in-depth，reader 與 DB
     trigger 已實測。
8. **stock port**：用真正的 `CommentEffectPort`（不是 fake）跑 repair→accept→release：
   `delivered`、1 confirmed row、0 board comment。這直接驗證 v5 已移除 v4.1 的
   `effect_fencing_unavailable` demo trap。

---

## 5. 獨立隔離 E2E（真實 board CLI＋dispatcher transition）

我自寫 `verify-v5/m2_v5_e2e.py`，不使用 `.research/m2-e2e.py`（該檔是 v4.1 時代、
不在 candidate、且作者已聲明 v5 未重跑）。它使用：

- 真實 `hermes kanban` CLI（`init/create/link?/notify-*`，只在 isolated
  `HERMES_HOME`／`HERMES_KANBAN_HOME`）；
- 真實 `hermes_cli.kanban_db` 的 `claim_task` / `claim_review_task` /
  `request_review` / `request_changes` / `complete_task`（dispatcher 同一 API）；
- 真實 `DepartmentService`、`department_submit`（真的 hash 本地檔案）、`Delivery`；
- bind 後 service 讀取改用 shipped read-only `KanbanDbBoard`（同一 isolated DB），
  避開每次 `hermes kanban show` 約 4 秒的 CLI 啟動；M2 dashboard 也用同一個
  `KanbanDbBoard`。

結果：**48/48 checks PASS**（0 FAIL、RESULT: PASS）。

鏈路：

```text
create root/work → bind + wake subscribe（讀回 api_server/e2e-manager/wake）
→ dispatcher claim → request_review(v1) → review claim → request_changes
→ ingest：review REQUEST_CHANGES、repair_round=1
→ accept/release：held/review_rejected、0 report
→ M2：無 final report
→ dispatcher re-claim → request_review(v2) → ingest
→ department_submit：revision 2、producer SHA
→ review claim → complete(metadata artifact_sha256 + note)
→ ingest：revision 2、status done、PASS run 4
→ release before accept：held/acceptance_missing
→ accept：releasable
→ release：delivered（canonical 四欄 identity）
→ release replay：already_delivered、1 row
→ board root/child comments：0 / 0
→ report body：v2、summary=independent re-review、generation 3、無 artifact bytes
→ M2 read：work done、PASS run 4、acceptance current、final_report delivered、
  report summary/identity、readiness already_delivered、history_count=1
→ board 不可用（missing.db）：task unknown，但 history 仍回 summary
→ M1 status：delivery report body 仍可讀
```

E2E 的 `bind` 回 `report.ok=false`，但 `ensured=(root, child)`；原因是
`session_owner_unverified`（public board 不能證明 runtime session owner），這是 M1 v5
的預期三態，不是失敗。我的 check 以 `ensured` 兩張卡都在為準。

---

## 6. 獨立 UI contract（SDK mock，不是畫面）

我自寫 `verify-v5/js/ui_contract.mjs`，用**自己的** public SDK stub 載入
`desktop/department-hub/plugin.js`，展開 jsx tree 檢查文字與 element type；
不是作者 `run.mjs` 的 assertion。結果 **60/60 PASS**。

覆蓋：

- 註冊 `/department-hub` route、sidebar、palette；en／zh-hant key 集合完全相同，
  所有 literal `t()` key 都存在，zh-hant 真的是繁體中文。
- 頁面文字：`Read-only`、`not a security sandbox`、`Source: Kanban`、
  `observed snapshot`；listbox `role/aria-label`、option `aria-selected/tabIndex/onKeyDown`
  （鍵盤可達）；狀態以文字呈現。
- ready：`worker-declared SHA-256`、`not a reviewer byte attestation`、review run；
  **不**出現 delivered／already delivered／reviewer verified。
- accept held 與 deliver held 的 reason code 逐字顯示；held 不得顯示 PASS。
- delivered：`final report: delivered`、canonical stored identity、summary、
  schema/generation、`Already delivered — existing report; no duplicate created`；
  不顯示 Ready。
- 第二世代：`delivery held (delivery_identity_conflict)`、原報告保留字樣、
  stored identity 與 current identity 分開、不顯示 Ready。
- v5 Delivered 區塊：`Delivered — plugin-persisted reports`、
  `deliveries.body_json` + `not a board comment`、交付時間、canonical identity、
  `Accepted report summary`；board 讀取失敗時仍顯示 summary。
- 超限：`report_too_large` 顯示、內容不回顯。
- HTML sentinel `<img …><script>…</script>` 只以文字 child 出現；
  tree 沒有 `img/script/iframe` element，source 沒有 `dangerouslySetInnerHTML` 指派。
- zh-hant：`已交付（plugin 持久報告）`，英文 heading 不出現。
- UI source 完全沒有 `ensured` 字樣。

**這仍是 mock，不是 real Desktop rendering。** 視覺、pixel、native grant/OAuth、
實際 route 載入一律未驗。

---

## 7. Findings

### P1-H（blocking for 「整合產物」驗收）— 任務指名的 project 目錄仍是 stale

`<repo>/` 現在是 **202 passed** 的
M1 v1 + 第一輪 M2 專案，不是本次 v5 candidate：

- 缺 `department_lib/artifacts.py`（`department_submit` 的 producer 模組）。
- 缺 `docs/m1-v4-contract.md`、`docs/m1-v5-contract.md`。
- 缺 `tests/test_m2_v2_contract.py` 與 v5 回歸測試。
- `README.md` 仍描述 first slice；`docs/m2-desktop.md:6,284` 還把測試指令指向這個
  stale 目錄（照文件跑會得到 202，不是 409）。

**影響**：任務 body 說要審「整合產物」該目錄；如果該目錄就是交付物，這一項 FAIL。
目前唯一有效安裝來源是 `t_68287415/candidate`。**不要安裝 project 目錄。**

**建議**：由 orchestrator 決定哪個才是交付物：
1. 若 project 目錄必須是最終整合位置 → 用 candidate 更新它，重跑 409/53，再由
   M2-R 複驗；或
2. 若 candidate tarball 是交付物 → 更新任務 body／docs，明確 project 目錄不是安裝來源。

### P2-D（non-blocking）— candidate 文件仍指向 stale project 與不存在的 v4.1 E2E

- `docs/m2-desktop.md:6` 的「專案」與 `:284` 的 `cd` 都是
  `<repo>`；照做得到 202 tests。
- `docs/m2-desktop.md` §10 要 M2-R「重跑 `.research/m2-e2e.py`，確認 44 checks」；
  該檔不在 candidate，且是 v4.1 時代證據。v5 沒有作者重跑的 E2E（alignment §10.2
  如實承認）。我已用自寫的 v5 E2E 48/48 取代，但文件本身仍誤導。

**建議**：把測試/E2E 指令改成 candidate 相對路徑；若要保留 E2E 指示，放進
candidate（例如 `tests/` 或 `tools/`）並更新為 v5 48-check，或直接刪除該段。

### P2-B（non-blocking observation，M1 v5 inherited）— `department_bind` 不檢查 caller

`__init__.py::_handle_bind` 接受 `caller_profile` 但沒有用它；我用
`caller_profile="outsider"` 實測仍寫入 ownership rows 並對 root/child 建 wake
subscription（`store.dept_for_task("work") == "dev"`）。Root 本身仍必須是 board 上
assignee=manager 的卡，所以不是任意劫持；但 v4 契約曾寫 `department_bind` 是
manager-only，v5 契約沒有再列這條，工具描述只說 root assignee 必須是 manager。

**判定**：不阻擋 M2-R（M2 backend 沒有 bind route；這是 M1 tool surface）。
**建議**：由 M1 擁有者決定是要在 handler 加 caller 檢查（manager 或 operator），
或在 v5/v5.1 契約明文寫「bind 只驗 root 所有權，不驗 caller」。目前兩種說法都不完整。

### P3-1（documentation）— contract §8 的 dashboard bundle 版本與候選不符

`docs/m1-v5-contract.md:206` 寫 `dashboard/manifest.json` `0.2.0`，候選是 `0.3.0`
（M2 因 payload 新增 report 欄位而 bump；alignment §6 有記）。契約又說「M2 must
compare the contract key」，所以不是 wire 變更，但版本表已 stale。

### P3-2（documentation）— contract 首行仍寫「independent review pending」

`docs/m1-v5-contract.md:3` 還寫 `(candidate; independent review pending)`，
但 `t_090d5884` 已 PASS。這是 M1 v5 文件的狀態字，不影響語義，但會讓 operator
誤以為 schema 未凍結。

### P3-3（cosmetic）— generated file 進 manifest

`tests/js/.tmp/plugin.mjs` 是 `run.mjs` 執行時 rewrite 的 generated 檔，卻列入
manifest；因為 rewrite 是 deterministic（我驗證與 fresh rewrite 逐位相同），
重跑測試不會造成 hash drift。可改成 build artifact 不進 manifest，或保留現狀。

### P3-4（cosmetic）— `not_attempted` 仍是 mutation refusal 的欄位名

mutation 409 payload 仍有 `not_attempted: ["config write", ...]`。它不是 reason
code，契約只禁止把 `not_attempted` 當 release/held code；M2 dashboard 也沒有把它
當 code。無需修改，僅記錄避免下次誤讀。

### P3-5（low risk）— `effect_raised` 正規化只匹配 exact code

`_delivery_readiness` 只在 dead_letter detail **等於** `effect_raised` 時轉 unknown；
若 legacy row 的 detail 是舊式 `"effect raised: <exc>"`（有空白與訊息），仍會顯示
dead-letter。v5 writer 的舊 `_apply` 是把該 detail 存在 `unknown` state，不是
dead_letter；實際 dead_letter 的 detail 是 `retry budget exhausted`，所以可達性低。
若要收緊，可改成 `detail.startswith("effect raised")` 或明確 allowlist。

---

## 8. 真實 Desktop：UNVERIFIED

本次審查沒有安裝 plugin、沒有重啟 gateway、沒有開 Desktop：

- 這台主機沒有 Desktop app（`~/.hermes/desktop` 只有 lock 檔）。
- 安裝會動 `~/.hermes/plugins/`、`~/.hermes/desktop-plugins/`、`config.yaml`，
  且需要重啟 gateway（會中斷正在跑的 worker）；依指示必須由 maintainer 執行。
- 因此以下全部 **UNVERIFIED**：
  - Settings → Plugins 是否出現 Department Hub、能否啟用；
  - sidebar `Departments` / palette `Open departments` / route `/department-hub`；
  - 真實 pixel rendering、窄版 layout、focus ring；
  - native grant/OAuth（若 gateway 開 auth）；
  - 實際 `Delivered` 區塊是否有報告、summary 是否以純文字呈現；
  - gateway 載入後的 health、真實 auth token 呼叫；
  - live model / manager wake。

手動驗收包已更新到 v5：`M2-DESKTOP-MANUAL-ACCEPTANCE-v5.md`，內含安裝前碰撞檢查、
精確 copy/config/restart 步驟、每個狀態的應見／不應見文字、Delivered 區塊（含可選的
隔離 fixture，不碰正式 DB）、可逆移除、風險與回報格式。

---

## 9. 本次審查的邊界

- 沒有修改 candidate、project 產品檔、profile、config、board、通知或服務。
- 沒有 install / gateway restart / push / deploy / X / catalog PR / FreshTape。
- 所有 runtime/E2E 寫入都在
  `~/.hermes/kanban/workspaces/t_750e8a14/verify-v5/` 的 temp/isolated
  `HERMES_HOME`、`HERMES_KANBAN_HOME` 與 temp SQLite；E2E 明確檢查
  `kanban_db_path()` 在 scratch 內才繼續。
- 審查後 candidate 已清除測試產生的 `__pycache__`，manifest 再驗 56/56 OK。
- project 目錄只寫了本報告 `.research/m2-review.md`（以及舊的 `.research` 證據），
  沒有改產品檔。

---

## 10. 必要後續 live proof（給 maintainer）

1. 依手動驗收包 §0 做碰撞檢查；全部 absent 才安裝 candidate tarball。
2. 安裝 agent half + desktop half、改 config、重啟 gateway（idle 時段）。
3. 開 `/department-hub`，截圖：en、zh-hant、窄版、空狀態、held/ready（若有）。
4. 若有真實交付：截 `Delivered — plugin-persisted reports`（summary、canonical
   identity、generation/bytes），並確認 HTML sentinel 只顯示文字、沒有執行。
5. 回報 health、route/sidebar/palette、鍵盤、read-only footer、manager 三欄、
   ready caveat、禁止字樣。
6. 若要驗 live wake：填真實 manager platform/chat_id，確認 UI 仍只說
   `unconfirmed (ensured_not_awake)`，不能把 `ensured` 當已醒。
7. 若 project 目錄才是最終交付物：先把它更新到 candidate（409/53）再複驗；
   在那之前不要用 project 目錄安裝。
