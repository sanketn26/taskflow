package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestVersion(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"version"}, &out, &errb); code != 0 || strings.TrimSpace(out.String()) == "" {
		t.Fatalf("code=%d out=%q", code, out.String())
	}
}

func TestPlannedCommandsFailClearly(t *testing.T) {
	for _, name := range []string{"migrate", "check", "controller"} {
		var out, errb bytes.Buffer
		if code := run([]string{name}, &out, &errb); code != 1 || !strings.Contains(errb.String(), "not implemented") {
			t.Errorf("%s: code=%d stderr=%q", name, code, errb.String())
		}
	}
}

func TestUnknownCommand(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"bogus"}, &out, &errb); code != 2 {
		t.Fatalf("code=%d", code)
	}
}
