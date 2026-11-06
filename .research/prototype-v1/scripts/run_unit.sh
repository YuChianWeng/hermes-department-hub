#!/bin/bash
# Focused + full unit run, hermetic env, no bytecode, no pytest cache.
set -u
PROTO=/home/ubuntu/projects/hermes-department-orchestrator/.research/prototype-v1
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python
TMP="$PROTO/evidence/unit-tmp"
mkdir -p "$TMP"
cd "$PROTO" || exit 1
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROTO" TMPDIR="$TMP"
"$PY" -m pytest tests -q -p no:cacheprovider
rc=$?
echo "UNIT_RC=$rc"
exit $rc
