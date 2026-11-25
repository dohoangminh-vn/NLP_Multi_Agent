#!/usr/bin/env bash
set -euo pipefail

# Chọn Python executable hợp lệ (ưu tiên PYTHON_BIN nếu có, sau đó python3/python có pytest)
resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "$PYTHON_BIN"
    return 0
  fi

  local candidates=(
    "python3"
    "python"
    "/c/Users/ADMIN/AppData/Local/Programs/Python/Python313/python.exe"
    "/c/Users/ADMIN/AppData/Local/Programs/Python/Python311/python.exe"
  )

  for cmd in "${candidates[@]}"; do
    if command -v "$cmd" >/dev/null 2>&1 || [[ -x "$cmd" ]]; then
      if "$cmd" -c "import pydantic, pytest, ruff" >/dev/null 2>&1; then
        echo "$cmd"
        return 0
      fi
    fi
  done

  echo "ERROR: Khong tim thay Python co du pydantic, pytest, ruff." >&2
  return 1
}

PY="$(resolve_python)"
echo "[init.sh] Using Python: $PY ($("$PY" --version))"

echo "[init.sh] Checking required directories..."
for dir in "data/raw_pdfs" "data/processed" "data/indexes" "src/rag" "tests/rag"; do
  if [[ ! -d "$dir" ]]; then
    echo "ERROR: Missing directory: $dir" >&2
    exit 1
  fi
done

echo "[init.sh] Checking core textbook PDF..."
if [[ ! -f "data/raw_pdfs/sgk-tin-hoc-10-kntt.pdf" ]]; then
  echo "ERROR: Missing data/raw_pdfs/sgk-tin-hoc-10-kntt.pdf" >&2
  exit 1
fi

echo "[init.sh] Running pytest tests/rag..."
"$PY" -m pytest tests/rag -v

echo "[init.sh] Running ruff check..."
"$PY" -m ruff check src/rag tests/rag

echo "[init.sh] Baseline verification PASSED."
