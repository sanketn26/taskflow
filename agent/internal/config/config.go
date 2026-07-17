// Package config loads the stub agent's runtime configuration for Phase 0.
// It is intentionally minimal: only the paths needed to prove process
// lifecycle and diagnostics. Phase 1 replaces this with the real protocol
// configuration surface.
package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// Config describes where the stub agent should listen and what paths it owns.
type Config struct {
	SocketPath string `yaml:"socket_path"`
	LogPath    string `yaml:"log_path"`
	StateDir   string `yaml:"state_dir"`
	ObjectDir  string `yaml:"object_dir"`
}

// Load reads and validates a YAML config file at path.
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config %s: %w", path, err)
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config %s: %w", path, err)
	}

	if cfg.SocketPath == "" {
		return nil, fmt.Errorf("config %s: socket_path is required", path)
	}

	return &cfg, nil
}
