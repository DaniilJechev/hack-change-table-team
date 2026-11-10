package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

type BriefItem struct {
	ID            string    `json:"id"`
	Source        string    `json:"source"`
	Type          string    `json:"type"`
	Title         string    `json:"title"`
	URL           string    `json:"url"`
	PriorityClass string    `json:"priority_class"`
	RelatedTask   string    `json:"related_task,omitempty"`
	Before        string    `json:"before,omitempty"`
	After         string    `json:"after,omitempty"`
	CreatedAt     time.Time `json:"created_at"`
}
type Brief struct {
	ID        string      `json:"id"`
	Items     []BriefItem `json:"items"`
	Status    string      `json:"status"`
	CreatedAt time.Time   `json:"created_at"`
	SentAt    *time.Time  `json:"sent_at"`
	ReadAt    *time.Time  `json:"read_at"`
}

func (s *Store) SaveBrief(ctx context.Context, actor Profile, input Brief) (Brief, error) {
	if !ValidID(input.ID) || input.Items == nil || len(input.Items) > 100 {
		return input, ErrInvalid
	}
	seen := map[string]bool{}
	for _, item := range input.Items {
		if !ValidID(item.ID) || seen[item.ID] || !validURL(item.URL) || !validText(item.Title, 2000) || !validText(item.Type, 128) || item.CreatedAt.IsZero() {
			return input, ErrInvalid
		}
		seen[item.ID] = true
		switch item.Source {
		case "github", "jira", "google_calendar":
		default:
			return input, ErrInvalid
		}
		switch item.PriorityClass {
		case "blocker", "people", "deadline", "information":
		default:
			return input, ErrInvalid
		}
	}
	raw, _ := json.Marshal(input.Items)
	var id string
	err := s.db.QueryRowContext(ctx, `INSERT INTO briefs(user_id,brief_id,items) VALUES($1,$2,$3)
 ON CONFLICT(user_id,brief_id) DO UPDATE SET items=briefs.items WHERE briefs.items=excluded.items RETURNING brief_id`, actor.UserID, input.ID, string(raw)).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return input, ErrConflict
	}
	if err != nil {
		return input, err
	}
	return s.Brief(ctx, actor, id)
}
func (s *Store) Brief(ctx context.Context, actor Profile, id string) (Brief, error) {
	var out Brief
	var raw []byte
	err := s.db.QueryRowContext(ctx, `SELECT brief_id,items,status,created_at,sent_at,read_at FROM briefs WHERE user_id=$1 AND brief_id=$2`, actor.UserID, id).Scan(&out.ID, &raw, &out.Status, &out.CreatedAt, &out.SentAt, &out.ReadAt)
	if err != nil {
		return out, noRows(err)
	}
	err = json.Unmarshal(raw, &out.Items)
	return out, err
}
func (s *Store) BriefStatus(ctx context.Context, actor Profile, id, status string) (Brief, error) {
	if status != "sent" && status != "failed" {
		return Brief{}, ErrInvalid
	}
	result, err := s.db.ExecContext(ctx, `UPDATE briefs SET status=$3,sent_at=CASE WHEN $3='sent' THEN coalesce(sent_at,now()) ELSE sent_at END
 WHERE user_id=$1 AND brief_id=$2 AND (status<>'sent' OR $3='sent')`, actor.UserID, id, status)
	if err != nil {
		return Brief{}, err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return Brief{}, err
	}
	if n == 0 {
		if _, err := s.Brief(ctx, actor, id); err != nil {
			return Brief{}, err
		}
		return Brief{}, ErrConflict
	}
	return s.Brief(ctx, actor, id)
}

type BriefEvent struct {
	ID        string    `json:"id"`
	Kind      string    `json:"kind"`
	ItemID    string    `json:"item_id,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

func (s *Store) BriefEvent(ctx context.Context, actor Profile, briefID string, event BriefEvent) (BriefEvent, error) {
	if !ValidID(event.ID) {
		return event, ErrInvalid
	}
	switch event.Kind {
	case "read", "click", "positive", "negative", "reply":
	default:
		return event, ErrInvalid
	}
	if event.Kind == "click" && !ValidID(event.ItemID) || event.Kind != "click" && event.ItemID != "" {
		return event, ErrInvalid
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return event, err
	}
	defer tx.Rollback()
	var raw []byte
	var status string
	err = tx.QueryRowContext(ctx, `SELECT items,status FROM briefs WHERE user_id=$1 AND brief_id=$2 FOR UPDATE`, actor.UserID, briefID).Scan(&raw, &status)
	if err != nil {
		return event, noRows(err)
	}
	if status != "sent" {
		return event, ErrConflict
	}
	if event.Kind == "click" {
		var items []BriefItem
		if err := json.Unmarshal(raw, &items); err != nil {
			return event, err
		}
		exists := false
		for _, item := range items {
			if item.ID == event.ItemID {
				exists = true
			}
		}
		if !exists {
			return event, ErrInvalid
		}
	}
	err = tx.QueryRowContext(ctx, `INSERT INTO brief_events(user_id,event_id,brief_id,kind,item_id) VALUES($1,$2,$3,$4,$5)
 ON CONFLICT(user_id,event_id) DO UPDATE SET kind=brief_events.kind
 WHERE brief_events.brief_id=excluded.brief_id AND brief_events.kind=excluded.kind AND brief_events.item_id=excluded.item_id
 RETURNING created_at`, actor.UserID, event.ID, briefID, event.Kind, event.ItemID).Scan(&event.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return event, ErrConflict
	}
	if err != nil {
		return event, err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE briefs SET read_at=coalesce(read_at,$3) WHERE user_id=$1 AND brief_id=$2`, actor.UserID, briefID, event.CreatedAt); err != nil {
		return event, err
	}
	return event, tx.Commit()
}
