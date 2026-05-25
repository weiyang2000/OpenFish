#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/apps/web"
MODE="${1:-all}"

usage() {
  cat <<'EOF'
Usage: scripts/run_service.sh [all|api|web]

Starts BettaFish locally without Docker Compose.

Environment overrides:
  BETTAFISH_API_HOST       API bind host, default 0.0.0.0
  BETTAFISH_API_PORT       API port, default 8000
  WEB_HOST                 Next.js bind host, default 127.0.0.1
  WEB_PORT                 Next.js port, default 3000
  NEXT_PUBLIC_API_BASE_URL Frontend API base URL
  NEXT_PUBLIC_WORKSPACE_ID Frontend workspace id, default workspace_demo
  NEXT_PUBLIC_USE_MOCKS    Frontend mock mode, default false
  BETTAFISH_API_RUN_WORKERS Enable real task workers, default true
  BETTAFISH_VENV_DIR        Python venv path, default .venv
  PYTHON_VERSION            Python version for uv venv, default 3.11
  PYPI_INDEX_URL            PyPI mirror for non-CUDA dependencies, default Tsinghua mirror
  PYPI_EXTRA_INDEX_URLS     Space-separated extra PyPI indexes for non-CUDA dependencies
  BETTAFISH_TORCH_VARIANT   auto|cpu|cuda, default auto
  PYTORCH_CUDA_INDEX_URL    PyTorch CUDA wheel index, default cu128
  SYNC_PYTHON_DEPS          Set to 1 to reinstall requirements into existing venv
  SKIP_NPM_INSTALL         Set to 1 to skip installing missing web deps
EOF
}

if [ "$MODE" = "-h" ] || [ "$MODE" = "--help" ]; then
  usage
  exit 0
fi

if [ "$MODE" != "all" ] && [ "$MODE" != "api" ] && [ "$MODE" != "web" ]; then
  usage >&2
  exit 2
fi

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

export BETTAFISH_API_HOST="${BETTAFISH_API_HOST:-0.0.0.0}"
export BETTAFISH_API_PORT="${BETTAFISH_API_PORT:-8000}"
export BETTAFISH_API_DB_PATH="${BETTAFISH_API_DB_PATH:-$ROOT_DIR/data/saas_api.sqlite3}"
export BETTAFISH_API_ARTIFACT_DIR="${BETTAFISH_API_ARTIFACT_DIR:-$ROOT_DIR/data/saas_api_artifacts}"
export BETTAFISH_API_RUN_WORKERS="${BETTAFISH_API_RUN_WORKERS:-true}"
export BETTAFISH_API_CRAWLER_ADAPTER="${BETTAFISH_API_CRAWLER_ADAPTER:-real}"

export WEB_HOST="${WEB_HOST:-127.0.0.1}"
export WEB_PORT="${WEB_PORT:-3000}"
export NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:${BETTAFISH_API_PORT}/api/v1}"
export NEXT_PUBLIC_WORKSPACE_ID="${NEXT_PUBLIC_WORKSPACE_ID:-workspace_demo}"
export NEXT_PUBLIC_USE_MOCKS="${NEXT_PUBLIC_USE_MOCKS:-false}"
export NEXT_TELEMETRY_DISABLED="${NEXT_TELEMETRY_DISABLED:-1}"

VENV_DIR="${BETTAFISH_VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON:-}"
PYPI_INDEX_URL="${PYPI_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PYTORCH_CUDA_INDEX_URL="${PYTORCH_CUDA_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

detect_torch_variant() {
  local requested="${BETTAFISH_TORCH_VARIANT:-auto}"
  local os_name
  os_name="$(uname -s)"

  if [ "$requested" = "cpu" ] || [ "$requested" = "cuda" ]; then
    if [ "$os_name" = "Darwin" ] && [ "$requested" = "cuda" ]; then
      echo "macOS detected; CUDA/NVIDIA dependencies are disabled." >&2
      echo "cpu"
      return
    fi
    echo "$requested"
    return
  fi

  if [ "$os_name" = "Darwin" ]; then
    echo "cpu"
    return
  fi

  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo "cuda"
  else
    echo "cpu"
  fi
}

uv_pypi_args() {
  printf '%s\n' "--index-url" "$PYPI_INDEX_URL"
  for index_url in ${PYPI_EXTRA_INDEX_URLS:-}; do
    printf '%s\n' "--extra-index-url" "$index_url"
  done
}

install_python_requirements() {
  local python_bin="$1"
  local variant="$2"
  local temp_requirements=""
  local req_file="$ROOT_DIR/requirements.txt"
  local -a pypi_args=()

  while IFS= read -r arg; do
    pypi_args+=("$arg")
  done < <(uv_pypi_args)

  if [ "$variant" = "cuda" ]; then
    echo "Installing CUDA PyTorch wheels from ${PYTORCH_CUDA_INDEX_URL}..."
    uv pip install --python "$python_bin" --index-url "$PYTORCH_CUDA_INDEX_URL" torch torchvision torchaudio
    temp_requirements="$(mktemp)"
    awk 'BEGIN { IGNORECASE = 1 } /^[[:space:]]*(torch|torchvision|torchaudio)([<=>[:space:]]|$)/ { next } { print }' "$req_file" > "$temp_requirements"
    echo "Installing non-CUDA Python dependencies from ${PYPI_INDEX_URL}..."
    uv pip install --python "$python_bin" "${pypi_args[@]}" -r "$temp_requirements"
    rm -f "$temp_requirements"
    return
  fi

  echo "Installing Python dependencies from ${PYPI_INDEX_URL}..."
  uv pip install --python "$python_bin" "${pypi_args[@]}" -r "$req_file"
}

ensure_python_env() {
  if [ -n "$PYTHON_BIN" ]; then
    return
  fi

  local torch_variant
  torch_variant="$(detect_torch_variant)"

  if [ ! -x "$VENV_DIR/bin/python" ]; then
    command -v uv >/dev/null 2>&1 || {
      echo "uv is required to create the Python virtual environment." >&2
      echo "Install uv first, then rerun this script: https://docs.astral.sh/uv/" >&2
      exit 1
    }

    echo "Creating Python virtual environment: $VENV_DIR"
    uv venv "$VENV_DIR" --python "${PYTHON_VERSION:-3.11}"
    install_python_requirements "$VENV_DIR/bin/python" "$torch_variant"
  elif [ "${SYNC_PYTHON_DEPS:-0}" = "1" ]; then
    command -v uv >/dev/null 2>&1 || {
      echo "uv is required when SYNC_PYTHON_DEPS=1." >&2
      exit 1
    }
    echo "Syncing Python dependencies for ${torch_variant} platform..."
    install_python_requirements "$VENV_DIR/bin/python" "$torch_variant"
  fi

  PYTHON_BIN="$VENV_DIR/bin/python"
}

check_port() {
  local port="$1"
  local label="$2"
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "$label port $port is already in use." >&2
      exit 1
    fi
  fi
}

ensure_api_deps() {
  "$PYTHON_BIN" - <<'PY'
import fastapi
import uvicorn
PY
}

ensure_web_deps() {
  if [ "${SKIP_NPM_INSTALL:-0}" = "1" ]; then
    return
  fi

  if [ ! -d "$WEB_DIR/node_modules" ]; then
    echo "Installing web dependencies..."
    (
      cd "$WEB_DIR"
      if [ -f package-lock.json ]; then
        npm ci
      else
        npm install
      fi
    )
  fi
}

api_pid=""
web_pid=""

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  for pid in ${web_pid:-} ${api_pid:-}; do
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait ${web_pid:-} ${api_pid:-} >/dev/null 2>&1 || true
  exit "$status"
}

monitor_processes() {
  while true; do
    if [ -n "$api_pid" ] && ! kill -0 "$api_pid" >/dev/null 2>&1; then
      wait "$api_pid"
      exit $?
    fi
    if [ -n "$web_pid" ] && ! kill -0 "$web_pid" >/dev/null 2>&1; then
      wait "$web_pid"
      exit $?
    fi
    sleep 2
  done
}

start_api() {
  mkdir -p "$(dirname "$BETTAFISH_API_DB_PATH")" "$BETTAFISH_API_ARTIFACT_DIR" "$ROOT_DIR/logs"
  ensure_python_env
  ensure_api_deps || {
    echo "Missing API dependencies. Try: SYNC_PYTHON_DEPS=1 scripts/run_service.sh api" >&2
    exit 1
  }

  echo "Starting API: http://localhost:${BETTAFISH_API_PORT}/api/v1"
  (
    cd "$ROOT_DIR"
    exec "$PYTHON_BIN" app.py
  )
}

start_web() {
  command -v npm >/dev/null 2>&1 || {
    echo "npm is required to start the web console." >&2
    exit 1
  }
  ensure_web_deps

  echo "Starting web: http://localhost:${WEB_PORT}"
  echo "Web API base: ${NEXT_PUBLIC_API_BASE_URL}"
  (
    cd "$WEB_DIR"
    exec npm run dev -- --webpack --hostname "$WEB_HOST" --port "$WEB_PORT"
  )
}

case "$MODE" in
  api)
    check_port "$BETTAFISH_API_PORT" "API"
    start_api
    ;;
  web)
    check_port "$WEB_PORT" "Web"
    start_web
    ;;
  all)
    check_port "$BETTAFISH_API_PORT" "API"
    check_port "$WEB_PORT" "Web"
    trap cleanup INT TERM EXIT

    start_api &
    api_pid=$!
    start_web &
    web_pid=$!

    echo "Local services are starting."
    echo "API health:  curl -H 'X-Workspace-Id: ${NEXT_PUBLIC_WORKSPACE_ID}' ${NEXT_PUBLIC_API_BASE_URL}/health"
    echo "Web UI:      http://localhost:${WEB_PORT}"
    echo "Press Ctrl+C to stop."
    monitor_processes
    ;;
esac
