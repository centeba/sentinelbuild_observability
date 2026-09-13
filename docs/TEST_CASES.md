# SentinelBuild Observability Platform — Test Cases

| | |
|---|---|
| **Version** | 1.1 — obs-gateway 0.2.0 · obs_telemetry (Flutter) 0.1.0 · @sentinelbuild/obs-telemetry (React) 0.1.0 · OTEL Collector 0.160.0 |
| **Companions** | [REQUIREMENTS.md](REQUIREMENTS.md) · [DESIGN.md](DESIGN.md) · [USER_MANUAL.md §10](USER_MANUAL.md#10-running-the-tests) |

## 1. Scope and approach

| Suite | ID prefix | Kind | Location | How to run |
|---|---|---|---|---|
| Gateway | `GW-` | Automated (pytest, in-process, real OTEL SDK) | `gateway/tests/` | `docker compose -f docker-compose.test.yml run --rm --build gateway-tests` |
| React client | `RC-` | Automated (vitest + jsdom) | `clients/react/obs-telemetry/test/` | `… run --rm --build react-client-tests` |
| Flutter client | `FC-` | Automated (flutter_test) | `clients/flutter/obs_telemetry/test/` | `… run --rm --build flutter-client-tests` |
| End-to-end | `E2E-` | Automated (smoke test against the full stack) | `tests/smoke/smoke_test.py` | `docker compose -f docker-compose.yml -f docker-compose.smoke.yml run --rm --build smoke-test` |
| Outage / fallback | `OUT-` | Automated (stops Loki and Grafana on the smoke stack) | `tests/smoke/outage_test.sh` | `tests/smoke/outage_test.sh` (smoke stack running) |
| RBAC edge | `RBAC-` | Automated (secure overlay on the smoke stack) | `tests/smoke/rbac_edge_test.sh` | `tests/smoke/rbac_edge_test.sh` |
| Lint | `LINT-` | Automated | `docker-compose.test.yml`, package scripts | see §8 |
| Deployment | `DEP-` | Automated in CI (config validation) | `.github/workflows/ci.yml` | CI job `stack` |
| Manual | `MAN-` | Manual (UI / operational behaviour) | this document | steps below |

### 1.1 Latest results

| Suite | Result | Date |
|---|---|---|
| Gateway | 132 passed · coverage 99.2 % · ruff clean · mypy --strict clean | 2026-09-13 |
| React client | 50 passed (6 files) · ESLint clean · typecheck clean · build OK | 2026-09-13 |
| React demo | ESLint clean · build OK · Docker image builds | 2026-09-13 |
| Flutter client | 22 passed · analyze clean | 2026-09-13 |
| End-to-end smoke | 16 / 16 checks passed (collector 0.160) | 2026-09-13 |
| Outage / fallback | 8 / 8 checks passed | 2026-09-13 |
| RBAC edge | 10 / 10 checks passed | 2026-09-13 |
| Lint (hadolint, yamllint, markdownlint) | clean | 2026-09-13 |
| Deployment | all compose combinations valid; collector configs valid in all three fallback modes, with and without the prod overlay | 2026-09-13 |
| Manual | MAN-1 and MAN-11 passed; other MAN cases not yet run | 2026-09-13 |

---

## 2. Gateway (`GW-`)

### 2.1 Ingest: forwarding and validation (`tests/test_ingest.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-1 | FR-2 | Post one fully populated error event | 204; one emit with body, level, app, `telemetry.type`, `app.version`, `stack`, `ctx.route`; no `company_id` | `test_forwards_event_with_attributes` |
| GW-2 | FR-2 | Empty event `{}` and an event with only `error` | Defaults `type=log`, `level=info`; body falls back to `error`, then `type` | `test_defaults_and_body_fallback` |
| GW-3 | FR-2 | Empty batch | 204, nothing emitted | `test_empty_batch_is_accepted` |
| GW-4 | FR-11 | `level` sent as `WARN`, ` Error `, `critical`, `trace` | Normalised to lowercase canonical names | `test_level_is_normalized` |
| GW-5 | FR-4.1, FR-11 | One invalid event in a batch, for each rule: unknown level; unknown type; message/error/url > 2000; stack > 8000; app > 64 or illegal characters; app_version > 64; > 32 context entries; context key > 128 or empty; value > 1024 or non-string; trace_id wrong length / all zero / non-hex; span_id wrong length / all zero (19 parametrised cases) | 422; **nothing** emitted, including the valid sibling event | `test_invalid_event_rejected[*]` |
| GW-6 | FR-4.1 | `OBS_MAX_EVENT_BYTES=500`; event under and over the limit | Under → 204; over → 422 | `test_event_byte_cap` |
| GW-7 | FR-4.1 | 300 × `é` (600 UTF-8 bytes) with limit 500 | 422: the limit counts bytes, not characters | `test_event_byte_cap_counts_utf8_bytes` |
| GW-8 | FR-4.4 | `OBS_MAX_BATCH_EVENTS=3`; batches of 3 and 4 | 3 → 204; 4 → 422 with nothing emitted | `test_batch_over_limit_rejected` |
| GW-9 | FR-8.2 | Uppercase `trace_id` / `span_id` | Forwarded lowercase | `test_trace_context_forwarded_lowercase` |

### 2.2 Ingest: dedupe (`tests/test_ingest.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-10 | FR-4.3 | The same event twice in one batch, then again in a new request | Emitted once | `test_dedupes_identical_in_window` |
| GW-11 | FR-4.3 | Same message, different stack | Both emitted | `test_distinct_stack_is_not_duplicate` |
| GW-12 | FR-4.3 | Dedupe window 0 s | Duplicates emitted | `test_dedupe_window_expires` |
| GW-13 | FR-4.3 | Identical error from tenants `acme` and `globex` (JWT) | Both emitted, each with its own `company_id` | `test_dedupe_is_per_tenant` |
| GW-14 | FR-4.3 | Identical anonymous event from two client IPs (one trusted hop) | One per IP | `test_dedupe_is_per_anonymous_client` |
| GW-15 | NFR-4 | Dedupe map over its cap, with expired and live entries; then with all entries live | Expired entries pruned; if everything is live the map is cleared instead of growing | `test_dedupe_map_is_bounded` |

### 2.3 Ingest: rate limit and client IP (`tests/test_ingest.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-16 | FR-4.2 | Limit 2; three requests | 204, 204, 429 | `test_rate_limit` |
| GW-17 | FR-4.2 | Limit 1; the second request carries an event | 429 and that event is **not** forwarded | `test_rate_limited_batch_is_not_forwarded` |
| GW-18 | FR-4.2, NFR-3 | Hops = 0; each request has a different spoofed `X-Forwarded-For` | Still limited on the socket peer (third request → 429) | `test_rate_limit_ignores_forwarded_for_by_default` |
| GW-19 | FR-4.2 | Hops = 1; client-controlled left entries vary, rightmost entry the same | Treated as the same client (429); a different rightmost entry → 204 | `test_rate_limit_uses_trusted_proxy_hop` |
| GW-20 | FR-4.2 | Hops = 2 with 1, 0 and 3 `X-Forwarded-For` entries | Uses the only entry / the peer / the second from the right | `test_client_ip_with_two_trusted_hops` |
| GW-21 | NFR-4 | Rate-limit map over its cap with stale, empty and live entries | Stale and empty keys pruned | `test_rate_limit_map_is_pruned` |

### 2.4 Ingest: enrichment (`tests/test_ingest.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-22 | FR-3.1 | Valid HS256 token with `org_id`, `sub` | `company_id=acme`, `user_id=user-1` | `test_enriches_tenant_and_user_from_jwt` |
| GW-23 | FR-3.2 | Expired, forged and malformed tokens | 204 for each; stored anonymously | `test_invalid_token_is_accepted_anonymously` |
| GW-24 | FR-3.2 | Token present but no verification configured | Anonymous | `test_token_ignored_when_verification_not_configured` |

### 2.5 Auth (`tests/test_auth.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-25 | FR-3.2 | No header; `Basic` scheme | `ANONYMOUS` | `test_no_header_or_non_bearer_is_anonymous` |
| GW-26 | FR-3.1 | Numeric `org_id` claim | Stringified `company_id="42"` | `test_hs256_claims` |
| GW-27 | FR-3.1 | Lowercase `bearer` scheme | Accepted | `test_bearer_scheme_is_case_insensitive` |
| GW-28 | FR-3.3 | Custom claim names `tenant`/`email` | Read from the configured claims | `test_custom_claim_names` |
| GW-29 | FR-3.1 | Valid token without tenant/user claims | Authenticated, ids `None` | `test_missing_claims_still_authenticated` |
| GW-30 | NFR-3 | Audience and issuer configured; right / wrong `aud` / wrong `iss` | Only the matching token is authenticated | `test_audience_and_issuer_enforced` |
| GW-31 | NFR-3 | HS256 token while only RS256 is allowed | Anonymous | `test_disallowed_algorithm_rejected` |
| GW-32 | FR-3.1 | RS256 token verified via JWKS signing key | Claims extracted | `test_jwks_rs256` |
| GW-33 | NFR-4 | Same JWKS URL requested twice; a different URL | Client reused per URL | `test_jwk_client_is_cached_per_url` |
| GW-34 | NFR-3 | Internal key unset; set with missing / empty / wrong / correct value | Open when unset; only the exact value passes | `test_internal_key` |

### 2.6 Health (`tests/test_health.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-35 | FR-10.3 | `GET /health`, `GET /healthz` | 200 `{"status":"ok","service":"obs-gateway"}` | `test_own_probes` |
| GW-36 | FR-10.1 | No targets | 200 with `healthy:0,total:0,services:[]` and a note | `test_status_no_targets` |
| GW-37 | FR-10.1 | Two targets answering 200 | 200 `ok`, 2/2, sorted by name | `test_status_all_healthy` |
| GW-38 | FR-10.2 | Targets answering 200, 503, and connection refused | 503 `degraded`, 1/3; entries carry `status_code` or `error` | `test_status_degraded` |
| GW-39 | FR-10.2 | Target times out | Unhealthy with `error: ReadTimeout` | `test_status_timeout_is_unhealthy` |
| GW-40 | NFR-3 | Internal key configured: no key / wrong / right; `/health` | 401 / 401 / 200; `/health` stays 200 | `test_status_requires_internal_key_when_configured` |

### 2.7 Emitter (`tests/test_emit.py`)

These tests use the real OpenTelemetry SDK with an in-memory exporter.

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-41 | FR-8.1, FR-8.2 | Emit with trace and span ids | Record body, severity ERROR/`ERROR`, trace and span ids set, `None` attributes dropped; resource `service.name=frontend/web`, `deployment.environment` set | `test_emits_log_record` |
| GW-42 | FR-8.2 | Emit without trace context | No trace id | `test_no_trace_context` |
| GW-97 | FR-5.2 | Encode an emitted record with the OTLP/protobuf log encoder | Severity 17 and trace id survive encoding (the path the HTTP exporter uses) | `test_record_encodes_as_otlp` |
| GW-43 | FR-11 | Each level alias (7 parametrised cases) | Correct OTEL severity number and canonical text | `test_severity_mapping[*]` |
| GW-44 | FR-8.1, NFR-4 | Cap 2 providers; apps `None`, `web`, `mobile`, `web` | `frontend`, `frontend/web`, `frontend` (overflow), `frontend/web` | `test_service_name_per_app_and_fallback` |
| GW-45 | FR-5.2 | `OBS_OTEL_ENDPOINT` empty | No export; `client_telemetry` line logged | `test_stdout_fallback_without_endpoint` |
| GW-46 | FR-5.2 | Processor raises on emit | No exception reaches the caller; failure logged | `test_export_failure_never_raises` |
| GW-47 | NFR-4 | Batch processor with a long delay; `shutdown()` | Pending record exported on shutdown | `test_shutdown_flushes_batched_records` |

### 2.8 App wiring and configuration (`tests/test_main.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-48 | FR-4.1 | `Content-Length` over the request limit | 413, nothing emitted | `test_body_over_limit_rejected_by_content_length` |
| GW-49 | FR-4.1 | Chunked body (no `Content-Length`) over the limit | 413, nothing emitted | `test_chunked_body_over_limit_rejected` |
| GW-50 | FR-4.1 | Body exactly at the limit | Processed (204) | `test_body_at_limit_is_processed` |
| GW-51 | NFR-7 | Ingest, then `GET /metrics` | Histogram series for the ingest route with status 204; `obs_ingest_events_total{outcome="accepted"}` | `test_metrics_endpoint` |
| GW-52 | NFR-7 | Unknown path | Recorded with `route="unmatched"` | `test_unmatched_route_label` |
| GW-53 | NFR-1 | Env: comma-separated CORS list, JSON algorithm list, JSON health targets, hops, byte and batch caps | Parsed correctly; `max_request_bytes` derived | `test_settings_from_env` |
| GW-54 | NFR-1 | `OBS_JWT_ALGORITHMS=HS256` as a plain string | `["HS256"]` (previously crashed at startup) | `test_settings_single_algorithm_plain_string` |
| GW-96 | FR-13.2 | App started with lifespan | Stack monitor task started, and cancelled cleanly on shutdown | `test_lifespan_starts_and_stops_stack_monitor` |

GW-53 also covers `OBS_STACK_TARGETS` (JSON) and `OBS_FALLBACK_TRIGGER_COMPONENTS` (comma-separated).

### 2.9 Stack monitor and local fallback (`tests/test_stack.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-55 | FR-13.2 | All components 200 | No fallback; snapshot `ok`; no health records | `test_all_healthy_records_nothing` |
| GW-56 | FR-14.1 | Healthy → Grafana refused and Tempo 503 → unchanged → Grafana recovers | Exactly three records (grafana unhealthy, tempo unhealthy, grafana recovered) with `detail`, `previous_detail`, `fallback_active` (true, then false because Tempo is not a trigger) | `test_transitions_are_recorded` |
| GW-57 | FR-14.1 | Collector times out at the first check | One `unhealthy` record with `previous_detail: null`; fallback active | `test_unhealthy_at_first_check_is_recorded` |
| GW-58 | FR-14.4 | Tempo down; triggers default, then `["tempo"]` | Inactive, then active | `test_fallback_trigger_components_are_configurable` |
| GW-59 | FR-13.2 | 204 and 302 answers | 2xx healthy; 3xx unhealthy | `test_non_2xx_is_unhealthy` |
| GW-60 | FR-13.2 | Loki up, Grafana 500 | `obs_stack_component_up` 1 / 0; `obs_fallback_active 1` | `test_metrics_reflect_health` |
| GW-61 | FR-13.2 | A check raises | Loop continues to the next interval | `test_run_survives_check_errors` |
| GW-62 | FR-13.2 | `/status/stack` before any check | 200 `unknown`, no components | `test_status_stack_unknown_before_first_check` |
| GW-63 | FR-13.2 | `/status/stack` with Grafana down | 503 `degraded`, `fallback_active`, log dir, sorted components with detail | `test_status_stack_degraded` |
| GW-64 | NFR-3 | Internal key configured | 401 without, 200 with | `test_status_stack_requires_internal_key` |
| GW-65 | FR-14.2 | Ingest while Grafana is down | Event in `events.jsonl` (service, level, body, trace_id, attributes without nulls) **and** still forwarded over OTLP | `test_events_written_locally_while_stack_unhealthy` |
| GW-66 | FR-14.2 | Ingest while healthy | No `events.jsonl` | `test_events_not_written_while_healthy` |
| GW-67 | FR-14.4 | Stack unhealthy, `OBS_FALLBACK_LOG_DIR` empty | No files written | `test_no_files_when_log_dir_unset` |
| GW-94 | FR-14.4 | Max 300 bytes, 2 backups, 20 events | `events.jsonl`, `.1`, `.2` only; newest event in the live file | `test_fallback_log_rotates` |
| GW-95 | FR-5.1 | Log dir path blocked by a file | No exception; `fallback_log_unavailable` logged | `test_unwritable_log_dir_is_ignored` |

### 2.10 RBAC (`tests/test_authz.py`)

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| GW-68 | FR-15.1 | `AllowAllAuthorizer`, anonymous | Allowed, Viewer | `test_allow_all` |
| GW-69–77 | FR-15.1 | `StaticRoleAuthorizer` (9 cases): anonymous; no roles; viewer logs; viewer status (denied); editor+viewer status (Editor role wins); own tenant; cross-tenant (denied); admin cross-tenant; admin arbitrary action | Allowed or denied as listed, with the highest mapped Grafana role | `test_static_roles[*]` |
| GW-78 | FR-15.1 | Custom `OBS_AUTHZ_ROLE_PERMISSIONS` | Only the configured permissions | `test_static_roles_are_configurable` |
| GW-79 | FR-15.1 | `ExternalAuthorizer` allowed answer | Request `{subject, action, tenant}`; decision uses `reason` and `grafana_role` | `test_external_sends_subject_and_uses_answer` |
| GW-80 | FR-15.1 | External answers denied / non-boolean / HTTP 500 / unreachable (4 cases) | Denied (fails closed) | `test_external_fails_closed[*]` |
| GW-81 | FR-15.1 | No `OBS_AUTHZ_URL`; anonymous | Denied with reason | `test_external_requires_url_and_authentication` |
| GW-82 | FR-15.1 | Each `OBS_AUTHZ_MODE` (3 cases) | Matching implementation | `test_mode_selects_authorizer[*]` |
| GW-83 | FR-15.1 | `roles` claim as a list and as a comma-separated string | Same roles tuple; empty when absent | `test_roles_claim_parsing` |
| GW-84 | FR-15.3 | Path → action for Loki, Tempo, Prometheus, a Grafana page and an empty path (5 cases) | `logs:read`, `traces:read`, `metrics:read`, `ui:access`, `ui:access` | `test_action_for_path[*]` |
| GW-85 | FR-15.2 | `/status` with static RBAC: no token / viewer / editor; `/status/stack` admin | 401 / 403 / 200; 200 | `test_status_guard_with_rbac` |
| GW-86 | FR-15.2 | `obs_token` cookie with an editor token | 200 | `test_status_guard_accepts_token_cookie` |
| GW-87 | FR-15.2 | Internal key right / wrong with RBAC on | 200 / 401 | `test_internal_key_still_works_for_services` |
| GW-88 | FR-15.4 | Anonymous ingest with RBAC on | 204 | `test_ingest_is_not_guarded` |
| GW-89 | FR-15.3 | `/authz/verify` without a token | 401 with reason header | `test_verify_unauthenticated` |
| GW-90 | FR-15.3 | Role lacking `logs:read` on `/loki/...` | 403 `no role grants logs:read` | `test_verify_denied` |
| GW-91 | FR-15.3 | Editor on `/loki/...` via `X-Forwarded-Uri` | 200 with `X-Obs-Action`, `X-WEBAUTH-USER`, `X-WEBAUTH-ROLE: Editor`, `X-Scope-OrgID: acme` | `test_verify_allowed_sets_identity_headers` |
| GW-92 | FR-15.1 | RBAC off, anonymous | 200, user `anonymous`, no tenant header | `test_verify_passthrough_when_rbac_disabled` |

---

## 3. React client (`RC-`)

| ID | Requirement | Area | Case | Expected | Test file › test |
|---|---|---|---|---|---|
| RC-1 | §6.1 | Wire shape | Event → wire | snake_case fields, `app`, `app_version` | `models.test.ts` › produces the snake_case wire shape |
| RC-2 | §6.1 | Wire shape | Minimal event | `level` defaults to `info`; absent optionals omitted | › defaults level to info and omits absent optionals |
| RC-3 | FR-4.1 | Caps | Oversized message / error / stack / url / app_version | Truncated to gateway limits | › caps message, error, stack, url and app_version |
| RC-4 | FR-4.1 | Caps | Invalid `app` characters | Replaced so the gateway pattern passes | › sanitizes app to the gateway pattern |
| RC-5 | FR-4.1 | Caps | > 32 context entries, long keys and values | First 32 kept; keys and values truncated | › keeps the first 32 context entries… |
| RC-6 | FR-8.2 | Caps | Invalid trace or span ids | Dropped | › drops invalid trace and span ids |
| RC-7 | FR-4.1 | Caps | Event over 16384 bytes | Context dropped, then stack shortened; ≤ 16384 bytes | › shrinks an oversized event… · › drops context without touching stack… |
| RC-8 | FR-4.1 | Caps | Pathological escaped input | Still ≤ 16384 bytes | › stays under the byte limit for pathological escaped input |
| RC-9 | FR-4.3 | Dedupe | Signature | `type\|message\|stack` | › is type\|message\|stack |
| RC-10 | FR-2 | Delivery | Flush | POST to `/api/telemetry/v1/ingest` in wire shape; same-origin path when endpoint is `''`; no request for an empty queue | `client.test.ts` › posts queued events… · › uses a same-origin path… · › does nothing with an empty queue |
| RC-11 | FR-4.3 | Dedupe | Same event inside and after the window | Once inside, again after | › dedupes identical events… · › accepts the same event again after the dedupe window |
| RC-12 | FR-3.1 | Token | `getToken` returns a token | `Authorization: Bearer …` | › attaches the bearer token from getToken |
| RC-13 | FR-3.2 | Token | `getToken` throws or rejects | Sent anonymously | › sends anonymously when getToken throws or rejects |
| RC-14 | FR-4.4 | Batching | `maxBatch` chunking, eager flush, clamp to 1..100 | As configured | › sends in chunks of maxBatch · › flushes eagerly when a batch fills · › clamps maxBatch to 1..100 |
| RC-15 | FR-5.1 | Retry | Failed chunk (5xx / 429 / network) | Stops draining; chunk kept in order at the front | › stops draining after a failed chunk… |
| RC-16 | NFR-4 | Retry | Queue beyond `maxQueue` | Oldest dropped | › bounds the queue at maxQueue… |
| RC-17 | FR-5.1 | Retry | Request hangs | Aborted after 4 s and re-queued | › aborts a request after 4 seconds and re-queues |
| RC-18 | FR-5.1 | Safety | `fetch` throws synchronously; client disabled | Never rejects; disabled ignores everything | › never rejects even if fetch throws synchronously · › ignores everything when disabled |
| RC-19 | FR-2 | Timer | `start()` | Periodic flush | › flushes periodically after start() |
| RC-20 | FR-2 | Unload | `flushOnUnload()` | `fetch keepalive`, chunks < 60 KB, sync token only; async token → anonymous; `sendBeacon` fallback | › uses fetch keepalive… · › sends anonymously when getToken is async · › falls back to navigator.sendBeacon… |
| RC-21 | FR-2 | Facade | Before `init`; mapping of log / event / perf / error; re-init and dispose | No-op before init; correct wire events; clean replacement | `facade.test.ts` (3 tests) |
| RC-22 | FR-2 | Handlers | Window `error`, `unhandledrejection`, cross-origin "Script error.", uninstall, `pagehide` / hidden flush, recursion guard | Reported once; removable; flushes; no recursion | `handlers.test.ts` (5 tests) |
| RC-23 | FR-2 | React | `TelemetryErrorBoundary`: render error, function fallback with reset, healthy children | Reports with `component_stack`; fallback rendered; children when no error | `react.test.tsx` (3 tests) |
| RC-24 | FR-8.2 | Trace | `createTraceContext()` | W3C-valid `traceparent`; random ids accepted by `toWire` | `trace.test.ts` (2 tests) |

---

## 4. Flutter client (`FC-`)

All tests are in `test/telemetry_test.dart`.

| ID | Requirement | Case | Expected | Test |
|---|---|---|---|---|
| FC-1 | FR-2 | Flush one event | POST to the ingest path with message and app | delivery › flush posts queued events… |
| FC-2 | FR-4.3 | Identical events | Sent once | delivery › dedupes identical events… |
| FC-3 | FR-3.1 | `getToken` returns a token | Bearer header | delivery › attaches bearer token… |
| FC-4 | FR-3.2 | `getToken` throws | Delivered without an auth header | delivery › a throwing getToken still delivers anonymously |
| FC-5 | FR-4.4 | 5 events, `maxBatch` 2 | Requests of 2, 2, 1 | delivery › sends in chunks of maxBatch |
| FC-6 | FR-4.4 | 150 events, `maxBatch` 500 | Clamped: 100, 50 | delivery › maxBatch is clamped… |
| FC-7 | FR-5.1 | 500, 503, 429, network error (4 cases) | Re-queued; delivered on the next flush | retry › re-queues on … |
| FC-8 | FR-5.1 | 422 | Dropped, not retried | retry › drops a batch the gateway rejects (422) |
| FC-9 | NFR-4 | 5 events, `maxQueue` 3, gateway down | Oldest dropped; m2..m4 kept | retry › queue is bounded by maxQueue… |
| FC-10 | FR-2 | `enabled: false` | Nothing queued or sent | retry › disabled client neither queues nor sends |
| FC-11 | FR-4.1 | Oversized message / error / url / app_version | Truncated | wire caps › caps oversized fields |
| FC-12 | FR-4.1 | Empty key, long key and value, 40 entries | Empty key dropped; 32 entries; key and value truncated | wire caps › caps context entries, keys and values |
| FC-13 | FR-4.1 | Maximum fields plus 32 × 1024 context | ≤ 16384 bytes; context dropped; message intact | wire caps › shrinks an oversized event… |
| FC-14 | FR-4.1 | Multi-byte (CJK) text in every field | ≤ 16384 bytes | wire caps › multi-byte text is shrunk by bytes… |
| FC-15 | FR-8.2 | Uppercase valid ids; all-zero / invalid ids | Lowercased; invalid dropped | wire caps › includes valid trace context… |
| FC-16 | FR-4.1 | `app` with spaces | Omitted; valid app kept | wire caps › omits an app name the gateway would reject |
| FC-17 | FR-11 | All `TelemetryLevel` values | Every wire name is accepted by the gateway | wire caps › every level has a wire name… |
| FC-18 | FR-2 | Facade before init; error event fields | No-ops; message, stack and trace id present | facade (2 tests) |

---

## 5. End-to-end (`E2E-`)

`tests/smoke/smoke_test.py` runs against the full stack. The gateway is configured by `docker-compose.smoke.yml` with a JWT secret, an internal key, a rate limit of 60/min and targets `gateway` plus an unreachable host.

| ID | Requirement / AC | Steps | Expected |
|---|---|---|---|
| E2E-1 | AC-3, FR-2 | POST an anonymous error event with `app=smoke-web` and the backend `trace_id` | 204 |
| E2E-2 | AC-3, FR-3.1 | POST a warning event with a valid HS256 token (`org_id=acme`, `sub=smoke-user`) | 204 |
| E2E-3 | AC-2, FR-1 | Emit a two-span OTLP trace from `smoke-backend`; `GET tempo/api/traces/<id>` | 200 |
| E2E-4 | AC-2, FR-7.2 | Emit an OTLP log inside the span; query Loki `{service_name="smoke-backend"}` | Line present with `trace_id` equal to the trace |
| E2E-5 | FR-8.1, FR-8.2 | Query Loki `{service_name="frontend/smoke-web"}` for E2E-1 | Present, `trace_id` equals the backend trace, no `company_id` |
| E2E-6 | AC-3, FR-9 | Query Loki for E2E-2 | `company_id=acme`, `user_id=smoke-user` |
| E2E-7 | AC-1, FR-7.1 | Grafana `/api/datasources`, `/api/dashboards/uid/obs-overview` | Datasources `obs-prometheus`, `obs-loki`, `obs-tempo`; dashboard exists |
| E2E-8 | NFR-7 | Prometheus `sum(obs_ingest_events_total{job="obs-gateway",outcome="accepted"})` | ≥ 2 |
| E2E-9 | FR-7.2 | Prometheus `traces_spanmetrics_calls_total{service="smoke-backend"}` | Series present (Tempo metrics-generator working) |
| E2E-10 | NFR-3 | `GET /status` without a key | 401 |
| E2E-11 | AC-6, FR-10.2 | `GET /status` with the key | 503 `degraded`; `gateway` ok, `unreachable` not ok |
| E2E-12 | AC-4, FR-4.1 | Message of 2001 chars | 422 |
| E2E-13 | FR-4.4 | 101 events | 422 |
| E2E-14 | FR-4.1 | Body of 100 × 16384 + 1 bytes | 413 |
| E2E-15 | AC-5, FR-4.2 | 65 rapid requests with limit 60 | At least one 429 |
| E2E-16 | AC-9, FR-13 | `GET /status/stack` with the key (monitor interval 3 s) | 200 `ok`; components otel-collector, loki, tempo, prometheus, grafana; `fallback_active: false` |

## 5a. Outage and local fallback (`OUT-`)

`tests/smoke/outage_test.sh` runs against the running smoke stack with `COLLECTOR_FALLBACK_MODE=failover` and `LOCAL_LOG_DIR=./logs`.

| ID | Requirement / AC | Steps | Expected |
|---|---|---|---|
| OUT-1 | AC-10 | Poll `/status/stack` | `ok` before the outage |
| OUT-2 | FR-14.2 | `docker compose stop loki grafana`; poll | `fallback_active: true` within 60 s |
| OUT-3 | FR-14.1 | Read `logs/obs-gateway/stack-health.jsonl` | Grafana `unhealthy` record |
| OUT-4 | FR-5.2, NFR-10 | POST a frontend error | 204 |
| OUT-5 | FR-14.2 | Read `logs/obs-gateway/events.jsonl` | The event is present |
| OUT-6 | FR-14.3 | POST an OTLP/JSON log to the collector `:4318/v1/logs` | Log record in `logs/otel-collector/logs.jsonl` (failover) |
| OUT-7 | NFR-10 | `docker compose start loki grafana`; poll | `/status/stack` back to `ok` within 120 s |
| OUT-8 | FR-14.1 | Read `stack-health.jsonl` | Loki `recovered` record |

## 5b. RBAC edge (`RBAC-`)

`tests/smoke/rbac_edge_test.sh` merges `docker-compose.secure.yml` onto the smoke stack (`OBS_AUTHZ_MODE=static`, HS256 test secret). Tokens are minted inside the gateway container.

| ID | Requirement / AC | Request through `obs-edge` `127.0.0.1:8443` | Expected |
|---|---|---|---|
| RBAC-1 | FR-15.3 | `GET /` without a token | 401 |
| RBAC-2 | FR-15.1 | `GET /` with a token that has no roles | 403 |
| RBAC-3 | FR-15.3 | `GET /api/user` as `obs-viewer` | Grafana user `vera` created via auth proxy |
| RBAC-4 | FR-15.3 | `GET /api/user/orgs` as viewer | Role `Viewer` |
| RBAC-5 | FR-15.3 | `GET /api/user/orgs` as `obs-admin` via `obs_token` cookie | Role `Admin` |
| RBAC-6 | FR-15.3 | `GET /loki/ready` as viewer | 200 |
| RBAC-7 | FR-15.3 | `GET /tempo/ready` as viewer | 200 |
| RBAC-8 | FR-15.3 | `GET /prometheus/api/v1/query?query=up` as viewer | 200 |
| RBAC-9 | FR-15.3 | `GET /loki/ready` without a token | 401 |
| RBAC-10 | FR-15.3 | `GET 127.0.0.1:3000` (Grafana host port) | Not reachable |

---

## 6. Deployment (`DEP-`)

These run in the CI `stack` job.

| ID | Requirement | Check | Expected |
|---|---|---|---|
| DEP-1 | NFR-1 | `docker compose config -q` (base, `--profile demo`, prod override, test, smoke) | All valid |
| DEP-2 | NFR-3 | Prod override without `GRAFANA_ADMIN_PASSWORD` / `OTEL_INGEST_TOKEN` / `OBS_INTERNAL_API_KEY` | `config` fails naming the missing variable (verified manually 2026-09-13) |
| DEP-3 | NFR-3, FR-14.3 | `otelcol validate` (0.160.0) of `config.yml` + `fallback-{failover,mirror,queue}.yml` + `config.prod.yml` | Valid for every mode |
| DEP-4 | FR-2 | `docker build -f examples/react-demo/Dockerfile .` | Builds |
| DEP-5 | NFR-7 | All Prometheus targets while the stack is up | `up` for prometheus, otel-collector, obs-gateway, loki, tempo, grafana (verified 2026-09-13) |

---

## 7. Manual test cases (`MAN-`)

| ID | Requirement | Preconditions | Steps | Expected |
|---|---|---|---|---|
| MAN-1 | FR-2, FR-8.2 | `docker compose --profile demo up -d` | 1. Open <http://127.0.0.1:5173>. 2. Click *Send log*, *Send event*, *Unhandled promise rejection*, *Error with trace context*, *Throw render error*. 3. Click *Flush now*. 4. In Grafana Explore → Loki run `{service_name="frontend/react-demo"}`. | Five lines: `event/INFO demo_click`, `log/INFO Demo log message`, `error/ERROR Demo unhandled rejection`, `error/ERROR Demo API call failed` with a `trace_id`, `error/ERROR Demo render error` with `ctx_component_stack`. The page shows "Caught: Demo render error". **Passed 2026-09-13.** |
| MAN-2 | FR-7.2 | Stack running; a service emitting OTLP traces and logs (or the smoke test just run) | 1. Loki: open a log line with `trace_id`. 2. Click **TraceID**. 3. In the trace, click *Logs for this span*. | Tempo trace opens; the logs link returns the same line(s). |
| MAN-3 | FR-7.2 | Traces with calls between two services | Explore → Tempo → **Service Graph** | Nodes and edges with request and error rates. |
| MAN-4 | FR-12, AC-7 | Stack running | 1. Set `OBS_LOG_LEVEL=DEBUG` in `.env`. 2. `docker compose up -d obs-gateway`. 3. POST an event. 4. `docker compose logs obs-gateway`. | DEBUG-level lines appear (e.g. `urllib3` connection lines from the OTLP exporter) that are absent at `INFO`; no code change. |
| MAN-5 | NFR-4 | Stack running | 1. POST an event. 2. Within 5 s run `docker compose stop obs-gateway`. 3. Query Loki. | The event is present (flushed on SIGTERM). |
| MAN-6 | FR-5.2 | Stack running | 1. `docker compose stop otel-collector`. 2. POST events. 3. Start the collector. | Ingest keeps returning 204 while the collector is down; the gateway logs export failures; no gateway crash. |
| MAN-7 | FR-2 | React app in a real browser | 1. Queue events without flushing. 2. Close the tab. | Events arrive (keepalive fetch on `pagehide`). |
| MAN-8 | NFR-3 | Gateway with `OBS_CORS_ALLOW_ORIGINS=https://app.example` | `curl -H "Origin: https://app.example" -X OPTIONS -H "Access-Control-Request-Method: POST" …/ingest` and the same with another origin | Allowed origin gets `access-control-allow-origin`; others don't; no `allow-credentials` header. |
| MAN-9 | NFR-3 | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` with secrets set | 1. OTLP POST to `:4318/v1/logs` without a token. 2. With `Authorization: Bearer <OTEL_INGEST_TOKEN>`. 3. Log in to Grafana with `admin/admin`. | 1 → 401; 2 → 200; 3 → rejected (password from `.env` required). |
| MAN-10 | FR-6 | — | Inspect `loki/config.yml`, `tempo/config.yml` and `PROMETHEUS_RETENTION` | 720h / 336h / 15d. |
| MAN-11 | FR-14.3 | Smoke stack running | For each of `COLLECTOR_FALLBACK_MODE=mirror` and `queue`: `docker compose up -d otel-collector`, then check the container state and `logs/otel-collector/`. | Collector running with no restarts and the matching `--config=…/fallback-<mode>.yml`; `mirror` writes `logs.jsonl`; `queue` creates `queue/exporter_otlp_http_loki_logs` and `…tempo_traces`. **Passed 2026-09-13.** |
| MAN-12 | FR-15.3, C-3 | Loki `auth_enabled: true`, collector sending `X-Scope-OrgID`, secure overlay | Query `/loki/…` as two users of different tenants | Each sees only their tenant's logs. |

## 8. Lint (`LINT-`)

| ID | Requirement | Scope | Command | Expected |
|---|---|---|---|---|
| LINT-1 | NFR-6 | Gateway Python | `ruff check src tests && mypy src` (in `gateway-tests`) | Clean |
| LINT-2 | NFR-6 | React client | `npm run lint` (typescript-eslint type-checked, react-hooks) | Clean |
| LINT-3 | NFR-6 | React demo | `npm run lint` (in `react-demo-lint`) | Clean |
| LINT-4 | NFR-6 | Dockerfiles | `docker compose -f docker-compose.test.yml run --rm lint-dockerfiles` | Clean at warning threshold (DL3013 ignored with reason) |
| LINT-5 | NFR-6 | All YAML | `… run --rm lint-yaml` | Clean |
| LINT-6 | NFR-6 | All Markdown | `… run --rm lint-markdown` | Clean |

---

## 9. Requirement coverage matrix

| Requirement | Covered by |
|---|---|
| FR-1 OTLP ingest | E2E-3, E2E-4 |
| FR-2 Frontend ingest | GW-1–3, RC-10, RC-19–23, FC-1, FC-10, FC-18, E2E-1, MAN-1, MAN-7 |
| FR-3 Enrichment and anonymous | GW-22–29, GW-32, RC-12–13, FC-3–4, E2E-2, E2E-6 |
| FR-4.1 Field and size caps | GW-5–7, GW-48–50, RC-3–8, FC-11–16, E2E-12, E2E-14 |
| FR-4.2 Rate limit | GW-16–21, E2E-15 |
| FR-4.3 Dedupe | GW-10–15, RC-9, RC-11, FC-2 |
| FR-4.4 Batch limit | GW-8, RC-14, FC-5–6, E2E-13 |
| FR-5 Fire-and-forget | GW-45–46, RC-15, RC-17–18, FC-7–8, MAN-6 |
| FR-6 Retention | MAN-10 |
| FR-7 Grafana and correlation | E2E-4, E2E-7, E2E-9, MAN-2, MAN-3 |
| FR-8 Service, environment, trace attributes | GW-9, GW-41–44, RC-6, RC-24, FC-15, E2E-5 |
| FR-9 Tenant not a metric label | E2E-6 (Loki metadata); code review of `metrics.py` (no tenant labels) |
| FR-10 Health | GW-35–40, E2E-10–11 |
| FR-11 Severities | GW-4, GW-43, FC-17 |
| FR-12 Env-driven levels | MAN-4 |
| NFR-1 Self-contained | GW-53–54, DEP-1 |
| NFR-3 Secure | GW-18, GW-30–31, GW-34, GW-40, E2E-10, DEP-2–3, MAN-8–9 |
| NFR-4 Resilient | GW-15, GW-21, GW-33, GW-47, RC-16, FC-9, MAN-5 |
| NFR-6 Quality gates | LINT-1–6, FC (analyze), CI |
| NFR-7 Platform metrics | GW-51–52, GW-60, E2E-8, DEP-5 |
| NFR-10 Operability during outages | OUT-1–8 |
| FR-13 Stack health checks | GW-55–64, GW-96, E2E-16, DEP-1 |
| FR-14 Local fallback logs | GW-56–58, GW-65–67, GW-94–95, OUT-2–8, DEP-3, MAN-11 |
| FR-15 RBAC seam | GW-68–92, RBAC-1–10, MAN-12 |
