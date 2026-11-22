# Changelog

## 0.3.0 — Phase 4 start: runtime policy engine (2026-11-22)

### Added
- `mmavie_runtime.policy.PolicyEngine`: campaign rules enforced in code — calling window in the lead's time zone (unknown zone fails closed), stop/DNC phrases end the call with a DNC disposition, output filter on every sentence before TTS (campaign rules plus always-on built-ins blocking requests for SSN, card, bank, Medicare numbers and passwords), caller facts accepted only with a quote the caller really said, transfer gate on required facts plus verified consent, disposition allowlist, call-length wrap-up and fixed closing, verbatim disclosures. Every verdict converts to a `PolicyDecision` session event.
- `record_caller_fact` added to the built-in functions. A fact counts only if the quote is real caller speech AND the value matches that turn: numbers/ranges must contain a number the caller said (digits or words), yes/no needs an un-negated affirmative or a clear negative, free text must use the caller's words. A DNC disposition cannot be replaced.

## 0.2.0 — Phase 2 preparation, before GPU (2026-11-22)

### Added
- Gateway speech routes: WebSocket relay for live STT and streaming TTS (`/v1/stream/stt`, `/v1/stream/tts`, `?model=<alias>`) and one-shot TTS (`/v1/audio/speech`), with header-only service auth, failover before accept, and one concurrency slot held per stream.
- `bench/`: speech benchmark (WER, end-of-speech → final, TTS first audio, real-time factor, concurrency) that saves every run with its commit, host and instance type; synthetic test-set builder; call-centre sentence set.
- `config/model-catalog.json` and `scripts/register_models.py`: pin Hugging Face revisions, download once, register with licence; the Speech Engine then runs with `HF_HUB_OFFLINE=1`.
- Runbook `docs/runbooks/phase-2-speech-gpu.md`.
- Smoke test now covers streaming STT and TTS through the Gateway (19 checks).

### Fixed
- Gateway stream slots are released even if the handler is cancelled while closing the upstream (found by test; would have leaked capacity).

### Found
- faster-whisper's `large-v3-turbo` downloads a third-party conversion (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`); recorded in the catalog and notices as licence-to-verify.

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
