#!/usr/bin/env bash
# Outage test: with the smoke stack running, stop Loki and Grafana and verify
# the local fallback logs, then restart them and verify recovery.
#
#   docker compose -f docker-compose.yml -f docker-compose.smoke.yml up -d --build --wait
#   tests/smoke/outage_test.sh
#
# Requires: bash, curl, grep. Uses COLLECTOR_FALLBACK_MODE=failover (default)
# and LOCAL_LOG_DIR (default ./logs). Exit code 0 when every check passes.
set -uo pipefail

cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.smoke.yml)
LOG_DIR="${LOCAL_LOG_DIR:-./logs}"
GATEWAY="http://127.0.0.1:8080"
COLLECTOR="http://127.0.0.1:4318"
KEY="smoke-internal-key"
RUN_ID="outage-$(date +%s)"
failures=0

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; failures=$((failures + 1)); }

# eventually <seconds> <command...>: retry every 2 s until the command succeeds.
eventually() {
  local deadline=$((SECONDS + $1)); shift
  until "$@" >/dev/null 2>&1; do
    ((SECONDS >= deadline)) && return 1
    sleep 2
  done
}

stack_status() { curl -s -H "x-internal-key: $KEY" "$GATEWAY/status/stack"; }
fallback_active() { stack_status | grep -q '"fallback_active":true'; }
fallback_inactive() { stack_status | grep -q '"status":"ok"'; }
file_has() { grep -q "$2" "$1"; }

send_frontend_event() {
  curl -s -o /dev/null -w '%{http_code}' -X POST "$GATEWAY/api/telemetry/v1/ingest" \
    -H 'content-type: application/json' \
    -d "{\"events\":[{\"type\":\"error\",\"level\":\"error\",\"message\":\"frontend $RUN_ID\",\"app\":\"outage-web\"}]}"
}

send_backend_log() {
  local now; now="$(date +%s)000000000"
  curl -s -o /dev/null -w '%{http_code}' -X POST "$COLLECTOR/v1/logs" -H 'content-type: application/json' -d "{
    \"resourceLogs\":[{\"resource\":{\"attributes\":[{\"key\":\"service.name\",\"value\":{\"stringValue\":\"outage-backend\"}}]},
    \"scopeLogs\":[{\"logRecords\":[{\"timeUnixNano\":\"$now\",\"severityNumber\":17,\"severityText\":\"ERROR\",
    \"body\":{\"stringValue\":\"backend $RUN_ID\"}}]}]}]}"
}

echo "run id $RUN_ID"
eventually 90 fallback_inactive && pass "stack healthy before outage" || fail "stack healthy before outage"

"${COMPOSE[@]}" stop loki grafana >/dev/null 2>&1

eventually 60 fallback_active && pass "gateway detects Loki/Grafana outage (fallback_active)" \
  || fail "gateway detects Loki/Grafana outage (fallback_active)"

eventually 30 file_has "$LOG_DIR/obs-gateway/stack-health.jsonl" '"component": "grafana", "state": "unhealthy"' \
  && pass "stack-health.jsonl records grafana unhealthy" || fail "stack-health.jsonl records grafana unhealthy"

[[ "$(send_frontend_event)" == "204" ]] && pass "ingest still returns 204 during outage" \
  || fail "ingest still returns 204 during outage"
eventually 30 file_has "$LOG_DIR/obs-gateway/events.jsonl" "frontend $RUN_ID" \
  && pass "frontend event written to obs-gateway/events.jsonl" || fail "frontend event written to obs-gateway/events.jsonl"

send_backend_log >/dev/null
eventually 60 file_has "$LOG_DIR/otel-collector/logs.jsonl" "backend $RUN_ID" \
  && pass "backend OTLP log failed over to otel-collector/logs.jsonl" \
  || fail "backend OTLP log failed over to otel-collector/logs.jsonl"

"${COMPOSE[@]}" start loki grafana >/dev/null 2>&1

eventually 120 fallback_inactive && pass "gateway detects recovery" || fail "gateway detects recovery"
eventually 30 file_has "$LOG_DIR/obs-gateway/stack-health.jsonl" '"component": "loki", "state": "recovered"' \
  && pass "stack-health.jsonl records loki recovered" || fail "stack-health.jsonl records loki recovered"

echo
if ((failures)); then echo "FAILED: $failures failing check(s)"; exit 1; fi
echo "OK: 0 failing check(s)"
