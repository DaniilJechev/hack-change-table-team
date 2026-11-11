package api

import (
	"encoding/json"
	"net/http"
	"testing"
)

func TestST4_BRIEF_1_RESTStateSettingsAndDelivery(t *testing.T) {
	_, server := application(t)
	for _, path := range []string{"/api/v1/brief-state", "/api/v1/brief-settings", "/api/v1/brief-stats"} {
		apiRequest(t, server, "", "GET", path, "", 401)
		apiRequest(t, server, "intern", "GET", path, "", 200)
	}
	for _, body := range []string{`{}`, `{"time":null}`, `{"time":"25:00"}`, `{"quiet":"on"}`, `{"user_id":"other","quiet":true}`} {
		apiRequest(t, server, "intern", "PATCH", "/api/v1/brief-settings", body, 400)
	}
	apiRequest(t, server, "intern", "PATCH", "/api/v1/brief-settings", `{"time":"08:15","quiet":true}`, 200)
	apiRequest(t, server, "intern", "PATCH", "/api/v1/brief-settings", `{"time":""}`, 200)
	apiRequest(t, server, "other", "GET", "/api/v1/brief-settings", "", 200)
	body := apiRequest(t, server, "intern", "POST", "/api/v1/briefs", `{"id":"b","items":[{"id":"i","source":"jira","type":"mention","title":"Synthetic","url":"https://example.org/issue#comment","priority_class":"people","created_at":"2026-11-11T10:00:00Z"}]}`, 200)
	var b struct {
		Tokens map[string]string `json:"redirect_tokens"`
	}
	if err := json.Unmarshal([]byte(body), &b); err != nil {
		t.Fatal(err)
	}
	path := "/r/" + b.Tokens["i"]
	client := *server.Client()
	client.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	request := func(method, path string, want int) {
		t.Helper()
		req, err := http.NewRequest(method, server.URL+path, nil)
		if err != nil {
			t.Fatal(err)
		}
		res, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		if res.StatusCode != want {
			t.Fatal(res.StatusCode, want)
		}
		if res.Header.Get("Cache-Control") != "no-store" || res.Header.Get("Referrer-Policy") != "no-referrer" {
			t.Fatal("missing privacy headers")
		}
		if want == 302 && res.Header.Get("Location") != "https://example.org/issue#comment" {
			t.Fatal(res.Header)
		}
	}
	request("GET", path, 404)
	request("GET", "/r/unknown", 404)
	apiRequest(t, server, "other", "PATCH", "/api/v1/briefs/b", `{"status":"sent","calendar":{}}`, 404)
	apiRequest(t, server, "intern", "PATCH", "/api/v1/briefs/b", `{"status":"failed","calendar":{}}`, 400)
	apiRequest(t, server, "intern", "PATCH", "/api/v1/briefs/b", `{"status":"sent","calendar":{"events":{}}}`, 200)
	request("HEAD", path, 302)
	request("GET", path+"?url=https://evil.example", 302)
	request("GET", path, 302)
	apiRequest(t, server, "intern", "GET", "/api/v1/brief-state", "", 200)
	apiRequest(t, server, "mentor", "GET", "/api/v1/brief-stats", "", 200)
}
func TestST4_METRICS_1_AuditAndQuestionCapabilityREST(t *testing.T) {
	_, server := application(t)
	for _, bad := range []string{`{}`, `{"id":"c","case":"brief_now","server":"github","tool":"list_pull_requests","outcome":"ok","arguments":{}}`, `{"id":"c","case":"brief_now","server":"github","tool":"bad token","outcome":"ok"}`} {
		apiRequest(t, server, "intern", "POST", "/api/v1/mcp-calls", bad, 400)
	}
	body := `{"id":"c","case":"brief_now","server":"github","tool":"list_pull_requests","outcome":"ok"}`
	apiRequest(t, server, "", "POST", "/api/v1/mcp-calls", body, 401)
	apiRequest(t, server, "intern", "POST", "/api/v1/mcp-calls", body, 200)
	apiRequest(t, server, "intern", "POST", "/api/v1/mcp-calls", body, 200)
	apiRequest(t, server, "intern", "POST", "/api/v1/mcp-calls", `{"id":"c","case":"brief_now","server":"github","tool":"list_pull_requests","outcome":"tool_denied"}`, 409)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"h3","text":"Synthetic?","capability":"h3"}`, 200)
	apiRequest(t, server, "intern", "POST", "/api/v1/questions", `{"id":"h4","text":"Synthetic?","capability":"h4"}`, 400)
}
