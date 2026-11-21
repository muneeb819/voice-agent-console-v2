# ADR 0004 — PostgreSQL with versioned agents and campaign policies

- **Status:** Accepted
- **Context:** V1 stores all configuration as one JSON document in SQLite. Editing any agent revises everything, there is no per-agent publish or rollback, and calls read whatever is saved at that moment.

## Decision

PostgreSQL (RDS in AWS) with forward-only numbered migrations (`db/migrations`, applied by `db/migrate.py` under an advisory lock, checksums enforced). Agents and campaign policies are immutable numbered versions with at most one `published` version each, enforced by a partial unique index. A session records the exact agent and policy versions it ran. Recordings and model weights go to S3, not the database.

## Consequences

- Rollback is "publish the previous version"; in-progress calls keep the version they started with.
- Every config change, model registration, export and recording access is written to `audit_log`.
