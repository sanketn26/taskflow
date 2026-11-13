package config

import (
	"errors"
	"strings"
	"testing"
)

func env(m map[string]string) func(string) (string, bool) {
	return func(k string) (string, bool) { v, ok := m[k]; return v, ok }
}

func TestRequiredAndDefaults(t *testing.T) {
	_, err := LoadFrom(env(nil))
	var ce *Error
	if !errors.As(err, &ce) || ce.Variable != BackendURL {
		t.Fatalf("err=%v", err)
	}
	s, err := LoadFrom(env(map[string]string{BackendURL: "memory://"}))
	if err != nil || s.BackendKind() != "memory" || s.StartupCheck != "" || s.AllowEphemeral {
		t.Fatalf("s=%v err=%v", s, err)
	}
}

func TestPostgresAliasAndRejections(t *testing.T) {
	s, err := LoadFrom(env(map[string]string{BackendURL: "postgres://u:p@h:5432/db"}))
	if err != nil || s.BackendKind() != "postgresql" {
		t.Fatalf("s=%v err=%v", s, err)
	}
	for _, bad := range []string{
		"postgres:/h/db", "mysql://h/db", "memory://host", "postgresql://u:p@[::1:5432/db",
		"postgresql://u:p@h:port/db",
	} {
		_, err := LoadFrom(env(map[string]string{BackendURL: bad}))
		if err == nil {
			t.Errorf("%q accepted", bad)
		} else if strings.Contains(err.Error(), "p@") || strings.Contains(err.Error(), bad) {
			t.Errorf("error echoes value: %v", err)
		}
	}
}

func TestOtherSettings(t *testing.T) {
	s, err := LoadFrom(env(map[string]string{BackendURL: "memory://", StartupCheck: "FULL", AllowEphemeral: "yes"}))
	if err != nil || s.StartupCheck != CheckFull || !s.AllowEphemeral {
		t.Fatalf("s=%v err=%v", s, err)
	}
	for k, v := range map[string]string{StartupCheck: "sometimes", AllowEphemeral: "maybe"} {
		_, err := LoadFrom(env(map[string]string{BackendURL: "memory://", k: v}))
		var ce *Error
		if !errors.As(err, &ce) || ce.Variable != k {
			t.Errorf("%s: %v", k, err)
		}
	}
}

func TestRedactNeverLeaks(t *testing.T) {
	for _, u := range []string{
		"postgresql://user:hunter2@host/db",
		"postgresql://user:hun/ter#2@host/db",
		"postgresql://user:hunter2@h1:1,h2:2/db",
		"postgresql://host/db?password=hunter2&sslmode=require",
		"postgresql://host/db?sslpassword=hunter2",
		"postgresql://user:hunter2@host/db?password=hunter2",
	} {
		if got := RedactURL(u); strings.Contains(got, "hunter2") || strings.Contains(got, "hun/ter") {
			t.Errorf("%q -> %q", u, got)
		}
	}
	if RedactURL("not a url") != "not a url" {
		t.Error("non-URL changed")
	}
}

func TestSettingsStringHidesURL(t *testing.T) {
	s, _ := LoadFrom(env(map[string]string{BackendURL: "postgresql://u:hunter2@h/db"}))
	if strings.Contains(s.String(), "hunter2") {
		t.Fatal(s.String())
	}
}
