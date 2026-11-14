package api

import (
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"
)

func traceBody(id, user, path, turn string) string {
	return `{"id":"` + id + `","user_id":"` + user + `","team_id":"demo-orders","role":"intern","demo":true,"path":"` + path +
		`","fallback":false,"case":"","duration_ms":120,"llm_calls":2,"tool_calls":1,"turn":` + turn + `}`
}

const sampleTurn = `{"request":{"text":"Кто мой наставник?"},"steps":[{"tool":"team_card","result":{"ok":true}}],"answer":{"text":"Синтетический ответ","sources":["https://example.org/card"]}}`

func listTraces(t *testing.T, body string) []map[string]any {
	t.Helper()
	var out struct {
		Traces []map[string]any `json:"traces"`
	}
	if err := json.Unmarshal([]byte(body), &out); err != nil {
		t.Fatal(err)
	}
	return out.Traces
}

func TestFR_TR_01_TraceRecordedForDemoUserWithoutActor(t *testing.T) {
	_, server := application(t)
	// demo-daniil is not in users; no X-Buddy-User header is sent.
	body := apiRequest(t, server, "", "POST", "/api/v1/agent-traces", traceBody("tr-0123456789abcdef0123456789abcdef", "demo-daniil", "agent", sampleTurn), 201)
	if !strings.Contains(body, `"id":"tr-0123456789abcdef0123456789abcdef"`) {
		t.Fatal(body)
	}
	traces := listTraces(t, apiRequest(t, server, "", "GET", "/api/v1/agent-traces?user_id=demo-daniil", "", 200))
	if len(traces) != 1 || traces[0]["user_id"] != "demo-daniil" || traces[0]["team_id"] != "demo-orders" || traces[0]["path"] != "agent" {
		t.Fatal(traces)
	}
}

func TestFR_TR_02_DuplicateIDStoresOneRowREST(t *testing.T) {
	_, server := application(t)
	body := traceBody("tr-dup", "intern", "router", sampleTurn)
	apiRequest(t, server, "", "POST", "/api/v1/agent-traces", body, 201)
	apiRequest(t, server, "", "POST", "/api/v1/agent-traces", body, 201)
	if traces := listTraces(t, apiRequest(t, server, "", "GET", "/api/v1/agent-traces", "", 200)); len(traces) != 1 {
		t.Fatal(traces)
	}
}

func TestFR_TR_02_OversizeBodyRejectedAndNotStored(t *testing.T) {
	_, server := application(t)
	big := `{"text":"` + strings.Repeat("я", 140<<10) + `"}` // > 256 KiB in UTF-8
	apiRequest(t, server, "", "POST", "/api/v1/agent-traces", traceBody("tr-big", "intern", "agent", big), 400)
	if traces := listTraces(t, apiRequest(t, server, "", "GET", "/api/v1/agent-traces", "", 200)); len(traces) != 0 {
		t.Fatal(traces)
	}
	apiRequest(t, server, "", "GET", "/api/v1/agent-traces/tr-big", "", 404)
}

func TestFR_TR_02_InvalidTraceRejected(t *testing.T) {
	_, server := application(t)
	ok := traceBody("tr-ok", "intern", "agent", sampleTurn)
	for _, bad := range []string{
		`{}`,
		strings.Replace(ok, `"case":""`, `"case":"","extra":1`, 1),                           // unknown field
		strings.Replace(ok, `"case":""`, `"case":"","created_at":"2026-01-01T00:00:00Z"`, 1), // server-owned
		traceBody("tr-ok", "intern", "chat", sampleTurn),                                     // bad path
		traceBody("tr ok", "intern", "agent", sampleTurn),                                    // bad id
		traceBody("tr-ok", "", "agent", sampleTurn),                                          // empty user
		traceBody("tr-ok", "intern", "agent", `[]`),                                          // turn not an object
		traceBody("tr-ok", "intern", "agent", `null`),
		strings.Replace(ok, `,"turn":`+sampleTurn, ``, 1), // turn missing
		strings.Replace(ok, `"duration_ms":120`, `"duration_ms":-1`, 1),
		strings.Replace(ok, `"tool_calls":1`, `"tool_calls":1.5`, 1),
		strings.Replace(ok, `"team_id":"demo-orders"`, `"team_id":" "`, 1),
		ok + `{}`, // trailing document
	} {
		apiRequest(t, server, "", "POST", "/api/v1/agent-traces", bad, 400)
	}
	if traces := listTraces(t, apiRequest(t, server, "", "GET", "/api/v1/agent-traces", "", 200)); len(traces) != 0 {
		t.Fatal(traces)
	}
}

func TestFR_TR_04_ReadRequiresServiceKey(t *testing.T) {
	_, server := application(t)
	for _, c := range []struct{ method, path, body string }{
		{"POST", "/api/v1/agent-traces", traceBody("tr-k", "intern", "agent", sampleTurn)},
		{"GET", "/api/v1/agent-traces", ""},
		{"GET", "/api/v1/agent-traces/tr-k", ""},
	} {
		for _, key := range []string{"", "wrong"} {
			req, err := http.NewRequest(c.method, server.URL+c.path, strings.NewReader(c.body))
			if err != nil {
				t.Fatal(err)
			}
			if key != "" {
				req.Header.Set("X-Buddy-Key", key)
			}
			req.Header.Set("X-Buddy-User", "intern") // a user header never substitutes for the service key
			res, err := server.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			res.Body.Close()
			if res.StatusCode != 401 {
				t.Fatal(c.method, c.path, key, res.StatusCode)
			}
		}
	}
	apiRequest(t, server, "", "GET", "/api/v1/agent-traces/tr-k", "", 404)
}

func TestFR_TR_04_FullTraceByIDReturnsTurnUnchanged(t *testing.T) {
	_, server := application(t)
	apiRequest(t, server, "", "POST", "/api/v1/agent-traces", traceBody("tr-full", "intern", "followup", sampleTurn), 201)
	var got map[string]any
	if err := json.Unmarshal([]byte(apiRequest(t, server, "", "GET", "/api/v1/agent-traces/tr-full", "", 200)), &got); err != nil {
		t.Fatal(err)
	}
	var want any
	if err := json.Unmarshal([]byte(sampleTurn), &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got["turn"], want) {
		t.Fatal(got["turn"])
	}
	for field, value := range map[string]any{"id": "tr-full", "user_id": "intern", "team_id": "demo-orders", "role": "intern", "demo": true, "path": "followup", "fallback": false, "case": "", "duration_ms": 120.0, "llm_calls": 2.0, "tool_calls": 1.0} {
		if got[field] != value {
			t.Fatal(field, got[field])
		}
	}
	if _, ok := got["created_at"].(string); !ok {
		t.Fatal(got)
	}
	apiRequest(t, server, "", "GET", "/api/v1/agent-traces/unknown", "", 404)
	apiRequest(t, server, "", "GET", "/api/v1/agent-traces/bad%20id", "", 404)
}

func TestFR_TR_04_ListNewestFirstWithoutTurnAndLimit(t *testing.T) {
	_, server := application(t)
	for _, c := range []struct{ id, user string }{{"tr-a", "intern"}, {"tr-b", "other"}, {"tr-c", "intern"}} {
		apiRequest(t, server, "", "POST", "/api/v1/agent-traces", traceBody(c.id, c.user, "trigger", sampleTurn), 201)
	}
	all := listTraces(t, apiRequest(t, server, "", "GET", "/api/v1/agent-traces", "", 200))
	if len(all) != 3 || all[0]["id"] != "tr-c" || all[2]["id"] != "tr-a" {
		t.Fatal(all)
	}
	for _, trace := range all {
		if _, has := trace["turn"]; has {
			t.Fatal("list exposes turn")
		}
	}
	if mine := listTraces(t, apiRequest(t, server, "", "GET", "/api/v1/agent-traces?user_id=intern&limit=1", "", 200)); len(mine) != 1 || mine[0]["id"] != "tr-c" {
		t.Fatal(mine)
	}
	apiRequest(t, server, "", "GET", "/api/v1/agent-traces?limit=200", "", 200)
	for _, bad := range []string{"0", "201", "-1", "ten"} {
		apiRequest(t, server, "", "GET", "/api/v1/agent-traces?limit="+bad, "", 400)
	}
	apiRequest(t, server, "", "GET", "/api/v1/agent-traces?user_id="+strings.Repeat("u", 129), "", 400)
}
