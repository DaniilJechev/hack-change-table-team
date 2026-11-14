package store

import (
	"context"
	"errors"
	"strings"
)

// ProvisionMetricsRole is used by the Compose server only. Credentials never enter logs.
// Views execute with their owner's privileges; the login cannot read source tables.
func (s *Store) ProvisionMetricsRole(ctx context.Context, password string) error {
	if password == "" {
		return nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// Serialize provisioning between backend replicas (roles are cluster-wide).
	if _, err = tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(704489102)`); err != nil {
		return errors.New("metrics role provisioning failed")
	}
	var exists bool
	if err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='buddy_metrics')`).Scan(&exists); err != nil {
		return err
	}
	if !exists {
		if _, err = tx.ExecContext(ctx, `CREATE ROLE buddy_metrics`); err != nil {
			return errors.New("metrics role provisioning failed")
		}
	}
	// PostgreSQL utility statements do not support bind parameters for passwords.
	// Ask PostgreSQL itself to quote the literal; do not format it in logs/errors.
	var statement string
	if err = tx.QueryRowContext(ctx, `SELECT format('ALTER ROLE buddy_metrics LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',$1::text)`, password).Scan(&statement); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, statement); err != nil {
		return errors.New("metrics role provisioning failed")
	}
	var schema string
	if err = tx.QueryRowContext(ctx, `SELECT current_schema()`).Scan(&schema); err != nil {
		return err
	}
	quoted := `"` + strings.ReplaceAll(schema, `"`, `""`) + `"`
	for _, stmt := range []string{
		`ALTER ROLE buddy_metrics SET default_transaction_read_only=on`,
		`ALTER ROLE buddy_metrics SET statement_timeout='5s'`,
		`GRANT USAGE ON SCHEMA ` + quoted + ` TO buddy_metrics`,
		`GRANT SELECT ON ` + quoted + `.metrics_briefs,` + quoted + `.metrics_helpful,` + quoted + `.metrics_access,` + quoted + `.metrics_mcp,` + quoted + `.metrics_agent TO buddy_metrics`,
	} {
		if _, err = tx.ExecContext(ctx, stmt); err != nil {
			return errors.New("metrics role provisioning failed")
		}
	}
	return tx.Commit()
}
