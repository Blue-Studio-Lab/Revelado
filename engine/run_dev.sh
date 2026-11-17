#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Start the Revelado engine for local development: http://127.0.0.1:8790 (token "dev" accepted).
# Machine-specific paths go in engine/.env (gitignored); see engine/.env.example.
set -euo pipefail
cd "$(dirname "$0")"
PY="${REVELADO_PYTHON:-$(command -v python3)}"
export REVELADO_DEV="${REVELADO_DEV:-1}"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
"$PY" - <<'PYCHECK'
import importlib.util, sys
missing = [m for m in ("fastapi", "uvicorn", "pydantic", "numpy", "scipy", "cv2", "PIL", "rawpy", "psutil", "tifffile", "imagecodecs")
           if importlib.util.find_spec(m) is None]
if missing:
    sys.exit("Faltan dependencias de Python: " + ", ".join(missing) +
             "\n  pip install -e 'engine[ai,dev]'   (o pip install " + " ".join(missing) + ")")
PYCHECK
exec "$PY" -m uvicorn revelado.api.app:app --host 127.0.0.1 --port "${REVELADO_PORT:-8790}" --workers 1 "$@"
