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

---

## Implementation Guide

> **Independent of Phase 5.** Shared Postgres/S3 can run with clustering off.
> Implement **exactly** the Phase 2 interfaces—no new store methods for callers.

### File map

```text
agent/internal/state/postgres.go
agent/internal/state/postgres_migrate.go
agent/internal/object/s3.go
agent/internal/config/config.go          # additive postgres/s3 fields
python/taskwire/config.py                # parity
agent/internal/state/postgres_test.go    # conformance + testcontainers
agent/internal/object/s3_test.go         # MinIO or similar
```

### Step 1 — Config (both languages)

```yaml
storage:
  state:
    type: postgres
    dsn: "postgres://taskwire@localhost:5432/taskwire?sslmode=disable"
    postgres_pool_max_conns: 10
    postgres_statement_timeout_ms: 5000
  objects:
    default: shared
    stores:
      shared:
        type: s3
        bucket: taskwire-objects
        region: us-east-1
        prefix: "prod/"
        endpoint: "http://127.0.0.1:9000"  # empty = AWS
        credentials_env: TASKWIRE_S3_CREDS
        multipart_threshold_bytes: 8388608
```

Validation:

```go
// type postgres ⇒ dsn non-empty; no sqlite_* fields present
// type s3 ⇒ bucket, region, credentials_env non-empty; no filesystem root
// wrong-backend fields ⇒ invalid_config
// unknown type ⇒ unsupported_backend
```

Credential resolution at **agent startup**, not in hermetic `Load()`:

```go
credsJSON := os.Getenv(store.CredentialsEnv)
if credsJSON == "" {
    return fmt.Errorf("missing env %s", store.CredentialsEnv)
}
```

### Step 2 — Postgres Claim (core exclusivity)

```sql
BEGIN;
SELECT id FROM tasks
 WHERE state = 'queued'
   AND /* capability predicates */
 ORDER BY created_at_ms
 FOR UPDATE SKIP LOCKED
 LIMIT 1;

UPDATE tasks SET
   state = 'leased',
   attempt = attempt + 1,
   lease_id = $1,
   lease_expiry_ms = $2,
   updated_at_ms = $3
 WHERE id = $4;
COMMIT;
```

Use the same fencing compare on `Complete`/`Fail` as SQLite (`lease_id` match).

### Step 3 — S3 ObjectStore

```go
func (s *S3Store) Put(ctx context.Context, key ObjectKey, body io.Reader, meta ObjectMetadata) (ObjectRef, error) {
	// if size >= multipart_threshold → multipart upload
	// compute sha256 while streaming
	// on success return ObjectRef{Store: s.name, Key: ..., Size, SHA256, Codec}
	// Put must not return until object is durable enough for immediate Get
}

func (s *S3Store) Get(ctx context.Context, ref ObjectRef) (io.ReadCloser, ObjectMetadata, error) {
	// download; verify size + sha256; mismatch → storage_consistency
}
```

### Step 4 — Conformance wiring

```go
func TestPostgresConformance(t *testing.T) {
	dsn := startPostgres(t) // testcontainers
	runStateConformance(t, func(t *testing.T) TaskStateStore {
		return openPostgres(t, dsn)
	})
}

func TestS3Conformance(t *testing.T) {
	endpoint := startMinio(t)
	runObjectConformance(t, func(t *testing.T) ObjectStore {
		return openS3(t, endpoint)
	})
}
```

### Step 5 — Multi-agent no double-claim

```python
# two AgentHarness instances, cluster.enabled=false, same postgres DSN + s3
# submit N tasks; assert each task_id claimed once across both agents' logs/status
```

### Done checklist

- [ ] Full Phase 2 state conformance on Postgres  
- [ ] Full Phase 2 object conformance on S3/MinIO  
- [ ] Two agents, no double-claim  
- [ ] `storage_unavailable` on pool/conn loss (retryable)  
- [ ] Credentials never logged  
- [ ] sqlite/filesystem tests unchanged when backends unused  

### Review request

```text
Please review Phase 6.
Commands: go test ./internal/state/ ./internal/object/ -count=1
          pytest -m integration -- multi-agent shared backend
Gaps: ...
```
