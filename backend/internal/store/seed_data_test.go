package store

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
)

type brokenReader struct{}

func (brokenReader) Read([]byte) (int, error) { return 0, errors.New("secret reader failure") }
func TestST2_DATA_1_SeedSchemaValidation(t *testing.T) {
	for _, reader := range []io.Reader{strings.NewReader("["), strings.NewReader("synthetic: true\nunknown: x"), strings.NewReader("x: 1\n---\nx: 2"), strings.NewReader(strings.Repeat("x", (1<<20)+1)), brokenReader{}} {
		var target struct {
			Synthetic bool `yaml:"synthetic"`
		}
		if err := decodeYAML(reader, &target); err == nil {
			t.Fatal("accepted invalid YAML")
		}
	}
	for _, card := range []map[string]any{
		{}, {"synthetic": true, "team_id": "orders"},
		{"synthetic": true, "team_id": "orders", "name": "Team", "people": "bad"},
		{"synthetic": true, "team_id": "orders", "name": "Team", "people": []any{"bad"}},
		{"synthetic": true, "team_id": "orders", "name": "Team", "people": []any{map[string]any{"user_id": "/bad"}}},
		{"synthetic": true, "team_id": "orders", "name": "Team", "faq": []any{"unapproved"}},
	} {
		if err := validateCard(card); err == nil {
			t.Fatal(card)
		}
	}
	base := CatalogObject{ID: "repo", Name: "Repository", Instrument: "github", URL: "https://example.org/repo", RequestURL: "https://example.org/request", OwnerID: "mentor", Roles: []string{"intern"}}
	if err := validateObject(base); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*CatalogObject){
		func(o *CatalogObject) { o.ID = "/bad" }, func(o *CatalogObject) { o.Instrument = "unknown" },
		func(o *CatalogObject) { o.Roles = nil }, func(o *CatalogObject) { o.Roles = []string{"intern", "intern"} },
		func(o *CatalogObject) { o.Roles = []string{"admin"} }, func(o *CatalogObject) { o.RequestURL = "ftp://example.org" },
		func(o *CatalogObject) { o.Forbidden = true },
	} {
		object := base
		mutate(&object)
		if err := validateObject(object); err == nil {
			t.Fatal(object)
		}
	}
}
func TestST2_DATA_1_SeedRejectsInvalidDocumentsAtomically(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	tests := []struct{ users, card, catalog string }{
		{"users: []", cardYAML, catalogYAML},
		{"[", cardYAML, catalogYAML},
		{profilesYAML, "[", catalogYAML},
		{profilesYAML, "name: missing team", catalogYAML},
		{profilesYAML, cardYAML, "["},
		{profilesYAML, cardYAML, "synthetic: false\nteam_id: orders"},
		{profilesYAML, cardYAML, strings.Replace(catalogYAML, "instrument: github", "instrument: unknown", 1)},
		{profilesYAML, cardYAML, catalogYAML + "  - {id: repo, instrument: github, name: Duplicate, url: 'https://example.org/repo', request_url: 'https://example.org/request', owner_id: lead, roles: [intern]}\n"},
		{profilesYAML, strings.Replace(cardYAML, "team_id: orders", "team_id: unknown", 1), catalogYAML},
		{profilesYAML, strings.Replace(cardYAML, "user_id: mentor", "user_id: other", 1), catalogYAML},
		{profilesYAML, cardYAML, strings.Replace(catalogYAML, "team_id: orders", "team_id: unknown", 1)},
	}
	first, _ := s.TeamCard(ctx, intern, "", 0)
	for _, tc := range tests {
		if err := s.SeedData(ctx, strings.NewReader(tc.users), strings.NewReader(tc.card), strings.NewReader(tc.catalog)); err == nil {
			t.Fatalf("accepted %+v", tc)
		}
		last, _ := s.TeamCard(ctx, intern, "", 0)
		if last.Version != first.Version {
			t.Fatal("version changed")
		}
	}
}

func TestST2_DATA_1_SeedOptionalProfileFields(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	users := strings.Replace(profilesYAML, "team_id: orders, mentor_id: mentor}", "team_id: orders, mentor_id: mentor, logins: {github: synthetic-login}, internship_started_on: '2026-11-01'}", 1)
	if err := s.SeedData(ctx, strings.NewReader(users), nil, nil); err != nil {
		t.Fatal(err)
	}
	profile, err := s.UserProfile(ctx, intern)
	if err != nil || profile.Logins["github"] != "synthetic-login" || profile.InternshipWeek == nil {
		t.Fatal(profile, err)
	}
	if err := s.SeedData(ctx, strings.NewReader(profilesYAML), nil, nil); err != nil {
		t.Fatal(err)
	}
	preserved, err := s.UserProfile(ctx, intern)
	if err != nil || preserved.Logins["github"] != "synthetic-login" {
		t.Fatal(preserved, err)
	}
	for _, bad := range []string{
		strings.Replace(users, "2026-11-01", "invalid-date", 1),
		strings.Replace(users, "github: synthetic-login", "unknown: synthetic-login", 1),
		strings.Replace(users, "github: synthetic-login", "github: ''", 1),
	} {
		if err := s.SeedData(ctx, strings.NewReader(bad), nil, nil); err == nil {
			t.Fatal("invalid profile metadata accepted")
		}
	}
}
