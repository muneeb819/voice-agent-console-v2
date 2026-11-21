# Provenance

Copied from `muneeb819/Voice-Agent-Console` → `speech-service/` at commit `13d210895d6312f5ed12535d31483fd31cbe3559` (2026-11-21), version 1.1.0, on 2026-11-22. Both repositories belong to MMA Digital Labs.

Changes in V2 so far:
- `Dockerfile`: added a CPU-only `dev` target (fake engines) for the local stack and CI.

Everything else is unchanged. Its 31 tests pass on CPU fakes; the real GPU path (Faster-Whisper on CUDA, Kokoro) has not yet been run in V2 and is the first task of Phase 2. Third-party model licences: see `THIRD_PARTY_NOTICES.md`.
