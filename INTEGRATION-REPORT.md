# INTEGRATION-REPORT — Department Hub 整合（M1 v5.1 ＋ M2 v5）

- 產生時間：2026-11-06T15:34:38+08:00（Asia/Taipei；逐項 log 內另有各自的 `recorded_at_asia_taipei`）
- 專案目錄：`<repo>`
- 分支：`main`／整合 commit：`014f372a3048fc054bd146caa17705f51233a5bb`
- 本報告與 `INTEGRATION-MANIFEST.sha256` 在本報告所在的第二個 commit（evidence commit）提交；報告不寫死該 commit 的 hash（避免自指），用 `git log -1 --format=%H` 取得。

## 0. 結論（先講）

**「這個目錄現在是否等於受審候選？」→ 是（就 plugin 內容而言，逐檔 byte-identical）。**

- 受審候選 `t_68287415/candidate` 的 **56 檔在磁碟上 56/56 sha256 相同**：`sha256sum -c candidate-manifest.sha256` 全 `OK`（`candidate-parity.log`）。
- 其中 **55 檔已進 git**；唯一未進版控的 `tests/js/.tmp/plugin.mjs` 是 `tests/js/run.mjs` 產生的 bundle（任務指定的 `.gitignore` 排除項），實測重跑後 sha256 仍為 `ab484b8983656c2fd13c9ea79e0348f85a78b18ac57c2a4f96a587e01234c64d`，與候選完全相同。
- `plugin.yaml` 已是 `0.5.0`（sha256 `f0c6328c…a5d9`），**不再是 0.1.0**；`delivery.py` 為受審版 `85e22203…ffb7`。
- `.research/` 歷史證據全數保留（未刪、未改），只把「執行用 sandbox／憑證／狀態 DB／uv cache」排除在 git 之外。
- 未使用 `t_ae64a9f7/product`（P1-1 修復前）。

## 1. 輸入與來源 hash

| 角色 | 路徑 | 識別 hash |
|---|---|---|
| M1 v5.1 受審 product（`t_090d5884` REVIEW: PASS） | `~/.hermes/kanban/workspaces/t_fb24c666/product` | `department_lib/delivery.py` = `85e22203335f6756a93bd074fbf77c0678fb3039fc337355b0ff78c0439effb7`；`plugin.yaml` = `f0c6328c49f34efa4a9c251a2f92e1a79013184d20053f715f4acc030a64a5d9` |
| M2 v5 對齊候選（`t_68287415`，56 檔） | `~/.hermes/kanban/workspaces/t_68287415/candidate` | manifest `~/.hermes/kanban/attachments/t_68287415/candidate-manifest.sha256` = `5280ab3e578529a01f9c15a55891845ba204f5eba903d3fab2d64968acf56247` |
| 整合前專案（v1, 無 git） | `<repo>` | 42 個非 `.research` 檔；`plugin.yaml` 0.1.0；逐檔 hash 見 `.research/pre-integration-backup-20261106T150011+0800.files.sha256` |

整合後再驗：`department_lib/delivery.py` 與 `plugin.yaml` 的 sha256 與上表**完全相同**（見 §4）。

## 2. 三方合併（候選 × 專案 v1 × `.research`）

規則：**以 M2 v5 候選為最終樹**（它比 v1 新且承載 M1 v5.1 的修復）；專案裡候選沒有的東西只有 `.research/`，全數保留；候選與 v1 同路徑的產品檔，一律採用候選。合併前已先備份（§6）。

### 2.A 採用候選（56 檔）

**(a) 覆蓋 v1 同路徑檔案 — 30 檔**

| 檔案 | v1 bytes → 候選 bytes |
|---|---|
| `README.md` | 6,715 → 8,756 |
| `__init__.py` | 13,014 → 15,067 |
| `dashboard/manifest.json` | 360 → 360 |
| `department_lib/acceptance.py` | 7,589 → 20,401 |
| `department_lib/board.py` | 7,664 → 8,185 |
| `department_lib/dashboard.py` | 20,926 → 53,463 |
| `department_lib/delivery.py` | 9,042 → 18,481 |
| `department_lib/kanban_cli.py` | 6,196 → 6,300 |
| `department_lib/kanban_db_source.py` | 11,572 → 14,527 |
| `department_lib/kanban_effects.py` | 3,385 → 4,255 |
| `department_lib/models.py` | 6,254 → 8,179 |
| `department_lib/orchestrator.py` | 12,928 → 35,960 |
| `department_lib/routing.py` | 6,280 → 6,871 |
| `department_lib/store.py` | 24,249 → 36,242 |
| `desktop/department-hub/plugin.js` | 34,087 → 51,158 |
| `docs/m2-desktop.md` | 20,198 → 31,921 |
| `plugin.yaml` | 1,171 → 1,231（0.1.0 → 0.5.0） |
| `tests/js/.tmp/plugin.mjs` | 34,105 → 51,176（generated，gitignored） |
| `tests/js/run.mjs` | 11,846 → 27,439 |
| `tests/support.py` | 3,273 → 4,290 |
| `tests/test_acceptance.py` | 12,996 → 15,991 |
| `tests/test_board.py` | 9,920 → 10,056 |
| `tests/test_dashboard.py` | 11,146 → 11,395 |
| `tests/test_dashboard_api.py` | 10,015 → 11,910 |
| `tests/test_delivery.py` | 8,470 → 9,860 |
| `tests/test_kanban_effects.py` | 3,272 → 3,485 |
| `tests/test_orchestrator.py` | 9,893 → 10,935 |
| `tests/test_plugin_registration.py` | 5,610 → 5,687 |
| `tests/test_routing.py` | 4,724 → 5,324 |
| `tests/test_store.py` | 7,947 → 10,168 |

**(b) 與 v1 完全相同、仍以候選為準 — 12 檔**

`conftest.py`、`dashboard/plugin_api.py`、`department_lib/__init__.py`、`department_lib/config.py`、`tests/__init__.py`、`tests/js/mocks/jsx-runtime.mjs`、`tests/js/mocks/react.mjs`、`tests/js/mocks/sdk.mjs`、`tests/test_config.py`、`tests/test_desktop_plugin.py`、`tests/test_kanban_db_source.py`、`tests/test_models.py`

**(c) 候選新增（v1 沒有）— 14 檔**

`department_lib/artifacts.py`、`docs/m1-v2-contract.md`、`docs/m1-v3-contract.md`、`docs/m1-v4-contract.md`、`docs/m1-v5-contract.md`、`tests/review_fixtures.py`、`tests/test_m1_v2_regressions.py`、`tests/test_m1_v3_boundaries.py`、`tests/test_m1_v3_fence_edges.py`、`tests/test_m1_v3_regressions.py`、`tests/test_m1_v4_regressions.py`、`tests/test_m1_v5_controls.py`、`tests/test_m1_v5_delivery.py`、`tests/test_m2_v2_contract.py`

### 2.B 保留專案（不從候選覆蓋）

**`.research/` 全部保留**（整合前 28,319 檔；現為 28,328 檔＝原有檔案未刪未改＋8 個 evidence log＋備份 tar 與其清單；約 823 MB）：

- 其中 **197 檔進 git**：M0/P0/M1/M2 報告與審查（`p0-report.md`、`m1-review.md`、`m2-report.md`、`m2-review.md`、`M2-DESKTOP-MANUAL-ACCEPTANCE.md` 等）、`evidence/` 下的 log/JSON、`m1-review-probes/snapshot/`（25 檔凍結快照＋sha256）、`prototype-v1/`、`prototype-v2/` 的程式與報告、`route-lab-377/evidence/`、兩個小型證據壓縮檔（`department-hub-m2-candidate.tar.gz` 149 KB、`p0-route-followup-evidence.zip` 188 KB）。
- 其餘 **不進 git、但原樣留在磁碟**：`.research/lab/`（P0 sandbox 的完整 `HERMES_HOME` 複本＋uv cache，含 `.api_key`）、`.research/prototype-v{1,2}/live-lab/`、`.research/route-lab-377/` 下的 `os-home`／`board`／`xdg`／`runtime`／`tmp` 子目錄。理由是它們是**執行環境快照**（憑證、state DB、日誌、約 800 MB 套件 cache），不是專案證據本身；提交會把憑證與狀態 DB 帶進版控。這不是刪除，檔案仍在原位。

理由：任務明定不得刪 `.research/` 歷史證據；候選不含 `.research/`，故無「候選版本」可覆蓋。

### 2.C 刪除（0 檔）

v1 的 42 個非 `.research` 檔案，全部在候選中有對應（30 覆蓋、12 相同），**沒有任何「v1 有、候選沒有」的產品檔**，因此本次整合沒有刪除任何檔案。

### 2.D 新增（非來自候選）

| 新增物 | 用途 |
|---|---|
| `.gitignore` | 排除快取、state DB、`tests/js/.tmp`、備份 tar、research sandbox |
| `.git/` | 新建 repo（`main`，initial commit `014f372a3048fc054bd146caa17705f51233a5bb`） |
| `INTEGRATION-MANIFEST.sha256` | 整合後逐檔 hash（本報告同批提交） |
| `INTEGRATION-REPORT.md` | 本報告 |
| `.research/integration-evidence/` | 本次實測原始 log（pytest／node／validate／doctor／parity） |
| `.research/pre-integration-backup-20261106T150011+0800.tar.gz`（＋`.files.sha256`） | 整合前 v1 完整備份（gitignored，留在磁碟） |

### 2.E 衝突判定

- 候選 vs 專案 v1：**0 個無法自動解決的衝突**（沒有 project-only 產品檔，沒有雙方都改的第三方檔案）。
- 候選 vs M1 v5.1 受審種子：**9 檔不同 + 1 檔新增**，全部是 M2 對齊工作（詳見 §5.3），其中 2 檔不在 M2 交接的 `changed_files` 清單裡，已在該節標明。

## 3. 專案目錄現況

### 3.1 結構（皆存在且與候選一致）

| 路徑 | 檔數 | 說明 |
|---|---|---|
| `README.md` | 1 | 安裝、最小流程、delivery 保證、限制 |
| `docs/` | 5 | `m1-v2`／`v3`／`v4`／`v5` 契約 ＋ `m2-desktop.md` |
| `tests/` | 30 | 含 `test_m2_v2_contract.py`、`test_m1_v5_*`、`js/` mock runner |
| `desktop/` | 1 | `department-hub/plugin.js`（SDK-only，無 JSX 語法） |
| `dashboard/` | 2 | `manifest.json`、`plugin_api.py` |
| `department_lib/` | 14 | 含新增 `artifacts.py` |
| 根目錄 | 4 | `README.md`、`__init__.py`、`conftest.py`、`plugin.yaml` |

### 3.2 `plugin.yaml`

`name: department-hub`、`version: "0.5.0"`、description 與候選 byte-identical（`f0c6328c49f34efa4a9c251a2f92e1a79013184d20053f715f4acc030a64a5d9`），`provides_tools` 7 個、`provides_hooks` 2 個。

### 3.3 Git

```
$ git log --oneline --max-parents=0     # 只列整合 commit（本報告所在的 evidence commit 見 git log）
014f372 chore: integrate reviewed Department Hub candidate (M1 v5.1 + M2 v5)

$ git rev-list --max-parents=0 HEAD      # 整合 commit（root）
014f372a3048fc054bd146caa17705f51233a5bb
```

- 分支 `main`；整合 commit 追蹤 253 檔（56 個產品檔（含 `.gitignore`）＋ 197 個 research 證據檔）；本次 8 個 evidence log 與本報告／manifest 於第二個 commit 加入，最終 `git ls-files` 共 **263** 檔。
- commit message 內含來源與受審 hash（候選 manifest、`delivery.py`、`plugin.yaml`、備份 tar）。
- **沒有 push、沒有 remote**。

### 3.4 `.gitignore` 與排除項

排除：`__pycache__/`、`*.py[cod]`、`.pytest_cache/`、`tests/js/.tmp/`、`*.db(-shm|-wal|-init.lock)`、`.research/pre-integration-backup-*.tar.gz`／`.files.sha256`、`.research/lab/`、`.research/*/live-lab/`、`.research/route-lab-377/{os-home,board,xdg,runtime,tmp}/`。

驗證：`git status --ignored --porcelain` 只有 10 個 ignored 入口；`git check-ignore` 對 56 個候選檔中只有 `tests/js/.tmp/plugin.mjs` 命中（generated bundle，預期）。

## 4. 實測結果（全部在專案目錄內執行，保留原始 log）

環境：`~/.hermes/hermes-agent/venv/bin/python`（Python 3.11.15、pytest 9.0.2）；`node v22.22.2`（PATH 預設 node 是 v10，太舊，明確指定 v22；與 `tests/test_desktop_plugin.py` 使用的候選一致）；`hermes` v0.23.0（2026.9.24）。`PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`-p no:cacheprovider`。

| 命令 | rc | 結果摘要 | log |
|---|---|---|---|
| `pytest tests -q -p no:cacheprovider` | 0 | **409 passed in 33.94s** | `pytest.log` |
| `node --check`（`plugin.js` 複本為 `.mjs`，node v22.22.2） | 0 | ESM 語法正確（`.js` 會被當 CJS，故用 `.mjs` 副本） | `node-check.log` |
| `node tests/js/run.mjs`（node v22.22.2） | 0 | **136 checks ALL PASS**；重生成的 `tests/js/.tmp/plugin.mjs` sha256 = `ab484b89…c64d`（與候選相同） | `js-mock.log` |
| `hermes plugins validate <plugin-only staging> --json` | 0 | `ok=true`，**16/16 checks ok**、0 warnings | `plugin-validate.log` |
| `hermes plugins validate . --json`（整個專案目錄，含 `.research/`） | 1 | `ok=false`；15/16 checks ok；失敗 check：`security scan`（`dangerous`：命中 uv cache 內的 AWS 範例金鑰 JSON 與 `.so` 二進位檔；下一列的對照證明命中全部來自該 sandbox）；warnings=0 | `plugin-validate-direct.log` |
| `hermes plugins validate . --json`（同一個專案目錄，`.research/` 暫時移開後放回） | 0 | `ok=true`；16/16 checks ok；失敗 check：無；warnings=0 | `plugin-validate-direct-excluding-research.log` |
| `hermes plugins doctor . --ci` | 0 | `manifest: department-hub 0.5.0 (standalone)`；`registrations: 7 tool(s), 2 hook(s)`；runtime discovery／manifest／import／registration OK | `plugin-doctor.log` |

**關於 validate 的三個 subject（重要）：** `validate` 的 security scan 會走訪目標目錄下**每一個檔案**，並把二進位檔與範例金鑰判為 `dangerous`。直接對 `.` 執行時它會一併掃 `.research/`（28,327 檔／823 MB 的 lab 快照與 uv cache）：跑完 16 分 35 秒後，**唯一**失敗的 check 是 `security scan`（命中的是 uv cache 內的 AWS 範例金鑰 JSON 與 `.so` 二進位檔；對照實驗證明來源就是 `.research/`），其餘 15/16 仍 ok——這是掃描範圍問題，不是 plugin 缺陷。為把這點釘死，另做對照：把 `.research/` 暫時移開（**搬移非刪除**，前後檔案數 28,327 → 28,327，內容未動）後，在**同一個專案目錄**重跑 `hermes plugins validate .`，得到 **ok=true、16/16 checks、0 warnings、rc=0**。因此 plugin 本身在專案目錄內的准入是通過的；`plugin-validate.log`（clean staging）是同一個結論的第三份佐證。

`hermes plugins doctor . --ci` 直接對專案目錄跑 13.7 秒完成、rc=0，不受 `.research` 影響。

## 5. 與受審候選的逐檔差異

### 5.1 產品檔（56/56）

```
$ sha256sum -c ~/.hermes/kanban/attachments/t_68287415/candidate-manifest.sha256
… 56 lines …
# OK lines: 56 / 56
```

差異 **0 檔**。`candidate-parity.log` 保存完整輸出。

### 5.2 專案獨有（非候選）的額外檔案

只有任務要求的整合產物：`.gitignore`、`INTEGRATION-MANIFEST.sha256`、`INTEGRATION-REPORT.md`、`.research/integration-evidence/*.log`、備份 tar（gitignored）。**沒有任何產品檔是候選沒有的。**

### 5.3 與 M1 v5.1 受審種子（`t_fb24c666/product`）的差異 — 請 reviewer 注意

候選相對受審種子有 **9 檔不同 + 1 檔新增**：

1. M2 對齊面（7 檔，與 `t_68287415` 交接的 `changed_files` 相符）：`README.md`、`dashboard/manifest.json`、`department_lib/dashboard.py`、`desktop/department-hub/plugin.js`、`docs/m2-desktop.md`、`tests/js/run.mjs`、`tests/js/.tmp/plugin.mjs`。
2. **不在 M2 交接 `changed_files` 清單中的 2 檔**：`department_lib/kanban_db_source.py`（11,572 → 14,527）與 `tests/support.py`（3,273 → 4,290）。內容是 M2 v2 契約要求的 board adapter 強化（保留 `events[].id` 為 `source_id`、拒絕重複 run id、`subscriptions()` 測試替身），由 `tests/test_m2_v2_contract.py` §6 直接覆蓋（例如 `test_board_adapter_preserves_event_source_id`、`test_board_adapter_refuses_invalid_event_source_id`）。
3. 新增 1 檔：`tests/test_m2_v2_contract.py`。

**含意：** 整合後的 `kanban_db_source.py`／`tests/support.py` 位元組並非 `t_090d5884` 審過的那份（審過的是 v1 版）；它們屬於 M2 作者自測範圍（409 passed），沒有獨立的 M1 複審背書。依任務指定「以 M2 v5 候選為最終樹」如實整合，但這一點應由 `t_e3d284ae` 的 reviewer 判定是否可接受。

## 6. 備份（整合前狀態）

| 項目 | 值 |
|---|---|
| 路徑 | `.research/pre-integration-backup-20261106T150011+0800.tar.gz` |
| 大小 | 203 MB（32,410 個 tar 條目） |
| sha256 | `cc93d600837c88b007fa662263437ad5c270eac3614aa166efc0b6657aa0f225` |
| 逐檔 hash | `.research/pre-integration-backup-20261106T150011+0800.files.sha256`（28,361 行；sha256 `c73e1c1f3d22b6262370f830fd0691ed7f2781fef6b492d80ff9b3475453fba6`）。註：清單由 `find` 產生，其中 1 行是該清單檔自身的自我參照（產生時 `-path` 排除字串少了 `./` 前綴），指向改名前的 `T070011+0000` 路徑；**權威來源是備份 tar**，清單僅作記錄 |
| 完整性 | `tar -tzf` rc=0（條目清單另存 workspace `backup-tar-list.txt`） |

備份在**任何修改之前**完成；內容為整個專案目錄（含 `.research/`，排除備份檔自身）。

## 7. 邊界與未做事項

- **沒有** push、deploy、發文、建 catalog PR；repo 無 remote。
- **沒有**修改正式 board／profile／credentials／gateway／Hermes core／FreshTape。
- `.research/` 未刪除、未改寫；只用 `.gitignore` 把執行環境 sandbox 排除在版控之外。
- **沒有**安裝到 maintainer 的 Desktop、**沒有**做真實 Desktop 畫面驗收（維持 UNVERIFIED；SDK-mock 與 doctor 只證明載入與資料投影）。
- 未改動任何產品語意；所有產品檔位元組直接來自受審候選。

## 8. 重現／驗證指令

```bash
cd <repo>
git rev-list --max-parents=0 HEAD           # 整合 commit = 014f372a3048fc054bd146caa17705f51233a5bb
git log -1 --format=%H                      # 本報告所在的 evidence commit
git status --short                          # 應為空（clean）
sha256sum -c INTEGRATION-MANIFEST.sha256    # 全 OK（本 manifest 不含自身與本報告）
sha256sum -c ~/.hermes/kanban/attachments/t_68287415/candidate-manifest.sha256  # 56/56 OK
~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q -p no:cacheprovider
~/.nvm/versions/node/v22.22.2/bin/node tests/js/run.mjs
~/.local/bin/hermes plugins doctor . --ci
```

## 9. 已知限制（給 reviewer）

1. `INTEGRATION-MANIFEST.sha256` 涵蓋 **261** 個檔案（sha256 `fe479a2c3fd59301a947b14c0be20fbdaf1a0283a2e3caf6ce1a5a9e1af9b6e6`），排除：`.git/`、`__pycache__/`、`.pytest_cache/`、`tests/js/.tmp/`、`*.pyc`／state DB、`.research/lab/` 等 sandbox、備份 tar、以及 manifest 自身與本報告（避免自我參照）。
2. `hermes plugins validate .` 對整個專案目錄（含 `.research/`）極慢（16 分 35 秒）且會把 research sandbox 裡的二進位檔／範例金鑰判為 `dangerous`（rc=1）；plugin 本身在專案目錄內（`.research/` 移開後）與 clean staging 都是 16/16、rc=0（§4）。
3. 真實 Desktop 渲染 **UNVERIFIED**；`tests/js/run.mjs` 是 SDK mock，不是畫面測試。
4. `README.md` 第 3 行仍寫「Independent review pending」——是候選原文（未改動以維持 byte-identity），若要對外發布可考慮更新措辭。
5. 兩個 generated／備份類產物不入版控，但都在磁碟上（`tests/js/.tmp/plugin.mjs`、`.research/pre-integration-backup-*`）。

## 10. 這個目錄現在是否等於受審候選？

**是。** 產品內容 56/56 逐檔 sha256 相同（55 檔在 git、1 檔 generated bundle 在磁碟且可由 `node tests/js/run.mjs` 重現相同 bytes）；`plugin.yaml` 0.5.0；409 tests passed；node mock 136/136；doctor rc=0；validate（plugin subject）16/16。唯一差異是任務要求新增的 `.gitignore`／manifest／本報告／測試 log，以及被 `.gitignore` 排除的備份與 sandbox。

---

## 11. 文件／repo 衛生修訂（2026-11-06，`t_00130130`）

`t_e3d284ae` 的整合驗收給「程式與 parity PASS、文件與 repo 衛生 REQUEST_CHANGES」。本節記錄該
裁決後的修訂。**產品邏輯沒有變更**：55 個共同檔中 53 檔 byte-identical，改變的只有 2 個文件
（README、`docs/m2-desktop.md`）加 harness 與 `.gitignore`。

| 檔案 | 舊 sha256 | 新 sha256 | 變更 |
|---|---|---|---|
| `README.md` | `2c2878c584cd3457762209f2c095ade2809a12bc362978ff3d3802c1a7b8599a` | `e3072effe89e1248ccf5b6d103626ce627af297e326b8bba8113de514e40cb2d` | 真實現況、Installation（agent／desktop／config／gateway／rollback）、Windows 與遠端 Desktop 路徑、validate 對象、E2E v5、Known limitations、supersedes |
| `docs/m2-desktop.md` | `7683d6d52ce35c8c1c06b8c971ea4078bb385ae1a78e1febc8ec86aad87bc8fa` | `efc0fb83f4166e86a8612bfb87ba90350a325abfacbbc7ef98b9f62b4a41fef7` | §6.5.1 v5 E2E、§7 實測安裝步驟、§9 限制、§11 supersedes |
| `.research/m2-e2e.py` | `1c4c539b89a962cc51301252de89fac80fa180c9df3c44a2c0261803f3fa959e` | `3ba2b947cd81c1eaf084ce25de7cd38cd0b61763bff746de9d71c36cc44a92a2` | v4→v5 harness（submit-first、無 root-comment 斷言、不以 `report.ok` 判 bind） |
| `.gitignore` | `c3cd4f14498d4dbee056679d53eac09b198b9b8f3a17fdff3ac8f910eb6a4aa0` | `0ba4b724ef49931b6e502273ab0f929008c70f3d6ed77f233a1e7e8ed4a9037a` | 新增兩個 evidence archive 的排除 |

### 11.1 repo 衛生

`.research/p0-route-followup-evidence.zip`（內含 `route-lab-377/os-home/.hermes/state.db`、
`.../profiles/manager/state.db`、`.../board/kanban.db`，內容為 synthetic）與
`.research/department-hub-m2-candidate.tar.gz`（純 source snapshot）已自索引移除：
`git rm --cached`，**兩檔仍在磁碟、位元組未動**（sha256
`c88d8891136a1f6e41b218d7786d61665a4e283c6623c8238fd9c2ba6ce0cc93` /
`cdd15ad32adc239979b19c88844f95385fb88f106877ff56dd8cdf4c662d6ef6`）。
`git ls-files` 由 263 → 261；`INTEGRATION-MANIFEST.sha256` 仍是 **261 條**並涵蓋這兩個磁碟檔，
manifest 的「磁碟內容」語義不變。新 manifest sha256 =
`9dd7f593732621533e52232c8f3b480c2ab3e3205181fe3c0ab9901bc53dc2a3`。

### 11.2 重跑結果

| 檢查 | 結果 |
|---|---|
| `sha256sum -c INTEGRATION-MANIFEST.sha256` | 261/261 OK、rc=0 |
| `sha256sum -c candidate-manifest.sha256` | 54/56 OK；**只有** `README.md`、`docs/m2-desktop.md` FAILED（即 docs delta） |
| 自寫 product-tree walk（排除 `.git`／`.research`／cache） | 共同 55 檔 **53/55 相同**；差異僅上述 2 檔；專案獨有 `.gitignore`／manifest／本報告；候選獨有 0 |
| `pytest tests -q` | **409 passed** |
| `node --check desktop/department-hub/plugin.js`；`node tests/js/run.mjs` | rc=0；**ALL PASS**（136 checks）；`tests/js/.tmp/plugin.mjs` 仍是 `ab484b89…c64d` |
| `hermes plugins doctor . --ci` | rc=0、7 tools / 2 hooks |
| `hermes plugins validate <staging>` | **ok=true、16/16、0 warnings、rc=0** |
| `.research/m2-e2e.py`（v5） | **RESULT: PASS（53 checks）**、rc=0 |

### 11.3 仍然未驗證

真實 Desktop 畫面（渲染／native grant／OAuth）仍 **UNVERIFIED**；本修訂只補文件與 harness，
不改變這個事實。載入器層級（v0.23.0 `runtime-loader.ts`）對 `plugin.js` 的實測見 README 與
`docs/m2-desktop.md` §7.2。

---

## 附錄：公開釋出時的清理（2026-11-09）

本報告寫成於整合當日；之後為了把這個 repo 公開，做了兩項**不改程式行為**的整理，兩者都在同一個 commit：

1. **識別資訊清理**：89 個被追蹤的檔案把維護者的家目錄、專案路徑與名字換成中性寫法——專案絕對路徑 → `<repo>`、家目錄前綴 → `~/…`、單獨出現的家目錄（文件與腳本裡）→ `$HOME`（Python 測試裡 → `os.path.expanduser("~")`）、維護者名字 → `maintainer`、Windows 使用者資料夾 → `C:\Users\<you>`。**只改字串，不動邏輯**；每個檔案的增刪行數對稱。另有 5 處是**功能性預設值**（Node 候選路徑、Hermes agent root 的預設），改成執行時展開，因此 `pytest` 與 `node tests/js/run.mjs` 仍能真的找到來源，而不是靜默跳過。
2. **`INTEGRATION-MANIFEST.sha256` 重新產生**：舊 manifest 有 **272** 條，其中兩條（`.research/department-hub-m2-candidate.tar.gz`、`.research/p0-route-followup-evidence.zip`）是 **gitignored、不在 repo 裡**的磁碟檔，所以 clone 下來無法逐條驗證。新 manifest 只涵蓋 **被追蹤的檔案**（排除自身與本報告），共 **270** 條；`sha256sum -c INTEGRATION-MANIFEST.sha256` 在乾淨 clone 上應全 OK。

**本報告前面的數字是清理前的狀態**：第 224 行「涵蓋 261 個檔案、sha256 `fe479a2c…`」與第 257 行「仍是 261 條並涵蓋這兩個磁碟檔」描述的是當時的 manifest，保留作為歷史紀錄；現在的條數與 sha256 以本附錄與 repo 內的檔案為準。程式（`department_lib/`、`dashboard/`、`tests/`、`desktop/`、`plugin.yaml`）在這次清理中沒有任何行為變更。
