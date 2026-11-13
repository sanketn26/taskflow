# Taskwire

Taskwire is a Kubernetes-native system for durable, distributed parallel processing. A small Go controller owns task state in PostgreSQL and enforces a fixed set of guarantees; your pods embed a library that talks to it over one protocol. Python is the first library, and the same library both submits tasks and runs them. Task results never pass through the controller or the database: pods publish them to a result channel you choose (S3 or Redis).

## Project status

**Design stage. Nothing that executes a task exists yet.** The repository contains the design, the phased implementation plan, and Python packaging and test scaffolding from an earlier all-Python design, which needs rework for the controller design (see [Phase 0](docs/phases/phase-0-repository.md)). Do not install it expecting a working system.

The design was reset in November 2026 from an earlier agent-based plan; [docs/decisions.md](docs/decisions.md) records what changed and why.

## The idea

```python
from taskwire import Taskwire, task

@task(name="reports.render", version="1", pool="cpu")
def render(doc_id: str) -> dict: ...

tw = Taskwire()
futures = tw.map(render, doc_ids)            # thousands of tasks, batched
results = tw.gather(futures, timeout=600)

tw.serve(pools=["cpu"], mode="process")      # the same library also runs tasks
```

```text
  your pods + Taskwire library ───── Taskwire API ─────►  controller (Go) ──►  PostgreSQL
  (submit, run, or both;                                  api · maintenance      task state
   scaled by KEDA)
        │
        └── result bytes, directly ──►  result channel (S3 · Redis)
```

- **Durable.** A submitted task is a committed row. Pod evictions, spot-node loss, and rolling deploys cost at most a retry.
- **One protocol, any language.** The controller enforces the guarantees and treats libraries as untrusted. A library is supported if it passes the conformance check.
- **No database access in your pods.** Only the controller connects to PostgreSQL. With S3, your pods hold no storage credential either.
- **Your deployment, your choice.** Run tasks in dedicated worker pods or inside your application, in child processes or in-process. Taskwire recommends and explains the trade-off; it does not enforce.
- **Kubernetes-native.** Pods that run tasks are ordinary pods in ordinary Deployments. They drain on `SIGTERM`, scale on queue depth (including to zero), and are installed with one Helm chart.
- **Observable.** Prometheus metrics, health probes, default alerts, and a dashboard are part of the first working version.
- **Stated guarantees.** Durable submit, exclusive claims, fenced outcomes, and bounded retries are defined once, tested against every backend, and checked by the controller before it serves a request.
- **At least once.** Each task's outcome is recorded once; side effects need application-level idempotency.

## Documentation

- [Vision](docs/vision.md) — why Taskwire exists, and what it is not for
- [Architecture](docs/architecture.md) — how it fits together
- [Decisions](docs/decisions.md) — the design reset and what was removed
- [Phases](docs/phases/README.md) — the authoritative implementation plan

## Development

The repository is a Go module (the controller, `cmd/` and `internal/`) with client libraries under [`clients/`](clients/) (Python first; Go, Rust and Java planned) and the protocol under [`proto/`](proto/). Requires Go and [uv](https://docs.astral.sh/uv/). Integration tests also need Docker, or any PostgreSQL 14+ server you point `TASKWIRE_TEST_DATABASE_URL` at.

```bash
make sync            # Python venv from clients/python/uv.lock
make build           # bin/taskwire
make lint unit       # go vet/test, ruff/pytest; no database required
make db-up           # PostgreSQL in Docker for integration tests (make db-down to stop)
make integration     # Go tests that need a real PostgreSQL
make packaging       # build the Python wheel and test it in a clean venv
```

Go and Python targets can be run separately: `make go-unit`, `make py-unit`, and so on. Add Python dependencies from `clients/python` with `uv add <pkg>` so `uv.lock` stays in sync.

## License

Taskwire is licensed under the MIT License. See [LICENSE](LICENSE).
