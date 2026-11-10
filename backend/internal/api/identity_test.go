package api

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

type fakeIdentity struct {
	err   error
	calls int
}

func (s *fakeIdentity) Ping(context.Context) error { s.calls++; return s.err }
func (s *fakeIdentity) Bind(_ context.Context, code string, telegramID, chatID int64) (store.Profile, error) {
	s.calls++
	if code != "valid" || telegramID != 123 || chatID != 123 {
		panic("unexpected bind arguments")
	}
	return store.Profile{UserID: "demo", Role: "intern", TeamID: "demo-orders"}, s.err
}
func (s *fakeIdentity) TelegramProfile(_ context.Context, id int64) (store.Profile, error) {
	s.calls++
	return store.Profile{UserID: "demo", Role: "intern", TeamID: "demo-orders"}, s.err
}

func TestNFR_AUTH_API_2_Authorization(t *testing.T) {
	for _, tc := range []struct {
		configured, supplied string
		status               int
	}{
		{"key", "", 401}, {"key", "wrong", 401}, {"", "", 503},
	} {
		s := &fakeIdentity{}
		w := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/auth/telegram/123", nil)
		r.Header.Set("X-Buddy-Key", tc.supplied)
		Handler(Identity{s, tc.configured}).ServeHTTP(w, r)
		if w.Code != tc.status || s.calls != 0 {
			t.Fatalf("status=%d, calls=%d", w.Code, s.calls)
		}
	}
}

func TestNFR_AUTH_API_1_InvalidRequest(t *testing.T) {
	valid := `{"code":"valid","telegram_user_id":123,"telegram_chat_id":123}`
	for _, body := range []string{
		`{`, `null`, `{}`, valid + `{}`, valid + `garbage`,
		`{"code":"valid","telegram_user_id":123,"telegram_chat_id":-123}`,
		`{"code":"valid","telegram_user_id":-123,"telegram_chat_id":-123}`,
		`{"code":"valid","telegram_user_id":123,"telegram_chat_id":123,"role":"teamlead"}`,
		`{"code":"` + strings.Repeat("a", 129) + `","telegram_user_id":123,"telegram_chat_id":123}`,
		strings.Repeat(" ", 4097) + valid,
	} {
		s := &fakeIdentity{}
		w := httptest.NewRecorder()
		r := httptest.NewRequest("POST", "/api/v1/auth/bind", strings.NewReader(body))
		r.Header.Set("X-Buddy-Key", "key")
		Handler(Identity{s, "key"}).ServeHTTP(w, r)
		if w.Code != 400 || s.calls != 0 {
			t.Fatalf("status=%d, calls=%d for body %.90s", w.Code, s.calls, body)
		}
	}
}

func TestNFR_AUTH_API_2_ProfilesAndSanitizedErrors(t *testing.T) {
	for _, tc := range []struct {
		method, path, body string
		err                error
		status             int
		contains           string
	}{
		{"POST", "/api/v1/auth/bind", `{"code":"valid","telegram_user_id":123,"telegram_chat_id":123}`, nil, 200, `"role":"intern"`},
		{"POST", "/api/v1/auth/bind", `{"code":"valid","telegram_user_id":123,"telegram_chat_id":123}`, store.ErrInvitation, 400, "invalid_invitation"},
		{"POST", "/api/v1/auth/bind", `{"code":"valid","telegram_user_id":123,"telegram_chat_id":123}`, store.ErrConflict, 409, "binding_conflict"},
		{"GET", "/api/v1/auth/telegram/123", "", nil, 200, `"team_id":"demo-orders"`},
		{"GET", "/api/v1/auth/telegram/124", "", store.ErrNotFound, 404, "not_found"},
		{"GET", "/api/v1/auth/telegram/no", "", nil, 400, "invalid_request"},
		{"GET", "/api/v1/auth/telegram/0", "", nil, 400, "invalid_request"},
		{"GET", "/api/v1/auth/telegram/123", "", errors.New("secret-driver-password"), 503, "unavailable"},
		{"GET", "/ready", "", nil, 200, `"status":"ok"`},
		{"GET", "/ready", "", errors.New("secret-driver-password"), 503, "unavailable"},
	} {
		t.Run(tc.path+tc.contains, func(t *testing.T) {
			w := httptest.NewRecorder()
			r := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
			r.Header.Set("X-Buddy-Key", "key")
			Handler(Identity{&fakeIdentity{err: tc.err}, "key"}).ServeHTTP(w, r)
			if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.contains) || strings.Contains(w.Body.String(), "secret") {
				t.Fatalf("status=%d, body=%s", w.Code, w.Body)
			}
			if w.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("identity responses must not be cached")
			}
		})
	}
}
