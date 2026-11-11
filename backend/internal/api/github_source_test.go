package api

import (
	"strings"
	"testing"
)

func TestGH_SOURCE_1_RESTProjectsConfiguredTeamOnly(t *testing.T) {
	s, server := application(t)
	env := map[string]string{"GITHUB_REPOSITORY": "DaniilJechev/hack-change-table-team", "GITHUB_TEAM_ID": "orders", "GITHUB_CATALOG_OBJECT_ID": "repo", "GITHUB_USER_LOGINS": `{"intern":"synthetic-user","other":"other-user"}`}
	if err := s.ConfigureGitHubSource(func(k string) string { return env[k] }); err != nil {
		t.Fatal(err)
	}
	profile := apiRequest(t, server, "intern", "GET", "/api/v1/profile", "", 200)
	if !strings.Contains(profile, `"github":"synthetic-user"`) {
		t.Fatal(profile)
	}
	catalog := apiRequest(t, server, "intern", "GET", "/api/v1/access-catalog", "", 200)
	if !strings.Contains(catalog, `"repo":"hack-change-table-team"`) || !strings.Contains(catalog, "https://github.com/DaniilJechev/hack-change-table-team") {
		t.Fatal(catalog)
	}
	foreign := apiRequest(t, server, "other", "GET", "/api/v1/profile", "", 200)
	if strings.Contains(foreign, "other-user") {
		t.Fatal("cross-team GitHub mapping leaked")
	}
	foreign = apiRequest(t, server, "other", "GET", "/api/v1/access-catalog", "", 200)
	if strings.Contains(foreign, "hack-change-table-team") {
		t.Fatal("cross-team repository leaked")
	}
}
