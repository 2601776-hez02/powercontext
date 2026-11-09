#!/usr/bin/env bash
# Create the virtual environment, install pinned dependencies and initialise seekdb.
#
#   ./scripts/bootstrap.sh
#   SDD_PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple ./scripts/bootstrap.sh   # faster in mainland China
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  echo "creating .venv with $PYTHON_BIN ($($PYTHON_BIN -V 2>&1))"
  "$PYTHON_BIN" -m venv .venv
fi

if command -v uv >/dev/null 2>&1; then
  install=(uv pip install --python .venv/bin/python)
  if [ -n "${SDD_PYPI_INDEX:-}" ]; then install+=(--default-index "$SDD_PYPI_INDEX"); fi
else
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  install=(.venv/bin/python -m pip install)
  if [ -n "${SDD_PYPI_INDEX:-}" ]; then install+=(--index-url "$SDD_PYPI_INDEX"); fi
fi

echo "installing requirements.txt (installer: ${install[0]})"
"${install[@]}" -r requirements.txt

.venv/bin/python - <<'PY'
import importlib.metadata as md

for name in ("powercontext", "pylibseekdb", "pymysql", "requests", "pytest"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        raise SystemExit(f"missing dependency: {name}")

import powercontext  # noqa: F401
import pylibseekdb  # noqa: F401
import pymysql  # noqa: F401
import requests  # noqa: F401

print("imports ok; embedded seekdb binding available")
PY

source scripts/env.sh
ensure_runtime
step "initialising the engineering-material index"
sdd init
