#!/bin/bash
set -euo pipefail
LAB=/home/ubuntu/projects/hermes-department-orchestrator/.research/route-lab-377
SRC=/home/ubuntu/.hermes/hermes-agent
if [[ ${1:-} != inside ]]; then
  exec unshare --user --map-root-user --mount --net --pid --fork --kill-child --mount-proc "$0" inside
fi
mount --make-rprivate /
mount -t tmpfs tmpfs /mnt
mkdir /mnt/source /mnt/lab /mnt/python
# The installed venv's interpreter is a symlink into uv's Python runtime.
mount --bind /home/ubuntu/.local/share/uv/python /mnt/python
mount -o remount,bind,ro /mnt/python
mount --bind "$SRC" /mnt/source
mount -o remount,bind,ro /mnt/source
mount --bind "$LAB" /mnt/lab
# The real home, profiles, board, credentials, sockets, other projects vanish.
mount -t tmpfs tmpfs /home/ubuntu
mkdir -p "$SRC" "$LAB" /home/ubuntu/.local/share/uv/python
mount --bind /mnt/python /home/ubuntu/.local/share/uv/python
mount --bind /mnt/source "$SRC"
mount --bind /mnt/lab "$LAB"
mount -t tmpfs tmpfs /run
mount -t tmpfs tmpfs /tmp
ip link set lo up
cd "$LAB"
exec env -i PATH=/usr/bin:/bin:/usr/sbin LANG=C.UTF-8 TZ=Asia/Taipei \
  HOME="$LAB/os-home" HERMES_HOME="$LAB/os-home/.hermes" \
  HERMES_KANBAN_HOME="$LAB/board" HERMES_KANBAN_DB="$LAB/board/kanban.db" HERMES_KANBAN_BOARD=default \
  XDG_CONFIG_HOME="$LAB/xdg/config" XDG_CACHE_HOME="$LAB/xdg/cache" XDG_DATA_HOME="$LAB/xdg/data" XDG_STATE_HOME="$LAB/xdg/state" XDG_RUNTIME_DIR="$LAB/runtime" \
  TMPDIR="$LAB/tmp" TMP="$LAB/tmp" TEMP="$LAB/tmp" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HERMES_DISABLE_LAZY_INSTALLS=1 UV_OFFLINE=1 PIP_NO_INDEX=1 \
  "$SRC/venv/bin/python" "$LAB/scripts/runner.py"
