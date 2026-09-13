#!/usr/bin/env bash
# RBAC edge test: brings up the smoke stack with docker-compose.secure.yml and
# checks that obs-edge enforces authentication and roles on the Grafana UI and
# the Loki / Tempo / Prometheus APIs (OBS_AUTHZ_MODE=static).
#
#   tests/smoke/rbac_edge_test.sh
#
# Requires: bash, curl. Exit code 0 when every check passes.
set -uo pipefail

cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.smoke.yml -f docker-compose.secure.yml)
EDGE="http://127.0.0.1:8443"
SECRET="smoke-test-secret-at-least-32-bytes-long"
failures=0

expect() {  # expect <name> <expected> <actual>
  if [[ "$3" == "$2" ]]; then echo "PASS  $1"; else echo "FAIL  $1 (expected $2, got $3)"; failures=$((failures + 1)); fi
}
expect_contains() {  # expect_contains <name> <needle> <haystack>
  if [[ "$3" == *"$2"* ]]; then echo "PASS  $1"; else echo "FAIL  $1 (missing $2 in: $3)"; failures=$((failures + 1)); fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

token() {  # token <sub> <python roles list>
  "${COMPOSE[@]}" exec -T obs-gateway python -c "import jwt, time; print(jwt.encode(
    {'sub': '$1', 'org_id': 'acme', 'roles': $2, 'exp': int(time.time()) + 600}, '$SECRET', algorithm='HS256'))"
}

"${COMPOSE[@]}" up -d --build --wait obs-edge obs-gateway grafana >/dev/null 2>&1 \
  || { echo "FAIL  stack did not become healthy"; "${COMPOSE[@]}" ps; exit 1; }

VIEWER=$(token vera '["obs-viewer"]')
ADMIN=$(token root '["obs-admin"]')
NOROLE=$(token nobody '[]')

expect "UI without token -> 401" 401 "$(code "$EDGE/")"
expect "UI with a token that has no roles -> 403" 403 "$(code -H "Authorization: Bearer $NOROLE" "$EDGE/")"
expect_contains "viewer signed in to Grafana via auth proxy" '"login":"vera"' \
  "$(curl -s -H "Authorization: Bearer $VIEWER" "$EDGE/api/user")"
expect_contains "viewer gets Grafana Viewer role" '"role":"Viewer"' \
  "$(curl -s -H "Authorization: Bearer $VIEWER" "$EDGE/api/user/orgs")"
expect_contains "admin (cookie) gets Grafana Admin role" '"role":"Admin"' \
  "$(curl -s --cookie "obs_token=$ADMIN" "$EDGE/api/user/orgs")"
expect "viewer may read logs (/loki)" 200 "$(code -H "Authorization: Bearer $VIEWER" "$EDGE/loki/ready")"
expect "viewer may read traces (/tempo)" 200 "$(code -H "Authorization: Bearer $VIEWER" "$EDGE/tempo/ready")"
expect "viewer may query metrics (/prometheus)" 200 \
  "$(code -H "Authorization: Bearer $VIEWER" "$EDGE/prometheus/api/v1/query?query=up")"
expect "logs without token -> 401" 401 "$(code "$EDGE/loki/ready")"
expect "Grafana host port closed" 000 "$(code --max-time 3 http://127.0.0.1:3000/api/health)"

echo
if ((failures)); then echo "FAILED: $failures failing check(s)"; exit 1; fi
echo "OK: 0 failing check(s)"
