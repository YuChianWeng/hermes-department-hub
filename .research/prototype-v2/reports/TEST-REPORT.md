# Department Hub 原型 v2｜測試報告（可重跑）

所有命令在 `<repo>/.research/prototype-v2` 執行。Python：`~/.hermes/hermes-agent/venv/bin/python`（3.11.15）。pytest 9.0.2。

> 本報告只涵蓋 v2；v1 的原始測試報告與證據仍留在 `prototype-v1/`，未被覆蓋（v1 manifest 193/193 通過）。

## 1. RED：先寫出會失敗到行為 assertion 的測試

### 1.1 有效 RED（v2 新增的 12 個 P1 回歸，對凍結的 v1 實作執行）

```bash
S=<scratch>; cp -r prototype-v1/deptproto "$S/deptproto"
cp prototype-v2/tests/test_p1_regressions.py "$S/"
cd "$S"; venv/bin/python -m pytest test_p1_regressions.py -q -p no:cacheprovider
# => 12 failed in 1.99s；全部是 AssertionError，沒有 import/collection error
```

- 日誌：`evidence/red-p1-frozen-v1-repro.log`（SHA-256 `000b3451c58deb96116e1efb646485d718a3e751b1b7bb5f0e7818349c2c83e4`）。
- 綁定：v1 `orchestrator.py` SHA-256 `b53feb6a5e66ec0ae61d681cb69e0d86cf2f0ac9d85f8a40ed5ad66e17819247`；測試檔 SHA-256 `a5a7270306dae9ca1c32f7e893d8be4e0c5400056e2f4ff9bef58946c590784f`。
- 12 個失敗分佈：F1 ×2、F2 ×2、F3 ×4、F4 ×1、F5 ×2、F6 ×1；每個都在產品行為斷言失敗（例如 `'confirmed' != 'held'`、`latest_verdict(...) is not None`），不是匯入錯誤。

### 1.2 過程中的 RED（保留，不重標）

- `evidence/red-p1-tests.log`、`evidence/red-p1-final.log`：較早的 RED 執行；同樣是 12 個失敗。`red-p1-frozen-v1-repro.log` 是本次以凍結 v1 重跑的乾淨版本。
- v1 原有的 `unit-tests-red.log` 是 `ModuleNotFoundError` 的 collection error；本卡不把它當有效 RED，已在 v1 審查指出。

## 2. GREEN：focused + full unit

```bash
venv/bin/python -m pytest tests/test_p1_regressions.py tests/test_v2_verification.py -q -p no:cacheprovider
# 23 passed, 3 subtests passed in 4.65s   (log: evidence/focused-final.log)

./scripts/sandbox.sh unit
# preflight paths/network OK；57 passed, 3 subtests passed in 9.07s；rc=0
# (log: evidence/sandbox-unit.log)
```

57 項分佈：

- v1 原有 34 項（`test_domain` 15、`test_events` 6、`test_crash_recovery` 6、`test_preflight` 7），行為不變且全數通過。
- `tests/test_p1_regressions.py` 12 項：F1 recovery stale（2）、F2 unknown effect（2）、F3 exact artifact/verdict/acceptance（4）、F4 unknown owner（1）、F5 detached report（2）、F6 verdict+status 原子（1）。
- `tests/test_v2_verification.py` 11 項：
  - `AtomicMutationCrashTests`（3）：每個 mutation boundary 真子程序 kill → rollback 後只恢復一次；commit 後 crash 冪等；並行 recovery 只套用一次。
  - `ProcessLevelStaleEffectTests`（1）：effect 已送出後 artifact 變更，recovery 記為歷史、不重送。
  - `ArtifactImmutabilityTests`（2）：來源檔變更不改變交付 bytes；blob 被改 → fail closed。
  - `ReportTopologyTests`（3）：board 讀取錯誤、report 缺失、work parents 與權威 board 不符，全部 fail closed。
  - `OwnerAndEffectConflictTests`（2）：處理階段 session 被撤銷 → 無 receipt；既有衝突 effect → 不確認、不重送。

## 3. 嚴格 sandbox／no-network 重跑

```bash
./scripts/sandbox.sh --probe     # unshare-ok / rc 0（不可用時 rc 78，且不啟動任何程序）
./scripts/sandbox.sh unit        # 57 passed / rc 0；preflight 先行
./scripts/sandbox.sh live        # 42/42 assertions / rc 0
```

`sandbox.sh unit` 的 preflight（`evidence/sandbox-unit.log` 第 1 行）記錄 12 個路徑全部 resolve 在 `prototype-v2/live-lab`，網路 `interfaces=["lo"]`、`routes=[]`。

## 4. 隔離 live stage（真 gateway + synthetic model + 手動 harness）

- Console：`evidence/sandbox-live.log`（43 行、rc=0；SHA-256 `dd6ba34247fbab95f0ebbf3d3740c7f675009ae722612df14b55c2898f11a92d`）。
- 完整證據：`live-lab/evidence/`；斷言 `assertions.json` → **42/42 pass**（SHA-256 `8a9452e65c9ffb66e4a98472d2cd0b9858ca152d7ff7cb74e8896c4dfa475d05`）。
- 受測：真 `hermes gateway run`；profiles `default` / `manager` / `reviewer`；deterministic synthetic model `127.0.0.1:18251`、gateway `127.0.0.1:18252`。
- Sessions：default `api-0b36333bf4394fee`、manager `api-9d018bfc598b4849`、reviewer `api-5e66804d3c85a299`；各 profile store 的 `profile_name` 相符；錯 profile key → 401。

### Live 案例與精確 ID（最終 run）

| 案例 | ID / 結果 |
|---|---|
| manager 真 wake + tool effect（work child） | `t_dbe3d901`；comment author=manager，`SYNTHETIC_ROUTE_RECEIPT t_dbe3d901` |
| review 1 REQUEST_CHANGES | `t_547f6861`；reviewer `{"verdict":"REQUEST_CHANGES","artifact_sha":"2e2b159a…"}`；prototype applied |
| rework + review 2 PASS（新 artifact） | `t_83f9b7fd`；`{"verdict":"PASS","artifact_sha":"ef65e542…"}`；fresh PASS 綁新 bytes |
| manager acceptance | `t_a30198c4`；`{"decision":"ACCEPT","artifact_sha":"ef65e542…"}`；prototype applied |
| 單一 final | report `t_745499c7`；delivery `dlv-616ebf987241`；marker `[[deptproto-final:616ebf9872419a6c]]`；恰 1 個；重複 deliver=`already_delivered` |
| default 收 report | report 完成後 default wake + receipt；default 對 work/review/accept 卡無訂閱、無 receipt |
| 缺訂閱 | `t_0b12c2fa`；無 wake；`dead_letter/missing_subscription` |
| 錯 owner（manager owner + default session） | `t_a61442fe`；無 wake；`dead_letter/wrong_owner` |
| 未映射 session owner | `dead_letter/unknown_owner`；無 `board_events` row、無 observation、無 receipt |
| 錯 report 邊（work→report 真 link） | `t_775c180f`；`held/wrong_report_edge`；0 comment |
| live crash + recovery | crash report `t_ffdc390a`；`os._exit(137)` 於 `after_effect_before_receipt`；crash 前 1 個 final；recover → `confirmed`；仍 1 個 comment、`receipts=1` |

### Live 隔離／清理

- namespaces：mount/net/pid/user 皆與 host 不同；uid 0（user namespace 內）。`preflight.json`（SHA-256 `78408bdcc1f4dea73f5caa7d35a579974070150322b0d4d4599c2c48d0c60244`）。
- network：`interfaces=["lo"]`、`routes=[]`。
- 正式資源遮蔽：`~/.hermes/profiles`、正式 `kanban.db`、`/run/user/1000`、Obsidian vault 皆不存在；installed source 與 uv Python 寫入 probe 皆 EROFS。
- 啟動前負向：production `HERMES_KANBAN_DB`、`/run/user/1000`、`HERMES_KANBAN_TASK` 都在 spawn 前被拒。
- cleanup：gateway SIGTERM rc=1、model rc=-15，全部 reaped；`ss -lntup` 僅表頭（`cleanup.json`，SHA-256 `e2c5d21206f5170b7565b2383608dccbe34f298be6b008ef7b872defae196b4b`）。

### 本輪保留的失敗 run

`evidence/historical-failed-live-run.json`（SHA-256 `440d61a31b2bc72e4d4346807fbdac5c34bcf2abb0b1f29ac112e9c9d8aacf22`）是一次失敗的 live run：harness `seed_proto()` 將 work 卡 parent 寫成字面 `t_setup`，與真 board 上的 setup id 不同，v2 的 `work_parent_mismatch` 正確 fail closed。修正 seed 參數後重跑 42/42；此檔保留原樣，不重標為通過。

## 5. 證據 hash 綁定

| 檔案 | SHA-256 |
|---|---|
| `evidence/sandbox-unit.log` | `bab29daa84ceb51d1532a6587059e908cd1809c95ae83b9caae44932be9b3916` |
| `evidence/focused-final.log` | `b82cf20bb75511419618aca67164bf6b6df018422220c00c3b6cadcdeada3dc1` |
| `evidence/sandbox-live.log` | `dd6ba34247fbab95f0ebbf3d3740c7f675009ae722612df14b55c2898f11a92d` |
| `evidence/red-p1-frozen-v1-repro.log` | `000b3451c58deb96116e1efb646485d718a3e751b1b7bb5f0e7818349c2c83e4` |
| `live-lab/evidence/assertions.json` | `8a9452e65c9ffb66e4a98472d2cd0b9858ca152d7ff7cb74e8896c4dfa475d05` |
| `live-lab/evidence/result.json` | `97e8c2750e21052deb2f6fa2feabef9c6295e8d40840d6499e4281f07203dbc6` |
| `live-lab/evidence/crash-run.json` | `4bcb888c3eef0386dc155ca0aec63789e467721cbece216a7426d540a352c830` |
| `live-lab/evidence/crash-recovery.json` | `77234dcda2e11bc2d818d207d5246722adebd50bce7729f2bd52da4db6b52deb` |
| `evidence/v1-immutability.json` | `ecc34a741d5d2bb04f33eebdf7b2178ba99fe9ae1aace05f07914483b9cd22f7` |

v1 manifest：`26f6046b714f0289b9aafa6e9c6df360b27d30180629f7127899943d51645b97`（193 行、193 不重複、0 missing、0 unlisted、0 mismatch）。

## 6. 重跑入口

```bash
cd <repo>/.research/prototype-v2
./scripts/run_unit.sh          # focused + full unit (57)
./scripts/sandbox.sh --probe   # namespace 能力檢查；不可用時 rc=78
./scripts/sandbox.sh unit      # 完整 unit 在 namespace 內
./scripts/sandbox.sh live      # 全新 lab run；42/42
```

`live_stage.py` 每次啟動會重置 `live-lab/{os-home,board,xdg,runtime,tmp,proto-*}` 與 JSONL 證據；不會觸碰 lab 外的正式資源。凍結的 manifest 是針對最後一次成功的 lab 狀態。

## 7. 已知限制（與 PROTOTYPE-REPORT 一致）

- synthetic model 無推理；不代表真模型品質。
- live 只走 proven 的 `completed` wake；`review_requested`/`changes_requested` 事件種類未實測。
- live 只注入 final delivery 一個 crash 邊界；其餘邊界為離線真程序死亡測試。
- 不宣稱 exactly-once；read-back 不可用時維持 `unknown` 並告警，不重送。
- 受信任單寫入者不是不可繞過 ACL；shared board metadata 不保密；並行 reconciler 未裁決。
- live 的 event→acceptance 由手動 harness 串接固定 payload，不是 autonomous E2E。
