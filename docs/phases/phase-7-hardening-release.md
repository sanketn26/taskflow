# Phase 7 — Hardening and Release

## Goal

Measure the system's limits, fix what the measurements expose, and publish a first release whose documented guarantees have each been tested.

## Benchmarks

A reproducible benchmark suite, run on documented hardware, reporting:

- Submit throughput for single submits and for `map`.
- Sustained tasks per second for no-op tasks at 10, 100, and 500 worker pods' worth of claimers.
- Claim latency and queue wait at each level.
- Database CPU, connection count, and table and index size over a multi-hour run.

The results are published with the release, including the point at which throughput stops scaling. No performance claim appears in the documentation without a benchmark behind it.

## Table churn decision

Task rows are updated several times and then deleted. Using the benchmark data, decide and document one of:

- keep a single table with tuned autovacuum and fill factor;
- move finished rows to a separate results table; or
- partition by time.

The decision is recorded in `docs/decisions.md` with the numbers that justify it.

## Connection scaling

- Test and document operation behind PgBouncer in transaction mode, including the startup assertion passing through it.
- Document connection counts per client, worker, and controller, and a sizing formula.
- Verify behaviour when PostgreSQL's connection limit is reached: clear errors, backoff, and recovery without restart.

## Failure testing

Seeded chaos runs on Kubernetes, each ending with the conservation ledger check:

- PostgreSQL primary failover during sustained load.
- Network partition between a node and the database for longer than a lease.
- Simultaneous loss of all controller replicas.
- Repeated random worker pod kills for a multi-hour soak.
- Disk-full and connection-exhaustion conditions on the database.

Every run records its seed, and a failing run can be replayed.

## Security review

- Any API token can read or cancel any task and can register to receive any task's input. Deliver scoped tokens (pools a token may submit to and serve), or document an installation as a single trust domain as a stated limitation.
- Only the controller holds database and S3 credentials; verify no library, chart template, or log exposes either.
- `cloudpickle` is off by default and its trust requirement is documented prominently.
- The container image runs non-root with a read-only root filesystem; the chart sets restrictive security contexts and supports network policies.
- Dependency audit and a software bill of materials for the image.

## Compatibility policy

Written down and tested:

- Supported PostgreSQL, Kubernetes, and Python versions.
- Which library protocol revisions each controller version accepts, and which controller versions may run against which schema versions.
- Metric names and labels as a stable contract.
- Upgrade and rollback procedures, each exercised in CI.

## Release

- Python wheel and source distribution to PyPI; the Go binary as release archives; container image and Helm chart to registries; all built in CI from a tagged commit.
- Changelog, upgrade notes, and a statement of known limitations.

## Exit gate

Benchmark results are published; every chaos scenario passes with conservation intact; each guarantee stated in the documentation maps to a named test; and a release is installed from public artifacts onto a clean cluster by following the published instructions only.
