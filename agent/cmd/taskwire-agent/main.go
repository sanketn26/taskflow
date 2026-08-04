// Command taskwire-agent is the Go sidecar binary. It serves the
// TaskwireControl gRPC service on a Unix domain socket, proving the process
// lifecycle (start, ready socket, graceful shutdown) the Python harness
// depends on; later phases add the worker pool, storage, and scheduler.
package main

import (
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/sanketn26/taskwire/agent/internal/config"
	"github.com/sanketn26/taskwire/agent/internal/controlserver"
)

// version is set at build time via -ldflags "-X main.version=...".
// It must always match the version reported by taskwire.__version__ in
// Python, since pyproject.toml is the single release source for both.
var version = "dev"

func main() {
	if len(os.Args) > 1 && os.Args[1] == "version" {
		fmt.Println(version)
		return
	}

	configPath := flagValue(os.Args[1:], "--config")
	if configPath == "" {
		log.Fatal("taskwire-agent: --config <path> is required")
	}

	cfg, err := config.Load(configPath)
	if err != nil {
		log.Fatalf("taskwire-agent: %v", err)
	}

	dirs := []string{filepath.Dir(cfg.Socket)}
	if cfg.Storage.State.Type == "sqlite" {
		dirs = append(dirs, filepath.Dir(cfg.Storage.State.DSN))
	}
	if store, ok := cfg.Storage.Objects.Stores[cfg.Storage.Objects.Default]; ok && store.Type == "filesystem" {
		dirs = append(dirs, store.Root)
	}
	for _, dir := range dirs {
		if dir == "" || dir == "." {
			continue
		}
		if err := os.MkdirAll(dir, 0o755); err != nil {
			log.Fatalf("taskwire-agent: create %s: %v", dir, err)
		}
	}

	maxMessageBytes := int(cfg.IPC.MaxMessageSizeMB * 1024 * 1024)
	srv, err := controlserver.New(cfg.Socket, version, maxMessageBytes)
	if err != nil {
		log.Fatalf("taskwire-agent: %v", err)
	}

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	serveErrCh := make(chan error, 1)
	go func() { serveErrCh <- srv.Serve() }()

	select {
	case sig := <-sigCh:
		log.Printf("taskwire-agent: received %s, shutting down", sig)
	case err := <-serveErrCh:
		if err != nil {
			log.Fatalf("taskwire-agent: serve error: %v", err)
		}
	}

	if err := srv.Close(); err != nil {
		log.Fatalf("taskwire-agent: shutdown error: %v", err)
	}
}

func flagValue(args []string, name string) string {
	for i, a := range args {
		if a == name && i+1 < len(args) {
			return args[i+1]
		}
	}
	return ""
}
