package api

import (
	"context"
	"encoding/json"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	"strings"
	"testing"
)

func TestMOCK_ACCESS_1_RESTRightsAndDecision(t *testing.T) {
	s, server := application(t)
	ctx := context.Background()
	if err := s.Seed(ctx, strings.NewReader(`users: [{user_id: lead, display_name: Lead, role: teamlead, team_id: orders}]`)); err != nil {
		t.Fatal(err)
	}
	if err := s.SeedMockServices(ctx, strings.NewReader(`synthetic: true
access_rules: [{team_id: orders, object_id: repo, status: pending, reason: Wait}]
`)); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/api/v1/mock/access-requests", "/api/v1/mock/progress"} {
		apiRequest(t, server, "", "GET", path, "", 401)
	}
	for _, body := range []string{`{`, `{}`, `{"object_id":"repo","user_id":"other"}`, `{"object_id":null}`, `{"object_id":"/bad"}`} {
		apiRequest(t, server, "intern", "POST", "/api/v1/mock/access-requests", body, 400)
	}
	apiRequest(t, server, "other", "POST", "/api/v1/mock/access-requests", `{"object_id":"repo"}`, 404)
	body := apiRequest(t, server, "intern", "POST", "/api/v1/mock/access-requests", `{"object_id":"repo"}`, 200)
	var record store.MockAccessRequest
	if err := json.Unmarshal([]byte(body), &record); err != nil {
		t.Fatal(err)
	}
	if record.Status != "pending" || !record.Simulated {
		t.Fatal(record)
	}
	list := apiRequest(t, server, "other", "GET", "/api/v1/mock/access-requests", "", 200)
	if strings.Contains(list, record.ID) {
		t.Fatal("leaked request")
	}
	path := "/api/v1/mock/access-requests/" + record.ID
	for _, who := range []string{"intern", "mentor"} {
		apiRequest(t, server, who, "PATCH", path, `{"status":"granted"}`, 403)
	}
	for _, bad := range []string{`{`, `{}`, `{"status":"denied"}`, `{"status":"pending"}`, `{"status":"granted","team_id":"other"}`} {
		apiRequest(t, server, "lead", "PATCH", path, bad, 400)
	}
	apiRequest(t, server, "lead", "PATCH", "/api/v1/mock/access-requests/missing", `{"status":"granted"}`, 404)
	apiRequest(t, server, "lead", "PATCH", path, `{"status":"granted"}`, 200)
	apiRequest(t, server, "lead", "PATCH", path, `{"status":"granted"}`, 200)
	apiRequest(t, server, "lead", "PATCH", path, `{"status":"denied","reason":"Changed"}`, 409)
	again := apiRequest(t, server, "intern", "POST", "/api/v1/mock/access-requests", `{"object_id":"repo"}`, 200)
	if !strings.Contains(again, `"status":"granted"`) {
		t.Fatal(again)
	}
}

func TestMOCK_PROGRESS_1_RESTOwnAndRepeated(t *testing.T) {
	_, server := application(t)
	initial := apiRequest(t, server, "intern", "GET", "/api/v1/mock/progress", "", 200)
	if !strings.Contains(initial, `"completed":false`) || !strings.Contains(initial, `"completed_at":null`) {
		t.Fatal(initial)
	}
	path := "/api/v1/mock/progress/equator_presentation"
	for _, bad := range []string{`{`, `{}`, `{"completed":null}`, `{"completed":"true"}`, `{"completed":true,"user_id":"other"}`} {
		apiRequest(t, server, "intern", "PUT", path, bad, 400)
	}
	first := apiRequest(t, server, "intern", "PUT", path, `{"completed":true}`, 200)
	second := apiRequest(t, server, "intern", "PUT", path, `{"completed":true}`, 200)
	if first != second {
		t.Fatal("duplicate changed timestamps")
	}
	own := apiRequest(t, server, "other", "GET", "/api/v1/mock/progress", "", 200)
	if strings.Contains(own, `"completed":true`) {
		t.Fatal("leaked progress")
	}
	apiRequest(t, server, "intern", "PUT", path, `{"completed":false}`, 200)
	apiRequest(t, server, "intern", "PUT", "/api/v1/mock/progress/unknown", `{"completed":true}`, 404)
}
