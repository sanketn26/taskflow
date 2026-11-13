package pgtest_test

import (
	"context"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"

	"github.com/sanketn26/taskflow/internal/pgtest"
)

func TestURLForDatabase(t *testing.T) {
	got, err := pgtest.URLForDatabase("postgresql://u:p@h:5432/postgres?sslmode=disable", "x")
	if err != nil || got != "postgresql://u:p@h:5432/x?sslmode=disable" {
		t.Fatalf("got %q err %v", got, err)
	}
	if _, err := pgtest.URLForDatabase("not a url", "x"); err == nil {
		t.Fatal("non-URL accepted")
	}
}

func conn(t *testing.T, url string) *pgx.Conn {
	t.Helper()
	c, err := pgx.Connect(context.Background(), url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close(context.Background()) })
	return c
}

func TestDatabaseAcceptsQueries(t *testing.T) {
	c := conn(t, pgtest.NewDatabase(t))
	var one int
	if err := c.QueryRow(context.Background(), "SELECT 1").Scan(&one); err != nil || one != 1 {
		t.Fatal(one, err)
	}
	var name string
	c.QueryRow(context.Background(), "SELECT current_database()").Scan(&name)
	if !strings.HasPrefix(name, pgtest.DatabasePrefix) {
		t.Fatalf("database %q", name)
	}
}

func TestServerVersionSupported(t *testing.T) {
	c := conn(t, pgtest.NewDatabase(t))
	var v int
	if err := c.QueryRow(context.Background(), "SELECT current_setting('server_version_num')::int").Scan(&v); err != nil {
		t.Fatal(err)
	}
	if v < 140000 {
		t.Fatalf("Taskwire supports PostgreSQL 14 and later, got %d", v)
	}
}

func TestConcurrentCallsGetSeparateDatabases(t *testing.T) {
	a, b := pgtest.NewDatabase(t), pgtest.NewDatabase(t)
	if a == b {
		t.Fatal("same database handed out twice")
	}
}

func TestDatabaseIsDroppedAtCleanup(t *testing.T) {
	server := pgtest.ServerURL()
	if server == "" {
		t.Skip("no server")
	}
	name, _, err := pgtest.Create(context.Background(), server)
	if err != nil {
		t.Fatal(err)
	}
	if err := pgtest.Drop(context.Background(), server, name); err != nil {
		t.Fatal(err)
	}
	c := conn(t, server)
	var n int
	c.QueryRow(context.Background(), "SELECT count(*) FROM pg_database WHERE datname=$1", name).Scan(&n)
	if n != 0 {
		t.Fatal("database still exists")
	}
}
