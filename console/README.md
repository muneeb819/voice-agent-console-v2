# Voice Agent Console V2 (Phase 6)

The administration UI is built in Phase 6, after the call engine works end to end (Phase 4), so every screen is wired to a real API rather than placeholders.

Planned areas, from the approved requirements: Dashboard, Agent Studio, Multi-Agent Management, Campaign Management, Persona and Prompt Studio, STT/TTS/LLM Model Management, Voice Library, Voice Cloning Laboratory, Real-Time Audio Laboratory, Testing Simulator, Sandbox, Deployment Manager, Telephony Integration, Session Monitoring, Infrastructure Monitoring, Security and User Management, Configuration and System Administration.

Until then, the console's server-side contracts already exist: agent versions and campaign policies are validated by `POST /v1/validate/agent-version` and `POST /v1/validate/campaign-policy` on the Agent Runtime.
