package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"errors"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
)

type Profile struct {
	UserID      string `json:"user_id" yaml:"user_id"`
	DisplayName string `json:"display_name" yaml:"display_name"`
	Role        string `json:"role" yaml:"role"`
	TeamID      string `json:"team_id" yaml:"team_id"`
	MentorID    string `json:"mentor_id,omitempty" yaml:"mentor_id,omitempty"`
}

func scanProfile(row *sql.Row) (Profile, error) {
	var p Profile
	err := row.Scan(&p.UserID, &p.DisplayName, &p.Role, &p.TeamID, &p.MentorID)
	if errors.Is(err, sql.ErrNoRows) {
		return p, ErrNotFound
	}
	return p, err
}

const profileColumns = `user_id, display_name, role, team_id, coalesce(mentor_id, '')`

func (s *Store) TelegramProfile(ctx context.Context, id int64) (Profile, error) {
	return scanProfile(s.db.QueryRowContext(ctx,
		`SELECT `+profileColumns+` FROM users WHERE telegram_user_id = $1`, id))
}

// IssueInvitation returns a random 256-bit code exactly once, to its local caller.
func (s *Store) IssueInvitation(ctx context.Context, userID string, ttl time.Duration) (string, error) {
	if ttl <= 0 || ttl > 7*24*time.Hour {
		return "", errors.New("invitation TTL must be between zero and seven days")
	}
	code := make([]byte, 32)
	if _, err := rand.Read(code); err != nil {
		return "", err
	}
	encoded := base64.RawURLEncoding.EncodeToString(code)
	hash := sha256.Sum256([]byte(encoded))
	result, err := s.db.ExecContext(ctx, `INSERT INTO invitations (code_hash, user_id, expires_at)
		SELECT $1, user_id, now() + $3 * interval '1 second' FROM users
		WHERE user_id = $2 AND telegram_user_id IS NULL`, hash[:], userID, ttl.Seconds())
	if err != nil {
		return "", err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return "", err
	}
	if n == 0 {
		return "", ErrNotFound
	}
	return encoded, nil
}

// Bind serializes on the invitation and user rows; a rollback never consumes a code.
func (s *Store) Bind(ctx context.Context, code string, telegramID, chatID int64) (Profile, error) {
	var empty Profile
	if code == "" || len(code) > 128 || telegramID <= 0 || chatID != telegramID {
		return empty, ErrInvitation
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback()
	hash := sha256.Sum256([]byte(code))
	var userID string
	err = tx.QueryRowContext(ctx, `SELECT user_id FROM invitations
		WHERE code_hash = $1 AND used_at IS NULL AND expires_at > now() FOR UPDATE`, hash[:]).Scan(&userID)
	if errors.Is(err, sql.ErrNoRows) {
		return empty, ErrInvitation
	}
	if err != nil {
		return empty, err
	}
	var existing sql.NullInt64
	if err := tx.QueryRowContext(ctx, `SELECT telegram_user_id FROM users WHERE user_id=$1 FOR UPDATE`, userID).Scan(&existing); err != nil {
		return empty, err
	}
	if existing.Valid {
		return empty, ErrConflict
	}
	_, err = tx.ExecContext(ctx, `UPDATE users SET telegram_user_id=$2, telegram_chat_id=$3 WHERE user_id=$1`, userID, telegramID, chatID)
	if err != nil {
		var pgerr *pgconn.PgError
		if errors.As(err, &pgerr) && pgerr.Code == "23505" {
			return empty, ErrConflict
		}
		return empty, err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE invitations SET used_at=now() WHERE code_hash=$1`, hash[:]); err != nil {
		return empty, err
	}
	profile, err := scanProfile(tx.QueryRowContext(ctx, `SELECT `+profileColumns+` FROM users WHERE user_id=$1`, userID))
	if err != nil {
		return empty, err
	}
	if err := tx.Commit(); err != nil {
		return empty, err
	}
	return profile, nil
}
