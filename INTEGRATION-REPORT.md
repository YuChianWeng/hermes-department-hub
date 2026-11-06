# INTEGRATION-REPORT — Department Hub 整合（M1 v5.1 ＋ M2 v5）

- 產生時間：2026-11-06T15:34:38+08:00（Asia/Taipei；逐項 log 內另有各自的 `recorded_at_asia_taipei`）
- 專案目錄：`/home/ubuntu/projects/hermes-department-orchestrator`
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
| M1 v5.1 受審 product（`t_090d5884` REVIEW: PASS） | `/home/ubuntu/.hermes/kanban/workspaces/t_fb24c666/product` | `department_lib/delivery.py` = `85e22203335f6756a93bd074fbf77c0678fb3039fc337355b0ff78c0439effb7`；`plugin.yaml` = `f0c6328c49f34efa4a9c251a2f92e1a79013184d20053f715f4acc030a64a5d9` |
| M2 v5 對齊候選（`t_68287415`，56 檔） | `/home/ubuntu/.hermes/kanban/workspaces/t_68287415/candidate` | manifest `/home/ubuntu/.hermes/kanban/attachments/t_68287415/candidate-manifest.sha256` = `5280ab3e578529a01f9c15a55891845ba204f5eba903d3fab2d64968acf56247` |
| 整合前專案（v1, 無 git） | `/home/ubuntu/projects/hermes-department-orchestrator` | 42 個非 `.research` 檔；`plugin.yaml` 0.1.0；逐檔 hash 見 `.research/pre-integration-backup-20261106T150011+0800.files.sha256` |

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

環境：`/home/ubuntu/.hermes/hermes-agent/venv/bin/python`（Python 3.11.15、pytest 9.0.2）；`node v22.22.2`（PATH 預設 node 是 v10，太舊，明確指定 v22；與 `tests/test_desktop_plugin.py` 使用的候選一致）；`hermes` v0.23.0（2026.9.24）。`PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`-p no:cacheprovider`。

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
$ sha256sum -c /home/ubuntu/.hermes/kanban/attachments/t_68287415/candidate-manifest.sha256
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
- **沒有**安裝到 Oneal 的 Desktop、**沒有**做真實 Desktop 畫面驗收（維持 UNVERIFIED；SDK-mock 與 doctor 只證明載入與資料投影）。
- 未改動任何產品語意；所有產品檔位元組直接來自受審候選。

## 8. 重現／驗證指令

```bash
cd /home/ubuntu/projects/hermes-department-orchestrator
git rev-list --max-parents=0 HEAD           # 整合 commit = 014f372a3048fc054bd146caa17705f51233a5bb
git log -1 --format=%H                      # 本報告所在的 evidence commit
git status --short                          # 應為空（clean）
sha256sum -c INTEGRATION-MANIFEST.sha256    # 全 OK（本 manifest 不含自身與本報告）
sha256sum -c /home/ubuntu/.hermes/kanban/attachments/t_68287415/candidate-manifest.sha256  # 56/56 OK
/home/ubuntu/.hermes/hermes-agent/venv/bin/python -m pytest tests -q -p no:cacheprovider
/home/ubuntu/.nvm/versions/node/v22.22.2/bin/node tests/js/run.mjs
/home/ubuntu/.local/bin/hermes plugins doctor . --ci
```

## 9. 已知限制（給 reviewer）

1. `INTEGRATION-MANIFEST.sha256` 涵蓋 **261** 個檔案（sha256 `fe479a2c3fd59301a947b14c0be20fbdaf1a0283a2e3caf6ce1a5a9e1af9b6e6`），排除：`.git/`、`__pycache__/`、`.pytest_cache/`、`tests/js/.tmp/`、`*.pyc`／state DB、`.research/lab/` 等 sandbox、備份 tar、以及 manifest 自身與本報告（避免自我參照）。
2. `hermes plugins validate .` 對整個專案目錄（含 `.research/`）極慢（16 分 35 秒）且會把 research sandbox 裡的二進位檔／範例金鑰判為 `dangerous`（rc=1）；plugin 本身在專案目錄內（`.research/` 移開後）與 clean staging 都是 16/16、rc=0（§4）。
3. 真實 Desktop 渲染 **UNVERIFIED**；`tests/js/run.mjs` 是 SDK mock，不是畫面測試。
4. `README.md` 第 3 行仍寫「Independent review pending」——是候選原文（未改動以維持 byte-identity），若要對外發布可考慮更新措辭。
5. 兩個 generated／備份類產物不入版控，但都在磁碟上（`tests/js/.tmp/plugin.mjs`、`.research/pre-integration-backup-*`）。

## 10. 這個目錄現在是否等於受審候選？

**是。** 產品內容 56/56 逐檔 sha256 相同（55 檔在 git、1 檔 generated bundle 在磁碟且可由 `node tests/js/run.mjs` 重現相同 bytes）；`plugin.yaml` 0.5.0；409 tests passed；node mock 136/136；doctor rc=0；validate（plugin subject）16/16。唯一差異是任務要求新增的 `.gitignore`／manifest／本報告／測試 log，以及被 `.gitignore` 排除的備份與 sandbox。
