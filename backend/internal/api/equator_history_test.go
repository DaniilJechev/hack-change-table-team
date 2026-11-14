package api

import (
	"strings"
	"testing"
)

func TestFR_H4_12_RESTHistoryAndRights(t *testing.T) {
	_, server := application(t)
	apiRequest(t, server, "", "GET", "/api/v1/equator/checks", "", 401)
	apiRequest(t, server, "intern", "POST", "/api/v1/equator/invalidate", "", 200)
	path := "/api/v1/equator/checks/h4-one"
	for _, bad := range []string{"{", "null", "{}", `{"id":"other"}`, `{"id":"h4-one","passed":true,"generation":-1}`} {
		apiRequest(t, server, "intern", "PUT", path, bad, 400)
	}
	apiRequest(t, server, "intern", "PUT", path, `{"id":"h4-one","user_id":"other","passed":true,"generation":1}`, 403)
	apiRequest(t, server, "intern", "PUT", path, `{"id":"h4-one","passed":true,"generation":1,"current":true}`, 200)
	list := apiRequest(t, server, "intern", "GET", "/api/v1/equator/checks", "", 200)
	if !strings.Contains(list, "h4-one") {
		t.Fatal(list)
	}
	list = apiRequest(t, server, "other", "GET", "/api/v1/equator/checks", "", 200)
	if strings.Contains(list, "h4-one") {
		t.Fatal(list)
	}
	apiRequest(t, server, "intern", "POST", path+"/claim", `{"part":"file","user_id":"other"}`, 400)
	apiRequest(t, server, "intern", "POST", path+"/claim", `{"part":"file"}`, 200)
	apiRequest(t, server, "intern", "POST", path+"/claim", `{"part":"file"}`, 409)
	apiRequest(t, server, "intern", "PATCH", path+"/delivery", `{"part":"file","state":"sent"}`, 200)
	apiRequest(t, server, "intern", "PATCH", path+"/delivery", `{"part":"file","state":"bad"}`, 400)
	apiRequest(t, server, "other", "POST", path+"/claim", `{"part":"file"}`, 404)
	apiRequest(t, server, "intern", "GET", "/api/v1/equator/mentor-chat", "", 200)
	apiRequest(t, server, "intern", "DELETE", "/api/v1/equator/checks", "", 200)
	apiRequest(t, server, "intern", "POST", path+"/claim", `{"part":"report"}`, 404)
}
