# ADR 0003 — Self-hosted model candidates and the "no external inference" rule

- **Status:** Accepted for evaluation (final picks are made from Phase 2–3 measurements)

## Decision

- **STT:** Faster-Whisper `large-v3-turbo` (MIT), already integrated. Compare against NVIDIA Parakeet for streaming latency.
- **TTS:** Kokoro-82M (Apache-2.0), already integrated. Chatterbox Turbo (MIT, watermarked) for consented cloned voices only.
- **LLM:** Qwen3 family (Apache-2.0) served by vLLM with tool calling; a mixture-of-experts model for quality and a small dense model for the low tier.
- **Rule:** the Gateway only routes to internal upstreams. `RouteTable` validation rejects public IPs and non-internal hostnames, so a configuration change cannot silently send a call to an external AI provider. Phase 9 certifies this with egress logs.

## Consequences

- Every model is registered with an exact revision and licence (`models` table) before it can be routed to.
- No benchmark is claimed until it is measured on the target GPU with recorded run IDs.
