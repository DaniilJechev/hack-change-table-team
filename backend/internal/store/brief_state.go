package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"regexp"
	"time"
)

// ResolveBriefLink treats the random token as a capability, never as a user ID.
// HEAD checks the target but does not count a preview as a read.
func (s *Store) ResolveBriefLink(ctx context.Context, token string, record bool) (string, error) {
	if len(token) != 64 {
		return "", ErrNotFound
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	var user, id, item, target string
	err = tx.QueryRowContext(ctx, `SELECT l.user_id,l.brief_id,l.item_id,l.url FROM brief_links l
 JOIN briefs b USING(user_id,brief_id) WHERE l.token=$1 AND b.status='sent' FOR UPDATE OF b`, token).Scan(&user, &id, &item, &target)
	if err != nil {
		return "", noRows(err)
	}
	if !validURL(target) {
		return "", ErrInvalid
	}
	if record {
		if _, err = tx.ExecContext(ctx, `INSERT INTO brief_events(user_id,event_id,brief_id,kind,item_id)
 VALUES($1,$2,$3,'click',$4) ON CONFLICT(user_id,event_id) DO NOTHING`, user, "redirect-"+token, id, item); err != nil {
			return "", err
		}
		if _, err = tx.ExecContext(ctx, `UPDATE briefs SET read_at=coalesce(read_at,clock_timestamp()) WHERE user_id=$1 AND brief_id=$2`, user, id); err != nil {
			return "", err
		}
	}
	return target, tx.Commit()
}

type BriefState struct {
	LastSent *time.Time     `json:"last_sent"`
	Seen     []string       `json:"seen"`
	Calendar map[string]any `json:"calendar"`
}

func (s *Store) BriefState(ctx context.Context, actor Profile) (BriefState, error) {
	out := BriefState{Seen: []string{}}
	var raw []byte
	err := s.db.QueryRowContext(ctx, `SELECT max(sent_at),
 (SELECT calendar FROM briefs WHERE user_id=$1 AND status='sent' AND calendar IS NOT NULL ORDER BY sent_at DESC,brief_id DESC LIMIT 1)
 FROM briefs WHERE user_id=$1 AND status='sent'`, actor.UserID).Scan(&out.LastSent, &raw)
	if err != nil {
		return out, err
	}
	if raw != nil {
		if err = json.Unmarshal(raw, &out.Calendar); err != nil {
			return out, err
		}
	}
	rows, err := s.db.QueryContext(ctx, `SELECT DISTINCT i->>'id' FROM briefs b,jsonb_array_elements(b.items) i
 WHERE b.user_id=$1 AND b.status='sent' ORDER BY 1`, actor.UserID)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return out, err
		}
		out.Seen = append(out.Seen, id)
	}
	return out, rows.Err()
}

type BriefSettings struct {
	Time  *string `json:"time"`
	Quiet bool    `json:"quiet"`
}

var briefTime = regexp.MustCompile(`^([01][0-9]|2[0-3]):[0-5][0-9]$`)

func (s *Store) BriefSettings(ctx context.Context, actor Profile) (BriefSettings, error) {
	var out BriefSettings
	err := s.db.QueryRowContext(ctx, `SELECT time,quiet FROM brief_settings WHERE user_id=$1`, actor.UserID).Scan(&out.Time, &out.Quiet)
	if errors.Is(err, sql.ErrNoRows) {
		err = nil
	}
	return out, err
}
func (s *Store) SetBriefSettings(ctx context.Context, actor Profile, at *string, quiet *bool) (BriefSettings, error) {
	if at == nil && quiet == nil || at != nil && *at != "" && !briefTime.MatchString(*at) {
		return BriefSettings{}, ErrInvalid
	}
	var out BriefSettings
	err := s.db.QueryRowContext(ctx, `INSERT INTO brief_settings(user_id,time,quiet) VALUES($1,nullif($2,''),coalesce($3,false))
 ON CONFLICT(user_id) DO UPDATE SET time=CASE WHEN $4 THEN excluded.time ELSE brief_settings.time END,
 quiet=coalesce($3,brief_settings.quiet) RETURNING time,quiet`, actor.UserID, at, quiet, at != nil).Scan(&out.Time, &out.Quiet)
	return out, err
}

type BriefStats struct {
	UserID      string `json:"user_id"`
	DisplayName string `json:"display_name"`
	Sent        int    `json:"sent"`
	Read        int    `json:"read"`
	Clicked     int    `json:"clicked"`
	Positive    int    `json:"positive"`
	Negative    int    `json:"negative"`
}

func (s *Store) BriefStats(ctx context.Context, actor Profile) ([]BriefStats, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT u.user_id,u.display_name,count(b.brief_id),count(b.read_at),
 count(*) FILTER(WHERE EXISTS(SELECT 1 FROM brief_events e WHERE e.user_id=b.user_id AND e.brief_id=b.brief_id AND kind='click')),
 count(*) FILTER(WHERE EXISTS(SELECT 1 FROM brief_events e WHERE e.user_id=b.user_id AND e.brief_id=b.brief_id AND kind='positive')),
 count(*) FILTER(WHERE EXISTS(SELECT 1 FROM brief_events e WHERE e.user_id=b.user_id AND e.brief_id=b.brief_id AND kind='negative'))
 FROM users u LEFT JOIN briefs b ON b.user_id=u.user_id AND b.status='sent'
 WHERE u.team_id=$2 AND (($3='intern' AND u.user_id=$1) OR ($3='mentor' AND u.role='intern' AND u.mentor_id=$1)
 OR ($3='teamlead' AND u.role='intern')) GROUP BY u.user_id ORDER BY u.user_id`, actor.UserID, actor.TeamID, actor.Role)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []BriefStats{}
	for rows.Next() {
		var row BriefStats
		if err := rows.Scan(&row.UserID, &row.DisplayName, &row.Sent, &row.Read, &row.Clicked, &row.Positive, &row.Negative); err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

type MCPCall struct {
	ID      string `json:"id"`
	Case    string `json:"case"`
	Server  string `json:"server"`
	Tool    string `json:"tool"`
	Outcome string `json:"outcome"`
}

var auditName = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,100}$`)

func (s *Store) SaveMCPCall(ctx context.Context, actor Profile, call MCPCall) error {
	if !ValidID(call.ID) || !auditName.MatchString(call.Case) || !auditName.MatchString(call.Server) || !auditName.MatchString(call.Tool) || !auditName.MatchString(call.Outcome) {
		return ErrInvalid
	}
	var id string
	err := s.db.QueryRowContext(ctx, `INSERT INTO mcp_calls(user_id,call_id,case_name,server,tool,outcome) VALUES($1,$2,$3,$4,$5,$6)
 ON CONFLICT(user_id,call_id) DO UPDATE SET call_id=mcp_calls.call_id WHERE mcp_calls.case_name=excluded.case_name AND mcp_calls.server=excluded.server
 AND mcp_calls.tool=excluded.tool AND mcp_calls.outcome=excluded.outcome RETURNING call_id`, actor.UserID, call.ID, call.Case, call.Server, call.Tool, call.Outcome).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrConflict
	}
	return err
}
