"""Strict configuration loader for taskwire.example.yaml's contract.

Unknown fields, duplicate YAML keys, YAML aliases, custom tags, non-string
map keys, and multiple documents are all rejected. Structural decoding
happens first; conditional validation (backend support, cluster/Kafka
prerequisites, routing rules, ...) happens second. This loader performs no
environment substitution, filesystem creation, executable lookup, or
network access — those are the agent command's job, so tests here stay
hermetic.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_RESERVED_ENV_PREFIX = "TASKWIRE_"


class ConfigError(Exception):
    """Raised for structural or semantic config errors.

    ``category`` is either ``invalid_config`` or ``unsupported_backend``,
    matching the stable categories the Go loader also returns.
    """

    def __init__(
        self, path: str, message: str, category: str = "invalid_config"
    ) -> None:
        self.path = path
        self.message = message
        self.category = category
        super().__init__(f"{path}: {message} [{category}]")


# --------------------------------------------------------------------
# Strict YAML loading: no duplicate keys, no aliases, only string keys.
# --------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        # Intercept before Composer resolves the alias to its anchored
        # node, since by that point there is no separate node type left
        # to detect: PyYAML just returns the same node object again.
        if self.check_event(yaml.events.AliasEvent):
            raise ConfigError("<root>", "YAML aliases are not permitted")
        return super().compose_node(parent, index)


def _no_duplicate_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        if key_node.tag != "tag:yaml.org,2002:str":
            raise ConfigError("<root>", "map keys must be strings")
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise ConfigError("<root>", f"duplicate key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_mapping
)


def _load_yaml_strict(text: str) -> Any:
    loader = _StrictLoader(text)
    try:
        return loader.get_single_data()
    except yaml.composer.ComposerError as exc:
        if "expected a single document" in str(exc):
            raise ConfigError(
                "<root>", "multiple YAML documents are not permitted"
            ) from None
        raise ConfigError("<root>", str(exc)) from None
    except yaml.YAMLError as exc:
        raise ConfigError("<root>", str(exc)) from None
    finally:
        loader.dispose()


# --------------------------------------------------------------------
# Field validation helpers
# --------------------------------------------------------------------


def _sub(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _req(d: dict, key: str, path: str) -> Any:
    if key not in d:
        raise ConfigError(_sub(path, key), "missing required field")
    return d[key]


def _no_unknown(d: dict, allowed: frozenset[str], path: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise ConfigError(path, f"unknown fields {sorted(extra)!r}")


def _as_dict(v: Any, path: str) -> dict:
    if not isinstance(v, dict):
        raise ConfigError(path, "must be a mapping")
    return v


def _as_str(v: Any, path: str) -> str:
    if not isinstance(v, str):
        raise ConfigError(path, "must be a string")
    return v


def _as_bool(v: Any, path: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(path, "must be a bool")
    return v


def _as_int(v: Any, path: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(path, "must be an integer")
    return v


def _as_positive_int(v: Any, path: str) -> int:
    n = _as_int(v, path)
    if n <= 0:
        raise ConfigError(path, "must be positive")
    return n


def _as_str_list(v: Any, path: str) -> list[str]:
    if not isinstance(v, list):
        raise ConfigError(path, "must be a list")
    return [_as_str(x, f"{path}[{i}]") for i, x in enumerate(v)]


def _as_str_map(v: Any, path: str) -> dict[str, str]:
    d = _as_dict(v, path)
    return {_as_str(k, path): _as_str(val, _sub(path, str(k))) for k, val in d.items()}


# --------------------------------------------------------------------
# Config dataclasses
# --------------------------------------------------------------------


@dataclass(frozen=True)
class IPCConfig:
    submit_ack_timeout_ms: int
    reconnect_backoff_ms: int
    result_batch_size: int
    task_query_batch_size: int
    read_timeout_ms: int
    write_timeout_ms: int
    object_transfer_timeout_ms: int
    object_chunk_bytes: int
    max_active_transfers: int
    max_transfer_bytes: int
    write_queue_size: int
    max_message_size_mb: int


@dataclass(frozen=True)
class QueueConfig:
    max_attempts: int
    lease_ttl_ms: int
    reaper_interval_ms: int


@dataclass(frozen=True)
class StateStoreConfig:
    type: str
    dsn: str
    sqlite_busy_timeout_ms: int
    sqlite_synchronous: str


@dataclass(frozen=True)
class ObjectStoreConfig:
    type: str
    root: str


@dataclass(frozen=True)
class ObjectsConfig:
    default: str
    inline_threshold_bytes: int
    result_retention_seconds: int
    sweep_interval_ms: int
    sweep_batch_size: int
    stores: dict[str, ObjectStoreConfig]


@dataclass(frozen=True)
class StorageConfig:
    state: StateStoreConfig
    objects: ObjectsConfig


@dataclass(frozen=True)
class TasksConfig:
    allow_inline_functions: bool


@dataclass(frozen=True)
class WorkerResources:
    max_memory_mb: int
    max_cpu_percent: int


@dataclass(frozen=True)
class WorkerPoolConfig:
    name: str
    runtime: str
    command: list[str]
    count: int
    working_directory: str
    environment: dict[str, str]
    labels: dict[str, str]
    resources: WorkerResources


@dataclass(frozen=True)
class WorkersConfig:
    shutdown_grace_ms: int
    restart_backoff_min_ms: int
    restart_backoff_max_ms: int
    restart_limit: int
    restart_window_seconds: int
    pools: list[WorkerPoolConfig]


@dataclass(frozen=True)
class ClusterConfig:
    enabled: bool
    allow_insecure: bool
    node_name: str
    bind_addr: str
    advertise_addr: str
    task_port: int
    seeds: list[str]
    mdns: bool
    encryption_key: str
    auth_clock_skew_ms: int
    auth_timeout_ms: int
    replay_cache_size: int
    transfer_timeout_ms: int
    ownership_timeout_ms: int
    steal_batch_size: int


@dataclass(frozen=True)
class RoutingRule:
    task_labels: dict[str, str]
    require_node_labels: dict[str, str]
    preference: str


@dataclass(frozen=True)
class RoutingConfig:
    rules: list[RoutingRule]


@dataclass(frozen=True)
class KafkaConfig:
    enabled: bool
    brokers: list[str]
    topic: str
    delivery_timeout_ms: int
    batch_size: int
    max_in_flight: int
    retry_backoff_ms: int
    shutdown_grace_ms: int
    preserve_owner_order: bool
    max_event_bytes: int
    published_retention_seconds: int


@dataclass(frozen=True)
class IntegrationsConfig:
    kafka: KafkaConfig


@dataclass(frozen=True)
class MetricsConfig:
    listen_addr: str


@dataclass(frozen=True)
class Config:
    socket: str
    socket_group: str
    ipc: IPCConfig
    queue: QueueConfig
    storage: StorageConfig
    tasks: TasksConfig
    workers: WorkersConfig
    cluster: ClusterConfig
    routing: RoutingConfig
    integrations: IntegrationsConfig
    metrics: MetricsConfig
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------
# Structural decode
# --------------------------------------------------------------------


def _decode_ipc(d: dict, path: str) -> IPCConfig:
    keys = frozenset(
        {
            "submit_ack_timeout_ms",
            "reconnect_backoff_ms",
            "result_batch_size",
            "task_query_batch_size",
            "read_timeout_ms",
            "write_timeout_ms",
            "object_transfer_timeout_ms",
            "object_chunk_bytes",
            "max_active_transfers",
            "max_transfer_bytes",
            "write_queue_size",
            "max_message_size_mb",
        }
    )
    _no_unknown(d, keys, path)
    return IPCConfig(
        submit_ack_timeout_ms=_as_positive_int(
            _req(d, "submit_ack_timeout_ms", path), _sub(path, "submit_ack_timeout_ms")
        ),
        reconnect_backoff_ms=_as_positive_int(
            _req(d, "reconnect_backoff_ms", path), _sub(path, "reconnect_backoff_ms")
        ),
        result_batch_size=_as_positive_int(
            _req(d, "result_batch_size", path), _sub(path, "result_batch_size")
        ),
        task_query_batch_size=_as_positive_int(
            _req(d, "task_query_batch_size", path), _sub(path, "task_query_batch_size")
        ),
        read_timeout_ms=_as_positive_int(
            _req(d, "read_timeout_ms", path), _sub(path, "read_timeout_ms")
        ),
        write_timeout_ms=_as_positive_int(
            _req(d, "write_timeout_ms", path), _sub(path, "write_timeout_ms")
        ),
        object_transfer_timeout_ms=_as_positive_int(
            _req(d, "object_transfer_timeout_ms", path),
            _sub(path, "object_transfer_timeout_ms"),
        ),
        object_chunk_bytes=_as_positive_int(
            _req(d, "object_chunk_bytes", path), _sub(path, "object_chunk_bytes")
        ),
        max_active_transfers=_as_positive_int(
            _req(d, "max_active_transfers", path), _sub(path, "max_active_transfers")
        ),
        max_transfer_bytes=_as_positive_int(
            _req(d, "max_transfer_bytes", path), _sub(path, "max_transfer_bytes")
        ),
        write_queue_size=_as_positive_int(
            _req(d, "write_queue_size", path), _sub(path, "write_queue_size")
        ),
        max_message_size_mb=_as_positive_int(
            _req(d, "max_message_size_mb", path), _sub(path, "max_message_size_mb")
        ),
    )


def _decode_queue(d: dict, path: str) -> QueueConfig:
    keys = frozenset(
        {"max_attempts", "lease_ttl_ms", "reaper_interval_ms"}
    )
    _no_unknown(d, keys, path)
    return QueueConfig(
        max_attempts=_as_positive_int(
            _req(d, "max_attempts", path), _sub(path, "max_attempts")
        ),
        lease_ttl_ms=_as_positive_int(
            _req(d, "lease_ttl_ms", path), _sub(path, "lease_ttl_ms")
        ),
        reaper_interval_ms=_as_positive_int(
            _req(d, "reaper_interval_ms", path), _sub(path, "reaper_interval_ms")
        ),
    )


def _decode_state_store(d: dict, path: str) -> StateStoreConfig:
    keys = frozenset({"type", "dsn", "sqlite_busy_timeout_ms", "sqlite_synchronous"})
    _no_unknown(d, keys, path)
    return StateStoreConfig(
        type=_as_str(_req(d, "type", path), _sub(path, "type")),
        dsn=_as_str(_req(d, "dsn", path), _sub(path, "dsn")),
        sqlite_busy_timeout_ms=_as_positive_int(
            _req(d, "sqlite_busy_timeout_ms", path),
            _sub(path, "sqlite_busy_timeout_ms"),
        ),
        sqlite_synchronous=_as_str(
            _req(d, "sqlite_synchronous", path), _sub(path, "sqlite_synchronous")
        ),
    )


def _decode_object_store(d: dict, path: str) -> ObjectStoreConfig:
    keys = frozenset({"type", "root"})
    _no_unknown(d, keys, path)
    return ObjectStoreConfig(
        type=_as_str(_req(d, "type", path), _sub(path, "type")),
        root=_as_str(_req(d, "root", path), _sub(path, "root")),
    )


def _decode_objects(d: dict, path: str) -> ObjectsConfig:
    keys = frozenset(
        {
            "default",
            "inline_threshold_bytes",
            "result_retention_seconds",
            "sweep_interval_ms",
            "sweep_batch_size",
            "stores",
        }
    )
    _no_unknown(d, keys, path)
    stores_raw = _as_dict(_req(d, "stores", path), _sub(path, "stores"))
    stores = {
        name: _decode_object_store(
            _as_dict(v, _sub(_sub(path, "stores"), name)),
            _sub(_sub(path, "stores"), name),
        )
        for name, v in stores_raw.items()
    }
    return ObjectsConfig(
        default=_as_str(_req(d, "default", path), _sub(path, "default")),
        inline_threshold_bytes=_as_positive_int(
            _req(d, "inline_threshold_bytes", path),
            _sub(path, "inline_threshold_bytes"),
        ),
        result_retention_seconds=_as_positive_int(
            _req(d, "result_retention_seconds", path),
            _sub(path, "result_retention_seconds"),
        ),
        sweep_interval_ms=_as_positive_int(
            _req(d, "sweep_interval_ms", path), _sub(path, "sweep_interval_ms")
        ),
        sweep_batch_size=_as_positive_int(
            _req(d, "sweep_batch_size", path), _sub(path, "sweep_batch_size")
        ),
        stores=stores,
    )


def _decode_storage(d: dict, path: str) -> StorageConfig:
    keys = frozenset({"state", "objects"})
    _no_unknown(d, keys, path)
    return StorageConfig(
        state=_decode_state_store(
            _as_dict(_req(d, "state", path), _sub(path, "state")), _sub(path, "state")
        ),
        objects=_decode_objects(
            _as_dict(_req(d, "objects", path), _sub(path, "objects")),
            _sub(path, "objects"),
        ),
    )


def _decode_tasks(d: dict, path: str) -> TasksConfig:
    keys = frozenset({"allow_inline_functions"})
    _no_unknown(d, keys, path)
    return TasksConfig(
        allow_inline_functions=_as_bool(
            _req(d, "allow_inline_functions", path),
            _sub(path, "allow_inline_functions"),
        ),
    )


def _decode_worker_resources(d: dict, path: str) -> WorkerResources:
    keys = frozenset({"max_memory_mb", "max_cpu_percent"})
    _no_unknown(d, keys, path)
    return WorkerResources(
        max_memory_mb=_as_positive_int(
            _req(d, "max_memory_mb", path), _sub(path, "max_memory_mb")
        ),
        max_cpu_percent=_as_positive_int(
            _req(d, "max_cpu_percent", path), _sub(path, "max_cpu_percent")
        ),
    )


def _decode_worker_pool(d: dict, path: str) -> WorkerPoolConfig:
    keys = frozenset(
        {
            "name",
            "runtime",
            "command",
            "count",
            "working_directory",
            "environment",
            "labels",
            "resources",
        }
    )
    _no_unknown(d, keys, path)
    count = _as_int(_req(d, "count", path), _sub(path, "count"))
    if count < 0:
        raise ConfigError(_sub(path, "count"), "must not be negative")
    runtime = _as_str(_req(d, "runtime", path), _sub(path, "runtime"))
    if runtime not in {"python", "nodejs", "go"}:
        raise ConfigError(_sub(path, "runtime"), "must be python, nodejs, or go")
    command = _as_str_list(_req(d, "command", path), _sub(path, "command"))
    if not command or any(not part for part in command):
        raise ConfigError(_sub(path, "command"), "must be a non-empty argv array")
    return WorkerPoolConfig(
        name=_as_str(_req(d, "name", path), _sub(path, "name")),
        runtime=runtime,
        command=command,
        count=count,
        working_directory=_as_str(
            _req(d, "working_directory", path), _sub(path, "working_directory")
        ),
        environment=_as_str_map(
            _req(d, "environment", path), _sub(path, "environment")
        ),
        labels=_as_str_map(_req(d, "labels", path), _sub(path, "labels")),
        resources=_decode_worker_resources(
            _as_dict(_req(d, "resources", path), _sub(path, "resources")),
            _sub(path, "resources"),
        ),
    )


def _decode_workers(d: dict, path: str) -> WorkersConfig:
    keys = frozenset(
        {
            "shutdown_grace_ms",
            "restart_backoff_min_ms",
            "restart_backoff_max_ms",
            "restart_limit",
            "restart_window_seconds",
            "pools",
        }
    )
    _no_unknown(d, keys, path)
    raw_pools = _req(d, "pools", path)
    if not isinstance(raw_pools, list):
        raise ConfigError(_sub(path, "pools"), "must be a list")
    return WorkersConfig(
        shutdown_grace_ms=_as_positive_int(
            _req(d, "shutdown_grace_ms", path), _sub(path, "shutdown_grace_ms")
        ),
        restart_backoff_min_ms=_as_positive_int(
            _req(d, "restart_backoff_min_ms", path),
            _sub(path, "restart_backoff_min_ms"),
        ),
        restart_backoff_max_ms=_as_positive_int(
            _req(d, "restart_backoff_max_ms", path),
            _sub(path, "restart_backoff_max_ms"),
        ),
        restart_limit=_as_positive_int(
            _req(d, "restart_limit", path), _sub(path, "restart_limit")
        ),
        restart_window_seconds=_as_positive_int(
            _req(d, "restart_window_seconds", path),
            _sub(path, "restart_window_seconds"),
        ),
        pools=[
            _decode_worker_pool(_as_dict(v, f"{path}.pools[{i}]"), f"{path}.pools[{i}]")
            for i, v in enumerate(raw_pools)
        ],
    )


def _decode_cluster(d: dict, path: str) -> ClusterConfig:
    keys = frozenset(
        {
            "enabled",
            "allow_insecure",
            "node_name",
            "bind_addr",
            "advertise_addr",
            "task_port",
            "seeds",
            "mdns",
            "encryption_key",
            "auth_clock_skew_ms",
            "auth_timeout_ms",
            "replay_cache_size",
            "transfer_timeout_ms",
            "ownership_timeout_ms",
            "steal_batch_size",
        }
    )
    _no_unknown(d, keys, path)
    return ClusterConfig(
        enabled=_as_bool(_req(d, "enabled", path), _sub(path, "enabled")),
        allow_insecure=_as_bool(
            _req(d, "allow_insecure", path), _sub(path, "allow_insecure")
        ),
        node_name=_as_str(_req(d, "node_name", path), _sub(path, "node_name")),
        bind_addr=_as_str(_req(d, "bind_addr", path), _sub(path, "bind_addr")),
        advertise_addr=_as_str(
            _req(d, "advertise_addr", path), _sub(path, "advertise_addr")
        ),
        task_port=_as_positive_int(_req(d, "task_port", path), _sub(path, "task_port")),
        seeds=_as_str_list(_req(d, "seeds", path), _sub(path, "seeds")),
        mdns=_as_bool(_req(d, "mdns", path), _sub(path, "mdns")),
        encryption_key=_as_str(
            _req(d, "encryption_key", path), _sub(path, "encryption_key")
        ),
        auth_clock_skew_ms=_as_positive_int(
            _req(d, "auth_clock_skew_ms", path), _sub(path, "auth_clock_skew_ms")
        ),
        auth_timeout_ms=_as_positive_int(
            _req(d, "auth_timeout_ms", path), _sub(path, "auth_timeout_ms")
        ),
        replay_cache_size=_as_positive_int(
            _req(d, "replay_cache_size", path), _sub(path, "replay_cache_size")
        ),
        transfer_timeout_ms=_as_positive_int(
            _req(d, "transfer_timeout_ms", path), _sub(path, "transfer_timeout_ms")
        ),
        ownership_timeout_ms=_as_positive_int(
            _req(d, "ownership_timeout_ms", path), _sub(path, "ownership_timeout_ms")
        ),
        steal_batch_size=_as_positive_int(
            _req(d, "steal_batch_size", path), _sub(path, "steal_batch_size")
        ),
    )


_ROUTING_PREFERENCES = frozenset({"local", "remote", "any"})


def _decode_routing_rule(d: dict, path: str) -> RoutingRule:
    keys = frozenset({"task_labels", "require_node_labels", "preference"})
    _no_unknown(d, keys, path)
    task_labels = _as_str_map(_req(d, "task_labels", path), _sub(path, "task_labels"))
    require_node_labels = _as_str_map(
        _req(d, "require_node_labels", path), _sub(path, "require_node_labels")
    )
    preference = _as_str(_req(d, "preference", path), _sub(path, "preference"))
    if preference not in _ROUTING_PREFERENCES:
        raise ConfigError(
            _sub(path, "preference"), f"must be one of {sorted(_ROUTING_PREFERENCES)!r}"
        )
    if not task_labels and not require_node_labels:
        raise ConfigError(path, "routing rule must not be empty")
    return RoutingRule(
        task_labels=task_labels,
        require_node_labels=require_node_labels,
        preference=preference,
    )


def _decode_routing(d: dict, path: str) -> RoutingConfig:
    keys = frozenset({"rules"})
    _no_unknown(d, keys, path)
    raw_rules = _req(d, "rules", path)
    if not isinstance(raw_rules, list):
        raise ConfigError(_sub(path, "rules"), "must be a list")
    rules = [
        _decode_routing_rule(_as_dict(r, f"{path}.rules[{i}]"), f"{path}.rules[{i}]")
        for i, r in enumerate(raw_rules)
    ]
    seen: set[tuple[tuple[str, str], ...]] = set()
    for i, rule in enumerate(rules):
        key = tuple(sorted(rule.task_labels.items()))
        if key in seen:
            raise ConfigError(
                f"{path}.rules[{i}]", "duplicate routing rule task_labels"
            )
        seen.add(key)
    return RoutingConfig(rules=rules)


def _decode_kafka(d: dict, path: str) -> KafkaConfig:
    keys = frozenset(
        {
            "enabled",
            "brokers",
            "topic",
            "delivery_timeout_ms",
            "batch_size",
            "max_in_flight",
            "retry_backoff_ms",
            "shutdown_grace_ms",
            "preserve_owner_order",
            "max_event_bytes",
            "published_retention_seconds",
        }
    )
    _no_unknown(d, keys, path)
    return KafkaConfig(
        enabled=_as_bool(_req(d, "enabled", path), _sub(path, "enabled")),
        brokers=_as_str_list(_req(d, "brokers", path), _sub(path, "brokers")),
        topic=_as_str(_req(d, "topic", path), _sub(path, "topic")),
        delivery_timeout_ms=_as_positive_int(
            _req(d, "delivery_timeout_ms", path), _sub(path, "delivery_timeout_ms")
        ),
        batch_size=_as_positive_int(
            _req(d, "batch_size", path), _sub(path, "batch_size")
        ),
        max_in_flight=_as_positive_int(
            _req(d, "max_in_flight", path), _sub(path, "max_in_flight")
        ),
        retry_backoff_ms=_as_positive_int(
            _req(d, "retry_backoff_ms", path), _sub(path, "retry_backoff_ms")
        ),
        shutdown_grace_ms=_as_positive_int(
            _req(d, "shutdown_grace_ms", path), _sub(path, "shutdown_grace_ms")
        ),
        preserve_owner_order=_as_bool(
            _req(d, "preserve_owner_order", path), _sub(path, "preserve_owner_order")
        ),
        max_event_bytes=_as_positive_int(
            _req(d, "max_event_bytes", path), _sub(path, "max_event_bytes")
        ),
        published_retention_seconds=_as_positive_int(
            _req(d, "published_retention_seconds", path),
            _sub(path, "published_retention_seconds"),
        ),
    )


def _decode_integrations(d: dict, path: str) -> IntegrationsConfig:
    keys = frozenset({"kafka"})
    _no_unknown(d, keys, path)
    return IntegrationsConfig(
        kafka=_decode_kafka(
            _as_dict(_req(d, "kafka", path), _sub(path, "kafka")), _sub(path, "kafka")
        )
    )


def _decode_metrics(d: dict, path: str) -> MetricsConfig:
    keys = frozenset({"listen_addr"})
    _no_unknown(d, keys, path)
    return MetricsConfig(
        listen_addr=_as_str(_req(d, "listen_addr", path), _sub(path, "listen_addr"))
    )


_ROOT_KEYS = frozenset(
    {
        "socket",
        "socket_group",
        "ipc",
        "queue",
        "storage",
        "tasks",
        "workers",
        "cluster",
        "routing",
        "integrations",
        "metrics",
    }
)


def _decode_root(d: dict) -> tuple[Config, list[str]]:
    _no_unknown(d, _ROOT_KEYS, "")
    warnings: list[str] = []

    storage = _decode_storage(_as_dict(_req(d, "storage", ""), "storage"), "storage")
    if storage.state.type == "memory":
        warnings.append("storage.state.type=memory is a development-only configuration")
    for name, store in storage.objects.stores.items():
        if store.type == "memory":
            warnings.append(
                f"storage.objects.stores.{name}.type=memory is a development-only configuration"
            )

    cfg = Config(
        socket=_as_str(_req(d, "socket", ""), "socket"),
        socket_group=_as_str(_req(d, "socket_group", ""), "socket_group"),
        ipc=_decode_ipc(_as_dict(_req(d, "ipc", ""), "ipc"), "ipc"),
        queue=_decode_queue(_as_dict(_req(d, "queue", ""), "queue"), "queue"),
        storage=storage,
        tasks=_decode_tasks(_as_dict(_req(d, "tasks", ""), "tasks"), "tasks"),
        workers=_decode_workers(_as_dict(_req(d, "workers", ""), "workers"), "workers"),
        cluster=_decode_cluster(_as_dict(_req(d, "cluster", ""), "cluster"), "cluster"),
        routing=_decode_routing(_as_dict(_req(d, "routing", ""), "routing"), "routing"),
        integrations=_decode_integrations(
            _as_dict(_req(d, "integrations", ""), "integrations"), "integrations"
        ),
        metrics=_decode_metrics(_as_dict(_req(d, "metrics", ""), "metrics"), "metrics"),
        warnings=warnings,
    )
    return cfg, warnings


# --------------------------------------------------------------------
# Conditional validation
# --------------------------------------------------------------------

_STATE_BACKENDS = frozenset({"memory", "sqlite"})
_OBJECT_BACKENDS = frozenset({"memory", "filesystem"})


def _validate(cfg: Config) -> None:
    if cfg.queue.lease_ttl_ms < 1000:
        raise ConfigError("queue.lease_ttl_ms", "must be >= 1000")

    if cfg.ipc.object_chunk_bytes > cfg.ipc.max_message_size_mb * 1024 * 1024:
        raise ConfigError(
            "ipc.object_chunk_bytes",
            "must not exceed ipc.max_message_size_mb converted to bytes",
        )

    if cfg.storage.state.type not in _STATE_BACKENDS:
        raise ConfigError(
            "storage.state.type",
            f"unsupported backend {cfg.storage.state.type!r}",
            "unsupported_backend",
        )
    if cfg.storage.state.type == "sqlite" and not cfg.storage.state.dsn:
        raise ConfigError("storage.state.dsn", "must be non-empty for sqlite backend")
    if cfg.storage.state.sqlite_synchronous not in ("FULL", "NORMAL"):
        raise ConfigError(
            "storage.state.sqlite_synchronous", "must be exactly FULL or NORMAL"
        )

    if cfg.storage.objects.default not in cfg.storage.objects.stores:
        raise ConfigError("storage.objects.default", "must name a configured store")
    for name, store in cfg.storage.objects.stores.items():
        store_path = f"storage.objects.stores.{name}"
        if store.type not in _OBJECT_BACKENDS:
            raise ConfigError(
                f"{store_path}.type",
                f"unsupported backend {store.type!r}",
                "unsupported_backend",
            )
        if store.type == "filesystem" and not store.root:
            raise ConfigError(
                f"{store_path}.root", "must be non-empty for filesystem backend"
            )

    if cfg.cluster.enabled and not cfg.cluster.allow_insecure:
        try:
            key = base64.b64decode(cfg.cluster.encryption_key, validate=True)
        except Exception:
            raise ConfigError(
                "cluster.encryption_key", "must be valid base64"
            ) from None
        if len(key) != 32:
            raise ConfigError(
                "cluster.encryption_key", "must decode to exactly 32 bytes"
            )

    if cfg.cluster.ownership_timeout_ms <= cfg.cluster.transfer_timeout_ms:
        raise ConfigError(
            "cluster.ownership_timeout_ms", "must exceed cluster.transfer_timeout_ms"
        )

    if cfg.integrations.kafka.enabled:
        if not cfg.integrations.kafka.brokers:
            raise ConfigError(
                "integrations.kafka.brokers", "must be non-empty when kafka is enabled"
            )
        if not cfg.integrations.kafka.topic:
            raise ConfigError(
                "integrations.kafka.topic", "must be non-empty when kafka is enabled"
            )

    seen_pools: set[str] = set()
    for i, pool in enumerate(cfg.workers.pools):
        if not pool.name or pool.name in seen_pools:
            raise ConfigError(
                f"workers.pools[{i}].name", "must be non-empty and unique"
            )
        seen_pools.add(pool.name)
        for key in pool.environment:
            if key.startswith(_RESERVED_ENV_PREFIX):
                raise ConfigError(
                    f"workers.pools[{i}].environment",
                    f"{key!r} is a reserved taskwire-owned variable",
                )

    if cfg.workers.restart_backoff_min_ms > cfg.workers.restart_backoff_max_ms:
        raise ConfigError(
            "workers.restart_backoff_min_ms",
            "must not exceed workers.restart_backoff_max_ms",
        )


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------


def load_config(path: str | Path) -> Config:
    config_path = Path(path)
    text = config_path.read_text()
    raw = _load_yaml_strict(text)
    if not isinstance(raw, dict):
        raise ConfigError("<root>", "config document must be a mapping")

    cfg, _ = _decode_root(raw)
    _validate(cfg)

    base_dir = config_path.resolve().parent

    def resolve(p: str) -> str:
        if not p:
            return p
        return str((base_dir / p).resolve()) if not Path(p).is_absolute() else p

    resolved_stores = {
        name: ObjectStoreConfig(
            type=s.type, root=resolve(s.root) if s.type == "filesystem" else s.root
        )
        for name, s in cfg.storage.objects.stores.items()
    }
    return Config(
        socket=resolve(cfg.socket),
        socket_group=cfg.socket_group,
        ipc=cfg.ipc,
        queue=cfg.queue,
        storage=StorageConfig(
            state=StateStoreConfig(
                type=cfg.storage.state.type,
                dsn=resolve(cfg.storage.state.dsn)
                if cfg.storage.state.type == "sqlite"
                else cfg.storage.state.dsn,
                sqlite_busy_timeout_ms=cfg.storage.state.sqlite_busy_timeout_ms,
                sqlite_synchronous=cfg.storage.state.sqlite_synchronous,
            ),
            objects=ObjectsConfig(
                default=cfg.storage.objects.default,
                inline_threshold_bytes=cfg.storage.objects.inline_threshold_bytes,
                result_retention_seconds=cfg.storage.objects.result_retention_seconds,
                sweep_interval_ms=cfg.storage.objects.sweep_interval_ms,
                sweep_batch_size=cfg.storage.objects.sweep_batch_size,
                stores=resolved_stores,
            ),
        ),
        tasks=cfg.tasks,
        workers=WorkersConfig(
            shutdown_grace_ms=cfg.workers.shutdown_grace_ms,
            restart_backoff_min_ms=cfg.workers.restart_backoff_min_ms,
            restart_backoff_max_ms=cfg.workers.restart_backoff_max_ms,
            restart_limit=cfg.workers.restart_limit,
            restart_window_seconds=cfg.workers.restart_window_seconds,
            pools=[
                WorkerPoolConfig(
                    name=p.name,
                    runtime=p.runtime,
                    command=p.command,
                    count=p.count,
                    working_directory=resolve(p.working_directory),
                    environment=p.environment,
                    labels=p.labels,
                    resources=p.resources,
                )
                for p in cfg.workers.pools
            ],
        ),
        cluster=cfg.cluster,
        routing=cfg.routing,
        integrations=cfg.integrations,
        metrics=cfg.metrics,
        warnings=cfg.warnings,
    )
