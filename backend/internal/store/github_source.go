package store

import (
	"encoding/json"
	"errors"
	"regexp"
	"strings"
)

type githubSource struct {
	team, object, owner, repo string
	logins                    map[string]string
}

var githubLogin = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$`)
var githubRepo = regexp.MustCompile(`^[A-Za-z0-9._-]{1,100}$`)

// ConfigureGitHubSource is called once before serving. It never edits synthetic seed.
func (s *Store) ConfigureGitHubSource(getenv func(string) string) error {
	repository := getenv("GITHUB_REPOSITORY")
	rawLogins := getenv("GITHUB_USER_LOGINS")
	if repository == "" {
		if rawLogins != "" {
			return errors.New("GITHUB_USER_LOGINS requires GITHUB_REPOSITORY")
		}
		s.github = nil
		return nil
	}
	parts := strings.Split(repository, "/")
	if len(parts) != 2 || !githubLogin.MatchString(parts[0]) || !githubRepo.MatchString(parts[1]) || parts[1] == "." || parts[1] == ".." || strings.HasSuffix(parts[1], ".git") {
		return errors.New("GITHUB_REPOSITORY must be owner/repo without URL or .git suffix")
	}
	team, object := getenv("GITHUB_TEAM_ID"), getenv("GITHUB_CATALOG_OBJECT_ID")
	if !ValidID(team) || !ValidID(object) {
		return errors.New("GITHUB_TEAM_ID and GITHUB_CATALOG_OBJECT_ID are required")
	}
	logins := map[string]string{}
	if rawLogins != "" {
		if len(rawLogins) > 16384 || json.Unmarshal([]byte(rawLogins), &logins) != nil || logins == nil || len(logins) > 100 {
			return errors.New("invalid GITHUB_USER_LOGINS mapping")
		}
		for id, login := range logins {
			if !ValidID(id) || !githubLogin.MatchString(login) {
				return errors.New("invalid GITHUB_USER_LOGINS mapping")
			}
		}
	}
	s.github = &githubSource{team: team, object: object, owner: parts[0], repo: parts[1], logins: logins}
	return nil
}

func (s *Store) githubProfile(p *UserProfile) {
	if s.github == nil || p.TeamID != s.github.team {
		return
	}
	delete(p.Logins, "github")
	if login, ok := s.github.logins[p.UserID]; ok {
		p.Logins["github"] = login
	}
}
func (s *Store) githubObject(actor Profile, o *CatalogObject) {
	g := s.github
	if g == nil || actor.TeamID != g.team || o.ID != g.object || o.Instrument != "github" || o.Forbidden {
		return
	}
	o.Name = "GitHub: " + g.owner + "/" + g.repo
	o.URL = "https://github.com/" + g.owner + "/" + g.repo
	o.Target = map[string]any{"owner": g.owner, "repo": g.repo, "keywords": []string{g.repo, "github", "репозиторий"}}
}
