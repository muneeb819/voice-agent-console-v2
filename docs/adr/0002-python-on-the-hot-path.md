# ADR 0002 — Python for inference and the call path; TypeScript only for the console

- **Status:** Accepted
- **Context:** The speech service, vLLM, Silero VAD and every model runtime are Python. V1's bridge is TypeScript and calls Deepgram over a WebSocket; V2 keeps the orchestration local, next to the models.

## Decision

Write Gateway, Agent Runtime, Audio Engine and Model Management in Python 3.11+ (FastAPI, asyncio, httpx). Console V2's UI stays TypeScript. V1's proven telephony logic (G.711, RTP framing, AudioSocket, SIP registration, barge-in thresholds) is ported to Python in Phase 4, keeping V1's test vectors.

## Consequences

- One language and one test toolchain across the latency-critical path.
- CPU-heavy media work (RTP timing, resampling) runs in worker threads or native libraries; if profiling in Phase 4 shows the Audio Engine is CPU-bound at 25 calls per host, that single service may move to Go without changing its contracts.
