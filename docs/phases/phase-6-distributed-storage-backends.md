# Phase 6 — Distributed Storage Backends

## Goal

Add PostgreSQL and S3 adapters that implement the exact `TaskStateStore` and
`ObjectStore` interfaces frozen in Phase 2 — no protocol, schema, config
key, or clustering behavior changes. This phase exists so a truly
distributed deployment can point every agent at one shared transactional
state store and one shared object store instead of each node owning
independent SQLite/filesystem storage. It is the concrete work Phase 2's
"PostgreSQL/S3 remain separately gated future adapters" and Phase 5's
S3 object-placement policy have been referring to since those phases were
written; this phase makes both adapters real and conformance-tested rather
than configuration placeholders.

Distributed storage backends and clustering (Phase 5) are independent,
composable gates: a deployment may enable clustering with per-node
SQLite/filesystem and forwarding, share a PostgreSQL/S3 backend across
agents with clustering disabled, or combine both. Neither phase requires
the other.

## Phase 0 Baseline and Dependencies

Depends on Phase 2's `TaskStateStore`/`ObjectStore` interfaces, its shared
conformance suite, and its backend registry (`unsupported_backend` for
anything unregistered). This phase adds `postgres` and `s3` as registered
backend `type` values; it does not touch the interfaces, does not change
what `memory`/`sqlite`/`filesystem` do, and every Phase 0–5 test must
remain green with the new backends absent from a given deployment's
configuration.

Extends the Phase 1 configuration contract additively. `storage.state`
gains fields required only when `type: "postgres"`; each entry under
`storage.objects.stores` gains fields required only when `type: "s3"`.
Unknown fields for the *active* backend type remain rejected exactly as
Phase 1 specifies ("backend-specific fields supplied for a different
backend are rejected rather than ignored") — a `sqlite`-typed state store
that also sets `postgres_dsn` is invalid, not silently ignored.

## Testable Outcome

Two or more agents configured with the same PostgreSQL state store and the
same S3 object store observe consistent task state without agent-to-agent
replication: a task claimed by one agent is never also claimed by another,
completions are durable across agent restart, and result replay/retention
behave identically to the SQLite/filesystem defaults. The identical Phase 2
conformance suite passes against real (or compatible test-double) Postgres
and S3 services.

## Configuration Additions

```yaml
storage:
  state:
    type: "postgres" # v0.1: memory | sqlite | postgres
    dsn: "" # required, non-empty for postgres (as for sqlite)
    postgres_pool_max_conns: 10
    postgres_statement_timeout_ms: 5000
  objects:
    stores:
      shared:
        type: "s3" # v0.1: memory | filesystem | s3
        bucket: ""
        region: ""
        prefix: ""
        endpoint: "" # empty selects AWS S3; non-empty selects an S3-compatible endpoint (e.g. MinIO)
        credentials_env: "" # names an env var holding credentials; never inline in YAML
        multipart_threshold_bytes: 8388608
```

`dsn` continues to carry credentials and is never logged, matching Phase
7's existing "never log ... DSNs with credentials" rule. `credentials_env`
follows the same pattern as `TASKWIRE_POSTGRES_DSN`-style indirection from
the Phase 0 example: the loader validates the field is a non-empty string
naming an environment variable; resolving that variable at startup is an
agent-command concern, not the hermetic config loader's, exactly like
`python_executable` and `working_directory` in Phase 1.

Validation additions to Phase 1's list:

- `type: "postgres"` requires non-empty `dsn`; `postgres_pool_max_conns`
  and `postgres_statement_timeout_ms` are positive.
- `type: "s3"` requires non-empty `bucket` and `region`; `credentials_env`
  is a non-empty string; `multipart_threshold_bytes` is positive.
- `sqlite_*`/`postgres_*` fields are mutually exclusive per the active
  `storage.state.type`; the same rule applies to `filesystem`/`s3` fields
  per object store entry. Supplying the wrong backend's fields is
  `invalid_config`, not silently ignored.
- Unknown backend `type` values other than the five now registered
  (`memory`, `sqlite`, `postgres` for state; `memory`, `filesystem`, `s3`
  for objects) remain `unsupported_backend`.

## PostgreSQL State Store

`agent/internal/state/postgres.go` implements `TaskStateStore` from Phase
2 with no new exported surface beyond that interface. `Claim` uses
`SELECT ... FOR UPDATE SKIP LOCKED` scoped to eligible, compatible tasks so
concurrent agents never claim the same row without an external lock
manager; `Complete`/`Fail`/`Cancel`/`AcknowledgeResult` remain single
transactions with the same fencing-by-lease-ID comparison Phase 2
specifies for SQLite. Schema creation and migrations are transactional and
versioned, matching Phase 2's requirement for SQLite. Connection pool size
and statement timeout are configured, not hardcoded; a lost connection or
exhausted pool surfaces `storage_unavailable` (retryable), never a silent
stall or a false `task_not_found`.

## S3 Object Store

`agent/internal/object/s3.go` implements `ObjectStore` from Phase 2 with
the same immutability and integrity guarantees as the filesystem adapter:
size and SHA-256 are verified on write (read back and checked, not trusted
from the caller) and on every read. Uploads above
`multipart_threshold_bytes` use multipart upload; object keys are
idempotent per Phase 2's `Put` contract, so a retried `Put` for the same
key is safe. `Delete` is idempotent and tolerates a not-found object,
matching the filesystem adapter. The adapter does not assume a specific
provider's consistency model beyond what `ObjectStore.Get` immediately
after `ObjectStore.Put` requires: durability is confirmed before `Put`
returns, not inferred from provider defaults.

## Required Tests

- The full Phase 2 conformance suite (idempotent create, concurrent claim
  exclusivity, fencing, renewal, expiry/requeue, max-attempt dead letter,
  cancellation races, cursor ordering/replay, acknowledgement idempotency,
  reopen behavior) run against a real PostgreSQL instance
  (testcontainers or equivalent) and, separately, an S3-compatible test
  double (e.g. MinIO) for the object-store conformance suite.
- Two agents sharing one PostgreSQL/S3 backend pair, with clustering
  disabled, prove no double-claim across agents — the database provides
  exclusivity, not a forwarding protocol.
- Connection loss and pool exhaustion during a claim/complete surface
  `storage_unavailable` and are retryable; no task is silently lost or
  double-executed.
- Statement timeout under load surfaces a bounded, typed error rather than
  an indefinite hang.
- Misconfigured `dsn`, missing `credentials_env` variable at startup,
  unreachable `endpoint`, and a nonexistent `bucket` each fail agent
  startup with a diagnosable error, not a silent fallback to another
  backend.
- Checksum mismatch and partial multipart upload cleanup, matching the
  filesystem adapter's partial-write cleanup test.
- Combined scenario: a Phase 5 cluster topology backed by a shared
  PostgreSQL/S3 pair proves clustering's forwarding protocol and a shared
  backend are not mutually exclusive and do not double-count tasks.
- Existing `memory`/`sqlite`/`filesystem` conformance and config tests
  remain green and unaffected by the new backend registrations.

## Implementation Order

1. PostgreSQL schema/migrations; reuse the Phase 2 state-store conformance
   suite against a real instance.
2. S3 adapter (put/get/stat/delete, checksum verification, multipart);
   reuse the Phase 2 object-store conformance suite against an
   S3-compatible test double.
3. Config loader additions in both languages (`postgres`/`s3` fields,
   validation, `credentials_env` startup resolution in the agent command)
   keeping Python/Go parity per Phase 1.
4. Failure-injection tests: connection loss, pool exhaustion, statement
   timeout, credential/endpoint/bucket misconfiguration.
5. Combined Phase 5 cluster + shared-backend scenario.
6. Run the root `unit`, `integration`, and `smoke-wheel` targets plus the
   new Postgres/S3 conformance suites; confirm `memory`/`sqlite`/
   `filesystem` behavior is unchanged.

## Exit Gate

Phase 6 is complete when PostgreSQL and S3 adapters pass the identical
Phase 2 conformance suite against real or compatible-test-double services,
config validation rejects every misconfiguration case with the correct
stable error code, credentials are never logged or written inline to a
config file, multi-agent shared-backend tests show no double-claim or lost
completion, and Phase 8's release-scope gate can honestly advertise
PostgreSQL/S3 support backed by passing tests rather than configuration
placeholders alone.
