#!/bin/bash
# Focused + full unit run, hermetic env, no bytecode, no pytest cache.
set -u
PROTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python
TMP="$PROTO/evidence/unit-tmp"
mkdir -p "$TMP"
cd "$PROTO" || exit 1
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROTO" TMPDIR="$TMP"
"$PY" -m pytest tests -q -p no:cacheprovider
rc=$?
echo "UNIT_RC=$rc"
exit $rc
