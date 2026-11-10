package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"strings"

	"gopkg.in/yaml.v3"
)

// Seed imports profiles without changing bindings or invitations.
func (s *Store) Seed(ctx context.Context, source io.Reader) error {
	var input struct {
		Users []Profile `yaml:"users"`
	}
	decoder := yaml.NewDecoder(io.LimitReader(source, 1<<20))
	decoder.KnownFields(true)
	if err := decoder.Decode(&input); err != nil {
		return fmt.Errorf("seed: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("seed: expected one YAML document")
	}
	if err := validateSeed(input.Users); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// Serialize seed writes so concurrent imports cannot invalidate this cross-row check.
	// Reads remain available; binding writes wait until this short import completes.
	if _, err := tx.ExecContext(ctx, `LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE`); err != nil {
		return err
	}
	for _, user := range input.Users {
		if _, err := tx.ExecContext(ctx, `INSERT INTO teams(team_id) VALUES ($1) ON CONFLICT DO NOTHING`, user.TeamID); err != nil {
			return err
		}
		// Team moves require an explicit migration; reimport cannot silently change authorization.
		var oldTeam string
		lookupErr := tx.QueryRowContext(ctx, `SELECT team_id FROM users WHERE user_id=$1`, user.UserID).Scan(&oldTeam)
		if lookupErr != nil && !errors.Is(lookupErr, sql.ErrNoRows) {
			return lookupErr
		}
		if lookupErr == nil && oldTeam != user.TeamID {
			return fmt.Errorf("seed.users.%s.team_id: cannot move existing user", user.UserID)
		}
		_, err := tx.ExecContext(ctx, `INSERT INTO users(user_id, display_name, role, team_id, mentor_id)
			VALUES ($1,$2,$3,$4,nullif($5,'')) ON CONFLICT (user_id)
			DO UPDATE SET display_name=excluded.display_name, role=excluded.role, mentor_id=excluded.mentor_id`,
			user.UserID, user.DisplayName, user.Role, user.TeamID, user.MentorID)
		if err != nil {
			return err
		}
	}
	// Validate stored dependents too: a partial seed may demote their mentor.
	var dependentID string
	err = tx.QueryRowContext(ctx, `SELECT dependent.user_id FROM users AS dependent
		LEFT JOIN users AS mentor ON mentor.user_id = dependent.mentor_id
		WHERE dependent.mentor_id IS NOT NULL
		  AND (mentor.user_id IS NULL OR mentor.role <> 'mentor' OR mentor.team_id <> dependent.team_id)
		ORDER BY dependent.user_id LIMIT 1`).Scan(&dependentID)
	if err == nil {
		return fmt.Errorf("seed.users.%s.mentor_id: mentor must belong to the same team and have role mentor", dependentID)
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	return tx.Commit()
}

func validateSeed(users []Profile) error {
	if len(users) == 0 {
		return fmt.Errorf("seed.users: must not be empty")
	}
	byID := make(map[string]Profile, len(users))
	for i, u := range users {
		if strings.TrimSpace(u.UserID) == "" || strings.TrimSpace(u.DisplayName) == "" || strings.TrimSpace(u.TeamID) == "" {
			return fmt.Errorf("seed.users[%d]: user_id, display_name and team_id are required", i)
		}
		if _, exists := byID[u.UserID]; exists {
			return fmt.Errorf("seed.users[%d].user_id: duplicate", i)
		}
		if u.Role != "intern" && u.Role != "mentor" && u.Role != "teamlead" {
			return fmt.Errorf("seed.users[%d].role: unknown role", i)
		}
		byID[u.UserID] = u
	}
	for i, u := range users {
		if u.MentorID == "" {
			continue
		}
		mentor, exists := byID[u.MentorID]
		if !exists || mentor.Role != "mentor" || mentor.TeamID != u.TeamID || u.UserID == u.MentorID {
			return fmt.Errorf("seed.users[%d].mentor_id: mentor must belong to the same team", i)
		}
	}
	return nil
}
