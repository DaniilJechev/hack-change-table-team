package api

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

func application(t *testing.T) (*store.Store, *httptest.Server) {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires TEST_DATABASE_URL")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	schema := fmt.Sprintf("test_data_api_%d", time.Now().UnixNano())
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
	users := `users:
  - {user_id: intern, display_name: Intern, role: intern, team_id: orders, mentor_id: mentor}
  - {user_id: mentor, display_name: Mentor, role: mentor, team_id: orders}
  - {user_id: other, display_name: Other, role: intern, team_id: other}
`
	card := `synthetic: true
team_id: orders
name: Учебная команда
people: [{user_id: mentor}]
faq: []
`
	catalog := `synthetic: true
team_id: orders
objects:
  - {id: repo, instrument: github, name: Repo, url: 'https://example.org/repo', request_url: 'https://example.org/request', owner_id: mentor, roles: [intern, mentor]}
`
	if err := s.SeedData(ctx, strings.NewReader(users), strings.NewReader(card), strings.NewReader(catalog)); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(Application(s, "test-key"))
	t.Cleanup(server.Close)
	return s, server
}
func apiRequest(t *testing.T, server *httptest.Server, user, method, path, body string, status int) string {
	t.Helper()
	req, err := http.NewRequest(method, server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("X-Buddy-Key", "test-key")
	if user != "" {
		req.Header.Set("X-Buddy-User", user)
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatal(err)
	}
	if res.StatusCode != status {
		t.Fatalf("%s %s: status=%d want=%d body=%s", method, path, res.StatusCode, status, raw)
	}
	if !json.Valid(raw) {
		t.Fatalf("invalid JSON: %s", raw)
	}
	return string(raw)
}
func TestST2_REST_1_AllResourceRoutes(t *testing.T) {
	_, server := application(t)
	call := func(user, method, path, body string, status int) string {
		t.Helper()
		return apiRequest(t, server, user, method, path, body, status)
	}
	call("intern", "GET", "/api/v1/profile", "", 200)
	card := call("intern", "GET", "/api/v1/team-card", "", 200)
	if !strings.Contains(card, "Учебная команда") {
		t.Fatal(card)
	}
	call("intern", "GET", "/api/v1/team-card?section=people&version=1", "", 200)
	call("other", "GET", "/api/v1/team-card", "", 404)
	for _, version := range []string{"bad", "-1", "0"} {
		call("intern", "GET", "/api/v1/team-card?version="+version, "", 400)
	}
	call("intern", "GET", "/api/v1/access-catalog?object=repo", "", 200)
	call("intern", "PUT", "/api/v1/access-marks/repo", "", 200)
	call("intern", "PUT", "/api/v1/access-marks/repo", "{}", 400)
	call("intern", "GET", "/api/v1/access-marks", "", 200)
	call("intern", "POST", "/api/v1/access-snapshots", `{"id":"s1","objects":[{"object_id":"repo","status":"granted"}]}`, 200)
	call("intern", "GET", "/api/v1/access-snapshots/latest", "", 200)
	call("mentor", "GET", "/api/v1/interns", "", 200)
	call("intern", "GET", "/api/v1/interns", "", 403)
	call("intern", "POST", "/api/v1/briefs", `{"id":"b1","items":[]}`, 200)
	call("intern", "GET", "/api/v1/briefs/b1", "", 200)
	call("other", "GET", "/api/v1/briefs/b1", "", 404)
	call("intern", "PATCH", "/api/v1/briefs/b1", `{"status":"sent"}`, 200)
	call("intern", "POST", "/api/v1/briefs/b1/events", `{"id":"e1","kind":"read"}`, 200)
	call("intern", "POST", "/api/v1/briefs/b1/events", `{"id":"e1","kind":"positive"}`, 409)
	call("intern", "POST", "/api/v1/questions", `{"id":"q1","text":"Зачем Kafka?","assigned_to":"mentor"}`, 200)
	call("intern", "GET", "/api/v1/questions/q1", "", 200)
	call("other", "GET", "/api/v1/questions/q1", "", 404)
	call("mentor", "POST", "/api/v1/questions/q1/answer", `{"text":"Учебный ответ о Kafka"}`, 200)
	call("intern", "PATCH", "/api/v1/questions/q1", `{"helpful":true}`, 200)
	call("intern", "PATCH", "/api/v1/questions/q1", `{}`, 400)
	call("mentor", "POST", "/api/v1/faq", `{"id":"f1","question_id":"q1","consent":false}`, 400)
	call("mentor", "POST", "/api/v1/faq", `{"id":"f1","question_id":"q1","consent":true}`, 200)
	call("intern", "GET", "/api/v1/faq/f1", "", 200)
	call("other", "GET", "/api/v1/faq/f1", "", 404)
	found := call("intern", "GET", "/api/v1/faq?text=Kafka", "", 200)
	if !strings.Contains(found, "Учебный ответ") {
		t.Fatal(found)
	}
	call("intern", "GET", "/api/v1/auth/telegram/123", "", 404)
	call("", "GET", "/health", "", 200)
	call("", "GET", "/ready", "", 200)
}
func TestNFR_PRIV_1_RESTValidationAndServerOwnedFields(t *testing.T) {
	s, server := application(t)
	for _, path := range []string{"/api/v1/profile", "/api/v1/team-card", "/api/v1/access-marks", "/api/v1/briefs/b1", "/api/v1/questions/q1", "/api/v1/faq/f1"} {
		apiRequest(t, server, "", "GET", path, "", 401)
		apiRequest(t, server, "missing", "GET", path, "", 401)
	}
	for _, tc := range []struct{ path, body string }{
		{"/api/v1/questions", `{"id":"q1","text":"x","team_id":"other"}`},
		{"/api/v1/briefs", `{"id":"b1","items":[],"user_id":"other"}`},
		{"/api/v1/access-snapshots", `{"id":"s1","objects":[],"created_at":"2000-01-01"}`},
		{"/api/v1/faq", `{"id":"f1","question_id":"q1","consent":true,"author_id":"mentor"}`},
		{"/api/v1/briefs/b1/events", `{"id":"e1","kind":"read","created_at":"2000-01-01"}`},
	} {
		apiRequest(t, server, "intern", "POST", tc.path, tc.body, 400)
	}
	for _, tc := range []struct{ method, path string }{
		{"POST", "/api/v1/questions"}, {"POST", "/api/v1/briefs"}, {"PATCH", "/api/v1/briefs/b1"},
		{"POST", "/api/v1/access-snapshots"}, {"POST", "/api/v1/briefs/b1/events"},
		{"POST", "/api/v1/questions/q1/answer"}, {"PATCH", "/api/v1/questions/q1"}, {"POST", "/api/v1/faq"},
	} {
		apiRequest(t, server, "intern", tc.method, tc.path, "{", 400)
	}
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{} {}`, 400)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"text":"`+strings.Repeat("x", 1<<20)+`"}`, 400)
	s.Close()
	body := apiRequest(t, server, "intern", "GET", "/api/v1/profile", "", 503)
	if strings.Contains(body, "postgres") || strings.Contains(body, "sql") {
		t.Fatal(body)
	}
}
