package api

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDESKTOP_02_InternalAPIAuthentication(t *testing.T) {
	for _, tc := range []struct {
		key, body string
		status    int
	}{{"", `{}`, 401}, {"wrong", `{}`, 401}, {"secret", `{"unexpected":1}`, 400}, {"secret", `null null`, 400}} {
		r := httptest.NewRequest("POST", "/internal/client/device", strings.NewReader(tc.body))
		r.Header.Set("X-Buddy-Key", tc.key)
		w := httptest.NewRecorder()
		ChannelHandler(nil, "secret").ServeHTTP(w, r)
		if w.Code != tc.status {
			t.Fatal(w.Code, w.Body.String())
		}
	}
}

func TestDESKTOP_03_InternalChannelHTTPPersistence(t *testing.T) {
	_, server := application(t)
	apiRequest(t, server, "", "POST", "/internal/client/device", `{}`, 200)
	apiRequest(t, server, "", "POST", "/internal/client/create", `{"user_id":"intern","kind":"message","id":"one","payload":{"role":"assistant","response":{"result":"synthetic"}}}`, 200)
	list := apiRequest(t, server, "", "POST", "/internal/client/list", `{"user_id":"intern","kind":"message"}`, 200)
	if !strings.Contains(list, "synthetic") {
		t.Fatal(list)
	}
	apiRequest(t, server, "", "POST", "/internal/client/get", `{"user_id":"other","kind":"message","id":"one"}`, 404)
	apiRequest(t, server, "", "POST", "/internal/client/telegram", `{"user_id":"intern"}`, 200)
	apiRequest(t, server, "", "POST", "/internal/client/profile", `{"user_id":"intern"}`, 200)
	apiRequest(t, server, "", "POST", "/internal/client/audience", `{}`, 200)
	apiRequest(t, server, "", "POST", "/internal/client/unknown", `{}`, 400)
}
