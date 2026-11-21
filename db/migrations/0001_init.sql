-- MMA-VIE core schema v1 (PostgreSQL 15+).
-- Replaces V1's single JSON document with typed, versioned, auditable tables.
-- Agent and campaign-policy content stays JSONB validated by mmavie_common.contracts,
-- with the identity, version and status columns relational for querying and audit.

CREATE TABLE workspaces (
    id          TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9_-]{1,64}$'),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Model registry: every model in use is pinned to a revision with its licence recorded.
CREATE TABLE models (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('stt', 'llm', 'tts', 'vad', 'clone')),
    name          TEXT NOT NULL,
    revision      TEXT NOT NULL,
    licence       TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    weights_uri   TEXT,                        -- s3://... copy we host
    sha256        TEXT CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    status        TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'approved', 'retired')),
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, name, revision)
);

CREATE TABLE agents (
    id            TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9_-]{1,64}$'),
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    name          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_versions (
    agent_id      TEXT NOT NULL REFERENCES agents(id),
    version       INTEGER NOT NULL CHECK (version >= 1),
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'retired')),
    spec          JSONB NOT NULL,              -- mmavie_common.contracts.agent.AgentVersion
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, version)
);
-- At most one published version per agent.
CREATE UNIQUE INDEX agent_versions_one_published ON agent_versions (agent_id) WHERE status = 'published';

CREATE TABLE campaigns (
    id               TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9_-]{1,64}$'),
    workspace_id     TEXT NOT NULL REFERENCES workspaces(id),
    name             TEXT NOT NULL,
    dialer_campaign  TEXT,                      -- VICIdial campaign id
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE campaign_policies (
    campaign_id   TEXT NOT NULL REFERENCES campaigns(id),
    version       INTEGER NOT NULL CHECK (version >= 1),
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'retired')),
    policy        JSONB NOT NULL,              -- mmavie_common.contracts.policy.CampaignPolicy
    approved_by   TEXT,                        -- compliance owner sign-off
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, version)
);
CREATE UNIQUE INDEX campaign_policies_one_published ON campaign_policies (campaign_id) WHERE status = 'published';

CREATE TABLE campaign_agents (
    campaign_id   TEXT NOT NULL REFERENCES campaigns(id),
    agent_id      TEXT NOT NULL REFERENCES agents(id),
    weight        INTEGER NOT NULL DEFAULT 1 CHECK (weight >= 0),
    PRIMARY KEY (campaign_id, agent_id)
);

CREATE TABLE voices (
    id            TEXT PRIMARY KEY,            -- stock name or 32-hex clone id
    kind          TEXT NOT NULL CHECK (kind IN ('stock', 'clone')),
    model_id      BIGINT REFERENCES models(id),
    display_name  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A cloned voice cannot exist without a consent record; withdrawal disables it.
CREATE TABLE voice_consents (
    voice_id          TEXT PRIMARY KEY REFERENCES voices(id),
    speaker_name      TEXT NOT NULL,
    consent_document  TEXT NOT NULL,           -- s3:// URI of the signed consent
    granted_at        TIMESTAMPTZ NOT NULL,
    withdrawn_at      TIMESTAMPTZ,
    recorded_by       TEXT NOT NULL
);

CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    campaign_id     TEXT REFERENCES campaigns(id),
    agent_id        TEXT,
    agent_version   INTEGER,
    policy_version  INTEGER,
    transport       TEXT NOT NULL CHECK (transport IN ('sip', 'audiosocket', 'browser', 'synthetic')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    end_reason      TEXT,
    disposition     TEXT,
    recording_uri   TEXT,
    FOREIGN KEY (agent_id, agent_version) REFERENCES agent_versions(agent_id, version)
);
CREATE INDEX sessions_campaign_started ON sessions (campaign_id, started_at DESC);

CREATE TABLE session_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn        INTEGER NOT NULL DEFAULT 0,
    type        TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    payload     JSONB NOT NULL                 -- mmavie_common.contracts.events.SessionEvent
);
CREATE INDEX session_events_session ON session_events (session_id, id);
CREATE INDEX session_events_type_ts ON session_events (type, ts DESC);

-- Append-only audit trail for config, model, export and recording access.
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_log_ts ON audit_log (ts DESC);
