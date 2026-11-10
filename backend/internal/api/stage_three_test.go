package api

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestFR_H2_01_UploadCardRESTAndFlags(t *testing.T) {
	s, server := application(t)
	if err := s.Seed(context.Background(), strings.NewReader(`users: [{user_id: lead, display_name: Lead, role: teamlead, team_id: orders}]`)); err != nil {
		t.Fatal(err)
	}
	card := `synthetic: true
team_id: orders
name: Новая карточка
people: [{user_id: mentor, name: Mentor, role: Наставник, areas: [Go], contact_url: 'https://example.org/mentor'}]
faq: []
`
	apiRequest(t, server, "intern", "POST", "/api/v1/team-card", card, 403)
	apiRequest(t, server, "", "POST", "/api/v1/team-card", card, 401)
	apiRequest(t, server, "mentor", "POST", "/api/v1/team-card", strings.Replace(card, "team_id: orders", "team_id: other", 1), 403)
	for _, bad := range []string{"[", strings.Replace(card, "role: Наставник, ", "", 1), strings.Repeat("x", (1<<20)+1)} {
		body := apiRequest(t, server, "mentor", "POST", "/api/v1/team-card", bad, 400)
		if !strings.Contains(body, "validation_error") || !strings.Contains(body, "fields") {
			t.Fatal(body)
		}
	}
	first := apiRequest(t, server, "mentor", "POST", "/api/v1/team-card", card, 200)
	second := apiRequest(t, server, "lead", "POST", "/api/v1/team-card", card, 200)
	var a, b map[string]any
	if err := json.Unmarshal([]byte(first), &a); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(second), &b); err != nil {
		t.Fatal(err)
	}
	if a["version"] != b["version"] {
		t.Fatal("duplicate version")
	}
	apiRequest(t, server, "intern", "GET", "/api/v1/team-card?version=1", "", 200)
	apiRequest(t, server, "intern", "GET", "/api/v1/flags", "", 200)
	apiRequest(t, server, "intern", "PUT", "/api/v1/flags/team_escalation", `{"enabled":false}`, 403)
	apiRequest(t, server, "mentor", "PUT", "/api/v1/flags/team_escalation", `{"enabled":false}`, 403)
	for _, bad := range []string{"{", `{}`, `{"enabled":null}`, `{"enabled":false,"team_id":"other"}`} {
		apiRequest(t, server, "lead", "PUT", "/api/v1/flags/team_escalation", bad, 400)
	}
	apiRequest(t, server, "lead", "PUT", "/api/v1/flags/unknown", `{"enabled":false}`, 400)
	apiRequest(t, server, "lead", "PUT", "/api/v1/flags/team_escalation", `{"enabled":false}`, 200)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"disabled","text":"Kafka?","assigned_to":"mentor"}`, 403)
	body := apiRequest(t, server, "other", "GET", "/api/v1/flags", "", 200)
	if !strings.Contains(body, `"team_escalation":true`) {
		t.Fatal(body)
	}
}

func TestFR_H2_05_ContextAndBotAnswerREST(t *testing.T) {
	_, server := application(t)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"q","text":"Kafka?","assigned_to":"mentor","context":{"current_task":"ORD-118","checked_sources":["people","faq"]}}`, 200)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"forged","text":"Kafka?","context":{"team_id":"other"}}`, 400)
	body := apiRequest(t, server, "mentor", "GET", "/api/v1/questions/q", "", 200)
	if !strings.Contains(body, `"team_id":"orders"`) || !strings.Contains(body, "ORD-118") {
		t.Fatal(body)
	}
	for _, bad := range []string{"{", `{"text":"Ответ"}`, `{"text":"Ответ","sources":["javascript:bad"]}`, `{"text":"Ответ","sources":[],"author_id":"mentor"}`} {
		apiRequest(t, server, "intern", "POST", "/api/v1/questions/q/bot-answer", bad, 400)
	}
	apiRequest(t, server, "other", "POST", "/api/v1/questions/q/bot-answer", `{"text":"Ответ","sources":["https://example.org/ORD-1"]}`, 404)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions/q/bot-answer", `{"text":"Ответ бота","sources":["/api/v1/team-card?version=1#people"]}`, 200)
	for _, bad := range []string{"{", `{}`, `{"helpful":null}`} {
		apiRequest(t, server, "intern", "PATCH", "/api/v1/questions/q/bot-answer", bad, 400)
	}
	apiRequest(t, server, "intern", "PATCH", "/api/v1/questions/q/bot-answer", `{"helpful":true}`, 200)
	apiRequest(t, server, "mentor", "PATCH", "/api/v1/questions/q/bot-answer", `{"helpful":true}`, 404)
	apiRequest(t, server, "mentor", "POST", "/api/v1/faq", `{"id":"f","question_id":"q","consent":true}`, 404)
	apiRequest(t, server, "mentor", "POST", "/api/v1/questions/q/answer", `{"text":"Ответ человека"}`, 200)
	apiRequest(t, server, "intern", "PATCH", "/api/v1/questions/q", `{"helpful":false}`, 200)
	body = apiRequest(t, server, "intern", "GET", "/api/v1/questions/q", "", 200)
	if !strings.Contains(body, `"helpful":false`) || !strings.Contains(body, `"bot_helpful":true`) {
		t.Fatal(body)
	}
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"unassigned","text":"Нет ответа"}`, 200)
	for _, bad := range []string{"{", `{}`, `{"assigned_to":"mentor","team_id":"other"}`} {
		apiRequest(t, server, "intern", "POST", "/api/v1/questions/unassigned/escalate", bad, 400)
	}
	apiRequest(t, server, "mentor", "POST", "/api/v1/questions/unassigned/escalate", `{"assigned_to":"mentor"}`, 404)
	for i := 0; i < 2; i++ {
		apiRequest(t, server, "intern", "POST", "/api/v1/questions/unassigned/escalate", `{"assigned_to":"mentor"}`, 200)
	}
	apiRequest(t, server, "intern", "POST", "/api/v1/questions/unassigned/escalate", `{"assigned_to":"intern"}`, 409)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"unassigned","text":"Нет ответа"}`, 200)
}
