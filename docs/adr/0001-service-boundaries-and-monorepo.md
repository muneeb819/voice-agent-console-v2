# ADR 0001 — Service boundaries and a single monorepo

- **Status:** Accepted (Phase 0 approval, 2026-11-22)
- **Context:** V1 runs the console, configuration, browser test and telephony bridge in one Node process; a crash or deploy drops every call. V2 must isolate the latency-critical call path from the control plane.

## Decision

Split MMA-VIE into the eight modules from the approved architecture, each its own deployable service with `/healthz`, `/readyz` and `/metrics`:

| Directory | Module |
| --- | --- |
| `services/gateway` | MMA AI Gateway |
| `services/speech` | MMA Speech Engine (ported from V1) |
| LLM Engine | vLLM (third-party server, configured, not written); `services/llm-fake` stands in for dev/CI |
| `services/audio-engine` | MMA Real-Time Audio Engine |
| `services/agent-runtime` | MMA Agent Runtime |
| `services/model-registry` | MMA Model Management |
| Observability | Prometheus/Grafana config + the shared metrics kit |
| `console/` | Voice Agent Console V2 (Phase 6) |

Keep them in one repository with one shared package, `packages/mmavie-common`, holding the service kit (settings, auth, health, metrics, logging) and the data contracts. One repo keeps contract changes atomic across services for a small team.

## Consequences

- A contract change and every service that uses it land in one pull request and one CI run.
- Each service still builds its own image and can be deployed and scaled alone.
- If the team grows, services can move to their own repos with the contracts package published.
