package store

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"time"
)

const (
	defaultTraceRetentionDays = 30
	MaxAgentTraces            = 200
)

var tracePaths = map[string]bool{"agent": true, "router": true, "followup": true, "trigger": true}

// AgentTrace is one graph turn (FR-TR-02). Turn is omitted from list responses.
type AgentTrace struct {
	ID         string          `json:"id"`
	CreatedAt  time.Time       `json:"created_at"`
	UserID     string          `json:"user_id"`
	TeamID     string          `json:"team_id"`
	Role       string          `json:"role"`
	Demo       bool            `json:"demo"`
	Path       string          `json:"path"`
	Fallback   bool            `json:"fallback"`
	Case       string          `json:"case"`
	DurationMS int64           `json:"duration_ms"`
	LLMCalls   int64           `json:"llm_calls"`
	ToolCalls  int64           `json:"tool_calls"`
	Turn       json.RawMessage `json:"turn,omitempty"`
}

// ConfigureAgentTraceRetention is called once at startup (FR-TR-03).
func (s *Store) ConfigureAgentTraceRetention(getenv func(string) string) error {
	days := defaultTraceRetentionDays
	if raw := getenv("AGENT_TRACE_RETENTION_DAYS"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 36500 {
			return errors.New("invalid AGENT_TRACE_RETENTION_DAYS: expected integer from 1 to 36500")
		}
		days = parsed
	}
	s.traceRetentionDays = days
	return nil
}
func (s *Store) retentionDays() int {
	if s.traceRetentionDays < 1 {
		return defaultTraceRetentionDays
	}
	return s.traceRetentionDays
}

func validCount(v int64) bool { return v >= 0 && v <= math.MaxInt32 }
func validTrace(t AgentTrace) bool {
	turn := bytes.TrimSpace(t.Turn)
	return ValidID(t.ID) && validText(t.UserID, 128) && validText(t.TeamID, 128) && len(t.Role) <= 64 &&
		tracePaths[t.Path] && len(t.Case) <= 128 && validCount(t.DurationMS) && validCount(t.LLMCalls) &&
		validCount(t.ToolCalls) && len(turn) > 0 && turn[0] == '{' && json.Valid(turn)
}

// SaveAgentTrace stores a trace once per id and prunes traces past retention in the same transaction.
func (s *Store) SaveAgentTrace(ctx context.Context, t AgentTrace) error {
	if !validTrace(t) {
		return ErrInvalid
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `DELETE FROM agent_traces WHERE created_at < now() - make_interval(days => $1)`, s.retentionDays()); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO agent_traces(id,user_id,team_id,role,demo,path,fallback,case_name,duration_ms,llm_calls,tool_calls,turn)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb) ON CONFLICT (id) DO NOTHING`,
		t.ID, t.UserID, t.TeamID, t.Role, t.Demo, t.Path, t.Fallback, t.Case, t.DurationMS, t.LLMCalls, t.ToolCalls, string(t.Turn)); err != nil {
		return err
	}
	return tx.Commit()
}

const traceColumns = `id,created_at,user_id,team_id,role,demo,path,fallback,case_name,duration_ms,llm_calls,tool_calls`

type scanner interface{ Scan(...any) error }

func scanTrace(row scanner, extra ...any) (AgentTrace, error) {
	var t AgentTrace
	err := row.Scan(append([]any{&t.ID, &t.CreatedAt, &t.UserID, &t.TeamID, &t.Role, &t.Demo, &t.Path, &t.Fallback, &t.Case, &t.DurationMS, &t.LLMCalls, &t.ToolCalls}, extra...)...)
	t.CreatedAt = t.CreatedAt.UTC()
	return t, err
}

// AgentTraces lists the newest traces without turn, optionally for one user.
func (s *Store) AgentTraces(ctx context.Context, userID string, limit int) ([]AgentTrace, error) {
	if limit < 1 || limit > MaxAgentTraces || len(userID) > 128 {
		return nil, ErrInvalid
	}
	rows, err := s.db.QueryContext(ctx, `SELECT `+traceColumns+` FROM agent_traces WHERE ($1='' OR user_id=$1) ORDER BY created_at DESC, id DESC LIMIT $2`, userID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []AgentTrace{}
	for rows.Next() {
		t, err := scanTrace(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// AgentTrace returns one full trace including turn.
func (s *Store) AgentTrace(ctx context.Context, id string) (AgentTrace, error) {
	if !ValidID(id) {
		return AgentTrace{}, ErrNotFound
	}
	var turn []byte
	t, err := scanTrace(s.db.QueryRowContext(ctx, `SELECT `+traceColumns+`,turn FROM agent_traces WHERE id=$1`, id), &turn)
	if err != nil {
		return AgentTrace{}, noRows(err)
	}
	t.Turn = turn
	return t, nil
}
