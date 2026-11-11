package store

import (
	"context"
	"strings"
	"testing"
)

func githubEnv(overrides map[string]string) func(string) string {
	env := map[string]string{"GITHUB_REPOSITORY": "DaniilJechev/hack-change-table-team", "GITHUB_TEAM_ID": "orders", "GITHUB_CATALOG_OBJECT_ID": "repo", "GITHUB_USER_LOGINS": `{"intern":"synthetic-user","other":"other-user"}`}
	for k, v := range overrides {
		env[k] = v
	}
	return func(k string) string { return env[k] }
}
func TestGH_SOURCE_1_ConfigValidation(t *testing.T) {
	for _, change := range []map[string]string{
		{"GITHUB_REPOSITORY": "https://github.com/a/b"}, {"GITHUB_REPOSITORY": "a/b.git"}, {"GITHUB_REPOSITORY": "a/.."},
		{"GITHUB_REPOSITORY": "", "GITHUB_USER_LOGINS": "{}"}, {"GITHUB_TEAM_ID": ""}, {"GITHUB_CATALOG_OBJECT_ID": ""},
		{"GITHUB_USER_LOGINS": "["}, {"GITHUB_USER_LOGINS": "null"}, {"GITHUB_USER_LOGINS": `{"intern":"bad_login"}`},
		{"GITHUB_USER_LOGINS": `{"bad user":"good"}`}, {"GITHUB_USER_LOGINS": strings.Repeat("s", 16385)},
	} {
		s := &Store{}
		err := s.ConfigureGitHubSource(githubEnv(change))
		if err == nil || strings.Contains(err.Error(), change["GITHUB_USER_LOGINS"]) && change["GITHUB_USER_LOGINS"] != "" {
			t.Fatalf("unsafe/missing error: %v", err)
		}
	}
	s := &Store{}
	if err := s.ConfigureGitHubSource(func(string) string { return "" }); err != nil || s.github != nil {
		t.Fatal(err)
	}
	if err := s.ConfigureGitHubSource(githubEnv(map[string]string{"GITHUB_USER_LOGINS": ""})); err != nil {
		t.Fatal(err)
	}
}
func TestGH_SOURCE_1_IsolationRolesAndSeedPreservation(t *testing.T) {
	s, intern, mentor, _, other := dataStore(t)
	ctx := context.Background()
	if err := s.ConfigureGitHubSource(githubEnv(nil)); err != nil {
		t.Fatal(err)
	}
	for n := 0; n < 2; n++ {
		if err := s.SeedData(ctx, strings.NewReader(profilesYAML), strings.NewReader(cardYAML), strings.NewReader(catalogYAML)); err != nil {
			t.Fatal(err)
		}
		p, err := s.UserProfile(ctx, intern)
		if err != nil || p.Logins["github"] != "synthetic-user" {
			t.Fatal(p, err)
		}
		objects, err := s.AccessCatalog(ctx, intern, nil)
		if err != nil {
			t.Fatal(err)
		}
		for _, o := range objects {
			if o.ID == "repo" && (o.Target["owner"] != "DaniilJechev" || o.Target["repo"] != "hack-change-table-team" || o.URL != "https://github.com/DaniilJechev/hack-change-table-team") {
				t.Fatal(o)
			}
			if o.ID == "prod" && o.Target != nil {
				t.Fatal("forbidden object overridden")
			}
		}
	}
	p, err := s.UserProfile(ctx, other)
	if err != nil || p.Logins["github"] != "" {
		t.Fatal("other team mapping leaked", p, err)
	}
	if _, err := s.db.Exec(`UPDATE users SET logins='{"github":"demo-mentor","jira":"synthetic.jira"}' WHERE user_id='mentor'`); err != nil {
		t.Fatal(err)
	}
	p, err = s.UserProfile(ctx, mentor)
	if err != nil || p.Logins["github"] != "" || p.Logins["jira"] != "synthetic.jira" {
		t.Fatal("synthetic GitHub login used", p, err)
	}
	obj := CatalogObject{ID: "repo", Instrument: "github", URL: "https://example.org/other"}
	s.githubObject(other, &obj)
	if obj.URL != "https://example.org/other" {
		t.Fatal("cross-team target leaked")
	}
	if err := s.ConfigureGitHubSource(func(string) string { return "" }); err != nil {
		t.Fatal(err)
	}
	objects, err := s.AccessCatalog(ctx, intern, []string{"repo"})
	if err != nil || objects[0].Target != nil || objects[0].URL != "https://example.org/repo" {
		t.Fatal("seed modified", objects, err)
	}
}
