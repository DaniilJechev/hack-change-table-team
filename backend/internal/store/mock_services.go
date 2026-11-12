package store

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

const EquatorItem = "equator_presentation"
const EquatorTitle = "Презентация к экватору"

type MockAccessRequest struct {
	ID        string    `json:"id"`
	UserID    string    `json:"user_id"`
	TeamID    string    `json:"team_id"`
	ObjectID  string    `json:"object_id"`
	Status    string    `json:"status"`
	Reason    string    `json:"reason"`
	ErrorCode string    `json:"error_code"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
	Simulated bool      `json:"simulated"`
}

const mockRequestColumns = `request_id,user_id,team_id,object_id,status,reason,error_code,created_at,updated_at`

func scanMockRequest(row interface{ Scan(...any) error }) (MockAccessRequest, error) {
	v := MockAccessRequest{Simulated: true}
	err := row.Scan(&v.ID, &v.UserID, &v.TeamID, &v.ObjectID, &v.Status, &v.Reason, &v.ErrorCode, &v.CreatedAt, &v.UpdatedAt)
	return v, noRows(err)
}

func (s *Store) RequestMockAccess(ctx context.Context, actor Profile, object string) (MockAccessRequest, error) {
	if !ValidID(object) {
		return MockAccessRequest{}, ErrInvalid
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return MockAccessRequest{}, err
	}
	defer tx.Rollback()
	// A per-user lock serializes duplicate requests even before the first row exists.
	var user string
	if err = tx.QueryRowContext(ctx, `SELECT user_id FROM users WHERE user_id=$1 AND team_id=$2 FOR UPDATE`, actor.UserID, actor.TeamID).Scan(&user); err != nil {
		return MockAccessRequest{}, noRows(err)
	}
	var raw []byte
	if err = tx.QueryRowContext(ctx, `SELECT document FROM access_catalog WHERE team_id=$1 AND object_id=$2 FOR SHARE`, actor.TeamID, object).Scan(&raw); err != nil {
		return MockAccessRequest{}, noRows(err)
	}
	var obj CatalogObject
	if err = json.Unmarshal(raw, &obj); err != nil {
		return MockAccessRequest{}, err
	}
	if !obj.visible(actor.Role) {
		return MockAccessRequest{}, ErrNotFound
	}
	if obj.Forbidden {
		return MockAccessRequest{}, ErrForbidden
	}
	existing, err := scanMockRequest(tx.QueryRowContext(ctx, `SELECT `+mockRequestColumns+` FROM mock_access_requests WHERE user_id=$1 AND object_id=$2 FOR UPDATE`, actor.UserID, object))
	if err == nil && existing.Status != "error" {
		return existing, tx.Commit()
	}
	if err != nil && !errors.Is(err, ErrNotFound) {
		return existing, err
	}
	var status, reason, code string
	err = tx.QueryRowContext(ctx, `SELECT status,reason,error_code FROM mock_access_rules WHERE team_id=$1 AND object_id=$2`, actor.TeamID, object).Scan(&status, &reason, &code)
	if errors.Is(err, sql.ErrNoRows) {
		status, reason, code = "error", "Правило имитации не настроено", "service_unconfigured"
	} else if err != nil {
		return existing, err
	}
	id := existing.ID
	if id == "" {
		var token [16]byte
		if _, err = rand.Read(token[:]); err != nil {
			return existing, err
		}
		id = "ar-" + hex.EncodeToString(token[:])
	}
	result, err := scanMockRequest(tx.QueryRowContext(ctx, `INSERT INTO mock_access_requests(request_id,user_id,team_id,object_id,status,reason,error_code)
        VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(user_id,object_id) DO UPDATE
        SET status=excluded.status,reason=excluded.reason,error_code=excluded.error_code,updated_at=clock_timestamp()
        RETURNING `+mockRequestColumns, id, actor.UserID, actor.TeamID, object, status, reason, code))
	if err != nil {
		return result, err
	}
	if status == "pending" || status == "granted" {
		if _, err = tx.ExecContext(ctx, `INSERT INTO access_marks(user_id,team_id,object_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING`, actor.UserID, actor.TeamID, object); err != nil {
			return result, err
		}
	}
	return result, tx.Commit()
}

func (s *Store) MockAccessRequests(ctx context.Context, actor Profile) ([]MockAccessRequest, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+mockRequestColumns+` FROM mock_access_requests WHERE user_id=$1 AND team_id=$2 ORDER BY object_id`, actor.UserID, actor.TeamID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []MockAccessRequest{}
	for rows.Next() {
		record, err := scanMockRequest(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, record)
	}
	return result, rows.Err()
}

func (s *Store) ResolveMockAccess(ctx context.Context, actor Profile, id, status, reason string) (MockAccessRequest, error) {
	if actor.Role != "teamlead" {
		return MockAccessRequest{}, ErrForbidden
	}
	if !ValidID(id) || (status != "granted" && status != "denied") || len(reason) > 1000 || (status == "denied" && strings.TrimSpace(reason) == "") {
		return MockAccessRequest{}, ErrInvalid
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return MockAccessRequest{}, err
	}
	defer tx.Rollback()
	record, err := scanMockRequest(tx.QueryRowContext(ctx, `SELECT `+mockRequestColumns+` FROM mock_access_requests WHERE request_id=$1 AND team_id=$2 FOR UPDATE`, id, actor.TeamID))
	if err != nil {
		return record, err
	}
	if record.Status != "pending" {
		if record.Status != status || record.Reason != reason {
			return record, ErrConflict
		}
		return record, tx.Commit()
	}
	record, err = scanMockRequest(tx.QueryRowContext(ctx, `UPDATE mock_access_requests SET status=$3,reason=$4,error_code='',updated_at=clock_timestamp() WHERE request_id=$1 AND team_id=$2 RETURNING `+mockRequestColumns, id, actor.TeamID, status, reason))
	if err != nil {
		return record, err
	}
	return record, tx.Commit()
}

type MockProgressItem struct {
	ItemID      string     `json:"item_id"`
	Title       string     `json:"title"`
	Completed   bool       `json:"completed"`
	CompletedAt *time.Time `json:"completed_at"`
	UpdatedAt   *time.Time `json:"updated_at"`
	Simulated   bool       `json:"simulated"`
}

func emptyProgress() MockProgressItem {
	return MockProgressItem{ItemID: EquatorItem, Title: EquatorTitle, Simulated: true}
}
func (s *Store) MockProgress(ctx context.Context, actor Profile) ([]MockProgressItem, error) {
	v := emptyProgress()
	err := s.db.QueryRowContext(ctx, `SELECT completed,completed_at,updated_at FROM mock_progress WHERE user_id=$1 AND team_id=$2 AND item_id=$3`, actor.UserID, actor.TeamID, EquatorItem).Scan(&v.Completed, &v.CompletedAt, &v.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		err = nil
	}
	return []MockProgressItem{v}, err
}
func (s *Store) MarkMockProgress(ctx context.Context, actor Profile, item string, completed bool) (MockProgressItem, error) {
	if item != EquatorItem {
		return MockProgressItem{}, ErrNotFound
	}
	v := emptyProgress()
	err := s.db.QueryRowContext(ctx, `INSERT INTO mock_progress(user_id,team_id,item_id,completed,completed_at)
      VALUES($1,$2,$3,$4,CASE WHEN $4 THEN now() ELSE NULL END)
      ON CONFLICT(user_id,item_id) DO UPDATE SET completed=excluded.completed,
      completed_at=CASE WHEN mock_progress.completed=excluded.completed THEN mock_progress.completed_at ELSE excluded.completed_at END,
      updated_at=CASE WHEN mock_progress.completed=excluded.completed THEN mock_progress.updated_at ELSE clock_timestamp() END
      RETURNING completed,completed_at,updated_at`, actor.UserID, actor.TeamID, item, completed).Scan(&v.Completed, &v.CompletedAt, &v.UpdatedAt)
	return v, err
}
