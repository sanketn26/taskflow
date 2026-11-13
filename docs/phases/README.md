# Taskwire Phase Implementation Guide

This directory is the **authoritative implementation contract**. Each phase file defines its goal, contract, required tests, and exit gate. If the vision or architecture document disagrees with a phase, **the phase wins**.

## Delivery order

```text
Kubernetes pre-alpha (do in order)
  Phase 0  Repository and test spine (Go binary and Python library)
  Phase 1  Backend contract                ← Go, inside the controller
  Phase 2  Controller API                  ← the Taskwire protocol
  Phase 3  Python library                  ← submit, run, read results
  Phase 4  Controller maintenance and observability
  Phase 5  Kubernetes packaging            ← pre-alpha gate

After pre-alpha
  Phase 6  Stage-two features (see "Pre-alpha scope"), then the Kafka result channel
  Phase 7  Hardening and release
  Phase 8  Examples and adoption docs
```

## Status

| Phase | Status | Notes |
|------:|--------|-------|
| 0 | **Reworked; remaining items in the phase file** | Go module, Go PostgreSQL fixture, `clients/` layout and trimmed Python package are in place. Protocol build and the Python-against-controller fixture arrive with Phase 2. CI has never run on GitHub |
| 1 | **Next** | Backend interface in Go, conformance suite, in-memory and PostgreSQL backends, startup assertion |
| 2–8 | **Not started** | Specification only. Phase 6 holds everything marked stage two above |

The repository was reset in November 2026; see [../decisions.md](../decisions.md). Nothing that executes a task exists yet.

## Pre-alpha scope

The phase documents specify the whole design. **Not all of it is built before the pre-alpha gate.** Phases 1 to 5 build the first column; everything in the second column is specified where shown, is built in Phase 6, and must not be started earlier. The purpose is to have a task running end to end on Kubernetes before adding anything that only matters at scale.

| Area | Pre-alpha (Phases 1–5) | Stage two (Phase 6) | Specified in |
|---|---|---|---|
| Backend | S1–S12, one-row reads with `ready` and `running`, UUIDv7 IDs, idempotency keys, stored statistics | Daily partitions and partition-drop retention (pre-alpha retention deletes rows in batches) | Phase 1 |
| Affinity | Every task is given its bucket at submit; claims ignore it | Bucket table, consistent hashing, owner-first claims, `_affinity` | Phase 1 |
| API | All task operations, long-poll claim and wait, fencing, no prefetch, result verification, `max_queued` | `submit_rate`, `max_running`, pacing and `Pressure`, load shedding, scoped tokens | Phase 2 |
| Results | S3 through signed links; memory for tests | Redis; Kafka | Phases 2, 3, 6 |
| Library | `submit`, `map`, futures, `serve` and `python -m taskwire run` in `process` mode | `thread` and `async` modes, `AsyncTaskwire` | Phase 3 |
| Conformance | The backend conformance suite; the library tested against a real controller | `taskwire library-check` as a published tool | Phase 2 |
| Observability | Per pool and per work type; health endpoints; logs | Per worker: status reports, `taskwire_instance_*`, `taskwire instances` | Phase 4 |
| Kubernetes | Chart, KEDA scaling, drain, default alerts for the pre-alpha metrics | Dashboards and alerts for stage-two features | Phase 5 |

Hooks that exist from the first migration so that stage two needs no rewrite: the `bucket` column, time-ordered task IDs, the idempotency-key table, `worker_status`, and the limits document accepting (and ignoring, with a logged warning) settings for features not yet built.

A required test or exit-gate clause that concerns a stage-two feature belongs to Phase 6, not to the phase whose document contains it.

## Provisional defaults

These numbers were chosen without measurements. Each is a placeholder until the named test sets it, and none may be described as a recommendation before then.

| Default | Value now | Set by |
|---|---|---|
| Buckets per pool | 1024 | Affinity tests at the largest supported worker count |
| Ring points per worker | 8 | The same; trades evenness against how many neighbours a change touches |
| Affinity window | 5 s | Latency of spilled tasks under a saturated owner |
| Pacing threshold | 0.7 | Back-pressure test: smooth slow-down without stalling |
| Library submit deadline | 30 s | The same |
| Input limit | 64 KiB | Churn benchmark with realistic inputs |
| API replicas | 3 | Phase 5 disruption tests |
| Database pool per controller | 16 | Churn benchmark |
| Statistics interval | 5 s minimum | Statistics cost test |
| Worker status rewrite interval | 15 s | Heartbeat write load at the largest supported worker count |

## Rules that apply to every phase

- Only the controller touches the backend. No library, in any language, holds a database credential or issues SQL.
- The controller API is the protocol. The controller enforces every guarantee itself and treats libraries as untrusted; a library is supported if it passes `taskwire library-check`.
- No task output is ever written to the backend or sent through the controller. Libraries put results in the result channel; the controller records state and a reference.
- Inside the controller, all storage access goes through the Phase 1 `Backend` interface. No Go package outside a backend package imports a database driver.
- A backend is supported only if it passes the conformance suite. Every controller process runs the startup assertion before doing any work.
- Execution is at least once. Never claim exactly-once.
- Expiry and scheduling use the backend clock, never the pod clock.
- Metric labels are bounded: pool, task name, outcome. Task IDs, owner IDs, and task versions are never labels.
- Never log task inputs, results, or database credentials.
- Tests poll observable state against a deadline (`harness.wait_until`); no fixed sleeps. Chaos tests record actions in a seeded `ChaosTimeline`.
- Controller unit tests use the in-memory backend. Integration tests run against a real PostgreSQL instance, never a mock. Library tests run against a real controller binary, never a fake of the protocol.
- Deployment style is the user's choice. Where the docs recommend dedicated workers or process mode, that is a recommendation with its trade-off stated, never an enforced rule.
- Earlier phases stay green: `make lint unit integration packaging`.

## How to implement a phase

1. Read the whole phase document.
2. Branch from `main`: `git checkout -b phase-N-<short-name>`.
3. Write the required tests first where practical, then implement.
4. Check the work against the phase's required tests and exit gate.
5. Do not start the next phase until the exit gate passes or the gaps are written down as explicit deferrals.

## Local loop

```bash
make sync                      # Python venv from clients/python/uv.lock
make format lint unit          # fast loop, no database (Go and Python)
make db-up                      # local PostgreSQL in Docker (make db-down to stop)
make integration               # Go tests; uses $TASKWIRE_TEST_DATABASE_URL, or the db-up server
make packaging                 # Python wheel in a clean venv
```
