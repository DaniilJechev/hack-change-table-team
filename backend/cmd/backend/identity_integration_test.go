package main

import (
	"bytes"
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNFR_DB_1_Commands(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires TEST_DATABASE_URL")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	schema := fmt.Sprintf("test_cli_%d", time.Now().UnixNano())
	if _, err := db.Exec(`CREATE SCHEMA ` + schema); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		defer db.Close()
		if _, err := db.Exec(`DROP SCHEMA ` + schema + ` CASCADE`); err != nil {
			t.Error(err)
		}
	})
	separator := "?"
	if strings.Contains(dsn, "?") {
		separator = "&"
	}
	path := filepath.Join(t.TempDir(), "users.yaml")
	if err := os.WriteFile(path, []byte("users: [{user_id: cli, display_name: CLI, role: intern, team_id: team}]"), 0600); err != nil {
		t.Fatal(err)
	}
	values := map[string]string{"DATABASE_URL": dsn + separator + "search_path=" + schema, "SEED_USERS_PATH": path, "BUDDY_SERVICE_KEY": "key"}
	getenv := func(k string) string { return values[k] }
	for _, args := range [][]string{{"seed"}, {"seed"}, {"invite", "--user", "cli", "--ttl", "1h"}} {
		var out bytes.Buffer
		if err := execute(context.Background(), args, getenv, &out); err != nil {
			t.Fatal(err)
		}
		if out.Len() == 0 {
			t.Fatal("empty command output")
		}
	}
	for _, args := range [][]string{{"invite"}, {"invite", "--bad"}, {"invite", "--user", "cli", "extra"}, {"invite", "--user", "missing"}, {"invite", "--user", "cli", "--ttl", "0s"}} {
		if err := execute(context.Background(), args, getenv, &bytes.Buffer{}); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
	values["SEED_USERS_PATH"] = "/missing/users.yaml"
	if err := execute(context.Background(), []string{"seed"}, getenv, &bytes.Buffer{}); err == nil {
		t.Fatal("missing seed accepted")
	}
	values["SEED_USERS_PATH"] = path
	if err := os.WriteFile(path, []byte("users: []"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := execute(context.Background(), []string{"seed"}, getenv, &bytes.Buffer{}); err == nil {
		t.Fatal("invalid seed accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	getter := func(k string) string {
		if k == "HTTP_ADDR" {
			cancel()
			return "127.0.0.1:0"
		}
		return values[k]
	}
	if err := execute(ctx, nil, getter, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
}

func TestNFR_AUTH_API_2_StartupConfiguration(t *testing.T) {
	for _, tc := range []struct {
		args []string
		env  map[string]string
	}{
		{[]string{"unknown"}, nil}, {nil, nil},
		{nil, map[string]string{"BUDDY_SERVICE_KEY": "key", "DATABASE_URL": "://password-secret-invalid"}},
		{[]string{"seed"}, map[string]string{"DATABASE_URL": "postgres://127.0.0.1:1/db?connect_timeout=1"}},
	} {
		err := execute(context.Background(), tc.args, func(k string) string { return tc.env[k] }, &bytes.Buffer{})
		if err == nil || strings.Contains(err.Error(), "password-secret") {
			t.Fatalf("unexpected error: %v", err)
		}
	}
}
