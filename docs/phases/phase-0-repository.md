# Phase 0 — Repository and Test Spine

## Goal

A repository in which later phases can add Go (controller) and per-language library code and have it linted, unit-tested, tested against a real PostgreSQL where relevant, and packaged, locally and in CI.

## Layout

```text
go.mod                  module github.com/sanketn26/taskflow (the controller)
cmd/taskwire/           the taskwire binary
internal/               controller packages (config, pgtest, version; later backend, api, ...)
proto/                  the Taskwire protocol (.proto files; Phase 2)
clients/
  python/               Python library: pyproject.toml, src/taskwire, tests, harness
  go/ rust/ java/       placeholders; each becomes a self-contained project
docs/                   vision, architecture, decisions, phases
Makefile                go-* and py-* targets; lint/unit/integration run both
.github/workflows/      one set of jobs per language
```

Each client directory is self-contained (own build files, tests, release version) so a library can be built, released and used without the rest of the repository. The Go client will be a separate Go module so applications do not inherit the controller's dependencies.

## Deliverables (done)

- Go module with `taskwire version`; `migrate`, `check` and `controller` exit non-zero with a "not implemented" message so later phases fill them in without changing the entry point.
- Controller configuration from environment variables (`internal/config`): typed, validated, errors name the variable and never echo a value, URL redaction fails closed. `TASKWIRE_BACKEND_URL` is required; its scheme selects the backend (`postgresql://`, `memory://`). No configuration file.
- PostgreSQL test fixture for Go (`internal/pgtest`): `TASKWIRE_TEST_DATABASE_URL` names a server tests may create databases on; each test gets a uniquely named database dropped at cleanup; unset means skip with instructions, and `TASKWIRE_REQUIRE_INTEGRATION=1` turns that into a failure. `make db-up` / `db-down` run a local container (`PG_VERSION` selects the major version).
- Python library skeleton: `python -m taskwire version`, settings (`TASKWIRE_CONTROLLER_URL`, `TASKWIRE_API_TOKEN`; token never in errors or `repr`), no dependencies, no database driver. `harness.wait_until` and `harness.ChaosTimeline` for later tests. A packaging test installs the built wheel into a clean environment with the source tree unreachable.
- CI jobs: Go lint, unit and build; Go integration on PostgreSQL 14 and 18; Python lint, unit (CPython 3.11–3.13) and packaging.

Supported PostgreSQL versions: 14 and later.

## Deferred to the phase that needs it

- `proto/` generation for Go and Python, with a CI drift check: added with the first `.proto` file (Phase 2).
- A fixture that builds the Go binary and starts `taskwire controller` on the in-memory backend for Python integration tests (Phase 2; `make py-integration` is a stub until then).
- A Go equivalent of the Python `wait_until` / `ChaosTimeline` helpers (Phase 1, with the first backend tests that need them).
- Release tooling for the binary and the clients (Phase 7).

## Required tests

- The PostgreSQL fixture creates and drops its database; two calls do not collide; unset variable skips, and the require flag fails.
- `taskwire version` prints the version; planned commands fail clearly.
- Configuration: missing required variable, malformed value, defaults, redaction never leaks a password.
- The Python wheel installs and reports its version from a clean environment.

## Status

**Implemented locally; never run in CI on GitHub.** `make lint unit integration packaging` passes locally against PostgreSQL 18; the PostgreSQL 14 and 18 legs pass locally.

## Exit gate

`make lint unit integration packaging` passes locally and in CI for both the Go binary and the Python library, with at least one Go integration test executing a query against PostgreSQL. The clause "one Python test talking to a controller started from the built binary" moves to Phase 2, when the controller exists.
