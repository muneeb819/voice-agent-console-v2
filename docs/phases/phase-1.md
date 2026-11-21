# Phase 1 milestone report — Independent Infrastructure Foundation

- **Date:** 2026-11-22
- **Branch / PR:** `phase-1/foundation` → [PR #1](https://github.com/muneeb819/voice-agent-console-v2/pull/1)
- **Scope change at approval:** AWS infrastructure is built by Muneeb ([ADR 0006](../adr/0006-aws-owned-by-operator.md)). This report covers the software side and the handoff spec.

## 1. Completed work

| Deliverable | Status |
| --- | --- |
| Monorepo with one directory per module and a shared kit + contracts package | Done |
| MMA AI Gateway: auth, alias routing, health probes, failover, load shedding, internal-only upstreams | Done |
| Speech Engine ported from V1 v1.1.0, plus a CPU dev image | Done (GPU path not yet run — Phase 2) |
| Agent Runtime, Audio Engine, Model Registry service shells with health contracts | Done |
| Contracts: agent versions with per-agent model pins, campaign policy, session events, routes | Done |
| PostgreSQL schema v1 + migration runner | Done |
| Local dev stack (Docker compose, and a no-Docker process runner) + smoke test | Done |
| CI: lint, tests on real Postgres, speech tests, image build, stack smoke | Done |
| ADRs 0001–0006 | Done |
| AWS infrastructure handoff spec | Done — [infra-handoff.md](../infra-handoff.md) |

## 2. Files and infrastructure changed

- **Repository:** `muneeb819/voice-agent-console-v2`, new branch `phase-1/foundation`; ~90 files added. `main` is unchanged until the PR is merged.
- **AWS:** none (owned by Muneeb).
- **V1 (`Voice-Agent-Console`):** none. It was only read.

## 3. Tests performed and 4. actual results

| Test | Where | Result |
| --- | --- | --- |
| `ruff check .` | Local + CI | Pass |
| Unit/contract/service tests | Local (Python 3.13) + CI (3.12) | 40/40 pass |
| Migrations + registry against real PostgreSQL 16 | Local + CI | Pass (applied once, idempotent, checksum enforced) |
| Speech service suite (CPU fakes) | Local + CI | 31/31 pass |
| Docker image build for all 7 images + `docker compose up` | CI only (sandbox cannot reach Docker Hub) | Pass |
| End-to-end smoke (`scripts/smoke.py`), 17 checks | Local processes + CI compose stack | 17/17 pass in both |

The smoke test's timings come from **fake models** and only show the plumbing adds little overhead (for example, about 23 ms to first streamed token from the fake LLM with a 20 ms per-token delay configured). They are **not** performance baselines. First real measurements come in Phase 2 on the GPU host.

## 5. Remaining issues

1. Real models have not run in V2 yet (needs the GPU host from the handoff spec).
2. Gateway proxies LLM traffic only; STT/TTS WebSocket proxying is Phase 2.
3. Agent Runtime and Audio Engine are shells; the conversation loop, policy engine and media path are Phase 4.
4. Console V2 UI not started (Phase 6, by plan).
5. PR #1 awaits your review and merge.

## 6. Costs

- AWS: USD 0 incurred by engineering.
- GitHub Actions: about 2 minutes of runner time per CI run.

## 7. Next milestone — Phase 2: Independent Speech Services

Needs from you: the dev GPU host and the items in the [handoff checklist](../infra-handoff.md#8-handoff-checklist--send-these-back-when-the-dev-environment-is-up).

Then: deploy the real Speech Engine on the L40S, register Whisper and Kokoro with pinned revisions, add Gateway STT/TTS streaming proxy, build an 8 kHz test set, and record the first measured STT accuracy and STT/TTS latency. Exit criteria from the approved roadmap: STT word error rate and latency recorded; TTS first-chunk p95 ≤ 300 ms on one stream.
