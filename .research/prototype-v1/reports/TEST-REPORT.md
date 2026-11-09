# Department Hub 原型 v1｜測試報告（可重跑）

所有命令在 `<repo>/.research/prototype-v1` 執行。Python：`~/.hermes/hermes-agent/venv/bin/python`（3.11.15）。pytest 9.0.2。

## 1. 失敗測試先行（red）

```
$ ~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q -p no:cacheprovider
ERROR tests/test_domain.py
ERROR tests/test_events.py
ERROR tests/test_preflight.py
E   ModuleNotFoundError: No module named 'deptproto'
3 errors in 0.14s
rc=2
```
完整輸出：`evidence/unit-tests-red.log`。測試先於實作寫成，red 後才建立 `deptproto/`。

## 2. Focused + full unit tests（green）

```
$ ./scripts/run_unit.sh
................................                                  [100%]
34 passed in 5.07s
UNIT_RC=0
```
（`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=prototype-v1`、`TMPDIR=evidence/unit-tmp`、`-p no:cacheprovider`。）

34 項分佈：

- `tests/test_domain.py`（15）：正常 reject→rework→fresh PASS→accept→single final；artifact content-addressed rev；未接受、stale PASS、錯 artifact、錯 owner、重複事件、亂序、非 PASS acceptance、錯 parent（pending manager root）、artifact hash 不符、review 卡=report 卡、錯 report 邊、unknown effect 不宣稱成功、永久不可讀→dead letter。
- `tests/test_events.py`（6）：同 event id 不同 payload 不重套、rejection 可視、缺訂閱 dead letter、admission≠completion、board event admission/receipt/processing、錯 owner board event 拒絕。
- `tests/test_crash_recovery.py`（6）：`after_intent`、`before_effect`、`after_effect_before_receipt`、`after_receipt_before_confirm`、`after_admit_before_process`、並行 deliver 單一 effect。每個 crash 都用真子程序 `os._exit(137)`，重啟後斷言 effect=1、receipt=1、confirmed、再次 deliver=already_delivered。
- `tests/test_preflight.py`（7）：合法 lab 通過；production DB、lab 外 HOME、symlink 逃逸、worker pin/SSH/proxy、缺 lazy-install guard、缺路徑皆 fail-before-start。

## 3. 隔離 live stage（真 gateway + synthetic model）

```
$ ./scripts/sandbox.sh --probe
unshare-ok
rc=0

$ ./scripts/sandbox.sh
live_rc=0
```
- console：`evidence/live-console.log`（本目錄）；sandbox 內完整證據：`live-lab/evidence/`。
- 斷言：`live-lab/evidence/assertions.json` → **40/40 pass**。
- 受測：真 `hermes gateway run`，profiles `default` / `manager` / `reviewer`；deterministic synthetic model 監聽 `127.0.0.1:18251`，gateway `127.0.0.1:18252`。
- Session：default `api-0b36333bf4394fee`、manager `api-9d018bfc598b4849`、reviewer `api-5e66804d3c85a299`；各自 `state.db` 的 `profile_name` 相符（`session-identities.json`）。
- 錯 profile key → 401；三 session 互異。

### Live 案例與精確 ID

| 案例 | ID / 結果 |
|---|---|
| manager 真 wake + tool effect（work child） | `t_66a88d72`，comment author=manager，`SYNTHETIC_ROUTE_RECEIPT t_66a88d72` |
| review 1 REQUEST_CHANGES | `t_d1701454`；reviewer comment `{"verdict":"REQUEST_CHANGES","artifact_sha":"2e2b159a…"}`；prototype applied |
| rework + review 2 PASS（新 artifact） | `t_b574d38d`；`{"verdict":"PASS","artifact_sha":"ef65e542…"}`；prototype applied |
| manager acceptance | `t_2d0d6acc`；`{"decision":"ACCEPT","artifact_sha":"ef65e542…"}`；prototype applied |
| 單一 final | `t_ce23ba7a`；`dlv-15d4ce7e7d3c`，marker `[[deptproto-final:15d4ce7e7d3cadde]]`，report 恰 1 個；重複 deliver=already_delivered |
| default 收 report | report 完成後 default wake + receipt；default 對 work/review/accept 卡無訂閱、無 receipt |
| 缺訂閱 | `t_f444f075`；無 wake；`dead_letter/missing_subscription` |
| 錯 owner（manager owner + default session） | `t_034dc30c`；無 wake；`dead_letter/wrong_owner` |
| 錯 report 邊 | `t_6d83ba76` 被 `t_3fd2c66` link；`held/wrong_report_edge`；report 0 comment |
| live crash + recovery | `t_de718ca5`；crash rc=137，board 已 1 個 final；recover → confirmed；仍 1 個 comment，receipts=1 |

### Live 隔離／清理

- namespaces：mount/net/pid/user 皆與 host 不同（`live-lab/evidence/preflight.json`）。
- network：interfaces 只有 `lo`，`routes=[]`。
- 正式資源遮蔽：`~/.hermes/profiles`、正式 `kanban.db`、`/run/user/1000`、Obsidian vault 皆不存在；source 與 uv Python 寫入 probe 皆 EROFS。
- 啟動前負向：production `HERMES_KANBAN_DB`、`/run/user/1000` XDG、`HERMES_KANBAN_TASK` 都在 spawn 前被拒（`assertions.json` 前 3 項）。
- cleanup：gateway SIGTERM rc=1（非 graceful 0）、model rc=-15，全部 reaped；`ss -lntup` 僅表頭（`live-lab/evidence/cleanup.json`）。
- 第一次 live stage 在前置修正前有 staging error（`live-lab/evidence/error-stage-2.json`，已保留不刪）；最終 run 為 rc=0、40/40。

## 4. 重跑入口

```bash
cd <repo>/.research/prototype-v1
./scripts/run_unit.sh          # focused + full unit (34)
./scripts/sandbox.sh --probe   # namespace 能力檢查；不可用時 rc=78
./scripts/sandbox.sh           # 全新 lab run；重置 lab state、保留 console log
```
`live_stage.py` 每次啟動會重置 `live-lab/{os-home,board,xdg,runtime,tmp,proto-*}` 與 JSONL 證據，因此可重跑；不會觸碰 lab 外的正式資源。

## 5. 已知限制（與 PROTOTYPE-REPORT 一致）

- synthetic model 無推理；不代表真模型品質。
- live 只走 proven 的 `completed` wake；`review_requested`/`changes_requested` 未以事件種類實測。
- 只 live 注入 final delivery 一個 crash 邊界；其餘邊界為離線真程序死亡測試。
- 不宣稱 exactly-once；read-back 不可用時是 `effect_unknown`/`dead_letter`。
- 受信任單寫入者不是不可繞過 ACL；shared board metadata 不保密。
