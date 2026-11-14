package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
)

// ChannelInput is private to the trusted agent, never forwarded from a client verbatim.
type ChannelInput struct {
	UserID  string          `json:"user_id"`
	ID      string          `json:"id"`
	Secret  string          `json:"secret"`
	Code    string          `json:"code"`
	Kind    string          `json:"kind"`
	Payload json.RawMessage `json:"payload"`
	After   int64           `json:"after"`
	Before  int64           `json:"before"`
}

func channelSecret() (string, error) {
	b := make([]byte, 32)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
func channelHash(s string) []byte { h := sha256.Sum256([]byte(s)); return h[:] }
func channelResult(e error) error {
	if errors.Is(e, sql.ErrNoRows) {
		return ErrNotFound
	}
	return e
}

// Channel is a service-authenticated persistence boundary. Every scoped query derives
// team membership from users; public device sessions never choose an actor or team.
func (s *Store) Channel(ctx context.Context, op string, in ChannelInput) (any, error) {
	switch op {
	case "device":
		_, _ = s.db.ExecContext(ctx, `DELETE FROM desktop_devices WHERE expires_at<now()`)
		secret, e := channelSecret()
		if e != nil {
			return nil, e
		}
		code := strings.ToUpper(hex.EncodeToString(channelHash(secret)[:5]))
		_, e = s.db.ExecContext(ctx, `INSERT INTO desktop_devices(secret_hash,code) VALUES($1,$2)`, channelHash(secret), code)
		return map[string]any{"device_secret": secret, "user_code": code, "expires_in": 300, "interval": 5}, e
	case "approve":
		if in.UserID == "" || len(in.Code) != 10 {
			return nil, ErrInvalid
		}
		r, e := s.db.ExecContext(ctx, `UPDATE desktop_devices SET user_id=$1 WHERE code=$2 AND user_id IS NULL AND NOT used AND expires_at>now() AND EXISTS(SELECT 1 FROM users WHERE user_id=$1 AND telegram_user_id IS NOT NULL)`, in.UserID, strings.ToUpper(in.Code))
		if e != nil {
			return nil, e
		}
		n, _ := r.RowsAffected()
		if n != 1 {
			return nil, ErrNotFound
		}
		return map[string]any{"ok": true}, nil
	case "token":
		if len(in.Secret) != 43 {
			return nil, ErrInvalid
		}
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			return nil, e
		}
		defer tx.Rollback()
		var user sql.NullString
		e = tx.QueryRowContext(ctx, `SELECT user_id FROM desktop_devices WHERE secret_hash=$1 AND NOT used AND expires_at>now() FOR UPDATE`, channelHash(in.Secret)).Scan(&user)
		if e != nil {
			return nil, channelResult(e)
		}
		if !user.Valid {
			return map[string]any{"pending": true}, nil
		}
		token, e := channelSecret()
		if e != nil {
			return nil, e
		}
		_, e = tx.ExecContext(ctx, `INSERT INTO desktop_sessions(token_hash,user_id,team_id) SELECT $1,user_id,team_id FROM users WHERE user_id=$2`, channelHash(token), user.String)
		if e != nil {
			return nil, e
		}
		_, e = tx.ExecContext(ctx, `UPDATE desktop_devices SET used=true WHERE secret_hash=$1`, channelHash(in.Secret))
		if e != nil {
			return nil, e
		}
		return map[string]any{"token": token, "expires_in": 2592000}, tx.Commit()
	case "session":
		var p Profile
		e := s.db.QueryRowContext(ctx, `SELECT u.user_id,u.display_name,u.role,u.team_id,coalesce(u.mentor_id,'') FROM desktop_sessions d JOIN users u ON u.user_id=d.user_id AND u.team_id=d.team_id WHERE d.token_hash=$1 AND d.expires_at>now()`, channelHash(in.Secret)).Scan(&p.UserID, &p.DisplayName, &p.Role, &p.TeamID, &p.MentorID)
		return p, channelResult(e)
	case "logout":
		_, e := s.db.ExecContext(ctx, `DELETE FROM desktop_sessions WHERE token_hash=$1`, channelHash(in.Secret))
		return map[string]any{"ok": true}, e
	case "audience":
		rows, e := s.db.QueryContext(ctx, `SELECT DISTINCT u.user_id,u.display_name,u.role,u.team_id,coalesce(u.mentor_id,'') FROM desktop_sessions d JOIN users u ON u.user_id=d.user_id AND u.team_id=d.team_id WHERE d.expires_at>now()`)
		if e != nil {
			return nil, e
		}
		defer rows.Close()
		items := []Profile{}
		for rows.Next() {
			var p Profile
			if e = rows.Scan(&p.UserID, &p.DisplayName, &p.Role, &p.TeamID, &p.MentorID); e != nil {
				return nil, e
			}
			items = append(items, p)
		}
		return map[string]any{"items": items}, rows.Err()
	case "interrupt":
		// The deployment runs one channel worker. Never replay unknown side effects.
		_, e := s.db.ExecContext(ctx, `WITH changed AS (
 UPDATE channel_records SET payload=jsonb_set(payload,'{status}','"interrupted"')
 WHERE kind='request' AND payload->>'status'='running' RETURNING user_id,team_id,id,payload,seq
) INSERT INTO channel_events(user_id,team_id,kind,payload)
 SELECT user_id,team_id,'request',jsonb_build_object('id',id,'record',payload,'seq',seq) FROM changed`)
		return map[string]any{"ok": true}, e
	}
	if in.UserID == "" {
		return nil, ErrInvalid
	}
	var team string
	if e := s.db.QueryRowContext(ctx, `SELECT team_id FROM users WHERE user_id=$1`, in.UserID).Scan(&team); e != nil {
		return nil, channelResult(e)
	}
	if op == "profile" {
		return scanProfile(s.db.QueryRowContext(ctx, `SELECT `+profileColumns+` FROM users WHERE user_id=$1`, in.UserID))
	}
	if op == "telegram" {
		var chat sql.NullInt64
		e := s.db.QueryRowContext(ctx, `SELECT telegram_chat_id FROM users WHERE user_id=$1`, in.UserID).Scan(&chat)
		return map[string]any{"chat_id": chat.Int64}, e
	}
	if op == "events" {
		rows, e := s.db.QueryContext(ctx, `SELECT seq,kind,payload FROM channel_events WHERE user_id=$1 AND team_id=$2 AND seq>$3 ORDER BY seq LIMIT 100`, in.UserID, team, in.After)
		if e != nil {
			return nil, e
		}
		defer rows.Close()
		items := []map[string]any{}
		for rows.Next() {
			var seq int64
			var kind string
			var p json.RawMessage
			if e = rows.Scan(&seq, &kind, &p); e != nil {
				return nil, e
			}
			items = append(items, map[string]any{"seq": seq, "kind": kind, "payload": p})
		}
		return map[string]any{"items": items}, rows.Err()
	}
	if !map[string]bool{"message": true, "request": true, "file": true, "read": true, "state": true}[in.Kind] {
		return nil, ErrInvalid
	}
	switch op {
	case "list":
		rows, e := s.db.QueryContext(ctx, `SELECT seq,id,payload,created_at FROM channel_records WHERE user_id=$1 AND team_id=$2 AND kind=$3 AND ($4::bigint=0 OR seq<$4) ORDER BY seq DESC LIMIT 100`, in.UserID, team, in.Kind, in.Before)
		if e != nil {
			return nil, e
		}
		defer rows.Close()
		items := []map[string]any{}
		for rows.Next() {
			var seq int64
			var id string
			var p json.RawMessage
			var at any
			if e = rows.Scan(&seq, &id, &p, &at); e != nil {
				return nil, e
			}
			items = append(items, map[string]any{"seq": seq, "id": id, "payload": p, "created_at": at})
		}
		return map[string]any{"items": items}, rows.Err()
	case "get":
		var p json.RawMessage
		e := s.db.QueryRowContext(ctx, `SELECT payload FROM channel_records WHERE user_id=$1 AND team_id=$2 AND kind=$3 AND id=$4`, in.UserID, team, in.Kind, in.ID).Scan(&p)
		return p, channelResult(e)
	case "put", "create":
		if len(in.ID) == 0 || len(in.ID) > 160 || !json.Valid(in.Payload) || len(in.Payload) > 15<<20 {
			return nil, ErrInvalid
		}
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			return nil, e
		}
		defer tx.Rollback()
		// Serialize writes per actor to make create/idempotency and events atomic.
		if _, e = tx.ExecContext(ctx, `SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE`, in.UserID); e != nil {
			return nil, e
		}
		if op == "create" {
			var old json.RawMessage
			e = tx.QueryRowContext(ctx, `SELECT payload FROM channel_records WHERE user_id=$1 AND team_id=$2 AND kind=$3 AND id=$4`, in.UserID, team, in.Kind, in.ID).Scan(&old)
			if e == nil {
				return map[string]any{"created": false, "payload": old}, nil
			}
			if !errors.Is(e, sql.ErrNoRows) {
				return nil, e
			}
		}
		var recordSeq int64
		e = tx.QueryRowContext(ctx, `INSERT INTO channel_records(user_id,team_id,kind,id,payload) VALUES($1,$2,$3,$4,$5) ON CONFLICT(user_id,team_id,kind,id) DO UPDATE SET payload=EXCLUDED.payload RETURNING seq`, in.UserID, team, in.Kind, in.ID, in.Payload).Scan(&recordSeq)
		if e != nil {
			return nil, e
		}
		if in.Kind != "message" && in.Kind != "request" {
			return map[string]any{"created": true, "payload": in.Payload, "seq": recordSeq}, tx.Commit()
		}
		var seq int64
		e = tx.QueryRowContext(ctx, `INSERT INTO channel_events(user_id,team_id,kind,payload) VALUES($1,$2,$3,jsonb_build_object('id',$4::text,'record',$5::jsonb,'seq',$6::bigint)) RETURNING seq`, in.UserID, team, in.Kind, in.ID, in.Payload, recordSeq).Scan(&seq)
		if e != nil {
			return nil, e
		}
		return map[string]any{"created": true, "payload": in.Payload, "seq": seq}, tx.Commit()
	default:
		return nil, ErrInvalid
	}
}
