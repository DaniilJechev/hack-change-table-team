package store

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"strings"
)

type MockRule struct {
	TeamID    string            `json:"team_id" yaml:"team_id"`
	ObjectID  string            `json:"object_id" yaml:"object_id"`
	Status    string            `json:"status" yaml:"status"`
	Reason    string            `json:"reason" yaml:"reason"`
	ErrorCode string            `json:"error_code" yaml:"error_code"`
	DemoSteps map[string]string `json:"demo_steps,omitempty" yaml:"demo_steps,omitempty"`
}
type mockSeed struct {
	Synthetic bool       `yaml:"synthetic"`
	Rules     []MockRule `yaml:"access_rules"`
}

func decodeMockSeed(source io.Reader) ([]MockRule, error) {
	var input mockSeed
	if err := decodeYAML(source, &input); err != nil {
		return nil, err
	}
	if !input.Synthetic {
		return nil, fmt.Errorf("seed.mock: synthetic=true required")
	}
	seen := map[string]bool{}
	for i, rule := range input.Rules {
		key := rule.TeamID + "/" + rule.ObjectID
		validCode := rule.ErrorCode == "service_unavailable" || rule.ErrorCode == "service_unconfigured" || rule.ErrorCode == "rate_limited"
		validStatus := rule.Status == "granted" || rule.Status == "pending" || rule.Status == "denied" || rule.Status == "error"
		if !ValidID(rule.TeamID) || !ValidID(rule.ObjectID) || seen[key] || !validStatus || len(rule.Reason) > 1000 || (rule.Status == "denied" && strings.TrimSpace(rule.Reason) == "") || (rule.Status == "error" && !validCode) || (rule.Status != "error" && rule.ErrorCode != "") {
			return nil, fmt.Errorf("seed.mock.access_rules[%d]: invalid rule", i)
		}
		for step, status := range rule.DemoSteps {
			if (step != "step_1" && step != "step_2" && step != "step_3") || (status != "granted" && status != "pending" && status != "denied" && status != "error") {
				return nil, fmt.Errorf("seed.mock.access_rules[%d]: invalid demo step", i)
			}
		}
		seen[key] = true
	}
	return input.Rules, nil
}
func seedMockRules(ctx context.Context, tx *sql.Tx, rules []MockRule) error {
	for _, rule := range rules {
		var exists bool
		if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM access_catalog WHERE team_id=$1 AND object_id=$2)`, rule.TeamID, rule.ObjectID).Scan(&exists); err != nil {
			return err
		}
		if !exists {
			return fmt.Errorf("seed.mock: unknown team object")
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO mock_access_rules(team_id,object_id,status,reason,error_code) VALUES($1,$2,$3,$4,$5)
        ON CONFLICT(team_id,object_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,error_code=excluded.error_code`, rule.TeamID, rule.ObjectID, rule.Status, rule.Reason, rule.ErrorCode); err != nil {
			return err
		}
	}
	return nil
}

// SeedMockServices is also available for importing rules into an already seeded installation.
func (s *Store) SeedMockServices(ctx context.Context, source io.Reader) error {
	rules, err := decodeMockSeed(source)
	if err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = seedMockRules(ctx, tx, rules); err != nil {
		return err
	}
	return tx.Commit()
}
