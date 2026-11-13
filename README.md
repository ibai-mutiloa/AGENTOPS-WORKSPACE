# AgentOps Workspace

AI-assisted operations workflows for industrial B2B teams. This repository is being built as a modular monolith with explicit evidence, tenant boundaries, and human approval before sensitive actions.

**Current state:** Milestone 0 foundation. The API exposes health checks and the database has an initial migration. Request processing, authentication, agent execution, and the web UI are not implemented yet.

## Why this project

The intended workflow turns a customer request into a grounded response and an exact action proposal. Trusted application code controls data access and execution; the model will not receive credentials or authority to approve its own actions. The engineering work centers on recoverable workflows, evidence provenance, idempotent execution, and tenant isolation.

## Local development

Requirements: Python 3.12–3.14, [uv](https://docs.astral.sh/uv/), Docker with Compose. The local PostgreSQL container publishes host port `55432` to avoid common port conflicts. The API uses the pure-Python `pg8000` driver, which works on Windows machines that block native driver DLLs.

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f infra/docker-compose.yml up -d db
cd apps/api
uv sync --python 3.12
uv run alembic upgrade head
uv run python -m uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Check `/health/live`, `/health/ready`, and `/docs`. The first endpoint checks the process; readiness also checks PostgreSQL.

Run quality checks from `apps/api`:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Local credentials in `.env.example` are **development-only**. Set unique secrets for any shared deployment. No hosted model account is required for Milestone 0.

## Repository map

- `apps/api`: FastAPI application, initial SQLAlchemy models, Alembic migration, tests.
- `infra`: PostgreSQL Compose service.
- `docs`: architecture, requirements, and decisions.
- `PROMPT.MD`: product brief from the project owner.

## Planned first workflow

Synthetic email intake → deterministic extraction and validation → tenant-scoped product/document lookup → evidence assessment → validated draft → persisted action proposal → human review → exact approved simulated send → audit trail. The smallest implementation will use deterministic data and a clearly labeled simulated email adapter. LLM drafting and LangGraph checkpointing follow once the trust boundaries and persistence are tested.

## Scope and limitations

No business endpoint or real email connector exists. The schema is a starting point, not the complete data model. Health checks have no authentication because they expose only service status. The readiness endpoint checks database connectivity, not migration version. See [architecture](docs/architecture.md), [requirements](docs/product-requirements.md), and [roadmap](docs/development-roadmap.md). Milestone 0 was verified with Python 3.14 on Windows; GitHub CI targets Python 3.12 and has not run yet.
