package main

import (
	"bytes"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// buildAgent compiles the current package into a temp binary with a known
// version, mirroring how the Makefile injects the release version.
func buildAgent(t *testing.T, version string) string {
	t.Helper()

	bin := filepath.Join(t.TempDir(), "taskwire-agent")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}

	cmd := exec.Command("go", "build",
		"-ldflags", "-X main.version="+version,
		"-o", bin,
		".",
	)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("go build failed: %v\n%s", err, stderr.String())
	}
	return bin
}

func TestVersionCommandPrintsInjectedVersion(t *testing.T) {
	bin := buildAgent(t, "9.9.9-test")

	out, err := exec.Command(bin, "version").Output()
	if err != nil {
		t.Fatalf("running %s version: %v", bin, err)
	}

	got := strings.TrimSpace(string(out))
	if got != "9.9.9-test" {
		t.Fatalf("version output = %q, want %q", got, "9.9.9-test")
	}
}

func TestMissingConfigFlagFailsWithActionableError(t *testing.T) {
	bin := buildAgent(t, "0.0.0")

	cmd := exec.Command(bin)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected non-zero exit when --config is missing, got success: %s", out)
	}
	if !strings.Contains(string(out), "--config") {
		t.Fatalf("error output %q does not mention --config", out)
	}
}
