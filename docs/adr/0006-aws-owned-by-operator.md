# ADR 0006 — AWS infrastructure is built and owned by Muneeb; software ships against a written contract

- **Status:** Accepted (Phase 1 approval change, 2026-11-22)

## Decision

Muneeb provisions and operates all AWS resources (VPC, GPU and CPU instances, RDS, S3, Secrets Manager, ALB, IAM). Engineering does not create, change or pay for AWS resources. The interface between the two is `docs/infra-handoff.md`: instance types, ports, security-group rules, IAM permissions, secret names, environment variables and health checks per service. Any change to that file is a reviewed pull request.

## Consequences

- Services read all configuration from environment variables and secrets; nothing assumes a specific host.
- Deployment steps that need AWS access are written as runbooks for Muneeb to execute, or are run by Muneeb's own automation.
- V1 and its infrastructure remain untouched.
