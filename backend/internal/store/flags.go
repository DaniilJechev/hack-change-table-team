package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strconv"
	"strings"
)

var flagNames = []string{"access_monitoring", "team_escalation", "faq_publication"}

// ConfigureFlagDefaults is called once at startup, before serving any requests.
func (s *Store) ConfigureFlagDefaults(getenv func(string) string) error {
	defaults := map[string]bool{}
	for _, name := range flagNames {
		value := true
		if raw := getenv("FEATURE_" + strings.ToUpper(name)); raw != "" {
			parsed, err := strconv.ParseBool(raw)
			if err != nil {
				return fmt.Errorf("invalid FEATURE_%s: expected boolean", strings.ToUpper(name))
			}
			value = parsed
		}
		defaults[name] = value
	}
	s.flagDefaults = defaults
	return nil
}
func (s *Store) defaultFlag(name string) bool {
	if s.flagDefaults == nil {
		return true
	}
	return s.flagDefaults[name]
}
func knownFlag(name string) bool {
	for _, n := range flagNames {
		if n == name {
			return true
		}
	}
	return false
}

func (s *Store) Flags(ctx context.Context, actor Profile) (map[string]bool, error) {
	out := map[string]bool{}
	for _, name := range flagNames {
		out[name] = s.defaultFlag(name)
	}
	rows, err := s.db.QueryContext(ctx, `SELECT name,enabled FROM team_flags WHERE team_id=$1`, actor.TeamID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var name string
		var enabled bool
		if err := rows.Scan(&name, &enabled); err != nil {
			return nil, err
		}
		out[name] = enabled
	}
	return out, rows.Err()
}
func (s *Store) SetFlag(ctx context.Context, actor Profile, name string, enabled bool) error {
	if actor.Role != "teamlead" {
		return ErrForbidden
	}
	if !knownFlag(name) {
		return ErrInvalid
	}
	// Recheck the persistent role and team in the statement that writes the flag.
	var saved bool
	err := s.db.QueryRowContext(ctx, `INSERT INTO team_flags(team_id,name,enabled,updated_by)
 SELECT team_id,$3,$4,user_id FROM users WHERE user_id=$1 AND team_id=$2 AND role='teamlead'
 ON CONFLICT(team_id,name) DO UPDATE SET enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=now()
 RETURNING enabled`, actor.UserID, actor.TeamID, name, enabled).Scan(&saved)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrForbidden
	}
	return err
}
func (s *Store) requireFlag(ctx context.Context, actor Profile, name string) error {
	var enabled bool
	err := s.db.QueryRowContext(ctx, `SELECT coalesce((SELECT enabled FROM team_flags WHERE team_id=$1 AND name=$2),$3)`, actor.TeamID, name, s.defaultFlag(name)).Scan(&enabled)
	if err != nil {
		return err
	}
	if !enabled {
		return ErrForbidden
	}
	return nil
}
