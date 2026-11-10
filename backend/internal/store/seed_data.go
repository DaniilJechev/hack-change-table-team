package store

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"time"

	"gopkg.in/yaml.v3"
)

func decodeYAML(source io.Reader, target any) error {
	raw, err := io.ReadAll(io.LimitReader(source, (1<<20)+1))
	if err != nil {
		return fmt.Errorf("seed: cannot read document")
	}
	if len(raw) > 1<<20 {
		return fmt.Errorf("seed: document exceeds 1 MiB")
	}
	decoder := yaml.NewDecoder(bytes.NewReader(raw))
	decoder.KnownFields(true)
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("seed: invalid YAML schema")
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("seed: expected one YAML document")
	}
	return nil
}

// SeedData imports existing users and team documents in one transaction. An absent
// document is allowed for identity-only CLI callers. It never erases personal data.
func (s *Store) SeedData(ctx context.Context, users, cardSource, catalogSource io.Reader) error {
	var input struct {
		Users []struct {
			Profile             `yaml:",inline"`
			Logins              map[string]string `yaml:"logins"`
			InternshipStartedOn string            `yaml:"internship_started_on"`
		} `yaml:"users"`
	}
	if err := decodeYAML(users, &input); err != nil {
		return err
	}
	profiles := make([]Profile, len(input.Users))
	for i, user := range input.Users {
		profiles[i] = user.Profile
		if user.InternshipStartedOn != "" {
			if _, err := time.Parse("2006-01-02", user.InternshipStartedOn); err != nil {
				return fmt.Errorf("seed.users[%d].internship_started_on: expected YYYY-MM-DD", i)
			}
		}
		for name, value := range user.Logins {
			switch name {
			case "github", "jira", "miro", "google", "telegram":
			default:
				return fmt.Errorf("seed.users[%d].logins: unknown service", i)
			}
			if !validText(value, 200) {
				return fmt.Errorf("seed.users[%d].logins.%s: nonempty value required", i, name)
			}
		}
	}
	if err := validateSeed(profiles); err != nil {
		return err
	}
	var card map[string]any
	if cardSource != nil {
		if err := decodeYAML(cardSource, &card); err != nil {
			return err
		}
		if err := validateCard(card); err != nil {
			return err
		}
	}
	var catalog struct {
		Synthetic   bool            `yaml:"synthetic"`
		Notice      string          `yaml:"notice"`
		TeamID      string          `yaml:"team_id"`
		Instruments []string        `yaml:"instruments"`
		Objects     []CatalogObject `yaml:"objects"`
	}
	if catalogSource != nil {
		if err := decodeYAML(catalogSource, &catalog); err != nil {
			return err
		}
		if !catalog.Synthetic || !ValidID(catalog.TeamID) {
			return fmt.Errorf("seed.catalog: synthetic=true and team_id required")
		}
		seen := map[string]bool{}
		for i, o := range catalog.Objects {
			if err := validateObject(o); err != nil {
				return fmt.Errorf("seed.catalog.objects[%d]: %w", i, err)
			}
			if seen[o.ID] {
				return fmt.Errorf("seed.catalog.objects[%d].id: duplicate", i)
			}
			seen[o.ID] = true
		}
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := seedProfiles(ctx, tx, profiles); err != nil {
		return err
	}

	for _, user := range input.Users {
		if user.Logins != nil {
			raw, _ := json.Marshal(user.Logins)
			if _, err := tx.ExecContext(ctx, `UPDATE users SET logins=$2 WHERE user_id=$1`, user.UserID, string(raw)); err != nil {
				return err
			}
		}
		if user.InternshipStartedOn != "" {
			if _, err := tx.ExecContext(ctx, `UPDATE users SET internship_started_on=$2::date WHERE user_id=$1`, user.UserID, user.InternshipStartedOn); err != nil {
				return err
			}
		}
	}
	if card != nil {
		team := card["team_id"].(string)
		if err := teamExists(ctx, tx, team); err != nil {
			return err
		}
		if people, ok := card["people"].([]any); ok {
			for i, p := range people {
				person := p.(map[string]any)
				var found bool
				if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM users WHERE user_id=$1 AND team_id=$2)`, person["user_id"], team).Scan(&found); err != nil {
					return err
				}
				if !found {
					return fmt.Errorf("seed.card.people[%d].user_id: unknown team member", i)
				}
			}
		}
		raw, err := json.Marshal(card)
		if err != nil {
			return fmt.Errorf("seed.card: unsupported value")
		}
		// Serialize imports via the users lock; compare with latest, so A -> B -> A is a new version.
		if _, err := tx.ExecContext(ctx, `INSERT INTO team_cards(team_id,document) SELECT $1,$2::jsonb
  WHERE NOT EXISTS(SELECT 1 FROM (SELECT document FROM team_cards WHERE team_id=$1 ORDER BY version DESC LIMIT 1) AS latest WHERE document=$2::jsonb)`, team, string(raw)); err != nil {
			return err
		}
	}
	if catalogSource != nil {
		if err := teamExists(ctx, tx, catalog.TeamID); err != nil {
			return err
		}
		for _, o := range catalog.Objects {
			var found bool
			if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM users WHERE user_id=$1 AND team_id=$2)`, o.OwnerID, catalog.TeamID).Scan(&found); err != nil {
				return err
			}
			if !found {
				return fmt.Errorf("seed.catalog.objects.%s.owner_id: unknown team member", o.ID)
			}
			raw, _ := json.Marshal(o)
			if _, err := tx.ExecContext(ctx, `INSERT INTO access_catalog(team_id,object_id,document) VALUES($1,$2,$3)
   ON CONFLICT(team_id,object_id) DO UPDATE SET document=excluded.document`, catalog.TeamID, o.ID, string(raw)); err != nil {
				return err
			}
		}
	}
	return tx.Commit()
}
func teamExists(ctx context.Context, tx *sql.Tx, id string) error {
	var exists bool
	if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM teams WHERE team_id=$1)`, id).Scan(&exists); err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("seed.team_id: unknown team")
	}
	return nil
}
func validateCard(card map[string]any) error {
	team, ok := card["team_id"].(string)
	if !ok || !ValidID(team) || card["synthetic"] != true {
		return fmt.Errorf("seed.card: synthetic=true and team_id required")
	}
	if name, ok := card["name"].(string); !ok || !validText(name, 200) {
		return fmt.Errorf("seed.card.name: required")
	}
	for _, key := range []string{"people", "routing", "do_not_disturb", "tools", "links"} {
		if value, ok := card[key]; ok {
			if _, ok := value.([]any); !ok {
				return fmt.Errorf("seed.card.%s: expected list", key)
			}
		}
	}
	if people, ok := card["people"].([]any); ok {
		for i, p := range people {
			person, ok := p.(map[string]any)
			if !ok {
				return fmt.Errorf("seed.card.people[%d]: expected mapping", i)
			}
			id, ok := person["user_id"].(string)
			if !ok || !ValidID(id) {
				return fmt.Errorf("seed.card.people[%d].user_id: required", i)
			}
		}
	}
	// Seed FAQ content is deliberately deferred to author-approved REST publication.
	if faq, ok := card["faq"]; ok && !reflect.DeepEqual(faq, []any{}) {
		return fmt.Errorf("seed.card.faq: use approved FAQ REST publication")
	}
	return nil
}
func validateObject(o CatalogObject) error {
	if !ValidID(o.ID) || !ValidID(o.OwnerID) || !validText(o.Name, 200) || !validURL(o.URL) {
		return fmt.Errorf("id, owner_id, name and http(s) url required")
	}
	switch o.Instrument {
	case "github", "jira", "miro", "google_calendar", "telegram":
	default:
		return fmt.Errorf("instrument: unknown")
	}
	if len(o.Roles) == 0 {
		return fmt.Errorf("roles: must not be empty")
	}
	seen := map[string]bool{}
	for _, r := range o.Roles {
		if seen[r] || r != "intern" && r != "mentor" && r != "teamlead" {
			return fmt.Errorf("roles: invalid or duplicate role")
		}
		seen[r] = true
	}
	if o.Forbidden {
		if !validText(o.Reason, 1000) {
			return fmt.Errorf("reason: required for forbidden object")
		}
	} else if !validURL(o.RequestURL) {
		return fmt.Errorf("request_url: http(s) url required")
	}
	return nil
}
