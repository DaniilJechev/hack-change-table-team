package mcp

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	sdk "github.com/modelcontextprotocol/go-sdk/mcp"
)

type fakeData struct {
	failure atomic.Bool
	role    atomic.Value
}

func (f *fakeData) User(_ context.Context, id string) (store.Profile, error) {
	if f.failure.Load() {
		return store.Profile{}, errors.New("password-secret database failure")
	}
	if id != "intern" && id != "mentor" && id != "other" {
		return store.Profile{}, store.ErrNotFound
	}
	role := "intern"
	if id == "mentor" {
		role = "mentor"
	}
	if value := f.role.Load(); value != nil && id == "mentor" {
		role = value.(string)
	}
	return store.Profile{UserID: id, TeamID: id + "-team", Role: role}, nil
}
func (f *fakeData) UserProfile(_ context.Context, p store.Profile) (store.UserProfile, error) {
	return store.UserProfile{Profile: p, Logins: map[string]string{}}, nil
}
func (f *fakeData) TeamCard(_ context.Context, p store.Profile, section string, _ int64) (store.TeamCard, error) {
	switch section {
	case "invalid":
		return store.TeamCard{}, store.ErrInvalid
	case "missing":
		return store.TeamCard{}, store.ErrNotFound
	case "failure":
		return store.TeamCard{}, errors.New("password-secret")
	}
	return store.TeamCard{TeamID: p.TeamID, Document: map[string]any{"name": p.TeamID}, Version: 1}, nil
}
func (f *fakeData) SearchFAQ(_ context.Context, p store.Profile, _ string) ([]store.FAQ, error) {
	return []store.FAQ{{ID: p.TeamID}}, nil
}
func (f *fakeData) AccessCatalog(_ context.Context, p store.Profile, _ []string) ([]store.CatalogObject, error) {
	return []store.CatalogObject{{ID: p.TeamID}}, nil
}
func (f *fakeData) AccessMarks(_ context.Context, p store.Profile) ([]store.AccessMark, error) {
	return []store.AccessMark{{ObjectID: p.UserID}}, nil
}
func (f *fakeData) Interns(_ context.Context, p store.Profile) ([]store.Intern, error) {
	if p.Role != "mentor" {
		return nil, store.ErrForbidden
	}
	return []store.Intern{}, nil
}

type headerTransport struct {
	user atomic.Value
	base http.RoundTripper
}

func (h *headerTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	r.Header.Set("X-Buddy-Key", "test-key")
	r.Header.Set("X-Buddy-User", h.user.Load().(string))
	return h.base.RoundTrip(r)
}
func TestST2_MCP_1_SixReadToolsAndRequestLocalIdentity(t *testing.T) {
	f := &fakeData{}
	server := httptest.NewServer(Handler(f, "test-key"))
	defer server.Close()
	headers := &headerTransport{base: http.DefaultTransport}
	headers.user.Store("mentor")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client := sdk.NewClient(&sdk.Implementation{Name: "test", Version: "1"}, nil)
	session, err := client.Connect(ctx, &sdk.StreamableClientTransport{Endpoint: server.URL, HTTPClient: &http.Client{Transport: headers}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	listed, err := session.ListTools(ctx, nil)
	if err != nil || len(listed.Tools) != 6 {
		t.Fatal(listed, err)
	}
	expected := map[string]bool{"user_profile_get": true, "team_card_get": true, "team_faq_search": true, "access_catalog_get": true, "access_marks_get": true, "interns_list": true}
	for _, tool := range listed.Tools {
		if !expected[tool.Name] || tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
			t.Fatal("unexpected or write tool", tool.Name)
		}
		delete(expected, tool.Name)
		args := map[string]any{}
		if tool.Name == "team_faq_search" {
			args["text"] = "Kafka"
		}
		result, err := session.CallTool(ctx, &sdk.CallToolParams{Name: tool.Name, Arguments: args})
		if err != nil || result.IsError {
			t.Fatal(tool.Name, result, err)
		}
	}
	call := func(name string, args map[string]any, wantError bool) string {
		t.Helper()
		v, e := session.CallTool(ctx, &sdk.CallToolParams{Name: name, Arguments: args})
		if e != nil {
			if wantError {
				return e.Error()
			}
			t.Fatal(e)
		}
		if v.IsError != wantError {
			t.Fatalf("%s: %+v", name, v)
		}
		var text strings.Builder
		for _, c := range v.Content {
			if item, ok := c.(*sdk.TextContent); ok {
				text.WriteString(item.Text)
			}
		}
		return text.String()
	}
	if result := call("team_card_get", map[string]any{}, false); !strings.Contains(result, "mentor-team") {
		t.Fatal(result)
	}
	// Same SDK session, changed request header: the preceding user's identity must never persist.
	headers.user.Store("other")
	if result := call("team_card_get", map[string]any{}, false); !strings.Contains(result, "other-team") || strings.Contains(result, "mentor-team") {
		t.Fatal(result)
	}
	call("interns_list", map[string]any{}, true)
	headers.user.Store("mentor")
	f.role.Store("intern")
	call("interns_list", map[string]any{}, true)
	for _, section := range []string{"invalid", "missing", "failure"} {
		text := call("team_card_get", map[string]any{"section": section}, true)
		if strings.Contains(text, "password-secret") {
			t.Fatal(text)
		}
	}
	call("team_card_get", map[string]any{"team_id": "foreign"}, true)
	call("user_profile_get", map[string]any{"user_id": "foreign"}, true)
	call("team_faq_search", map[string]any{}, true)
	call("team_card_put", map[string]any{}, true)
	headers.user.Store("missing")
	if _, err := session.CallTool(ctx, &sdk.CallToolParams{Name: "user_profile_get", Arguments: map[string]any{}}); err == nil {
		t.Fatal("unknown user accepted")
	}
}
func TestNFR_PRIV_1_MCPRejectsUnauthorizedHTTP(t *testing.T) {
	f := &fakeData{}
	for _, tc := range []struct {
		key, user, configured string
		status                int
	}{
		{"", "intern", "key", 401}, {"wrong", "intern", "key", 401}, {"key", "", "key", 401},
		{"key", "missing", "key", 401}, {"key", "/bad", "key", 401}, {"key", "intern", "", 503},
	} {
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{}`))
		req.Header.Set("X-Buddy-Key", tc.key)
		req.Header.Set("X-Buddy-User", tc.user)
		out := httptest.NewRecorder()
		Handler(f, tc.configured).ServeHTTP(out, req)
		if out.Code != tc.status {
			t.Fatal(out.Code, out.Body.String())
		}
	}
	f.failure.Store(true)
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{}`))
	req.Header.Set("X-Buddy-Key", "key")
	req.Header.Set("X-Buddy-User", "intern")
	out := httptest.NewRecorder()
	Handler(f, "key").ServeHTTP(out, req)
	if out.Code != 503 || strings.Contains(out.Body.String(), "password-secret") {
		t.Fatal(out.Code, out.Body.String())
	}
}
