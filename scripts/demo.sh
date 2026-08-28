#!/usr/bin/env bash
# Start and manage the local Papertrail demonstration stack.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
RUNTIME_DIR="$ROOT/.runtime/demo"
HOME_DIR="$ROOT/.runtime/home"
HF_HOME_DIR="$ROOT/.runtime/huggingface"
MINERU_BIN="$ROOT/.runtime/mineru-venv/bin/mineru-api"
PYTHON_BIN="$ROOT/.venv/bin/python"
VITE_BIN="$ROOT/dashboard/node_modules/.bin/vite"
MODEL="${PAPERTRAIL_OLLAMA_MODEL:-llama3.1:8b}"

MINERU_PORT=8000
API_PORT=8080
VITE_PORT=5173
OLLAMA_PORT=11434

mkdir -p "$RUNTIME_DIR" "$HOME_DIR" "$HF_HOME_DIR"

usage() {
  cat <<'EOF'
Usage: scripts/demo.sh [start|stop|restart|status|logs]

Commands:
  start    Start the local demo stack (default).
  stop     Stop the demo stack.
  restart  Stop then start the demo stack.
  status   Show service health and URLs.
  logs     Follow all demo service logs.
EOF
}

say() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_file() {
  [[ -x "$1" ]] || fail "Required executable not found: $1"
}

listener_pids() {
  local port="$1"
  lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

port_is_listening() {
  [[ -n "$(listener_pids "$1")" ]]
}

pid_is_listener() {
  local pid="$1" port="$2"
  [[ -n "$(lsof -nP -a -p "$pid" -iTCP:"$port" -sTCP:LISTEN 2>/dev/null)" ]]
}


service_command_matches() {
  local name="$1" pid="$2" port="$3" command_line
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"

  case "$name" in
    ollama)
      [[ "$command_line" == *"ollama serve"* ]]
      ;;
    mineru)
      [[ "$command_line" == *"$MINERU_BIN"* && "$command_line" == *"--host 127.0.0.1"* && "$command_line" == *"--port $port"* ]]
      ;;
    api)
      [[ "$command_line" == *"-m uvicorn papertrail.app:create_app --factory --host 127.0.0.1 --port $port"* ]]
      ;;
    vite)
      [[ "$command_line" == *"$VITE_BIN --host 127.0.0.1 --port $port --strictPort"* ]]
      ;;
    *) return 1 ;;
  esac
}

process_start_identity() {
  ps -p "$1" -o lstart= 2>/dev/null || true
}

write_pid_record() {
  local name="$1" pid="$2" start_identity
  start_identity="$(process_start_identity "$pid")"
  [[ -n "$start_identity" ]] || fail "Unable to record start identity for $name (PID: $pid)."
  printf '%s\n%s\n' "$pid" "$start_identity" >"$RUNTIME_DIR/$name.pid"
}

read_pid_record() {
  local name="$1" pid_file="$RUNTIME_DIR/$1.pid"
  RECORDED_PID=""
  RECORDED_START_IDENTITY=""
  [[ -f "$pid_file" ]] || return 1
  {
    IFS= read -r RECORDED_PID || true
    IFS= read -r RECORDED_START_IDENTITY || true
  } <"$pid_file"
  [[ "$RECORDED_PID" =~ ^[0-9]+$ && -n "$RECORDED_START_IDENTITY" ]] || return 1
}

tracked_process_matches() {
  local name="$1" port="$2" start_identity
  read_pid_record "$name" || return 1
  kill -0 "$RECORDED_PID" 2>/dev/null || return 1
  start_identity="$(process_start_identity "$RECORDED_PID")"
  [[ "$start_identity" == "$RECORDED_START_IDENTITY" ]] || return 1
  service_command_matches "$name" "$RECORDED_PID" "$port" || return 1
  pid_is_listener "$RECORDED_PID" "$port"
}

fail_if_port_occupied() {
  local port="$1" name="$2" pid command_line
  local -a listeners=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && listeners+=("$pid")
  done < <(listener_pids "$port")

  ((${#listeners[@]})) || return 0
  for pid in "${listeners[@]}"; do
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    printf 'Error: %s cannot start because port %s is occupied by PID %s: %s\n' "$name" "$port" "$pid" "${command_line:-<command unavailable>}" >&2
  done
  fail "Stop the listener on port $port manually, then run scripts/demo.sh start again."
}

remove_pid_file() {
  rm -f "$RUNTIME_DIR/$1.pid"
}

tracked_service_is_healthy() {
  local name="$1" port="$2"
  tracked_process_matches "$name" "$port"
}

stop_demo_service() {
  local name="$1" port="$2" pid=""

  if tracked_process_matches "$name" "$port"; then
    pid="$RECORDED_PID"
    say "Stopping tracked $name (PID: $pid)."
    kill -TERM "$pid" 2>/dev/null || true
    local deadline=$((SECONDS + 10))
    while kill -0 "$pid" 2>/dev/null && ((SECONDS < deadline)); do sleep 1; done
    if kill -0 "$pid" 2>/dev/null && tracked_process_matches "$name" "$port"; then
      say "Force-stopping tracked $name (PID: $pid)."
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi

  remove_pid_file "$name"
}

stop_managed_ollama() {
  local pid=""
  if tracked_process_matches ollama "$OLLAMA_PORT"; then
    pid="$RECORDED_PID"
    say "Stopping Ollama started by this script (PID: $pid)."
    kill -TERM "$pid" 2>/dev/null || true
  fi
  remove_pid_file ollama
}

wait_for_http() {
  local url="$1" description="$2" log_file="$3" deadline=$((SECONDS + 90))
  while ! curl --fail --silent --show-error --max-time 3 "$url" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      printf 'Error: Timed out waiting for %s at %s.\n' "$description" "$url" >&2
      if [[ -f "$log_file" ]]; then
        printf '%s log tail (%s):\n' "$description" "$log_file" >&2
        tail -n 40 "$log_file" >&2 || true
      fi
      return 1
    fi
    sleep 1
  done
}

ollama_is_healthy() {
  curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$OLLAMA_PORT/api/tags" >/dev/null 2>&1
}

ollama_has_model() {
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$OLLAMA_PORT/api/tags" |
    "$PYTHON_BIN" -c 'import json, sys; model = sys.argv[1]; tags = json.load(sys.stdin).get("models", []); sys.exit(0 if any(item.get("name") == model or item.get("model") == model for item in tags) else 1)' "$MODEL"
}

check_dependencies() {
  require_command curl
  require_command lsof
  require_command nohup
  require_command ollama
  require_file "$PYTHON_BIN"
  require_file "$MINERU_BIN"
  require_file "$VITE_BIN"
}

ensure_ollama() {
  if ollama_is_healthy; then
    say "Ollama is healthy on port $OLLAMA_PORT; reusing it."
  else
    if port_is_listening "$OLLAMA_PORT"; then
      fail "Port $OLLAMA_PORT is occupied but Ollama is not healthy; refusing to stop or replace it."
    fi
    say "Starting Ollama on port $OLLAMA_PORT."
    nohup ollama serve >"$RUNTIME_DIR/ollama.log" 2>&1 &
    write_pid_record ollama "$!"
    wait_for_http "http://127.0.0.1:$OLLAMA_PORT/api/tags" "Ollama" "$RUNTIME_DIR/ollama.log" || return 1
  fi

  if ! ollama_has_model; then
    fail "Ollama model '$MODEL' is unavailable. Run: ollama pull $MODEL"
  fi
  say "Ollama model '$MODEL' is available."
}

start_service() {
  local name="$1" port="$2" log_file="$3"
  shift 3
  fail_if_port_occupied "$port" "$name"
  say "Starting $name on port $port."
  nohup "$@" >"$log_file" 2>&1 &
  write_pid_record "$name" "$!"
}

start_demo() {
  check_dependencies
  ensure_ollama

  if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$MINERU_PORT/health" >/dev/null 2>&1; then
    if tracked_service_is_healthy mineru "$MINERU_PORT"; then
      say "MinerU is healthy on port $MINERU_PORT; reusing it."
    else
      fail_if_port_occupied "$MINERU_PORT" "MinerU"
    fi
  else
    stop_demo_service mineru "$MINERU_PORT"
    start_service mineru "$MINERU_PORT" "$RUNTIME_DIR/mineru.log" env HOME="$HOME_DIR" HF_HOME="$HF_HOME_DIR" "$MINERU_BIN" --host 127.0.0.1 --port "$MINERU_PORT"
  fi
  wait_for_http "http://127.0.0.1:$MINERU_PORT/health" "MinerU" "$RUNTIME_DIR/mineru.log" || return 1

  if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$API_PORT/health/ready" >/dev/null 2>&1; then
    if tracked_service_is_healthy api "$API_PORT"; then
      say "FastAPI is ready on port $API_PORT; reusing it."
    else
      fail_if_port_occupied "$API_PORT" "FastAPI"
    fi
  else
    stop_demo_service api "$API_PORT"
    start_service api "$API_PORT" "$RUNTIME_DIR/api.log" env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" PAPERTRAIL_MINERU_BASE_URL="http://127.0.0.1:$MINERU_PORT" PAPERTRAIL_OLLAMA_BASE_URL="http://127.0.0.1:$OLLAMA_PORT" PAPERTRAIL_OLLAMA_MODEL="$MODEL" PAPERTRAIL_WORK_DIR="$ROOT/.papertrail-data" PAPERTRAIL_PROCESSOR_CONFIG_PATH="$ROOT/config/processors.yaml" "$PYTHON_BIN" -m uvicorn papertrail.app:create_app --factory --host 127.0.0.1 --port "$API_PORT"
  fi
  wait_for_http "http://127.0.0.1:$API_PORT/health/ready" "FastAPI" "$RUNTIME_DIR/api.log" || return 1

  if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$VITE_PORT/" >/dev/null 2>&1; then
    if tracked_service_is_healthy vite "$VITE_PORT"; then
      say "Vite is healthy on port $VITE_PORT; reusing it."
    else
      fail_if_port_occupied "$VITE_PORT" "Vite"
    fi
  else
    stop_demo_service vite "$VITE_PORT"
    cd "$ROOT/dashboard"
    start_service vite "$VITE_PORT" "$RUNTIME_DIR/vite.log" "$VITE_BIN" --host 127.0.0.1 --port "$VITE_PORT" --strictPort
    cd "$ROOT"
  fi
  wait_for_http "http://127.0.0.1:$VITE_PORT/" "Vite" "$RUNTIME_DIR/vite.log" || return 1

  say "Demo is ready. Dashboard: http://127.0.0.1:$VITE_PORT"
  say "API: http://127.0.0.1:$API_PORT/docs  MinerU: http://127.0.0.1:$MINERU_PORT/docs"
  if [[ "${DEMO_NO_OPEN:-}" != "1" ]] && [[ "$(uname)" == "Darwin" ]]; then
    open "http://127.0.0.1:$VITE_PORT" >/dev/null 2>&1 || say "Unable to open the dashboard automatically."
  fi
}

show_status() {
  local name="$1" url="$2" port="$3"
  if curl --fail --silent --show-error --max-time 3 "$url" >/dev/null 2>&1; then
    printf '%-8s healthy  %s\n' "$name" "$url"
  elif port_is_listening "$port"; then
    printf '%-8s unhealthy (listening on port %s)\n' "$name" "$port"
  else
    printf '%-8s stopped\n' "$name"
  fi
}

status_demo() {
  say "Demo status:"
  show_status "Ollama" "http://127.0.0.1:$OLLAMA_PORT/api/tags" "$OLLAMA_PORT"
  show_status "MinerU" "http://127.0.0.1:$MINERU_PORT/health" "$MINERU_PORT"
  show_status "FastAPI" "http://127.0.0.1:$API_PORT/health/ready" "$API_PORT"
  show_status "Vite" "http://127.0.0.1:$VITE_PORT/" "$VITE_PORT"
  say "Dashboard: http://127.0.0.1:$VITE_PORT"
  say "API docs:  http://127.0.0.1:$API_PORT/docs"
}

stop_demo() {
  stop_demo_service vite "$VITE_PORT"
  stop_demo_service api "$API_PORT"
  stop_demo_service mineru "$MINERU_PORT"
  stop_managed_ollama
  say "Demo stopped."
}

logs_demo() {
  mkdir -p "$RUNTIME_DIR"
  touch "$RUNTIME_DIR"/{ollama,mineru,api,vite}.log
  tail -n 100 -F "$RUNTIME_DIR"/{ollama,mineru,api,vite}.log
}

case "${1:-start}" in
  start) start_demo ;;
  stop) stop_demo ;;
  restart) stop_demo; start_demo ;;
  status) status_demo ;;
  logs) logs_demo ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
