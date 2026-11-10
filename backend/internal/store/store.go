package store

import (
	"context"
	"database/sql"
	"errors"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"

	"github.com/DaniilJechev/hack-change-table-team/backend/migrations"
)

var (
	ErrInvitation = errors.New("invalid invitation")
	ErrConflict   = errors.New("binding conflict")
	ErrNotFound   = errors.New("user not found")
)

type Store struct{ db *sql.DB }

// Open never includes the DSN or driver error in its error messages.
func Open(ctx context.Context, dsn string) (*Store, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, errors.New("invalid database configuration")
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	s := &Store{db: db}
	if err := s.Ping(ctx); err != nil {
		db.Close()
		return nil, errors.New("database unavailable")
	}
	return s, nil
}

func (s *Store) Close() error                   { return s.db.Close() }
func (s *Store) Ping(ctx context.Context) error { return s.db.PingContext(ctx) }

func (s *Store) Migrate(ctx context.Context) error {
	provider, err := goose.NewProvider(goose.DialectPostgres, s.db, migrations.Files)
	if err != nil {
		return err
	}
	_, err = provider.Up(ctx)
	return err
}
