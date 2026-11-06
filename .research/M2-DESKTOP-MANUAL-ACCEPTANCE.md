# Department Hub M2 — 真實 Desktop 手動驗收包

- 版本：M2 candidate（對齊 `department-hub/m1-contract/v4.1`）
- 審查卡：`t_750e8a14`（reviewer）
- 安裝來源：`/home/ubuntu/.hermes/kanban/workspaces/t_c410652a/candidate`
- 封裝檔：`/home/ubuntu/.hermes/kanban/workspaces/t_750e8a14/department-hub-m2-candidate.tar.gz`
  - sha256 `cdd15ad32adc239979b19c88844f95385fb88f106877ff56dd8cdf4c662d6ef6`
- 逐檔 manifest：`candidate-manifest.sha256`
  - sha256 `b86be90380b0b46d8cdea07a6a2e6ad32d925edd2f46366997e97f550feeac16`
  - 52/52 `sha256sum -c` 已在本次審查通過

> **不要安裝 `/home/ubuntu/projects/hermes-department-orchestrator/`。**
> 該目錄仍是 M1 v1 + 第一輪 M2（202 tests），不是本次對齊 v4.1 的候選；缺少
> `department_lib/artifacts.py`、`docs/m1-v4-contract.md`、M2 契約測試與 M1 v2–v4.1 修復。
> 本次只授權安裝上面的 candidate 封裝。

---

## 0. 安裝前檢查（全部必須符合，任一不符就停止並回報）

在跑 gateway 的主機（這台 Oracle，`/home/ubuntu`）執行：

```bash
test ! -e ~/.hermes/plugins/department-hub && echo "OK agent-half absent"
test ! -e ~/.hermes/desktop-plugins/department-hub && echo "OK desktop-half absent"
! grep -q 'department-hub' ~/.hermes/config.yaml && echo "OK config has no department-hub"
test ! -e ~/.hermes/department-hub/state.db && echo "OK state db absent"
hermes -p default gateway status
ss -ltnp | grep 8644 || true
```

本次審查時的實測現況（2026-11-06 04:0x UTC）：

| 檢查 | 現況 |
|---|---|
| `~/.hermes/plugins/department-hub` | 不存在 |
| `~/.hermes/desktop-plugins` | 整個目錄不存在（安裝時才建立） |
| `~/.hermes/config.yaml` 的 `plugins.enabled` | 沒有 `department-hub` |
| `~/.hermes/department-hub/state.db` | 不存在 |
| gateway | 執行中：default-profile multiplexer，PID 1124，`127.0.0.1:8644` |
| `GET /api/plugins/department-hub/health` | 目前 404（plugin 尚未安裝，符合預期） |

若上述任何一項已經存在（同名 plugin、symlink、config 已有 `department-hub`）：
**停止，不要覆寫**；先回報既有項目與來源，由 Oneal 決定是否 `--force` 或改用其他 id。

另外確認：

```bash
# 不可在 worker 正在跑的時候重啟 gateway
hermes kanban list --status running 2>/dev/null || hermes kanban list
```

本次審查本身是一個 Kanban worker；請等本卡結束、board 上没有 running worker 再重啟。

---

## 1. 備份與解封裝

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a ~/.hermes/config.yaml ~/.hermes/config.yaml.pre-department-hub.$STAMP
sha256sum ~/.hermes/config.yaml > ~/.hermes/config.yaml.pre-department-hub.$STAMP.sha256

STAGE=~/m2r-stage
BUNDLE=/home/ubuntu/.hermes/kanban/workspaces/t_750e8a14/department-hub-m2-candidate.tar.gz
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar -xzf "$BUNDLE" -C "$STAGE"
cd "$STAGE"
sha256sum -c candidate-manifest.sha256 | tail -3
```

預期：`candidate-manifest.sha256` 全部 `OK`（52 檔）。若任何一行 `FAILED`，停止，不要安裝。

---

## 2. 精確安裝步驟

### 2.1 Agent half（plugin + dashboard backend）

```bash
install -d ~/.hermes/plugins/department-hub
cp -a "$STAGE/plugin.yaml" "$STAGE/__init__.py" \
      "$STAGE/department_lib" "$STAGE/dashboard" \
      ~/.hermes/plugins/department-hub/
```

不要複製 `tests/`、`docs/`、`.research/` 到 plugin 目錄；它們不是 runtime 必需。
確認：

```bash
test -f ~/.hermes/plugins/department-hub/plugin.yaml
test -f ~/.hermes/plugins/department-hub/__init__.py
test -f ~/.hermes/plugins/department-hub/dashboard/plugin_api.py
test -f ~/.hermes/plugins/department-hub/department_lib/artifacts.py
```

### 2.2 Desktop half

```bash
install -d ~/.hermes/desktop-plugins/department-hub
install -m 0644 "$STAGE/desktop/department-hub/plugin.js" \
  ~/.hermes/desktop-plugins/department-hub/plugin.js
test -s ~/.hermes/desktop-plugins/department-hub/plugin.js
```

資料夾名稱必須是 `department-hub`（等於 plugin id）。若 Oneal 的 Desktop 是原生跑在
Mac、使用 Mac 自己的 `~/.hermes`，同一份 `plugin.js` 也要放到 Mac 的
`~/.hermes/desktop-plugins/department-hub/`；本包的 gateway 步驟只適用這台 Oracle。

### 2.3 編輯 `~/.hermes/config.yaml`

目前 `plugins:` 區塊在 line 753 附近，只有 `enabled` / `disabled`，**沒有 `entries`**。
做兩件事：

1. 在 `plugins.enabled` 清單加一行 `- department-hub`。
2. 在 `plugins:` 下新增 `entries:` 區塊（縮排與 `enabled` 同級）。

完成後長這樣（只列相關部分）：

```yaml
plugins:
  enabled:
    - advanced-delegation
    - claude-subscription-directsdk-experimental
    - disk-cleanup
    - hermes-mobile-device-access
    - homeassistant
    - memory-wiki
    - office-3d
    - rea
    - spotify
    - department-hub          # ← 新增
  disabled: []
  entries:                    # ← 這個 key 原本不存在
    department-hub:
      settings:
        state_path: /home/ubuntu/.hermes/department-hub/state.db
        departments:
          - id: dev
            name: Development
            manager:
              profile: default          # 先用 default 做單一 profile 的最小驗證
              platform: api_server      # 佔位；真實 wake 需改成 manager 的 platform/chat_id
              chat_id: api-dev-session
            members: [normal-worker]
            reviewers: [reviewer]
            max_repair_rounds: 2
```

注意：

- `manager.profile` 必須是一個已存在、且已啟用 `kanban` toolset 的 profile。用 `default`
  是最小安裝面；若改用其他 profile，該 profile 也要安裝並啟用 `department-hub`（見 §2.5）。
- `platform` / `chat_id` 是 wake subscription 的比對值。`api_server` / `api-dev-session` 只是
  形狀佔位，`department_bind` 會回 `session_owner_unverified`；UI 會如實顯示
  `unconfirmed (ensured_not_awake)`，這是正確的，不是安裝失敗。要測真實 wake，請填
  manager 實際的 platform 與 chat_id；**不要猜 credentials**。
- 不要用 tabs；YAML 縮排錯會讓 gateway 起不來。備份檔已在上一步建立。

### 2.4 重啟 gateway

```bash
hermes -p default gateway restart
hermes -p default gateway status
```

預期：gateway 重新啟動並回報 running。這一步會中斷所有正在跑的 Kanban worker 與訊息處理；
必須在 idle 時段執行。

若 restart 失敗（例如 service 未安裝），先用 `hermes -p default gateway stop` 再
`hermes -p default gateway start`；仍然失敗就依 §4 還原 config，不要把 gateway 留在半啟動狀態。

### 2.5 後端 health 檢查

```bash
curl -sS http://127.0.0.1:8644/api/plugins/department-hub/health | python3 -m json.tool
```

預期（local loopback、gateway 未開 auth）：

```json
{
  "schema": "department-hub/health/v1",
  "plugin": "department-hub",
  "state": "ok",
  "read_only": true,
  "caller": {"kind": "loopback", "profile_bound": false},
  "kanban": {"available": true, ...},
  "departments": 1,
  "limits": {"refresh_seconds": 10, "max_tasks_per_department": 50}
}
```

- `404`：plugin 沒有被 gateway 載入 → 檢查 `plugins.enabled`、plugin 目錄、gateway log。
- `401`：gateway 開了 auth；這是正確的拒絕，請用 Desktop（它會帶 authenticated principal），
  不要把 auth 關掉來繞過。
- `state: "degraded"`：board 或 config 讀取失敗；先修 board/config，不要當成 UI bug。

### 2.6 若要跑完整多 profile chain（非第一次視覺驗收必需）

`department_submit` 需要 member/manager 呼叫，`department_accept` / `department_deliver` 需要
manager。若 manager 不是 `default`，把 agent half 也安裝到該 profile：

```bash
install -d ~/.hermes/profiles/<manager>/plugins/department-hub
cp -a "$STAGE/plugin.yaml" "$STAGE/__init__.py" \
      "$STAGE/department_lib" "$STAGE/dashboard" \
      ~/.hermes/profiles/<manager>/plugins/department-hub/
# 並在 ~/.hermes/profiles/<manager>/config.yaml 的 plugins.enabled 加 department-hub，
# 以及相同的 plugins.entries.department-hub.settings（state_path 指到同一個絕對路徑）。
```

worker / reviewer profile 若要自己呼叫 `department_submit` / `department_ingest`，也比照辦理。
本次 M2 沒有 installer，以上是手動 operator 步驟，不是原子安裝。

---

## 3. 開啟 Desktop 與預期畫面

1. 開 Hermes Desktop → **Settings → Plugins**。
2. 找到 **Department Hub**（`defaultEnabled: false`，預設關閉）→ 開啟。
3. Sidebar 應出現 **Departments**（zh-hant：**部門**）。
4. Command palette 搜 **Open departments**（zh-hant：**開啟部門管理**）→ Enter。
5. 直接 route：`/department-hub`。

> 真實畫面本次審查**沒有**觀察過（此主機沒有 Desktop app；`~/.hermes/desktop` 只有一個
> `interrupted_turns.json.lock`）。以下文字是程式與 SDK mock 的**預期**輸出；必須由 Oneal
> 看到畫面後才能標成 PASS。

### 3.1 頁面固定元素

| 位置 | 應看到（en / zh-hant） |
|---|---|
| Header | `Departments` / `部門` |
| Footer | `Read-only view. A Hermes profile is a routing/identity binding, not a security sandbox. No credentials or transcripts are shown.` / `唯讀檢視。Hermes profile 是路由／身分綁定，不是安全沙箱。不顯示憑證或對話內容。` |
| Snapshot | `Server snapshot: <time>. Status is an observed snapshot; it is not a live push.` / `伺服器快照：<time>。狀態是觀測快照，不是即時推播。` |
| Manager panel | `Manager status (three separate checks, never one light)`，三個獨立欄位：`Subscription` / `Session owner` / `Wake confirmed`；不得只有一個綠燈 |
| Manager values | `subscribed · N matching · M foreign`、`unverified (session_owner_unverified)`、`unconfirmed (ensured_not_awake)` |
| Empty config | `No departments configured` + `Add one under plugins.entries.department-hub.settings.departments, then restart the gateway. This page cannot write config.` |
| No bound cards | `No bound cards` + `Bind a board-verified root card assigned to this manager (department_bind) to see work here.` |
| Read failure | `Cannot read departments` / `The backend did not answer: <detail>`；**不得**顯示空清單 |

### 3.2 每個狀態應看到什麼／不該看到什麼

| 狀態 | 應看到（en；zh-hant 對應） | **不該**看到 |
|---|---|---|
| **ready**（manager 已接受 exact evidence） | `Ready to deliver — manager-accepted evidence: revision N · review run N · worker-declared SHA-256 <short>`；下一行 `Ready to deliver means the manager accepted this exact revision / run / SHA. The artifact hash is supplied by the worker; it is not a reviewer byte attestation.`；另有 `manager accepted revision N` | `final report: delivered`、`already delivered`、`reviewer verified`、`reviewer 已驗證`、`reviewer 蓋章` |
| **held / review** | `review held (<code>)` + `held: <code>`；`not accepted by the manager`；`delivery held (<code>)` | `review PASS`（若 held）、`Ready to deliver`、`final report: delivered` |
| **held / delivery** | `delivery held (<code>)`（例如 `delivery_identity_conflict`、`effect_fencing_unavailable`、`acceptance_missing`） | `Ready to deliver`、`already delivered`、`final report: delivered` |
| **unknown** | `final report: unknown (<code>)`；`delivery unknown (<code>) — not resent` | `final report: delivered`、`already delivered`、重送字樣 |
| **dead-letter** | `final report: dead-letter (<code>)`；`delivery dead-letter (<code>)` | `Ready to deliver`、`already delivered` |
| **conflict**（第二世代已 delivered 後又有新 PASS） | `delivery held (delivery_identity_conflict)`；`delivery identity conflict — this generation is not deliverable; revision N · run N · PASS`；`delivered identity: revision N · run N · PASS` | `Ready to deliver`、`可再次交付`、把新世代顯示成 delivered |
| **in-flight**（有 delivery intent 但未 read-back） | `final report: in flight (unconfirmed; never resent)` | `already delivered`、`final report: delivered` |
| **delivered**（confirmed row） | `final report: delivered`；`delivered identity: revision N · run N · PASS`；`already delivered · revision N · run N · PASS` | `Ready to deliver` |

### 3.3 重要：stock port 下 `delivered` 可能看不到

M1 v1 的 stock `CommentEffectPort` 沒有上游 generation + comment 的原子 fence，因此
`department_deliver` 在正式 plugin 會回：

```text
action = held
reason = effect_fencing_unavailable
```

本次獨立 probe 已重現：`accept` 回 `releasable`，但 `release` 回
`held/effect_fencing_unavailable`，comment 0 次。這是已接受的 v1 範圍決策，不是 M2 bug。
**因此正式環境的 UI 很可能停在 `Ready to deliver`，不會出現 `final report: delivered`。**
要看到 `delivered` / `already_delivered` / 第二世代 conflict，必須用 fake/local effect port
（測試或隔離 fixture），不要為此改 production port 或假造 board comment。

### 3.4 建議截圖清單

1. Sidebar + `/department-hub` 全頁（1280×800）。
2. 窄視窗（約 720px）——確認沒有水平爆版、manager 三欄變成單欄。
3. 鍵盤：Tab 進部門 list → ArrowDown / ArrowUp / Home / End → Enter；截圖 focus 與選取文字。
4. zh-hant 與 en 各一張（sidebar / header / footer 文字）。
5. 空狀態（`No departments configured`）。
6. 若已有 bound card：一張 held、一張 ready（含 caveat 行）；若沒有，明確記錄「未觀察到」，
   不要用 SDK mock 截圖冒充 real Desktop。
7. 讀取失敗狀態（可暫時把 `state_path` 指到不可讀路徑，或停 gateway 後開頁；記得還原）。

---

## 4. 可逆移除（只刪本次建立且可證明所有權的項目）

```bash
# 1) 停 gateway（會中斷 worker；確認 idle）
hermes -p default gateway stop

# 2) 移除本次安裝的 plugin（不要動其他 plugin）
rm -rf ~/.hermes/plugins/department-hub
rm -rf ~/.hermes/desktop-plugins/department-hub

# 3) 還原 config（用 §1 的備份；把 <STAMP> 換成實際值）
cp -a ~/.hermes/config.yaml.pre-department-hub.<STAMP> ~/.hermes/config.yaml
sha256sum -c ~/.hermes/config.yaml.pre-department-hub.<STAMP>.sha256

# 4) 啟動並確認
hermes -p default gateway start
hermes -p default gateway status
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8644/api/plugins/department-hub/health
hermes plugins list | grep department-hub || echo "OK: plugin removed"
```

Plugin 自己的 state DB（`~/.hermes/department-hub/state.db`）**不會**被上述步驟刪除；它含
department registry、receipts、acceptance/delivery rows（沒有 chat transcript）。要徹底清除：

```bash
mv ~/.hermes/department-hub/state.db ~/.hermes/department-hub/state.db.pre-m2r
# 或確認不需要後 rm -rf ~/.hermes/department-hub
```

不要刪 `~/.hermes/config.yaml` 以外的其他 profile 檔案；不要把 `desktop-plugins` 整個目錄刪掉
（裡面可能已有其他 plugin）。

---

## 5. 風險與回報格式

**風險**

1. **gateway 重啟會中斷所有正在跑的 Kanban worker、訊息與排程**；必須在 idle 時段做，且不能在
   本審查 worker 尚未結束時做。
2. YAML 寫錯會讓 gateway 起不來；備份與還原步驟已列，發生時先還原再回報。
3. Loopback API 在 gateway 未開 auth 時沒有 authentication（`caller.kind=loopback`，
   `profile_bound=false`）；不要把 `8644` 暴露到公網，也不要把這個 API 當成有 profile ACL。
4. `Ready to deliver` 只代表 manager 接受了 worker 宣告的 revision/run/SHA，**不是** reviewer
   對 bytes 的驗證；artifact SHA 不在 delivery 時重算。
5. stock port 下 `department_deliver` 必然 `held/effect_fencing_unavailable`（見 §3.3）。
6. Profile 不是安全沙箱；M2 沒有 installer、沒有 profile ACL、沒有自動交付。
7. 不要 push、deploy、發 X、建 catalog PR、改 FreshTape 或正式 profiles/通知/服務。

**回報格式（請回傳）**

```text
安裝主機 / Desktop 版本：
安裝時間：
config 備份路徑 + sha256：
manifest 驗證：PASS/FAIL（貼 tail -3）
health：HTTP <code>，state=<state>，departments=<n>
Desktop：route/sidebar/palette 是否出現（Y/N）
截圖：<檔名 list>（含 en、zh-hant、窄版、空狀態、held/ready 若有）
鍵盤：Arrow/Home/End/Enter 是否可用（Y/N）
read-only footer：Y/N
manager 三欄：Y/N
ready caveat 文字：Y/N
禁止字樣檢查：PASS/FAIL（列出看到什麼）
未觀察到的狀態：<list>（不要推測）
問題：<逐項，附截圖與時間>
```

若任何一項 FAIL，先把畫面與 gateway log 留下，依 §4 還原，再回報；不要用「大部分看起來可以」
給 PASS。

---

## 6. 本次審查已做／未做

**已做（離線）**

- candidate manifest 52/52、逐檔 hash、與 M1 v4.1 product 的 6 改 + 1 新增比對。
- `pytest` 377 passed、contract 42 passed、node --check rc0、SDK mock 122 checks、plugin validate
  16 checks / 0 warnings、plugin doctor rc0、standalone import 8 routes。
- 獨立 runtime probe 23 checks：ingest/duplicate/restart、fail-closed rejection、repair+PASS、
  單次 deliver、read model ready/delivered/conflict、manager 三態、資料最小化、stock fence。
- 後端授權 probe：auth-required 8/8 401；mutation 3/3 409 且不建 service；header 不能冒充 caller；
  無 escalation/deliver mutation route。

**未做（必須由 Oneal 手動）**

- 真實 Desktop 載入、像素渲染、native grant/OAuth、實際 `/department-hub` 畫面。
- gateway 重啟與真實 auth token 呼叫。
- live model / manager wake。
- 安裝/移除的原子性與 rollback（M2 沒有 installer；本包是手動步驟）。

**本次審查結論：離線 candidate PASS；真實 Desktop `UNVERIFIED — awaiting manual install`。**
