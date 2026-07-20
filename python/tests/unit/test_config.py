from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taskwire.config import ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_PATH = REPO_ROOT / "taskwire.example.yaml"
NORMALIZED_PATH = REPO_ROOT / "testdata" / "config" / "normalized.yaml"

MINIMAL_VALID_BODY = """
socket: "./agent.sock"
socket_group: "taskwire"
ipc:
  submit_ack_timeout_ms: 5000
  reconnect_backoff_ms: 250
  result_batch_size: 100
  task_query_batch_size: 100
  read_timeout_ms: 30000
  write_timeout_ms: 30000
  object_transfer_timeout_ms: 60000
  object_chunk_bytes: 262144
  max_active_transfers: 4
  max_transfer_bytes: 1073741824
  write_queue_size: 256
queue:
  max_attempts: 5
  max_frame_size_mb: 16
  lease_ttl_ms: 30000
  reaper_interval_ms: 1000
storage:
  state:
    type: "memory"
    dsn: ""
    sqlite_busy_timeout_ms: 5000
    sqlite_synchronous: "FULL"
  objects:
    default: "local"
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    sweep_interval_ms: 60000
    sweep_batch_size: 100
    stores:
      local:
        type: "memory"
        root: ""
tasks:
  allow_inline_functions: false
workers:
  shutdown_grace_ms: 30000
  restart_backoff_min_ms: 250
  restart_backoff_max_ms: 30000
  restart_limit: 5
  restart_window_seconds: 60
  pools: []
cluster:
  enabled: false
  allow_insecure: false
  node_name: ""
  bind_addr: "0.0.0.0:7946"
  advertise_addr: ""
  task_port: 7947
  seeds: []
  mdns: true
  encryption_key: ""
  auth_clock_skew_ms: 30000
  auth_timeout_ms: 5000
  replay_cache_size: 4096
  transfer_timeout_ms: 30000
  ownership_timeout_ms: 120000
  steal_batch_size: 10
routing:
  rules: []
integrations:
  kafka:
    enabled: false
    brokers: []
    topic: "taskwire-results"
    delivery_timeout_ms: 30000
    batch_size: 100
    max_in_flight: 10
    retry_backoff_ms: 1000
    shutdown_grace_ms: 10000
    preserve_owner_order: true
    max_event_bytes: 1048576
    published_retention_seconds: 604800
metrics:
  listen_addr: ""
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "taskwire.yaml"
    path.write_text(body)
    return path


def test_load_example_config_matches_normalized_fixture():
    cfg = load_config(EXAMPLE_PATH)
    expected = yaml.safe_load(NORMALIZED_PATH.read_text())
    base_dir = EXAMPLE_PATH.resolve().parent

    def resolve(rel: str) -> str:
        return str((base_dir / rel).resolve())

    assert cfg.socket == resolve(expected["socket"])
    assert cfg.storage.state.dsn == resolve(expected["storage"]["state"]["dsn"])
    assert cfg.storage.objects.stores["local"].root == resolve(
        expected["storage"]["objects"]["stores"]["local"]["root"]
    )
    assert cfg.workers.pools[0].working_directory == resolve(
        expected["workers"]["pools"][0]["working_directory"]
    )

    assert cfg.socket_group == expected["socket_group"]
    assert cfg.ipc.submit_ack_timeout_ms == expected["ipc"]["submit_ack_timeout_ms"]
    assert cfg.queue.lease_ttl_ms == expected["queue"]["lease_ttl_ms"]
    assert cfg.workers.pools[0].count == expected["workers"]["pools"][0]["count"]
    assert cfg.cluster.enabled == expected["cluster"]["enabled"]
    assert cfg.integrations.kafka.topic == expected["integrations"]["kafka"]["topic"]
    assert cfg.warnings == expected["warnings"]


def test_load_minimal_valid_config_warns_on_memory_backends(tmp_path):
    cfg = load_config(_write(tmp_path, MINIMAL_VALID_BODY))
    assert len(cfg.warnings) == 2


def test_load_rejects_unknown_top_level_field(tmp_path):
    body = MINIMAL_VALID_BODY + "\nbogus: 1\n"
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, body))
    assert exc.value.category == "invalid_config"


def test_load_rejects_unknown_nested_field(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        "  reaper_interval_ms: 1000", "  reaper_interval_ms: 1000\n  bogus: 1"
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_load_rejects_duplicate_key(tmp_path):
    body = "socket: a\nsocket: b\n" + MINIMAL_VALID_BODY
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_load_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/taskwire.yaml")


def test_load_rejects_alias(tmp_path):
    body = "anchor: &a taskwire\nsocket_group: *a\n" + MINIMAL_VALID_BODY
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_load_rejects_multiple_documents(tmp_path):
    body = MINIMAL_VALID_BODY + "\n---\nsocket: b\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_load_rejects_custom_tag(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        'socket: "./agent.sock"', "socket: !!python/object:os.system {}"
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_load_rejects_non_string_key(tmp_path):
    body = MINIMAL_VALID_BODY + "\n1: 2\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


@pytest.mark.parametrize(
    "old,new,category",
    [
        ("  lease_ttl_ms: 30000", "  lease_ttl_ms: 999", "invalid_config"),
        (
            '    type: "memory"\n    dsn: ""',
            '    type: "postgres"\n    dsn: ""',
            "unsupported_backend",
        ),
        ('    default: "local"', '    default: "missing"', "invalid_config"),
        (
            "  ownership_timeout_ms: 120000",
            "  ownership_timeout_ms: 100",
            "invalid_config",
        ),
        (
            "  restart_backoff_min_ms: 250",
            "  restart_backoff_min_ms: 99999",
            "invalid_config",
        ),
    ],
)
def test_conditional_validation_branches(tmp_path, old, new, category):
    body = MINIMAL_VALID_BODY.replace(old, new, 1)
    assert body != MINIMAL_VALID_BODY
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, body))
    assert exc.value.category == category


def test_validate_sqlite_requires_dsn(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        '    type: "memory"\n    dsn: ""', '    type: "sqlite"\n    dsn: ""', 1
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_validate_cluster_requires_valid_key(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        "  enabled: false\n  allow_insecure: false",
        "  enabled: true\n  allow_insecure: false",
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_validate_kafka_requires_brokers_and_topic(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        "    enabled: false\n    brokers: []", "    enabled: true\n    brokers: []"
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_validate_reserved_worker_environment_key_rejected(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        "  pools: []",
        '  pools:\n    - {name: p, runtime: python, command: [python3], count: 0, working_directory: ".", environment: {TASKWIRE_SOCKET: x}, labels: {}, resources: {max_memory_mb: 1, max_cpu_percent: 1}}',
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_rejects_retired_import_modules_field(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        "  allow_inline_functions: false",
        '  allow_inline_functions: false\n  import_modules: ["old.tasks"]',
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, body))


def test_relative_paths_resolve_against_config_file_directory(tmp_path):
    body = MINIMAL_VALID_BODY.replace(
        'socket: "./agent.sock"', 'socket: "sub/agent.sock"'
    )
    cfg = load_config(_write(tmp_path, body))
    assert cfg.socket == str((tmp_path / "sub" / "agent.sock").resolve())
