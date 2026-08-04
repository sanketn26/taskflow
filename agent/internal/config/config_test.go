package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("could not determine test file path")
	}
	// agent/internal/config/config_test.go -> repo root is three levels up.
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "..")
}

func TestLoadExampleConfigMatchesNormalizedFixture(t *testing.T) {
	root := repoRoot(t)
	examplePath := filepath.Join(root, "taskwire.example.yaml")

	cfg, err := Load(examplePath)
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}

	normalizedPath := filepath.Join(root, "testdata", "config", "normalized.yaml")
	data, err := os.ReadFile(normalizedPath)
	if err != nil {
		t.Fatal(err)
	}
	var expected map[string]interface{}
	if err := yaml.Unmarshal(data, &expected); err != nil {
		t.Fatal(err)
	}

	baseDir := filepath.Dir(examplePath)
	resolve := func(rel string) string {
		return filepath.Clean(filepath.Join(baseDir, rel))
	}

	if cfg.Socket != resolve(expected["socket"].(string)) {
		t.Errorf("socket: got %q, want %q", cfg.Socket, resolve(expected["socket"].(string)))
	}
	storage := expected["storage"].(map[string]interface{})
	state := storage["state"].(map[string]interface{})
	if cfg.Storage.State.DSN != resolve(state["dsn"].(string)) {
		t.Errorf("storage.state.dsn: got %q, want %q", cfg.Storage.State.DSN, resolve(state["dsn"].(string)))
	}
	objects := storage["objects"].(map[string]interface{})
	stores := objects["stores"].(map[string]interface{})
	local := stores["local"].(map[string]interface{})
	if cfg.Storage.Objects.Stores["local"].Root != resolve(local["root"].(string)) {
		t.Errorf("storage.objects.stores.local.root: got %q, want %q", cfg.Storage.Objects.Stores["local"].Root, resolve(local["root"].(string)))
	}
	workers := expected["workers"].(map[string]interface{})
	pools := workers["pools"].([]interface{})
	pool := pools[0].(map[string]interface{})
	if cfg.Workers.Pools[0].WorkingDirectory != resolve(pool["working_directory"].(string)) {
		t.Errorf("workers.pools[0].working_directory: got %q", cfg.Workers.Pools[0].WorkingDirectory)
	}

	if cfg.SocketGroup != "taskwire" {
		t.Errorf("socket_group: got %q", cfg.SocketGroup)
	}
	if cfg.IPC.SubmitAckTimeoutMs != 5000 {
		t.Errorf("ipc.submit_ack_timeout_ms: got %d", cfg.IPC.SubmitAckTimeoutMs)
	}
	if cfg.Queue.LeaseTTLMs != 30000 {
		t.Errorf("queue.lease_ttl_ms: got %d", cfg.Queue.LeaseTTLMs)
	}
	if cfg.Workers.Pools[0].Count != 4 {
		t.Errorf("workers.pools[0].count: got %d", cfg.Workers.Pools[0].Count)
	}
	if cfg.Cluster.Enabled {
		t.Error("cluster.enabled: got true, want false")
	}
	if cfg.Integrations.Kafka.Topic != "taskwire-results" {
		t.Errorf("integrations.kafka.topic: got %q", cfg.Integrations.Kafka.Topic)
	}
	if len(cfg.Warnings) != 0 {
		t.Errorf("warnings: got %v, want none", cfg.Warnings)
	}
}

func writeTemp(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "taskwire.yaml")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

const minimalValidBody = `
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
  max_message_size_mb: 16
queue:
  max_attempts: 5
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
`

func TestLoadMinimalValidConfigWarnsOnMemoryBackends(t *testing.T) {
	cfg, err := Load(writeTemp(t, minimalValidBody))
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if len(cfg.Warnings) != 2 {
		t.Fatalf("expected 2 memory-backend warnings, got %v", cfg.Warnings)
	}
}

func TestLoadRejectsUnknownTopLevelField(t *testing.T) {
	_, err := Load(writeTemp(t, minimalValidBody+"\nbogus: 1\n"))
	assertConfigError(t, err, "invalid_config")
}

func TestLoadRejectsUnknownNestedField(t *testing.T) {
	body := replaceLine(minimalValidBody, "  reaper_interval_ms: 1000", "  reaper_interval_ms: 1000\n  bogus: 1")
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestLoadRejectsDuplicateKey(t *testing.T) {
	body := "socket: a\nsocket: b\n" + minimalValidBody
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestLoadRejectsMissingFile(t *testing.T) {
	if _, err := Load("/nonexistent/taskwire.yaml"); err == nil {
		t.Fatal("expected error for missing config file")
	}
}

func TestLoadRejectsAlias(t *testing.T) {
	body := "anchor: &a taskwire\nsocket_group: *a\n" + minimalValidBody
	_, err := Load(writeTemp(t, body))
	if err == nil {
		t.Fatal("expected error for YAML alias")
	}
}

func TestLoadRejectsMultipleDocuments(t *testing.T) {
	body := minimalValidBody + "\n---\nsocket: b\n"
	_, err := Load(writeTemp(t, body))
	if err == nil {
		t.Fatal("expected error for multiple YAML documents")
	}
}

func TestValidateLeaseTTLTooLow(t *testing.T) {
	body := replaceLine(minimalValidBody, "  lease_ttl_ms: 30000", "  lease_ttl_ms: 999")
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateUnsupportedStateBackend(t *testing.T) {
	body := replaceLine(minimalValidBody, `    type: "memory"`, `    type: "postgres"`)
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "unsupported_backend")
}

func TestValidateSqliteRequiresDSN(t *testing.T) {
	body := minimalValidBody
	body = replaceLine(body, `    type: "memory"`, `    type: "sqlite"`)
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateDefaultObjectStoreMustExist(t *testing.T) {
	body := replaceLine(minimalValidBody, `    default: "local"`, `    default: "missing"`)
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateClusterRequiresValidKey(t *testing.T) {
	body := minimalValidBody
	body = replaceLine(body, "  enabled: false", "  enabled: true")
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateOwnershipTimeoutMustExceedTransferTimeout(t *testing.T) {
	body := replaceLine(minimalValidBody, "  ownership_timeout_ms: 120000", "  ownership_timeout_ms: 100")
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateKafkaRequiresBrokersAndTopic(t *testing.T) {
	body := minimalValidBody
	body = replaceLine(body, "    enabled: false\n    brokers: []", "    enabled: true\n    brokers: []")
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateRestartBackoffMinMustNotExceedMax(t *testing.T) {
	body := replaceLine(minimalValidBody, "  restart_backoff_min_ms: 250", "  restart_backoff_min_ms: 99999")
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func TestValidateReservedWorkerEnvironmentKeyRejected(t *testing.T) {
	body := replaceLine(minimalValidBody, "  pools: []", `  pools:
    - {name: p, runtime: python, command: [python3], count: 0, working_directory: ".", environment: {TASKWIRE_SOCKET: x}, labels: {}, resources: {max_memory_mb: 1, max_cpu_percent: 1}}`)
	_, err := Load(writeTemp(t, body))
	assertConfigError(t, err, "invalid_config")
}

func assertConfigError(t *testing.T, err error, wantCategory string) {
	t.Helper()
	if err == nil {
		t.Fatal("expected an error")
	}
	ce, ok := err.(*ConfigError)
	if !ok {
		t.Fatalf("expected *ConfigError, got %T: %v", err, err)
	}
	if ce.Category != wantCategory {
		t.Fatalf("got category %q, want %q (%v)", ce.Category, wantCategory, err)
	}
}

func replaceLine(body, old, new string) string {
	if !strings.Contains(body, old) {
		panic("replaceLine: old text not found: " + old)
	}
	return strings.Replace(body, old, new, 1)
}
