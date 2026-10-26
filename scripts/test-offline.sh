#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -n "${PYTHON_BIN:-}" ]]; then
  zvg_python="$PYTHON_BIN"
elif [[ -x .venv/bin/python ]]; then
  zvg_python=".venv/bin/python"
else
  zvg_python="python3"
fi
"$zvg_python" -m compileall -q collector tests .github/scripts/sync-zvgpro-db.py
for zvg_test in \
  tests/test_parser.py \
  tests/test_detail_parser.py \
  tests/test_state_coverage.py \
  tests/test_official_notice_parsers.py \
  tests/test_report_photo_queue.py \
  tests/test_sync_client.py
do
  "$zvg_python" "$zvg_test"
done
