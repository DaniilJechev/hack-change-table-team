package api

import (
	"encoding/json"
	"net/http/httptest"
	"testing"
)

func TestST0_INFRA_1_HealthAndInvalidRequests(t *testing.T) {
	for _, tc := range []struct {
		method, path string
		status       int
	}{
		{"GET", "/health", 200}, {"HEAD", "/health", 200},
		{"POST", "/health", 405}, {"GET", "/missing", 404},
	} {
		t.Run(tc.method+tc.path, func(t *testing.T) {
			w := httptest.NewRecorder()
			Handler().ServeHTTP(w, httptest.NewRequest(tc.method, tc.path, nil))
			if w.Code != tc.status {
				t.Fatalf("status %d, want %d", w.Code, tc.status)
			}
			if tc.method == "GET" && tc.status == 200 {
				var body map[string]string
				if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				if body["status"] != "ok" || body["service"] != "backend" {
					t.Fatal(body)
				}
				if w.Header().Get("Content-Type") != "application/json" {
					t.Fatal("missing JSON header")
				}
			}
		})
	}
}
