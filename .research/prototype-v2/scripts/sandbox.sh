#!/bin/bash
# Portable fail-before-start OS isolation launcher (prototype v2).
#
# Isolation: user + mount + network + PID namespaces. The real HOME (profiles,
# boards, credentials, sockets) is replaced by tmpfs; only this prototype tree,
# the installed Hermes source (read-only) and the uv Python runtime (read-only)
# are mounted back. There is no external route; only loopback exists.
#
# Usage:
#   ./scripts/sandbox.sh --probe     # unshare capability; rc 78 if unavailable
#   ./scripts/sandbox.sh unit        # full offline unit suite in the sandbox
#   ./scripts/sandbox.sh live        # real isolated gateway + synthetic model
#
# If unshare is unavailable the launcher fails closed with rc 78 and no Hermes
# process is started.
set -euo pipefail

PROTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB="$PROTO/live-lab"
SRC=/home/ubuntu/.hermes/hermes-agent
MODE="${1:-unit}"

if [[ "$MODE" == "--probe" ]]; then
  if unshare --user --map-root-user --mount --net --pid --fork --kill-child \
       --mount-proc sh -c 'test "$(id -u)" = 0' 2>/dev/null; then
    echo "unshare-ok"
    exit 0
  fi
  echo "fail-closed: unshare unavailable" >&2
  exit 78
fi

if [[ "$MODE" != "inside" ]]; then
  if ! unshare --user --map-root-user --mount --net --pid --fork --kill-child \
       --mount-proc true 2>/dev/null; then
    echo "fail-closed: unshare unavailable; no process started" >&2
    exit 78
  fi
  exec unshare --user --map-root-user --mount --net --pid --fork --kill-child \
    --mount-proc "$0" inside "$MODE"
fi

INNER_MODE="${2:-unit}"

mount --make-rprivate /
mount -t tmpfs tmpfs /mnt
mkdir -p /mnt/proto /mnt/source /mnt/python
mount --bind "$PROTO" /mnt/proto
mount --bind /home/ubuntu/.local/share/uv/python /mnt/python
mount -o remount,bind,ro /mnt/python
mount --bind "$SRC" /mnt/source
mount -o remount,bind,ro /mnt/source

# The production home, profiles, boards, credentials and sockets disappear.
mount -t tmpfs tmpfs /home/ubuntu
mkdir -p "$PROTO" "$SRC" /home/ubuntu/.local/share/uv/python
mount --bind /mnt/proto "$PROTO"
mount --bind /mnt/python /home/ubuntu/.local/share/uv/python
mount --bind /mnt/source "$SRC"
mount -t tmpfs tmpfs /run
mount -t tmpfs tmpfs /tmp
ip link set lo up
mkdir -p "$LAB"/{os-home,xdg,runtime,tmp,board,evidence}

cd "$LAB"
exec env -i \
  PATH=/usr/bin:/bin:/usr/sbin LANG=C.UTF-8 TZ=Asia/Taipei \
  HOME="$LAB/os-home" HERMES_HOME="$LAB/os-home/.hermes" \
  HERMES_KANBAN_HOME="$LAB/board" HERMES_KANBAN_DB="$LAB/board/kanban.db" \
  HERMES_KANBAN_BOARD=default \
  XDG_CONFIG_HOME="$LAB/xdg/config" XDG_CACHE_HOME="$LAB/xdg/cache" \
  XDG_DATA_HOME="$LAB/xdg/data" XDG_STATE_HOME="$LAB/xdg/state" \
  XDG_RUNTIME_DIR="$LAB/runtime" \
  TMPDIR="$LAB/tmp" TMP="$LAB/tmp" TEMP="$LAB/tmp" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  HERMES_DISABLE_LAZY_INSTALLS=1 UV_OFFLINE=1 PIP_NO_INDEX=1 \
  PROTO_LAB="$LAB" PROTO_ROOT="$PROTO" DEPT_WRITER_ID=trusted-single-writer \
  PYTHONPATH="$PROTO" \
  bash -c '
    set -euo pipefail
    PY="$1/venv/bin/python"
    case "$2" in
      unit)
        "$PY" -c "import json,os; from deptproto.preflight import assert_lab_env, assert_no_network; print(json.dumps({\"paths\": assert_lab_env(os.environ, os.environ[\"PROTO_LAB\"]), \"network\": assert_no_network()}))"
        exec "$PY" -m pytest "$PROTO_ROOT/tests" -q -p no:cacheprovider
        ;;
      live)
        exec "$PY" "$PROTO_ROOT/scripts/live_stage.py"
        ;;
      *)
        echo "unknown mode: $2" >&2
        exit 2
        ;;
    esac
  ' _ "$SRC" "$INNER_MODE"
