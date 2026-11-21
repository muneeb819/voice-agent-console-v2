# Changelog

## 0.1.0 — Phase 1 foundation (2026-11-22)

### Added
- `packages/mmavie-common`: service kit (fail-closed settings, bearer-token auth, `/healthz`, `/readyz`, `/metrics`, JSON logs with request ids) and versioned contracts for agent versions with per-agent model pins, campaign policies, session events and Gateway routes.
- MMA AI Gateway: OpenAI-compatible LLM routing by alias, active health probes, failover before first byte, per-upstream load shedding (503 + `Retry-After`), internal-upstream-only route validation.
- Speech Engine ported unchanged from V1 `speech-service` v1.1.0, plus a CPU-only `dev` Docker target.
- Agent Runtime, Audio Engine and Model Registry service shells with health contracts; contract validation endpoints; model registration with revision + licence and audit logging.
- PostgreSQL schema v1 (agents and policies as immutable versions, sessions, session events, model registry, voices and consents, audit log) with a checksum-enforcing migration runner.
- Local stack: `compose.yaml`, `scripts/run-local.sh`, `scripts/smoke.py`.
- CI: lint, tests with real PostgreSQL, speech tests, image build + stack smoke test.
- ADRs 0001–0006 and the AWS infrastructure handoff spec.
