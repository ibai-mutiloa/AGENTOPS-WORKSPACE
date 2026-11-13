# ADR 0001: Modular monolith and transactional proposals

Status: accepted for the initial implementation.

## Decision

Run transport, application services, workflow coordination, and adapters in one API process with PostgreSQL as the transactional store. Persist action proposals independently from future LangGraph checkpoints. The exact proposal payload, version, hash, and idempotency key are the execution authority after approval.

## Alternatives

- Microservices with a message broker: adds distributed failure modes before workload requires them.
- LangGraph state as the only source of approval truth: makes authorization and concurrency semantics harder to audit and test.
- A separate vector database: duplicates storage and tenant permission logic at MVP scale.

## Consequences

The API remains easy to run and test. Scaling a slow model workflow independently may eventually require a worker process, but the same service interfaces and database can be retained. Exactly-once external effects cannot be guaranteed by a local database alone; adapter idempotency and reconciliation remain required.

The local PostgreSQL connection uses `pg8000`, a pure-Python SQLAlchemy driver. This avoids native DLL restrictions observed in the Windows development environment. A later performance test may justify switching drivers without changing the application service boundary.
