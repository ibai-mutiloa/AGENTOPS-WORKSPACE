# Product requirements: initial scope

Source of truth for the broader product vision: [`PROMPT.MD`](../PROMPT.MD). This document tracks the first engineering slices and their acceptance evidence.

## Milestone 0

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| M0-01 | Reproducible backend dependencies and quality checks | `uv sync`, Ruff, mypy, pytest |
| M0-02 | PostgreSQL local service | `docker compose up`, health status |
| M0-03 | Versioned initial schema | `alembic upgrade head`, schema inspection |
| M0-04 | API service health | `/health/live` returns 200; `/health/ready` returns 200 with DB and 503 without it |
| M0-05 | Architectural and security boundaries documented | Architecture and ADR review |

## First vertical slice (next)

| IDs | Behavior | Acceptance test |
| --- | --- | --- |
| RF-001–005 | Synthetic request intake, persisted message, classification, extraction, completeness | Known product reference extracted; ambiguous input flagged |
| RF-019–024 | Tenant-scoped mock product and document lookup, draft | Verified facts cite stored sources; unknown compatibility abstains |
| RF-028–034 | Exact action proposal and approval | No send before approval; edit invalidates prior approval; duplicate approval sends once |
| RF-046–050 | Authentication, authorization, audit, tenant isolation | Two-tenant tests and unauthorized approval rejection |

No LLM capability is claimed for the deterministic first implementation. Synthetic fixtures must never contain real customer or employer data.

## Safety acceptance

1. Server derives the active organization from authenticated membership, never a model-supplied ID.
2. Business reads and writes include organization scope.
3. The model can propose data but cannot grant permissions or execute sensitive actions.
4. Compatibility is verified only from authoritative product rules or explicitly applicable documents.
5. Approval binds the exact payload version and an idempotency key.
6. Ambiguous external outcomes are recorded for human resolution, not retried blindly.
