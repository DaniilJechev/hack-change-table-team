package api

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

func TestNFR_AUTH_1_RESTBindingWithPostgres(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires TEST_DATABASE_URL")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	schema := fmt.Sprintf("test_api_%d", time.Now().UnixNano())
	if _, err := db.Exec(`CREATE SCHEMA ` + schema); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		defer db.Close()
		if _, err := db.Exec(`DROP SCHEMA ` + schema + ` CASCADE`); err != nil {
			t.Error(err)
		}
	})
	sep := "?"
	if strings.Contains(dsn, "?") {
		sep = "&"
	}
	ctx := context.Background()
	s, err := store.Open(ctx, dsn+sep+"search_path="+schema)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	if err := s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.Seed(ctx, strings.NewReader("users: [{user_id: demo-daniil, display_name: Даниил, role: intern, team_id: demo-orders}]")); err != nil {
		t.Fatal(err)
	}
	code, err := s.IssueInvitation(ctx, "demo-daniil", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	service := httptest.NewServer(Handler(Identity{Store: s, ServiceKey: "test-key"}))
	defer service.Close()
	client := service.Client()
	client.Timeout = time.Second
	request := func(method, path, body string, status int) string {
		t.Helper()
		req, err := http.NewRequest(method, service.URL+path, strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-Buddy-Key", "test-key")
		response, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		var payload json.RawMessage
		if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != status {
			t.Fatalf("%s: status=%d body=%s", path, response.StatusCode, payload)
		}
		return string(payload)
	}
	body := fmt.Sprintf(`{"code":%q,"telegram_user_id":123,"telegram_chat_id":123}`, code)
	profile := request("POST", "/api/v1/auth/bind", body, 200)
	if !strings.Contains(profile, `"team_id":"demo-orders"`) || !strings.Contains(profile, `"role":"intern"`) {
		t.Fatal(profile)
	}
	request("POST", "/api/v1/auth/bind", body, 400)
	if request("GET", "/api/v1/auth/telegram/123", "", 200) != profile {
		t.Fatal("lookup differs from bound profile")
	}
	request("GET", "/api/v1/auth/telegram/456", "", 404)
	request("GET", "/ready", "", 200)
	s.Close()
	request("GET", "/ready", "", 503)
	request("GET", "/api/v1/auth/telegram/123", "", 503)
}
