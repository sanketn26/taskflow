# Phase 8 — Examples and Adoption

## Goal

Documentation and runnable examples that let a team evaluate Taskwire in an afternoon and adopt it without reading the phase documents.

Every example is executed in CI against released artifacts. Documentation describes only behaviour that has shipped.

## Quick start

From nothing to a completed `map` in under ten minutes on a local `kind` cluster: install PostgreSQL, install the chart, deploy one worker pool, and run a client script.

## Examples

| Example | Shows |
|---|---|
| Fan-out document processing | `map`, `as_completed`, retries, a scaling worker pool |
| API with background jobs | Submit from a web request, return a task ID, poll or reattach from another request |
| CPU and GPU pools | Two pools with different images, node selectors, and scaling rules |
| I/O-bound fan-out | An `async` pool making many concurrent API calls |
| Idempotent side effects | Designing a task that is safe to run more than once |
| Large inputs and results | Object storage and its limits |
| Delayed and prioritised work | `_run_after` and `_priority` |
| Cooperative cancellation | A long task that checks `cancel_requested` |

## Guides

- **Concepts:** tasks, pools, leases, attempts, and what at-least-once means for application code.
- **Operations:** sizing PostgreSQL, connection pooling, retention, upgrades, backup and restore, and reading the dashboard and alerts.
- **Autoscaling:** choosing between depth and age signals; scaling to zero; spot nodes.
- **Migrating from Celery:** a concept mapping, what has no equivalent, and a staged migration approach.
- **When not to use Taskwire:** the poor fits from the vision document, with the alternative for each.
- **Backend contract reference:** the guarantees, the interface, and how to run the conformance suite against a new backend.

## Required tests

- Each example runs to completion in CI on `kind` from released artifacts.
- Code blocks in the guides are extracted and executed, or explicitly marked as illustrative.
- A link checker passes over all documentation.

## Exit gate

A person who has not seen the repository completes the quick start by following it literally, and every example and guide code block is covered by CI.
