#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
"${PYTHON_BIN:-python3}" -m venv .venv
.venv/bin/python -m pip install -r collector/requirements.txt
printf '%s\n' 'Entwicklungsumgebung bereit. Tests: bash scripts/test-offline.sh'
