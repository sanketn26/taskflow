// Package pgtest gives integration tests a throwaway PostgreSQL database.
//
// TASKWIRE_TEST_DATABASE_URL points at a server the tests may create
// databases on. Each call creates a uniquely named database, so concurrent
// runs (parallel CI jobs, two developers on one server) cannot collide.
package pgtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	EnvVar         = "TASKWIRE_TEST_DATABASE_URL"
	RequireVar     = "TASKWIRE_REQUIRE_INTEGRATION"
	DatabasePrefix = "taskwire_test_"
)

const howToRun = EnvVar + " is not set. Start a server with `make db-up` and run " +
	"`make integration`, or point the variable at any PostgreSQL 14+ server " +
	"these tests may create databases on."

// ServerURL returns the configured server URL, or "" when unset or blank.
func ServerURL() string { return strings.TrimSpace(os.Getenv(EnvVar)) }

// Required reports whether a missing server must fail instead of skip.
func Required() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(RequireVar))) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// URLForDatabase returns server with its database replaced by name.
func URLForDatabase(server, name string) (string, error) {
	u, err := url.Parse(server)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return "", fmt.Errorf("%s must be a postgresql:// URL", EnvVar)
	}
	u.Path = "/" + name
	u.RawPath = ""
	return u.String(), nil
}

// NewDatabase creates a database for the calling test and drops it at cleanup.
// It skips the test when no server is configured, or fails if
// TASKWIRE_REQUIRE_INTEGRATION is set. It returns the database's URL.
func NewDatabase(t testing.TB) string {
	t.Helper()
	server := ServerURL()
	if server == "" {
		if Required() {
			t.Fatal(howToRun)
		}
		t.Skip(howToRun)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	name, dbURL, err := Create(ctx, server)
	if err != nil {
		t.Fatalf("cannot create a test database: %v (is the server running? try `make db-up`)", redact(err, server))
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := Drop(ctx, server, name); err != nil {
			t.Errorf("dropping %s: %v", name, redact(err, server))
		}
	})
	return dbURL
}

// Create makes a uniquely named database and returns its name and URL.
func Create(ctx context.Context, server string) (name, dbURL string, err error) {
	var b [8]byte
	if _, err = rand.Read(b[:]); err != nil {
		return "", "", err
	}
	name = DatabasePrefix + hex.EncodeToString(b[:])
	if dbURL, err = URLForDatabase(server, name); err != nil {
		return "", "", err
	}
	err = exec(ctx, server, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize())
	return name, dbURL, err
}

// Drop removes name, disconnecting anything still attached to it.
func Drop(ctx context.Context, server, name string) error {
	return exec(ctx, server, "DROP DATABASE IF EXISTS "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)")
}

func exec(ctx context.Context, server, stmt string) error {
	conn, err := pgx.Connect(ctx, server)
	if err != nil {
		return err
	}
	defer conn.Close(ctx)
	_, err = conn.Exec(ctx, stmt)
	return err
}

// redact keeps a password that a driver error might echo out of test logs.
func redact(err error, server string) string {
	msg := err.Error()
	if u, perr := url.Parse(server); perr == nil {
		if pw, ok := u.User.Password(); ok && pw != "" {
			msg = strings.ReplaceAll(msg, pw, "***")
		}
	}
	return msg
}
