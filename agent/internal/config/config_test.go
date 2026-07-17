package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadValidConfig(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "taskwire.yaml")
	body := "socket_path: " + filepath.Join(dir, "agent.sock") + "\n" +
		"log_path: " + filepath.Join(dir, "agent.log") + "\n" +
		"state_dir: " + filepath.Join(dir, "state") + "\n" +
		"object_dir: " + filepath.Join(dir, "objects") + "\n"
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if cfg.SocketPath == "" {
		t.Fatal("SocketPath not populated")
	}
}

func TestLoadRejectsMissingSocketPath(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "taskwire.yaml")
	if err := os.WriteFile(path, []byte("log_path: /tmp/x.log\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	if _, err := Load(path); err == nil {
		t.Fatal("expected error for missing socket_path")
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load("/nonexistent/taskwire.yaml"); err == nil {
		t.Fatal("expected error for missing config file")
	}
}
