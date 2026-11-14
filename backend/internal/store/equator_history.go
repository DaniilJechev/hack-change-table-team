package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strconv"
	"time"
)

func (s *Store) ConfigureEquatorHistory(getenv func(string) string) error {
	days := 7
	if raw := getenv("H4_HISTORY_TTL_DAYS"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 || n > 36500 {
			return errors.New("invalid H4_HISTORY_TTL_DAYS: expected integer from 1 to 36500")
		}
		days = n
	}
	s.equatorHistoryDays = days
	return nil
}

func (s *Store) equatorDays() int {
	if s.equatorHistoryDays < 1 {
		return 7
	}
	return s.equatorHistoryDays
}

// Every mutation locks the actor's user row, including the gap before a first record exists.
func (s *Store) equatorTx(ctx context.Context, actor Profile) (*sql.Tx, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	var id string
	if err = tx.QueryRowContext(ctx, `SELECT user_id FROM users WHERE user_id=$1 AND team_id=$2 FOR UPDATE`, actor.UserID, actor.TeamID).Scan(&id); err != nil {
		tx.Rollback()
		return nil, noRows(err)
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM equator_checks WHERE expires_at<=clock_timestamp()`); err != nil {
		tx.Rollback()
		return nil, err
	}
	return tx, nil
}

func scanEquator(row interface{ Scan(...any) error }) (map[string]any, error) {
	var raw []byte
	var current bool
	var file, report string
	var created, expires time.Time
	if err := row.Scan(&raw, &current, &file, &report, &created, &expires); err != nil {
		return nil, noRows(err)
	}
	var v map[string]any
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil, err
	}
	v["current"], v["delivery_file"], v["delivery_report"] = current, file, report
	v["created_at"], v["expires_at"] = created.UTC().Format(time.RFC3339Nano), expires.UTC().Format(time.RFC3339Nano)
	return v, nil
}

const equatorColumns = `document,current,delivery_file,delivery_report,created_at,expires_at`

func (s *Store) EquatorHistory(ctx context.Context, actor Profile) ([]map[string]any, error) {
	tx, err := s.equatorTx(ctx, actor)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `SELECT `+equatorColumns+` FROM equator_checks WHERE user_id=$1 AND team_id=$2 ORDER BY created_at DESC,check_id DESC`, actor.UserID, actor.TeamID)
	if err != nil {
		return nil, err
	}
	items := []map[string]any{}
	for rows.Next() {
		v, e := scanEquator(rows)
		if e != nil {
			rows.Close()
			return nil, e
		}
		items = append(items, v)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	return items, tx.Commit()
}

func (s *Store) InvalidateEquator(ctx context.Context, actor Profile, clear bool) (int64, error) {
	tx, err := s.equatorTx(ctx, actor)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	var generation int64
	err = tx.QueryRowContext(ctx, `INSERT INTO equator_generations(user_id,generation) VALUES($1,1) ON CONFLICT(user_id) DO UPDATE SET generation=equator_generations.generation+1 RETURNING generation`, actor.UserID).Scan(&generation)
	if err != nil {
		return 0, err
	}
	query := `UPDATE equator_checks SET current=false WHERE user_id=$1 AND team_id=$2`
	if clear {
		query = `DELETE FROM equator_checks WHERE user_id=$1 AND team_id=$2`
	}
	if _, err = tx.ExecContext(ctx, query, actor.UserID, actor.TeamID); err != nil {
		return 0, err
	}
	return generation, tx.Commit()
}

func (s *Store) SaveEquator(ctx context.Context, actor Profile, v map[string]any) (map[string]any, error) {
	id, ok := v["id"].(string)
	if !ok || !ValidID(id) {
		return nil, ErrInvalid
	}
	if owner, ok := v["user_id"].(string); ok && owner != actor.UserID {
		return nil, ErrForbidden
	}
	if team, ok := v["team_id"].(string); ok && team != actor.TeamID {
		return nil, ErrForbidden
	}
	if demo, _ := v["demo"].(bool); demo {
		return nil, ErrInvalid
	}
	if _, ok := v["passed"].(bool); !ok {
		return nil, ErrInvalid
	}
	generation, ok := v["generation"].(float64)
	if !ok || generation < 0 || generation != float64(int64(generation)) {
		return nil, ErrInvalid
	}
	// Identity and clock belong to the trusted backend; payload cannot extend retention.
	v["user_id"], v["team_id"], v["demo"] = actor.UserID, actor.TeamID, false
	raw, err := json.Marshal(v)
	if err != nil || len(raw) > 1<<20 {
		return nil, ErrInvalid
	}
	tx, err := s.equatorTx(ctx, actor)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var latest int64
	err = tx.QueryRowContext(ctx, `SELECT generation FROM equator_generations WHERE user_id=$1`, actor.UserID).Scan(&latest)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	current, _ := v["current"].(bool)
	current = current && int64(generation) == latest
	// Result data is immutable. Telegram attaches the file_id after the initial check.
	saved, err := scanEquator(tx.QueryRowContext(ctx, `INSERT INTO equator_checks(user_id,team_id,check_id,generation,document,expires_at,current)
 VALUES($1,$2,$3,$4,$5,clock_timestamp()+make_interval(days=>$6),$7)
 ON CONFLICT(user_id,check_id) DO UPDATE SET document=CASE
 WHEN coalesce(equator_checks.document->>'file_id','')='' THEN
 jsonb_set(equator_checks.document,'{file_id}',coalesce(excluded.document->'file_id','""'::jsonb))
 ELSE equator_checks.document END
 RETURNING `+equatorColumns, actor.UserID, actor.TeamID, id, int64(generation), raw, s.equatorDays(), current))
	if err != nil {
		return nil, err
	}
	return saved, tx.Commit()
}

func (s *Store) EquatorDelivery(ctx context.Context, actor Profile, id, part, state string, claim bool, retryUnknown ...bool) (map[string]any, error) {
	if !ValidID(id) || (part != "file" && part != "report") {
		return nil, ErrInvalid
	}
	if !claim && state != "sent" && state != "failed" && state != "unknown" {
		return nil, ErrInvalid
	}
	tx, err := s.equatorTx(ctx, actor)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	v, err := scanEquator(tx.QueryRowContext(ctx, `SELECT `+equatorColumns+` FROM equator_checks WHERE user_id=$1 AND team_id=$2 AND check_id=$3 FOR UPDATE`, actor.UserID, actor.TeamID, id))
	if err != nil {
		return nil, err
	}
	existing, _ := v["delivery_"+part].(string)
	if claim {
		retry := len(retryUnknown) > 0 && retryUnknown[0] && existing == "unknown"
		if v["current"] != true || v["passed"] != true || (existing != "pending" && existing != "failed" && !retry) {
			return nil, ErrConflict
		}
		state = "sending"
	} else if existing != "sending" {
		if existing == state {
			return v, tx.Commit()
		}
		return nil, ErrConflict
	}
	// part is a closed enum above, never caller SQL.
	updated, err := scanEquator(tx.QueryRowContext(ctx, `UPDATE equator_checks SET delivery_`+part+`=$4 WHERE user_id=$1 AND team_id=$2 AND check_id=$3 RETURNING `+equatorColumns, actor.UserID, actor.TeamID, id, state))
	if err != nil {
		return nil, err
	}
	return updated, tx.Commit()
}

func (s *Store) MentorChat(ctx context.Context, actor Profile) (*int64, error) {
	var chat sql.NullInt64
	err := s.db.QueryRowContext(ctx, `SELECT m.telegram_chat_id FROM users u LEFT JOIN users m ON m.user_id=u.mentor_id AND m.team_id=u.team_id AND m.role='mentor' WHERE u.user_id=$1 AND u.team_id=$2`, actor.UserID, actor.TeamID).Scan(&chat)
	if err != nil {
		return nil, noRows(err)
	}
	if !chat.Valid {
		return nil, nil
	}
	return &chat.Int64, nil
}
