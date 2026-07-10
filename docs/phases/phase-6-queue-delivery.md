# Phase 6 — Queue Result Delivery

> **Revision required before implementation:** agent-relayed, cursor-replayable results are the default and replace direct TCP callbacks. Kafka is an optional notification/retention integration after terminal result state has been recorded; workers do not bypass the agent to publish completion. This phase must be rewritten around result records and `ObjectRef`, following [Storage and Reference Architecture](../storage.md).

## Goal

Config-driven result delivery. Add Kafka as a delivery mode alongside direct TCP. The developer's code does not change. The platform engineer sets `result_delivery.mode: "queue"` in config and it just works.

## Testable Outcome

- `result_delivery.mode: "direct"` continues working exactly as Phase 3-4
- `result_delivery.mode: "queue"` — worker publishes result to Kafka topic; Runtime consumes it and resolves the Future
- Direct mode: max_retries exhausted → `future.result()` raises `DeliveryFailedError`
- Queue mode: Runtime restarts after task completes → Future still resolves (Kafka retains the message)
- Developer code is identical for both modes — only config changes

---

## Files

```
python/taskwire/worker/delivery.py           (new — Strategy pattern)
python/taskwire/worker/runner.py             (update: use DeliveryStrategy)
python/taskwire/ipc/result_server.py         (no change)
python/taskwire/ipc/kafka_consumer.py        (new)
python/taskwire/runtime.py                   (update: create correct consumer based on config)
python/tests/integration/test_queue_delivery.py
```

---

## Python

### `python/taskwire/worker/delivery.py`

Introduces the Strategy pattern for result delivery. `WorkerRunner` calls `DeliveryStrategy.deliver(...)` — it does not know or care whether delivery is direct or via Kafka.

#### `DeliveryStrategy` (Protocol — structural subtyping)

```python
class DeliveryStrategy(Protocol):
    def deliver(
        self,
        task_id: bytes,
        result_bytes: bytes,
        is_error: bool,
    ) -> bool: ...
```

Any class with a matching `deliver` method satisfies this protocol. No inheritance required.

#### `DirectDelivery`

Delivers result directly to the worker-provided callback address. Retries with exponential backoff.

| Field | Type | Description |
|-------|------|-------------|
| `_callback_addr` | `str` | `"host:port"` from the task envelope |
| `_max_retries` | `int` | From `config.result_delivery.direct.max_retries` |
| `_retry_backoff_ms` | `int` | From `config.result_delivery.direct.retry_backoff_ms` |
| `_strategy` | `str` | `"exponential"` or `"fixed"` |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(callback_addr: str, cfg: DirectDeliveryConfig)` | Store fields |
| `deliver` | `(task_id, result_bytes, is_error) -> bool` | Parse `callback_addr`. For attempt in range(`_max_retries`): open TCP socket, connect, send RESULT frame. On success: return `True`. On `OSError`: compute backoff delay (`backoff_ms * 2^attempt` if exponential), sleep, retry. After all retries fail: log warning and return `False`. Caller (`WorkerRunner.run`) reports failure via COMPLETE `status: "delivery_failed"`; the sidecar relays that to the Runtime as `DeliveryFailedError`. |

#### `KafkaDelivery`

Delivers result to a Kafka topic. The worker is a producer. It waits for producer flush before COMPLETE so the sidecar never releases a task whose result has not reached Kafka.

| Field | Type | Description |
|-------|------|-------------|
| `_producer` | `confluent_kafka.Producer` | Producer created once per worker process |
| `_topic` | `str` | From `config.result_delivery.queue.topic` |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(cfg: QueueDeliveryConfig)` | Create `confluent_kafka.Producer({"bootstrap.servers": ",".join(cfg.brokers), "acks": "all"})`. Store topic. |
| `deliver` | `(task_id, result_bytes, is_error) -> bool` | Build message value: `msgpack.dumps({"task_id": task_id, "result": result_bytes, "is_error": is_error})`. Produce with `acks=all`, wait for that record's delivery callback up to a configured deadline, and return `True` only when the broker acknowledges it. `flush()` alone is not proof of success; callback errors, timeout, or undelivered records return `False` so the worker reports `delivery_failed`. |
| `close` | `() -> None` | `_producer.close()` |

#### `DeliveryFactory`

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `create` | `staticmethod(cfg: ResultDeliveryConfig, callback_addr: str) -> DeliveryStrategy` | If `cfg.mode == "direct"`: return `DirectDelivery(callback_addr, cfg.direct)`. If `cfg.mode == "queue"`: return `KafkaDelivery(cfg.queue)`. Else: raise `ConfigError`. |

**Pattern:** Strategy (`DeliveryStrategy` Protocol). Factory Method (`DeliveryFactory.create`). Open/Closed — adding a new delivery mode (e.g. Redis Streams) requires a new class and one new `if` in the factory, nothing else.

---

### `python/taskwire/worker/runner.py` (updates)

`WorkerRunner` no longer calls `_deliver_result` directly. It uses `DeliveryStrategy`.

Changes to `WorkerRunner`:

| Change | Description |
|--------|-------------|
| Add `_delivery` field | `DeliveryStrategy`, created via `DeliveryFactory.create(config, envelope.callback_addr)` at the start of each task loop iteration (because `callback_addr` comes from the task envelope) |
| Update `run` loop | After `_execute`, call `delivered = self._delivery.deliver(task_id, result_bytes, is_error)`, keep heartbeat alive until it returns, then send COMPLETE with `status: "ok"` or `"delivery_failed"` exactly as Phase 3 does |
| Remove `_deliver_result` | Logic moved to `DirectDelivery.deliver` |

---

### `python/taskwire/ipc/kafka_consumer.py`

The Runtime-side consumer for queue mode. Runs in a background thread, resolves Futures as results arrive.

#### `KafkaResultConsumer`

Provides the same interface contract as `ResultServer` — both call `on_result(task_id, result_bytes, is_error)`. The Runtime does not know which it is using.

| Field | Type | Description |
|-------|------|-------------|
| `_on_result` | `Callable[[bytes, bytes, bool], None]` | Same callback signature as `ResultServer` |
| `_consumer` | `confluent_kafka.Consumer` | Runtime-side consumer |
| `_thread` | `threading.Thread` | Daemon thread running `_consume` |
| `_stop_event` | `threading.Event` | Signals `_consume` to exit |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(cfg: QueueDeliveryConfig, on_result: Callable)` | Create `confluent_kafka.Consumer({"bootstrap.servers": ",".join(cfg.brokers), "group.id": f"taskwire-{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}", "auto.offset.reset": "latest", "enable.auto.commit": True})`, subscribe to `cfg.topic`. Store callback. Start daemon thread. |
| `stop` | `() -> None` | Set `_stop_event`. `_consumer.close()`. Thread exits on next poll. |
| `_consume` | `() -> None` | Loop until `_stop_event` set. `msg = _consumer.poll(timeout=0.5)`. Ignore `None`; raise/log `msg.error()`; otherwise `msgpack.loads(msg.value())` → `{task_id, result, is_error}`. Call `_on_result(task_id, result, is_error)`. Tolerate unknown `task_id` (it belongs to another Runtime — `_pending.pop(..., None)` discards it). |

**Consumer group — this must be unique per Runtime, not per host.** Kafka splits a topic's partitions among members of the *same* group. If two Runtime processes on one host shared `taskwire-{hostname}`, each would receive only a subset of partitions — half their results would be delivered to the *other* process and silently dropped, manifesting as futures that randomly never resolve. Every Runtime is its own group (hostname + pid + random suffix for restart safety); each consumes the full topic and discards results that aren't in its `_pending`. This is wasteful (every Runtime reads every result) but correct; partitioning results per-runtime via a reply-topic-per-client scheme is a documented future optimisation, not Phase 6 scope.

**Client library:** use `confluent-kafka` (librdkafka bindings — maintained, fast, C-backed so it doesn't fight the GIL) as the `[kafka]` extra, not `kafka-python`, which has been effectively unmaintained for years. The Protocol-based design means the import is isolated to two files if this ever changes.

---

### `python/taskwire/runtime.py` (updates)

`Runtime.__init__` creates either `ResultServer` or `KafkaResultConsumer` based on config. Both provide `stop()` and both call `_on_result`. The rest of `Runtime` is unchanged.

Add a `ResultReceiver` Protocol internally:

```python
class ResultReceiver(Protocol):
    def stop(self) -> None: ...
```

Both `ResultServer` and `KafkaResultConsumer` satisfy this. `Runtime._result_receiver` holds whichever was created.

Change to `__init__`:

```python
if config.result_delivery.mode == "direct":
    self._result_receiver = ResultServer(
        advertise_addr=config.advertise_addr,
        on_result=self._on_result,
    )
elif config.result_delivery.mode == "queue":
    self._result_receiver = KafkaResultConsumer(
        cfg=config.result_delivery.queue,
        on_result=self._on_result,
    )
```

Change to `shutdown`:
```python
self._result_receiver.stop()
```

`_build_payload` changes for queue mode — `callback_addr` is not needed:

```python
def _build_payload(self, task, args, kwargs):
    return msgpack.dumps({
        "task_bytes": cloudpickle.dumps(...),
        "callback_addr": self._result_receiver.address if hasattr(self._result_receiver, "address") else "",
        "delivery_mode": self._config.result_delivery.mode,
        "label": task.label or "",
        "idempotent": task.idempotent,
    })
```

The Go sidecar stamps `delivery_mode` onto the TASK frame. The worker uses it to select the correct `DeliveryStrategy`.

---

## Tests

### `python/tests/integration/test_queue_delivery.py`

Requires running `taskwire-agent` and Kafka (use `testcontainers` for Kafka in CI).

| Test | Asserts |
|------|---------|
| `test_direct_mode_still_works` | Ensure Phase 3/4 behaviour unchanged with `mode: direct` |
| `test_kafka_mode_basic` | `mode: queue`. Submit `@task` fn. `future.result()` resolves correctly. |
| `test_kafka_retention_on_restart` | Submit task, record `task_id`. Kill Runtime before result arrives. New Runtime created with `Runtime.reattach(task_ids=[...])` (registers bare futures for known IDs) and `auto_offset_reset="earliest"` — future resolves from the Kafka backlog. Note: persisting the task_id list across restarts is the *application's* job; `reattach` is the hook taskwire provides. |
| `test_direct_delivery_failed_error` | `mode: direct`. Mock callback address that always refuses connection. `max_retries: 2`. After retries: `future.result()` raises `DeliveryFailedError` with correct `task_id`. |
| `test_kafka_mode_concurrent` | 50 tasks submitted. All Futures resolve. No duplicates (at-least-once with idempotent resolution — duplicate delivery hits `_pending.pop(..., None)` and is discarded). |
| `test_two_runtimes_one_host` | Two Runtime processes on the same machine, queue mode. Each submits 10 tasks. All 20 futures resolve in the *correct* process (regression test for the consumer-group partitioning bug). |

---

## Implementation Guide

Build order:

1. **`DeliveryStrategy` refactor with direct mode only** — pure mechanical extraction from `WorkerRunner._deliver_result`; the Phase 3/4 integration suite is the safety net and must stay green before Kafka appears.
2. **`KafkaDelivery`** — test against `testcontainers` Kafka with a raw consumer asserting the message shape.
3. **`KafkaResultConsumer` + Runtime wiring** — then the e2e tests.
4. **`Runtime.reattach`** — last, smallest, and clearly documented as the restart-recovery hook.

Gotchas:

- **`flush()` per result is the worker's latency floor in queue mode** (~ms each). Acceptable for v1; note in docs that queue mode trades result latency for durability. Do not batch silently — a worker crash after `send` but before `flush` would lose results.
- **Worker Kafka producer lifetime**: create once per worker process, not per task (`DeliveryFactory` must cache it; the spec's "created at the start of each task loop iteration" applies to `DirectDelivery`, which is per-envelope, but `KafkaDelivery` is hoisted).
- **testcontainers in CI**: gate Kafka tests behind a marker (`pytest -m kafka`) so the default suite stays infra-free, matching the Phase 1–5 discipline.

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy | `DeliveryStrategy` Protocol | `WorkerRunner` is open for new delivery mechanisms; closed for modification |
| Factory Method | `DeliveryFactory.create` | Config-driven selection; only one place to update when adding modes |
| Protocol (structural subtyping) | `DeliveryStrategy`, `ResultReceiver` | Python 3.13 native; no inheritance tax; duck-typed but statically checkable with mypy |
| Open/Closed | `Runtime`, `WorkerRunner` | New delivery mode = new class + one factory line; no existing code changes |
