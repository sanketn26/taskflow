# Phase 6 — Queue Result Delivery

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
sdk/taskflow/worker/delivery.py           (new — Strategy pattern)
sdk/taskflow/worker/runner.py             (update: use DeliveryStrategy)
sdk/taskflow/ipc/result_server.py         (no change)
sdk/taskflow/ipc/kafka_consumer.py        (new)
sdk/taskflow/runtime.py                   (update: create correct consumer based on config)
sdk/tests/integration/test_queue_delivery.py
```

---

## Python

### `sdk/taskflow/worker/delivery.py`

Introduces the Strategy pattern for result delivery. `WorkerRunner` calls `DeliveryStrategy.deliver(...)` — it does not know or care whether delivery is direct or via Kafka.

#### `DeliveryStrategy` (Protocol — structural subtyping)

```python
class DeliveryStrategy(Protocol):
    def deliver(
        self,
        task_id: bytes,
        result_bytes: bytes,
        is_error: bool,
    ) -> None: ...
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
| `deliver` | `(task_id, result_bytes, is_error) -> None` | Parse `callback_addr`. For attempt in range(`_max_retries`): open TCP socket, connect, send RESULT frame. On success: return. On `OSError`: compute backoff delay (`backoff_ms * 2^attempt` if exponential), sleep, retry. After all retries fail: log warning and return. Caller (`WorkerRunner.run`) is responsible for raising `DeliveryFailedError` at the Future level. |

#### `KafkaDelivery`

Delivers result to a Kafka topic. The worker is a producer. Fire-and-forget from the worker's perspective — Kafka durability handles the rest.

| Field | Type | Description |
|-------|------|-------------|
| `_producer` | `KafkaProducer` | `kafka-python` producer, created once per worker process |
| `_topic` | `str` | From `config.result_delivery.queue.topic` |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(cfg: QueueDeliveryConfig)` | Create `KafkaProducer(bootstrap_servers=cfg.brokers, acks="all")`. Store topic. |
| `deliver` | `(task_id, result_bytes, is_error) -> None` | Build message value: `msgpack.dumps({"task_id": task_id, "result": result_bytes, "is_error": is_error})`. Call `_producer.send(topic, key=task_id, value=value)`. `_producer.flush()` to ensure delivery before returning. |
| `close` | `() -> None` | `_producer.close()` |

#### `DeliveryFactory`

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `create` | `staticmethod(cfg: ResultDeliveryConfig, callback_addr: str) -> DeliveryStrategy` | If `cfg.mode == "direct"`: return `DirectDelivery(callback_addr, cfg.direct)`. If `cfg.mode == "queue"`: return `KafkaDelivery(cfg.queue)`. Else: raise `ConfigError`. |

**Pattern:** Strategy (`DeliveryStrategy` Protocol). Factory Method (`DeliveryFactory.create`). Open/Closed — adding a new delivery mode (e.g. Redis Streams) requires a new class and one new `if` in the factory, nothing else.

---

### `sdk/taskflow/worker/runner.py` (updates)

`WorkerRunner` no longer calls `_deliver_result` directly. It uses `DeliveryStrategy`.

Changes to `WorkerRunner`:

| Change | Description |
|--------|-------------|
| Add `_delivery` field | `DeliveryStrategy`, created via `DeliveryFactory.create(config, envelope.callback_addr)` at the start of each task loop iteration (because `callback_addr` comes from the task envelope) |
| Update `run` loop | After `_execute`, call `self._delivery.deliver(task_id, result_bytes, is_error)` |
| Remove `_deliver_result` | Logic moved to `DirectDelivery.deliver` |

---

### `sdk/taskflow/ipc/kafka_consumer.py`

The Runtime-side consumer for queue mode. Runs in a background thread, resolves Futures as results arrive.

#### `KafkaResultConsumer`

Provides the same interface contract as `ResultServer` — both call `on_result(task_id, result_bytes, is_error)`. The Runtime does not know which it is using.

| Field | Type | Description |
|-------|------|-------------|
| `_on_result` | `Callable[[bytes, bytes, bool], None]` | Same callback signature as `ResultServer` |
| `_consumer` | `KafkaConsumer` | `kafka-python` consumer |
| `_thread` | `threading.Thread` | Daemon thread running `_consume` |
| `_stop_event` | `threading.Event` | Signals `_consume` to exit |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(cfg: QueueDeliveryConfig, on_result: Callable)` | Create `KafkaConsumer(cfg.topic, bootstrap_servers=cfg.brokers, group_id=f"taskflow-{socket.gethostname()}", auto_offset_reset="latest", enable_auto_commit=True)`. Store callback. Start daemon thread. |
| `stop` | `() -> None` | Set `_stop_event`. `_consumer.close()`. Thread exits on next poll. |
| `_consume` | `() -> None` | Loop until `_stop_event` set. `records = _consumer.poll(timeout_ms=500)`. For each record: `msgpack.loads(record.value)` → `{task_id, result, is_error}`. Call `_on_result(task_id, result, is_error)`. |

**Note on consumer group:** Each Runtime instance uses `f"taskflow-{socket.gethostname()}"` as group ID. Multiple Runtime instances on the same host share offset commits. If you need per-process isolation, use `f"taskflow-{os.getpid()}"`. Platform engineer can override via config.

---

### `sdk/taskflow/runtime.py` (updates)

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

### `sdk/tests/integration/test_queue_delivery.py`

Requires running `taskflow-agent` and Kafka (use `testcontainers` for Kafka in CI).

| Test | Asserts |
|------|---------|
| `test_direct_mode_still_works` | Ensure Phase 3/4 behaviour unchanged with `mode: direct` |
| `test_kafka_mode_basic` | `mode: queue`. Submit `@task` fn. `future.result()` resolves correctly. |
| `test_kafka_retention_on_restart` | Submit task. Kill Runtime before result arrives. Restart Runtime with same consumer group. `future` (re-registered from task_id log) resolves from Kafka backlog. |
| `test_direct_delivery_failed_error` | `mode: direct`. Mock callback address that always refuses connection. `max_retries: 2`. After retries: `future.result()` raises `DeliveryFailedError` with correct `task_id`. |
| `test_kafka_mode_concurrent` | 50 tasks submitted. All Futures resolve. No duplicates (at-least-once with idempotent resolution — duplicate delivery hits `_pending.pop(..., None)` and is discarded). |

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy | `DeliveryStrategy` Protocol | `WorkerRunner` is open for new delivery mechanisms; closed for modification |
| Factory Method | `DeliveryFactory.create` | Config-driven selection; only one place to update when adding modes |
| Protocol (structural subtyping) | `DeliveryStrategy`, `ResultReceiver` | Python 3.13 native; no inheritance tax; duck-typed but statically checkable with mypy |
| Open/Closed | `Runtime`, `WorkerRunner` | New delivery mode = new class + one factory line; no existing code changes |
