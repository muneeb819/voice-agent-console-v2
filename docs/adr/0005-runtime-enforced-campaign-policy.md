# ADR 0005 — Campaign rules are enforced by the runtime, not the prompt

- **Status:** Accepted
- **Context:** In V1 most campaign and compliance rules are prompt text. A model can ignore a prompt; it cannot ignore code.

## Decision

`CampaignPolicy` (in `mmavie_common.contracts.policy`) defines hard rules the Agent Runtime evaluates on every turn and tool call: stop/DNC phrases, calling window, verbatim disclosures with playback evidence, an output filter applied to each LLM sentence before speech, transfer gates on caller-stated facts and consent, call-length and concurrency limits, and an allowed disposition list. Every decision emits a `PolicyDecision` session event. Prompt text may repeat the rules, but enforcement never depends on it.

## Consequences

- Each hard rule needs an automated test before a campaign can publish (Phase 4 / Phase 8 gate).
- Policies carry `approved_by` for the compliance owner's sign-off. They implement policy; they are not legal advice.
