# Taskwire Phase Implementation Guide

This directory is the **authoritative implementation contract**. Each phase file defines its goal, contract, required tests, and exit gate. If the vision or architecture document disagrees with a phase, **the phase wins**.

## Delivery order

```text
Kubernetes pre-alpha (do in order)
  Phase 0  Repository and test spine
  Phase 1  Backend contract
  Phase 2  Worker
  Phase 3  Client SDK
  Phase 4  Controller and observability
  Phase 5  Kubernetes packaging            ← pre-alpha gate

After pre-alpha
  Phase 6  Object storage for large values
  Phase 7  Hardening and release
  Phase 8  Examples and adoption docs
```

## Status

| Phase | Status | Notes |
|------:|--------|-------|
| 0 | **Done locally; CI pending** | Packaging, `taskwire` command, environment configuration, PostgreSQL test fixture, CI on PostgreSQL 14 and 18 |
| 1 | **Next** | Backend interface, conformance suite, in-memory and PostgreSQL backends, startup assertion |
| 2–8 | **Not started** | Specification only |

The repository was reset in November 2026; see [../decisions.md](../decisions.md). Nothing that executes a task exists yet.

## Rules that apply to every phase

- All storage access goes through the Phase 1 `Backend` interface. No module outside a backend package imports a database driver.
- A backend is supported only if it passes the conformance suite. Every process runs the startup assertion before doing any work.
- Execution is at least once. Never claim exactly-once.
- Expiry and scheduling use the backend clock, never the pod clock.
- Metric labels are bounded: pool, task name, outcome. Task IDs, owner IDs, and task versions are never labels.
- Never log task inputs, results, or database credentials.
- Tests poll observable state against a deadline (`harness.wait_until`); no fixed sleeps. Chaos tests record actions in a seeded `ChaosTimeline`.
- Unit tests use the in-memory backend. Integration tests run against a real PostgreSQL instance, never a mock.
- Earlier phases stay green: `make lint unit integration packaging`.

## How to implement a phase

1. Read the whole phase document.
2. Branch from `main`: `git checkout -b phase-N-<short-name>`.
3. Write the required tests first where practical, then implement.
4. Check the work against the phase's required tests and exit gate.
5. Do not start the next phase until the exit gate passes or the gaps are written down as explicit deferrals.

## Local loop

```bash
make sync                      # create .venv from uv.lock
make format lint unit          # fast loop, no database
make db-up                      # local PostgreSQL in Docker (make db-down to stop)
make integration               # uses $TASKWIRE_TEST_DATABASE_URL, or the db-up server
make packaging                 # wheel in a clean venv
```
