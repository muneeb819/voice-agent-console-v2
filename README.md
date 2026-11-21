# Voice Agent Console V2 — MMA Voice Intelligence Engine (MMA-VIE)

MMA Digital Labs' self-hosted voice-agent platform. V2 runs speech-to-text, the LLM and text-to-speech on our own infrastructure, so **no external AI inference provider is needed for normal operation**. It is a separate project from [Voice Agent Console V1](https://github.com/muneeb819/Voice-Agent-Console), which stays untouched as the reference and fallback.

**Status:** Phase 1 (foundation). Services run end to end on fake models for development; real models arrive in Phases 2–3. Nothing here is production-ready yet.

## Layout

```
packages/mmavie-common   shared service kit (settings, auth, health, metrics, logs) + data contracts
services/gateway         MMA AI Gateway: auth, model-alias routing, failover, load shedding
services/speech          MMA Speech Engine: Faster-Whisper STT, Kokoro TTS, consented cloning (ported from V1)
services/agent-runtime   MMA Agent Runtime: conversation state + runtime-enforced campaign policy (Phase 4)
services/audio-engine    MMA Real-Time Audio Engine: SIP/RTP, AudioSocket, VAD, barge-in (Phase 4)
services/model-registry  MMA Model Management: models with pinned revision + licence (Phase 5 builds on it)
services/llm-fake        dev/CI stand-in for the vLLM LLM Engine — never deployed
console/                 Voice Agent Console V2 UI (Phase 6)
db/                      PostgreSQL migrations + runner
docs/                    ADRs, AWS handoff spec, phase reports
```

## Run it locally

With Docker:

```bash
docker compose up -d --build   # Postgres, migrations, all services, fake LLM + fake speech
make smoke                     # 17 end-to-end checks
```

Without Docker (needs Python 3.11+ and a PostgreSQL you can reach):

```bash
make install
make local DB=postgresql://user@127.0.0.1:5432/mmavie
make smoke
make local-stop
```

Tests: `make lint test` (set `MMAVIE_TEST_DATABASE_URL` to include the database tests) and `make test-speech`.

All local ports bind to `127.0.0.1`. Tokens beginning with `dev-` are for local use only; every service refuses them in staging and prod.

## Rules this codebase follows

- **No external AI inference.** The Gateway only routes to internal upstreams; the route table rejects public addresses.
- **Campaign rules are enforced in code** by the Agent Runtime, not only written into prompts ([ADR 0005](docs/adr/0005-runtime-enforced-campaign-policy.md)).
- **Every model is pinned** to a revision with its licence recorded before it can be used.
- **No benchmark without evidence.** Performance numbers are reported only from recorded runs on the target hardware.
- **AWS is owned by Muneeb.** Services are built against [docs/infra-handoff.md](docs/infra-handoff.md).

## Documents

- Phase 0 engineering report (architecture, costs, risks, roadmap) — kept in the project workspace
- [Architecture decisions](docs/adr/)
- [AWS infrastructure handoff spec](docs/infra-handoff.md)
- [Phase 1 milestone report](docs/phases/phase-1.md)
- [Changelog](CHANGELOG.md)
