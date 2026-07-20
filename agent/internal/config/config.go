// Package config loads and validates taskwire.example.yaml's contract:
// structural decoding first, conditional validation second, no
// environment substitution, filesystem creation, executable lookup, or
// network access (those are the agent command's job so loader tests stay
// hermetic).
package config

import (
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const reservedEnvPrefix = "TASKWIRE_"

// ConfigError carries a dotted field path and a stable category
// ("invalid_config" or "unsupported_backend"), matching the Python loader.
type ConfigError struct {
	Path     string
	Message  string
	Category string
}

func (e *ConfigError) Error() string {
	return fmt.Sprintf("%s: %s [%s]", e.Path, e.Message, e.Category)
}

func newErr(path, message string) *ConfigError {
	return &ConfigError{Path: path, Message: message, Category: "invalid_config"}
}

func newBackendErr(path, message string) *ConfigError {
	return &ConfigError{Path: path, Message: message, Category: "unsupported_backend"}
}

// --------------------------------------------------------------------
// Config structs
// --------------------------------------------------------------------

type IPCConfig struct {
	SubmitAckTimeoutMs      int64
	ReconnectBackoffMs      int64
	ResultBatchSize         int64
	TaskQueryBatchSize      int64
	ReadTimeoutMs           int64
	WriteTimeoutMs          int64
	ObjectTransferTimeoutMs int64
	ObjectChunkBytes        int64
	MaxActiveTransfers      int64
	MaxTransferBytes        int64
	WriteQueueSize          int64
}

type QueueConfig struct {
	MaxAttempts      int64
	MaxFrameSizeMB   int64
	LeaseTTLMs       int64
	ReaperIntervalMs int64
}

type StateStoreConfig struct {
	Type                string
	DSN                 string
	SQLiteBusyTimeoutMs int64
	SQLiteSynchronous   string
}

type ObjectStoreConfig struct {
	Type string
	Root string
}

type ObjectsConfig struct {
	Default                string
	InlineThresholdBytes   int64
	ResultRetentionSeconds int64
	SweepIntervalMs        int64
	SweepBatchSize         int64
	Stores                 map[string]ObjectStoreConfig
}

type StorageConfig struct {
	State   StateStoreConfig
	Objects ObjectsConfig
}

type TasksConfig struct {
	AllowInlineFunctions bool
}

type WorkerResources struct {
	MaxMemoryMB   int64
	MaxCPUPercent int64
}

type WorkerPoolConfig struct {
	Name             string
	Runtime          string
	Command          []string
	Count            int64
	WorkingDirectory string
	Environment      map[string]string
	Labels           map[string]string
	Resources        WorkerResources
}

type WorkersConfig struct {
	ShutdownGraceMs      int64
	RestartBackoffMinMs  int64
	RestartBackoffMaxMs  int64
	RestartLimit         int64
	RestartWindowSeconds int64
	Pools                []WorkerPoolConfig
}

type ClusterConfig struct {
	Enabled            bool
	AllowInsecure      bool
	NodeName           string
	BindAddr           string
	AdvertiseAddr      string
	TaskPort           int64
	Seeds              []string
	MDNS               bool
	EncryptionKey      string
	AuthClockSkewMs    int64
	AuthTimeoutMs      int64
	ReplayCacheSize    int64
	TransferTimeoutMs  int64
	OwnershipTimeoutMs int64
	StealBatchSize     int64
}

type RoutingRule struct {
	TaskLabels        map[string]string
	RequireNodeLabels map[string]string
	Preference        string
}

type RoutingConfig struct {
	Rules []RoutingRule
}

type KafkaConfig struct {
	Enabled                   bool
	Brokers                   []string
	Topic                     string
	DeliveryTimeoutMs         int64
	BatchSize                 int64
	MaxInFlight               int64
	RetryBackoffMs            int64
	ShutdownGraceMs           int64
	PreserveOwnerOrder        bool
	MaxEventBytes             int64
	PublishedRetentionSeconds int64
}

type IntegrationsConfig struct {
	Kafka KafkaConfig
}

type MetricsConfig struct {
	ListenAddr string
}

// Config is the fully decoded and validated configuration contract.
type Config struct {
	Socket       string
	SocketGroup  string
	IPC          IPCConfig
	Queue        QueueConfig
	Storage      StorageConfig
	Tasks        TasksConfig
	Workers      WorkersConfig
	Cluster      ClusterConfig
	Routing      RoutingConfig
	Integrations IntegrationsConfig
	Metrics      MetricsConfig
	Warnings     []string
}

// --------------------------------------------------------------------
// Field validation helpers
// --------------------------------------------------------------------

func sub(path, key string) string {
	if path == "" {
		return key
	}
	return path + "." + key
}

func reqField(d map[string]interface{}, key, path string) (interface{}, error) {
	v, ok := d[key]
	if !ok {
		return nil, newErr(sub(path, key), "missing required field")
	}
	return v, nil
}

func noUnknown(d map[string]interface{}, allowed map[string]bool, path string) error {
	extra := make([]string, 0)
	for k := range d {
		if !allowed[k] {
			extra = append(extra, k)
		}
	}
	if len(extra) > 0 {
		sort.Strings(extra)
		return newErr(path, fmt.Sprintf("unknown fields %v", extra))
	}
	return nil
}

func asMap(v interface{}, path string) (map[string]interface{}, error) {
	m, ok := v.(map[string]interface{})
	if !ok {
		return nil, newErr(path, "must be a mapping")
	}
	return m, nil
}

func asStr(v interface{}, path string) (string, error) {
	s, ok := v.(string)
	if !ok {
		return "", newErr(path, "must be a string")
	}
	return s, nil
}

func asBool(v interface{}, path string) (bool, error) {
	b, ok := v.(bool)
	if !ok {
		return false, newErr(path, "must be a bool")
	}
	return b, nil
}

func asInt(v interface{}, path string) (int64, error) {
	n, ok := v.(int64)
	if !ok {
		return 0, newErr(path, "must be an integer")
	}
	return n, nil
}

func asPositiveInt(v interface{}, path string) (int64, error) {
	n, err := asInt(v, path)
	if err != nil {
		return 0, err
	}
	if n <= 0 {
		return 0, newErr(path, "must be positive")
	}
	return n, nil
}

func asStrList(v interface{}, path string) ([]string, error) {
	a, ok := v.([]interface{})
	if !ok {
		return nil, newErr(path, "must be a list")
	}
	out := make([]string, len(a))
	for i, x := range a {
		s, err := asStr(x, fmt.Sprintf("%s[%d]", path, i))
		if err != nil {
			return nil, err
		}
		out[i] = s
	}
	return out, nil
}

func asArray(v interface{}, path string) ([]interface{}, error) {
	a, ok := v.([]interface{})
	if !ok {
		return nil, newErr(path, "must be a list")
	}
	return a, nil
}

func asStrMap(v interface{}, path string) (map[string]string, error) {
	d, err := asMap(v, path)
	if err != nil {
		return nil, err
	}
	out := make(map[string]string, len(d))
	for k, val := range d {
		s, err := asStr(val, sub(path, k))
		if err != nil {
			return nil, err
		}
		out[k] = s
	}
	return out, nil
}

// --------------------------------------------------------------------
// Structural decode
// --------------------------------------------------------------------

func decodeIPC(d map[string]interface{}, path string) (IPCConfig, error) {
	allowed := map[string]bool{
		"submit_ack_timeout_ms": true, "reconnect_backoff_ms": true, "result_batch_size": true,
		"task_query_batch_size": true, "read_timeout_ms": true, "write_timeout_ms": true,
		"object_transfer_timeout_ms": true, "object_chunk_bytes": true, "max_active_transfers": true,
		"max_transfer_bytes": true, "write_queue_size": true,
	}
	if err := noUnknown(d, allowed, path); err != nil {
		return IPCConfig{}, err
	}
	get := func(key string) (int64, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return 0, err
		}
		return asPositiveInt(v, sub(path, key))
	}
	var cfg IPCConfig
	var err error
	if cfg.SubmitAckTimeoutMs, err = get("submit_ack_timeout_ms"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.ReconnectBackoffMs, err = get("reconnect_backoff_ms"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.ResultBatchSize, err = get("result_batch_size"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.TaskQueryBatchSize, err = get("task_query_batch_size"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.ReadTimeoutMs, err = get("read_timeout_ms"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.WriteTimeoutMs, err = get("write_timeout_ms"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.ObjectTransferTimeoutMs, err = get("object_transfer_timeout_ms"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.ObjectChunkBytes, err = get("object_chunk_bytes"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.MaxActiveTransfers, err = get("max_active_transfers"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.MaxTransferBytes, err = get("max_transfer_bytes"); err != nil {
		return IPCConfig{}, err
	}
	if cfg.WriteQueueSize, err = get("write_queue_size"); err != nil {
		return IPCConfig{}, err
	}
	return cfg, nil
}

func decodeQueue(d map[string]interface{}, path string) (QueueConfig, error) {
	allowed := map[string]bool{"max_attempts": true, "max_frame_size_mb": true, "lease_ttl_ms": true, "reaper_interval_ms": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return QueueConfig{}, err
	}
	get := func(key string) (int64, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return 0, err
		}
		return asPositiveInt(v, sub(path, key))
	}
	var cfg QueueConfig
	var err error
	if cfg.MaxAttempts, err = get("max_attempts"); err != nil {
		return QueueConfig{}, err
	}
	if cfg.MaxFrameSizeMB, err = get("max_frame_size_mb"); err != nil {
		return QueueConfig{}, err
	}
	if cfg.LeaseTTLMs, err = get("lease_ttl_ms"); err != nil {
		return QueueConfig{}, err
	}
	if cfg.ReaperIntervalMs, err = get("reaper_interval_ms"); err != nil {
		return QueueConfig{}, err
	}
	return cfg, nil
}

func decodeStateStore(d map[string]interface{}, path string) (StateStoreConfig, error) {
	allowed := map[string]bool{"type": true, "dsn": true, "sqlite_busy_timeout_ms": true, "sqlite_synchronous": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return StateStoreConfig{}, err
	}
	typeV, err := reqField(d, "type", path)
	if err != nil {
		return StateStoreConfig{}, err
	}
	typ, err := asStr(typeV, sub(path, "type"))
	if err != nil {
		return StateStoreConfig{}, err
	}
	dsnV, err := reqField(d, "dsn", path)
	if err != nil {
		return StateStoreConfig{}, err
	}
	dsn, err := asStr(dsnV, sub(path, "dsn"))
	if err != nil {
		return StateStoreConfig{}, err
	}
	busyV, err := reqField(d, "sqlite_busy_timeout_ms", path)
	if err != nil {
		return StateStoreConfig{}, err
	}
	busy, err := asPositiveInt(busyV, sub(path, "sqlite_busy_timeout_ms"))
	if err != nil {
		return StateStoreConfig{}, err
	}
	syncV, err := reqField(d, "sqlite_synchronous", path)
	if err != nil {
		return StateStoreConfig{}, err
	}
	sync, err := asStr(syncV, sub(path, "sqlite_synchronous"))
	if err != nil {
		return StateStoreConfig{}, err
	}
	return StateStoreConfig{Type: typ, DSN: dsn, SQLiteBusyTimeoutMs: busy, SQLiteSynchronous: sync}, nil
}

func decodeObjectStore(d map[string]interface{}, path string) (ObjectStoreConfig, error) {
	allowed := map[string]bool{"type": true, "root": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return ObjectStoreConfig{}, err
	}
	typeV, err := reqField(d, "type", path)
	if err != nil {
		return ObjectStoreConfig{}, err
	}
	typ, err := asStr(typeV, sub(path, "type"))
	if err != nil {
		return ObjectStoreConfig{}, err
	}
	rootV, err := reqField(d, "root", path)
	if err != nil {
		return ObjectStoreConfig{}, err
	}
	root, err := asStr(rootV, sub(path, "root"))
	if err != nil {
		return ObjectStoreConfig{}, err
	}
	return ObjectStoreConfig{Type: typ, Root: root}, nil
}

func decodeObjects(d map[string]interface{}, path string) (ObjectsConfig, error) {
	allowed := map[string]bool{
		"default": true, "inline_threshold_bytes": true, "result_retention_seconds": true,
		"sweep_interval_ms": true, "sweep_batch_size": true, "stores": true,
	}
	if err := noUnknown(d, allowed, path); err != nil {
		return ObjectsConfig{}, err
	}
	defaultV, err := reqField(d, "default", path)
	if err != nil {
		return ObjectsConfig{}, err
	}
	def, err := asStr(defaultV, sub(path, "default"))
	if err != nil {
		return ObjectsConfig{}, err
	}
	get := func(key string) (int64, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return 0, err
		}
		return asPositiveInt(v, sub(path, key))
	}
	inlineThreshold, err := get("inline_threshold_bytes")
	if err != nil {
		return ObjectsConfig{}, err
	}
	retention, err := get("result_retention_seconds")
	if err != nil {
		return ObjectsConfig{}, err
	}
	sweepInterval, err := get("sweep_interval_ms")
	if err != nil {
		return ObjectsConfig{}, err
	}
	sweepBatch, err := get("sweep_batch_size")
	if err != nil {
		return ObjectsConfig{}, err
	}
	storesV, err := reqField(d, "stores", path)
	if err != nil {
		return ObjectsConfig{}, err
	}
	storesMap, err := asMap(storesV, sub(path, "stores"))
	if err != nil {
		return ObjectsConfig{}, err
	}
	stores := make(map[string]ObjectStoreConfig, len(storesMap))
	for name, v := range storesMap {
		storePath := sub(sub(path, "stores"), name)
		sm, err := asMap(v, storePath)
		if err != nil {
			return ObjectsConfig{}, err
		}
		store, err := decodeObjectStore(sm, storePath)
		if err != nil {
			return ObjectsConfig{}, err
		}
		stores[name] = store
	}
	return ObjectsConfig{
		Default: def, InlineThresholdBytes: inlineThreshold, ResultRetentionSeconds: retention,
		SweepIntervalMs: sweepInterval, SweepBatchSize: sweepBatch, Stores: stores,
	}, nil
}

func decodeStorage(d map[string]interface{}, path string) (StorageConfig, error) {
	allowed := map[string]bool{"state": true, "objects": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return StorageConfig{}, err
	}
	stateV, err := reqField(d, "state", path)
	if err != nil {
		return StorageConfig{}, err
	}
	stateMap, err := asMap(stateV, sub(path, "state"))
	if err != nil {
		return StorageConfig{}, err
	}
	state, err := decodeStateStore(stateMap, sub(path, "state"))
	if err != nil {
		return StorageConfig{}, err
	}
	objectsV, err := reqField(d, "objects", path)
	if err != nil {
		return StorageConfig{}, err
	}
	objectsMap, err := asMap(objectsV, sub(path, "objects"))
	if err != nil {
		return StorageConfig{}, err
	}
	objects, err := decodeObjects(objectsMap, sub(path, "objects"))
	if err != nil {
		return StorageConfig{}, err
	}
	return StorageConfig{State: state, Objects: objects}, nil
}

func decodeTasks(d map[string]interface{}, path string) (TasksConfig, error) {
	allowed := map[string]bool{"allow_inline_functions": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return TasksConfig{}, err
	}
	allowV, err := reqField(d, "allow_inline_functions", path)
	if err != nil {
		return TasksConfig{}, err
	}
	allow, err := asBool(allowV, sub(path, "allow_inline_functions"))
	if err != nil {
		return TasksConfig{}, err
	}
	return TasksConfig{AllowInlineFunctions: allow}, nil
}

func decodeWorkerResources(d map[string]interface{}, path string) (WorkerResources, error) {
	allowed := map[string]bool{"max_memory_mb": true, "max_cpu_percent": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return WorkerResources{}, err
	}
	memV, err := reqField(d, "max_memory_mb", path)
	if err != nil {
		return WorkerResources{}, err
	}
	mem, err := asPositiveInt(memV, sub(path, "max_memory_mb"))
	if err != nil {
		return WorkerResources{}, err
	}
	cpuV, err := reqField(d, "max_cpu_percent", path)
	if err != nil {
		return WorkerResources{}, err
	}
	cpu, err := asPositiveInt(cpuV, sub(path, "max_cpu_percent"))
	if err != nil {
		return WorkerResources{}, err
	}
	return WorkerResources{MaxMemoryMB: mem, MaxCPUPercent: cpu}, nil
}

func decodeWorkerPool(d map[string]interface{}, path string) (WorkerPoolConfig, error) {
	allowed := map[string]bool{"name": true, "runtime": true, "command": true, "count": true, "working_directory": true, "environment": true, "labels": true, "resources": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return WorkerPoolConfig{}, err
	}
	str := func(key string) (string, error) {
		v, e := reqField(d, key, path)
		if e != nil {
			return "", e
		}
		return asStr(v, sub(path, key))
	}
	name, err := str("name")
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	runtimeName, err := str("runtime")
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	if runtimeName != "python" && runtimeName != "nodejs" && runtimeName != "go" {
		return WorkerPoolConfig{}, newErr(sub(path, "runtime"), "must be python, nodejs, or go")
	}
	commandV, err := reqField(d, "command", path)
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	command, err := asStrList(commandV, sub(path, "command"))
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	if len(command) == 0 {
		return WorkerPoolConfig{}, newErr(sub(path, "command"), "must be a non-empty argv array")
	}
	countV, err := reqField(d, "count", path)
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	count, err := asInt(countV, sub(path, "count"))
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	if count < 0 {
		return WorkerPoolConfig{}, newErr(sub(path, "count"), "must not be negative")
	}
	wd, err := str("working_directory")
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	envV, err := reqField(d, "environment", path)
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	env, err := asStrMap(envV, sub(path, "environment"))
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	labelsV, err := reqField(d, "labels", path)
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	labels, err := asStrMap(labelsV, sub(path, "labels"))
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	resourcesV, err := reqField(d, "resources", path)
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	resourcesMap, err := asMap(resourcesV, sub(path, "resources"))
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	resources, err := decodeWorkerResources(resourcesMap, sub(path, "resources"))
	if err != nil {
		return WorkerPoolConfig{}, err
	}
	return WorkerPoolConfig{Name: name, Runtime: runtimeName, Command: command, Count: count, WorkingDirectory: wd, Environment: env, Labels: labels, Resources: resources}, nil
}

func decodeWorkers(d map[string]interface{}, path string) (WorkersConfig, error) {
	allowed := map[string]bool{"shutdown_grace_ms": true, "restart_backoff_min_ms": true, "restart_backoff_max_ms": true, "restart_limit": true, "restart_window_seconds": true, "pools": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return WorkersConfig{}, err
	}
	get := func(key string) (int64, error) {
		v, e := reqField(d, key, path)
		if e != nil {
			return 0, e
		}
		return asPositiveInt(v, sub(path, key))
	}
	shutdownGrace, err := get("shutdown_grace_ms")
	if err != nil {
		return WorkersConfig{}, err
	}
	backoffMin, err := get("restart_backoff_min_ms")
	if err != nil {
		return WorkersConfig{}, err
	}
	backoffMax, err := get("restart_backoff_max_ms")
	if err != nil {
		return WorkersConfig{}, err
	}
	restartLimit, err := get("restart_limit")
	if err != nil {
		return WorkersConfig{}, err
	}
	restartWindow, err := get("restart_window_seconds")
	if err != nil {
		return WorkersConfig{}, err
	}
	poolsV, err := reqField(d, "pools", path)
	if err != nil {
		return WorkersConfig{}, err
	}
	rawPools, err := asArray(poolsV, sub(path, "pools"))
	if err != nil {
		return WorkersConfig{}, err
	}
	pools := make([]WorkerPoolConfig, len(rawPools))
	for i, v := range rawPools {
		p := fmt.Sprintf("%s.pools[%d]", path, i)
		m, e := asMap(v, p)
		if e != nil {
			return WorkersConfig{}, e
		}
		pools[i], e = decodeWorkerPool(m, p)
		if e != nil {
			return WorkersConfig{}, e
		}
	}
	return WorkersConfig{ShutdownGraceMs: shutdownGrace, RestartBackoffMinMs: backoffMin, RestartBackoffMaxMs: backoffMax, RestartLimit: restartLimit, RestartWindowSeconds: restartWindow, Pools: pools}, nil
}

func decodeCluster(d map[string]interface{}, path string) (ClusterConfig, error) {
	allowed := map[string]bool{
		"enabled": true, "allow_insecure": true, "node_name": true, "bind_addr": true, "advertise_addr": true,
		"task_port": true, "seeds": true, "mdns": true, "encryption_key": true, "auth_clock_skew_ms": true,
		"auth_timeout_ms": true, "replay_cache_size": true, "transfer_timeout_ms": true,
		"ownership_timeout_ms": true, "steal_batch_size": true,
	}
	if err := noUnknown(d, allowed, path); err != nil {
		return ClusterConfig{}, err
	}
	str := func(key string) (string, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return "", err
		}
		return asStr(v, sub(path, key))
	}
	boolean := func(key string) (bool, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return false, err
		}
		return asBool(v, sub(path, key))
	}
	posInt := func(key string) (int64, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return 0, err
		}
		return asPositiveInt(v, sub(path, key))
	}

	enabled, err := boolean("enabled")
	if err != nil {
		return ClusterConfig{}, err
	}
	allowInsecure, err := boolean("allow_insecure")
	if err != nil {
		return ClusterConfig{}, err
	}
	nodeName, err := str("node_name")
	if err != nil {
		return ClusterConfig{}, err
	}
	bindAddr, err := str("bind_addr")
	if err != nil {
		return ClusterConfig{}, err
	}
	advertiseAddr, err := str("advertise_addr")
	if err != nil {
		return ClusterConfig{}, err
	}
	taskPort, err := posInt("task_port")
	if err != nil {
		return ClusterConfig{}, err
	}
	seedsV, err := reqField(d, "seeds", path)
	if err != nil {
		return ClusterConfig{}, err
	}
	seeds, err := asStrList(seedsV, sub(path, "seeds"))
	if err != nil {
		return ClusterConfig{}, err
	}
	mdns, err := boolean("mdns")
	if err != nil {
		return ClusterConfig{}, err
	}
	encryptionKey, err := str("encryption_key")
	if err != nil {
		return ClusterConfig{}, err
	}
	authClockSkew, err := posInt("auth_clock_skew_ms")
	if err != nil {
		return ClusterConfig{}, err
	}
	authTimeout, err := posInt("auth_timeout_ms")
	if err != nil {
		return ClusterConfig{}, err
	}
	replayCache, err := posInt("replay_cache_size")
	if err != nil {
		return ClusterConfig{}, err
	}
	transferTimeout, err := posInt("transfer_timeout_ms")
	if err != nil {
		return ClusterConfig{}, err
	}
	ownershipTimeout, err := posInt("ownership_timeout_ms")
	if err != nil {
		return ClusterConfig{}, err
	}
	stealBatch, err := posInt("steal_batch_size")
	if err != nil {
		return ClusterConfig{}, err
	}
	return ClusterConfig{
		Enabled: enabled, AllowInsecure: allowInsecure, NodeName: nodeName, BindAddr: bindAddr,
		AdvertiseAddr: advertiseAddr, TaskPort: taskPort, Seeds: seeds, MDNS: mdns, EncryptionKey: encryptionKey,
		AuthClockSkewMs: authClockSkew, AuthTimeoutMs: authTimeout, ReplayCacheSize: replayCache,
		TransferTimeoutMs: transferTimeout, OwnershipTimeoutMs: ownershipTimeout, StealBatchSize: stealBatch,
	}, nil
}

var routingPreferences = map[string]bool{"local": true, "remote": true, "any": true}

func decodeRoutingRule(d map[string]interface{}, path string) (RoutingRule, error) {
	allowed := map[string]bool{"task_labels": true, "require_node_labels": true, "preference": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return RoutingRule{}, err
	}
	taskLabelsV, err := reqField(d, "task_labels", path)
	if err != nil {
		return RoutingRule{}, err
	}
	taskLabels, err := asStrMap(taskLabelsV, sub(path, "task_labels"))
	if err != nil {
		return RoutingRule{}, err
	}
	requireV, err := reqField(d, "require_node_labels", path)
	if err != nil {
		return RoutingRule{}, err
	}
	require, err := asStrMap(requireV, sub(path, "require_node_labels"))
	if err != nil {
		return RoutingRule{}, err
	}
	prefV, err := reqField(d, "preference", path)
	if err != nil {
		return RoutingRule{}, err
	}
	pref, err := asStr(prefV, sub(path, "preference"))
	if err != nil {
		return RoutingRule{}, err
	}
	if !routingPreferences[pref] {
		return RoutingRule{}, newErr(sub(path, "preference"), "must be one of [any local remote]")
	}
	if len(taskLabels) == 0 && len(require) == 0 {
		return RoutingRule{}, newErr(path, "routing rule must not be empty")
	}
	return RoutingRule{TaskLabels: taskLabels, RequireNodeLabels: require, Preference: pref}, nil
}

func decodeRouting(d map[string]interface{}, path string) (RoutingConfig, error) {
	allowed := map[string]bool{"rules": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return RoutingConfig{}, err
	}
	rulesV, err := reqField(d, "rules", path)
	if err != nil {
		return RoutingConfig{}, err
	}
	rulesRaw, ok := rulesV.([]interface{})
	if !ok {
		return RoutingConfig{}, newErr(sub(path, "rules"), "must be a list")
	}
	rules := make([]RoutingRule, len(rulesRaw))
	for i, r := range rulesRaw {
		rulePath := fmt.Sprintf("%s.rules[%d]", path, i)
		rm, err := asMap(r, rulePath)
		if err != nil {
			return RoutingConfig{}, err
		}
		rule, err := decodeRoutingRule(rm, rulePath)
		if err != nil {
			return RoutingConfig{}, err
		}
		rules[i] = rule
	}
	seen := make(map[string]bool, len(rules))
	for i, r := range rules {
		keys := make([]string, 0, len(r.TaskLabels))
		for k, v := range r.TaskLabels {
			keys = append(keys, k+"="+v)
		}
		sort.Strings(keys)
		key := strings.Join(keys, ",")
		if seen[key] {
			return RoutingConfig{}, newErr(fmt.Sprintf("%s.rules[%d]", path, i), "duplicate routing rule task_labels")
		}
		seen[key] = true
	}
	return RoutingConfig{Rules: rules}, nil
}

func decodeKafka(d map[string]interface{}, path string) (KafkaConfig, error) {
	allowed := map[string]bool{
		"enabled": true, "brokers": true, "topic": true, "delivery_timeout_ms": true, "batch_size": true,
		"max_in_flight": true, "retry_backoff_ms": true, "shutdown_grace_ms": true, "preserve_owner_order": true,
		"max_event_bytes": true, "published_retention_seconds": true,
	}
	if err := noUnknown(d, allowed, path); err != nil {
		return KafkaConfig{}, err
	}
	enabledV, err := reqField(d, "enabled", path)
	if err != nil {
		return KafkaConfig{}, err
	}
	enabled, err := asBool(enabledV, sub(path, "enabled"))
	if err != nil {
		return KafkaConfig{}, err
	}
	brokersV, err := reqField(d, "brokers", path)
	if err != nil {
		return KafkaConfig{}, err
	}
	brokers, err := asStrList(brokersV, sub(path, "brokers"))
	if err != nil {
		return KafkaConfig{}, err
	}
	topicV, err := reqField(d, "topic", path)
	if err != nil {
		return KafkaConfig{}, err
	}
	topic, err := asStr(topicV, sub(path, "topic"))
	if err != nil {
		return KafkaConfig{}, err
	}
	posInt := func(key string) (int64, error) {
		v, err := reqField(d, key, path)
		if err != nil {
			return 0, err
		}
		return asPositiveInt(v, sub(path, key))
	}
	deliveryTimeout, err := posInt("delivery_timeout_ms")
	if err != nil {
		return KafkaConfig{}, err
	}
	batchSize, err := posInt("batch_size")
	if err != nil {
		return KafkaConfig{}, err
	}
	maxInFlight, err := posInt("max_in_flight")
	if err != nil {
		return KafkaConfig{}, err
	}
	retryBackoff, err := posInt("retry_backoff_ms")
	if err != nil {
		return KafkaConfig{}, err
	}
	shutdownGrace, err := posInt("shutdown_grace_ms")
	if err != nil {
		return KafkaConfig{}, err
	}
	preserveV, err := reqField(d, "preserve_owner_order", path)
	if err != nil {
		return KafkaConfig{}, err
	}
	preserve, err := asBool(preserveV, sub(path, "preserve_owner_order"))
	if err != nil {
		return KafkaConfig{}, err
	}
	maxEventBytes, err := posInt("max_event_bytes")
	if err != nil {
		return KafkaConfig{}, err
	}
	publishedRetention, err := posInt("published_retention_seconds")
	if err != nil {
		return KafkaConfig{}, err
	}
	return KafkaConfig{
		Enabled: enabled, Brokers: brokers, Topic: topic, DeliveryTimeoutMs: deliveryTimeout,
		BatchSize: batchSize, MaxInFlight: maxInFlight, RetryBackoffMs: retryBackoff,
		ShutdownGraceMs: shutdownGrace, PreserveOwnerOrder: preserve, MaxEventBytes: maxEventBytes,
		PublishedRetentionSeconds: publishedRetention,
	}, nil
}

func decodeIntegrations(d map[string]interface{}, path string) (IntegrationsConfig, error) {
	allowed := map[string]bool{"kafka": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return IntegrationsConfig{}, err
	}
	kafkaV, err := reqField(d, "kafka", path)
	if err != nil {
		return IntegrationsConfig{}, err
	}
	kafkaMap, err := asMap(kafkaV, sub(path, "kafka"))
	if err != nil {
		return IntegrationsConfig{}, err
	}
	kafka, err := decodeKafka(kafkaMap, sub(path, "kafka"))
	if err != nil {
		return IntegrationsConfig{}, err
	}
	return IntegrationsConfig{Kafka: kafka}, nil
}

func decodeMetrics(d map[string]interface{}, path string) (MetricsConfig, error) {
	allowed := map[string]bool{"listen_addr": true}
	if err := noUnknown(d, allowed, path); err != nil {
		return MetricsConfig{}, err
	}
	v, err := reqField(d, "listen_addr", path)
	if err != nil {
		return MetricsConfig{}, err
	}
	s, err := asStr(v, sub(path, "listen_addr"))
	if err != nil {
		return MetricsConfig{}, err
	}
	return MetricsConfig{ListenAddr: s}, nil
}

var rootKeys = map[string]bool{
	"socket": true, "socket_group": true, "ipc": true, "queue": true, "storage": true, "tasks": true,
	"workers": true, "cluster": true, "routing": true, "integrations": true, "metrics": true,
}

func decodeRoot(d map[string]interface{}) (Config, error) {
	if err := noUnknown(d, rootKeys, ""); err != nil {
		return Config{}, err
	}

	socketV, err := reqField(d, "socket", "")
	if err != nil {
		return Config{}, err
	}
	socket, err := asStr(socketV, "socket")
	if err != nil {
		return Config{}, err
	}
	groupV, err := reqField(d, "socket_group", "")
	if err != nil {
		return Config{}, err
	}
	group, err := asStr(groupV, "socket_group")
	if err != nil {
		return Config{}, err
	}

	ipcV, err := reqField(d, "ipc", "")
	if err != nil {
		return Config{}, err
	}
	ipcMap, err := asMap(ipcV, "ipc")
	if err != nil {
		return Config{}, err
	}
	ipc, err := decodeIPC(ipcMap, "ipc")
	if err != nil {
		return Config{}, err
	}

	queueV, err := reqField(d, "queue", "")
	if err != nil {
		return Config{}, err
	}
	queueMap, err := asMap(queueV, "queue")
	if err != nil {
		return Config{}, err
	}
	queue, err := decodeQueue(queueMap, "queue")
	if err != nil {
		return Config{}, err
	}

	storageV, err := reqField(d, "storage", "")
	if err != nil {
		return Config{}, err
	}
	storageMap, err := asMap(storageV, "storage")
	if err != nil {
		return Config{}, err
	}
	storage, err := decodeStorage(storageMap, "storage")
	if err != nil {
		return Config{}, err
	}

	tasksV, err := reqField(d, "tasks", "")
	if err != nil {
		return Config{}, err
	}
	tasksMap, err := asMap(tasksV, "tasks")
	if err != nil {
		return Config{}, err
	}
	tasks, err := decodeTasks(tasksMap, "tasks")
	if err != nil {
		return Config{}, err
	}

	workersV, err := reqField(d, "workers", "")
	if err != nil {
		return Config{}, err
	}
	workersMap, err := asMap(workersV, "workers")
	if err != nil {
		return Config{}, err
	}
	workers, err := decodeWorkers(workersMap, "workers")
	if err != nil {
		return Config{}, err
	}

	clusterV, err := reqField(d, "cluster", "")
	if err != nil {
		return Config{}, err
	}
	clusterMap, err := asMap(clusterV, "cluster")
	if err != nil {
		return Config{}, err
	}
	cluster, err := decodeCluster(clusterMap, "cluster")
	if err != nil {
		return Config{}, err
	}

	routingV, err := reqField(d, "routing", "")
	if err != nil {
		return Config{}, err
	}
	routingMap, err := asMap(routingV, "routing")
	if err != nil {
		return Config{}, err
	}
	routing, err := decodeRouting(routingMap, "routing")
	if err != nil {
		return Config{}, err
	}

	integrationsV, err := reqField(d, "integrations", "")
	if err != nil {
		return Config{}, err
	}
	integrationsMap, err := asMap(integrationsV, "integrations")
	if err != nil {
		return Config{}, err
	}
	integrations, err := decodeIntegrations(integrationsMap, "integrations")
	if err != nil {
		return Config{}, err
	}

	metricsV, err := reqField(d, "metrics", "")
	if err != nil {
		return Config{}, err
	}
	metricsMap, err := asMap(metricsV, "metrics")
	if err != nil {
		return Config{}, err
	}
	metrics, err := decodeMetrics(metricsMap, "metrics")
	if err != nil {
		return Config{}, err
	}

	var warnings []string
	if storage.State.Type == "memory" {
		warnings = append(warnings, "storage.state.type=memory is a development-only configuration")
	}
	for name, store := range storage.Objects.Stores {
		if store.Type == "memory" {
			warnings = append(warnings, fmt.Sprintf("storage.objects.stores.%s.type=memory is a development-only configuration", name))
		}
	}

	return Config{
		Socket: socket, SocketGroup: group, IPC: ipc, Queue: queue, Storage: storage, Tasks: tasks,
		Workers: workers, Cluster: cluster, Routing: routing, Integrations: integrations, Metrics: metrics,
		Warnings: warnings,
	}, nil
}

// --------------------------------------------------------------------
// Conditional validation
// --------------------------------------------------------------------

var stateBackends = map[string]bool{"memory": true, "sqlite": true}
var objectBackends = map[string]bool{"memory": true, "filesystem": true}

func validate(cfg *Config) error {
	if cfg.Queue.LeaseTTLMs < 1000 {
		return newErr("queue.lease_ttl_ms", "must be >= 1000")
	}
	if cfg.IPC.ObjectChunkBytes > cfg.Queue.MaxFrameSizeMB*1024*1024 {
		return newErr("ipc.object_chunk_bytes", "must not exceed queue.max_frame_size_mb converted to bytes")
	}

	if !stateBackends[cfg.Storage.State.Type] {
		return newBackendErr("storage.state.type", fmt.Sprintf("unsupported backend %q", cfg.Storage.State.Type))
	}
	if cfg.Storage.State.Type == "sqlite" && cfg.Storage.State.DSN == "" {
		return newErr("storage.state.dsn", "must be non-empty for sqlite backend")
	}
	if cfg.Storage.State.SQLiteSynchronous != "FULL" && cfg.Storage.State.SQLiteSynchronous != "NORMAL" {
		return newErr("storage.state.sqlite_synchronous", "must be exactly FULL or NORMAL")
	}

	if _, ok := cfg.Storage.Objects.Stores[cfg.Storage.Objects.Default]; !ok {
		return newErr("storage.objects.default", "must name a configured store")
	}
	for name, store := range cfg.Storage.Objects.Stores {
		storePath := "storage.objects.stores." + name
		if !objectBackends[store.Type] {
			return newBackendErr(storePath+".type", fmt.Sprintf("unsupported backend %q", store.Type))
		}
		if store.Type == "filesystem" && store.Root == "" {
			return newErr(storePath+".root", "must be non-empty for filesystem backend")
		}
	}

	if cfg.Cluster.Enabled && !cfg.Cluster.AllowInsecure {
		key, err := base64.StdEncoding.DecodeString(cfg.Cluster.EncryptionKey)
		if err != nil {
			return newErr("cluster.encryption_key", "must be valid base64")
		}
		if len(key) != 32 {
			return newErr("cluster.encryption_key", "must decode to exactly 32 bytes")
		}
	}

	if cfg.Cluster.OwnershipTimeoutMs <= cfg.Cluster.TransferTimeoutMs {
		return newErr("cluster.ownership_timeout_ms", "must exceed cluster.transfer_timeout_ms")
	}

	if cfg.Integrations.Kafka.Enabled {
		if len(cfg.Integrations.Kafka.Brokers) == 0 {
			return newErr("integrations.kafka.brokers", "must be non-empty when kafka is enabled")
		}
		if cfg.Integrations.Kafka.Topic == "" {
			return newErr("integrations.kafka.topic", "must be non-empty when kafka is enabled")
		}
	}

	seenPools := map[string]bool{}
	for i, pool := range cfg.Workers.Pools {
		if pool.Name == "" || seenPools[pool.Name] {
			return newErr(fmt.Sprintf("workers.pools[%d].name", i), "must be non-empty and unique")
		}
		seenPools[pool.Name] = true
		for _, part := range pool.Command {
			if part == "" {
				return newErr(fmt.Sprintf("workers.pools[%d].command", i), "argv entries must be non-empty")
			}
		}
		for key := range pool.Environment {
			if strings.HasPrefix(key, reservedEnvPrefix) {
				return newErr(fmt.Sprintf("workers.pools[%d].environment", i), fmt.Sprintf("%q is a reserved taskwire-owned variable", key))
			}
		}
	}

	if cfg.Workers.RestartBackoffMinMs > cfg.Workers.RestartBackoffMaxMs {
		return newErr("workers.restart_backoff_min_ms", "must not exceed workers.restart_backoff_max_ms")
	}

	return nil
}

// --------------------------------------------------------------------
// Public API
// --------------------------------------------------------------------

// Load reads and validates a YAML config file at path.
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config %s: %w", path, err)
	}

	raw, err := loadYAMLStrict(data)
	if err != nil {
		return nil, &ConfigError{Path: "<root>", Message: err.Error(), Category: "invalid_config"}
	}
	root, ok := raw.(map[string]interface{})
	if !ok {
		return nil, newErr("<root>", "config document must be a mapping")
	}

	cfg, err := decodeRoot(root)
	if err != nil {
		return nil, err
	}
	if err := validate(&cfg); err != nil {
		return nil, err
	}

	absPath, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("resolve config path %s: %w", path, err)
	}
	baseDir := filepath.Dir(absPath)
	resolve := func(p string) string {
		if p == "" || filepath.IsAbs(p) {
			return p
		}
		return filepath.Clean(filepath.Join(baseDir, p))
	}

	cfg.Socket = resolve(cfg.Socket)
	if cfg.Storage.State.Type == "sqlite" {
		cfg.Storage.State.DSN = resolve(cfg.Storage.State.DSN)
	}
	for name, store := range cfg.Storage.Objects.Stores {
		if store.Type == "filesystem" {
			store.Root = resolve(store.Root)
			cfg.Storage.Objects.Stores[name] = store
		}
	}
	for i := range cfg.Workers.Pools {
		cfg.Workers.Pools[i].WorkingDirectory = resolve(cfg.Workers.Pools[i].WorkingDirectory)
	}

	return &cfg, nil
}
