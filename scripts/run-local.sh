#!/usr/bin/env bash
# Run the whole dev stack as local processes (no Docker), for machines where containers
# are unavailable. Needs Python 3.11+, the packages from `make install`, and a PostgreSQL
# reachable at $MMAVIE_DATABASE_URL. Same ports and dev tokens as compose.yaml.
#   scripts/run-local.sh start | stop
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$PWD/.run"; mkdir -p "$RUN"
: "${MMAVIE_DATABASE_URL:=postgresql://mmavie:dev-postgres-password@127.0.0.1:5432/mmavie}"
export MMAVIE_DATABASE_URL MMAVIE_ENVIRONMENT=dev
PP="packages/mmavie-common/src"

svc() { # name port pythonpath module [ENV=VAL ...]
  local name=$1 port=$2 path=$3 module=$4; shift 4
  env PYTHONPATH="$PP:$path" "$@" uvicorn --factory "$module" --host 127.0.0.1 --port "$port" \
    >"$RUN/$name.log" 2>&1 & echo $! >"$RUN/$name.pid"
}

case "${1:-start}" in
  start)
    python db/migrate.py
    svc llm-fake 8090 services/llm-fake mmavie_llm_fake.main:app_factory \
      MMAVIE_SERVICE_NAME=llm-fake MMAVIE_SERVICE_TOKENS=gateway:dev-gateway-to-llm MMAVIE_FAKE_TOKEN_DELAY_MS=20
    (cd services/speech
     env SPEECH_BACKEND=fake SPEECH_DATA_DIR="$RUN/speech-data" SPEECH_API_KEYS=gateway:dev-gateway-to-speech \
       uvicorn --factory app.main:app_factory --host 127.0.0.1 --port 8001 >"$RUN/speech.log" 2>&1 &
     echo $! >"$RUN/speech.pid")
    sleep 2
    svc gateway 8080 services/gateway mmavie_gateway.main:app_factory MMAVIE_SERVICE_NAME=gateway \
      MMAVIE_SERVICE_TOKENS=console:dev-console-to-gateway,runtime:dev-runtime-to-gateway \
      MMAVIE_GATEWAY_ROUTES_FILE=config/gateway.routes.local.json \
      LLM_UPSTREAM_TOKEN=dev-gateway-to-llm SPEECH_UPSTREAM_TOKEN=dev-gateway-to-speech
    svc agent-runtime 8081 services/agent-runtime mmavie_runtime.main:app_factory MMAVIE_SERVICE_NAME=agent-runtime \
      MMAVIE_SERVICE_TOKENS=console:dev-console-to-runtime MMAVIE_GATEWAY_URL=http://127.0.0.1:8080
    svc audio-engine 8082 services/audio-engine mmavie_audio.main:app_factory MMAVIE_SERVICE_NAME=audio-engine \
      MMAVIE_SERVICE_TOKENS=console:dev-console-to-audio
    svc model-registry 8083 services/model-registry mmavie_registry.main:app_factory MMAVIE_SERVICE_NAME=model-registry \
      MMAVIE_SERVICE_TOKENS=console:dev-console-to-registry
    sleep 3; echo "started; logs in $RUN/"
    ;;
  stop)
    for f in "$RUN"/*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done
    echo stopped
    ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
