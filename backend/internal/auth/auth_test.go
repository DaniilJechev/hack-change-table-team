package auth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

type resolver struct {
	calls int
	err   error
}

func (r *resolver) User(_ context.Context, id string) (store.Profile, error) {
	r.calls++
	return store.Profile{UserID: id, TeamID: "orders", Role: "intern"}, r.err
}
func TestNFR_PRIV_1_SharedAuthBoundary(t *testing.T) {
	for _, tc := range []struct {
		key, configured, user string
		err                   error
		status, calls         int
	}{
		{"", "key", "intern", nil, 401, 0}, {"key", "", "intern", nil, 503, 0},
		{"key", "key", "", nil, 401, 0}, {"key", "key", "/bad", nil, 401, 0},
		{"key", "key", "unknown", store.ErrNotFound, 401, 1},
		{"key", "key", "intern", errors.New("password-secret"), 503, 1},
		{"key", "key", "intern", nil, 204, 1},
	} {
		users := &resolver{err: tc.err}
		next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			actor := Actor(r.Context())
			if actor.UserID != "intern" || actor.TeamID != "orders" {
				t.Fatal(actor)
			}
			if _, ok := r.Context().Deadline(); !ok {
				t.Fatal("unbounded request")
			}
			w.WriteHeader(204)
		})
		req := httptest.NewRequest("GET", "/api/v1/profile", nil)
		req.Header.Set("X-Buddy-Key", tc.key)
		req.Header.Set("X-Buddy-User", tc.user)
		out := httptest.NewRecorder()
		Service(tc.configured, User(users, next)).ServeHTTP(out, req)
		if out.Code != tc.status || users.calls != tc.calls || strings.Contains(out.Body.String(), "password-secret") {
			t.Fatal(out.Code, users.calls, out.Body.String())
		}
	}
}
