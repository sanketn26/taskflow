# Taskwire

Taskwire is a Kubernetes-native library for durable, distributed parallel processing in Python. It guarantees a fixed set of task semantics through a backend interface and asserts them at start-up. PostgreSQL is the production backend and the only stateful dependency: there is no broker, no result backend, and no server between your pods and the database.

## Project status

**Design stage. Nothing that executes a task exists yet.** The repository contains the design, the phased implementation plan, and the packaging and test scaffolding. Do not install it expecting a working library.

The design was reset in November 2026 from an earlier agent-based plan; [docs/decisions.md](docs/decisions.md) records what changed and why.

## The idea

```python
from taskwire import Client, task

@task(name="reports.render", version="1", pool="cpu")
def render(doc_id: str) -> dict: ...

with Client() as tw:
    futures = tw.map(render, doc_ids)        # thousands of tasks, one batched write
    results = tw.gather(futures, timeout=600)
```

```text
  application pods ──┐
  (Client SDK)       │
                     │   SQL
  worker pools ──────┼──────────►  PostgreSQL        S3-compatible store
  (Deployments,      │             task state        large values only
   scaled by KEDA)   │
                     │
  controller ────────┘
  (lease recovery, retention, Prometheus metrics)
```

- **Durable.** A submitted task is a committed row. Pod evictions, spot-node loss, and rolling deploys cost at most a retry.
- **Kubernetes-native.** Workers are ordinary pods in ordinary Deployments. They drain on `SIGTERM`, scale on queue depth (including to zero), and are installed with one Helm chart.
- **Observable.** Prometheus metrics, health probes, default alerts, and a dashboard are part of the first working version.
- **Stated guarantees.** Durable submit, exclusive claims, fenced outcomes, and bounded retries are defined once, tested against every backend, and checked by each process before it does any work.
- **At least once.** Each task's outcome is recorded once; side effects need application-level idempotency.

## Documentation

- [Vision](docs/vision.md) — why Taskwire exists, and what it is not for
- [Architecture](docs/architecture.md) — how it fits together
- [Decisions](docs/decisions.md) — the design reset and what was removed
- [Phases](docs/phases/README.md) — the authoritative implementation plan

## Development

Requires [uv](https://docs.astral.sh/uv/). Integration tests also need Docker, or any PostgreSQL 14+ server you point `TASKWIRE_TEST_DATABASE_URL` at.

```bash
make sync            # create .venv from uv.lock (Python 3.12 via .python-version)
make lint unit       # ruff + pytest, no database required
make db-up           # PostgreSQL in Docker for integration tests (make db-down to stop)
make integration     # tests that need a real PostgreSQL
make packaging       # build the wheel and test it in a clean venv
```

Add dependencies with `uv add <pkg>` (or `uv add --dev <pkg>`) so `uv.lock` stays in sync.

## License

Taskwire is licensed under the MIT License. See [LICENSE](LICENSE).
