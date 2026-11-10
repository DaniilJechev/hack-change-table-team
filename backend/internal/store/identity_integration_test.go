package store

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

// Tests use a dedicated database and a unique schema; no developer tables are touched.
func integrationStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set TEST_DATABASE_URL or run make test-backend-integration")
	}
	ctx := context.Background()
	s, err := Open(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	schema := fmt.Sprintf("test_%d", time.Now().UnixNano())
	if _, err := s.db.ExecContext(ctx, `CREATE SCHEMA `+schema); err != nil {
		t.Fatal(err)
	}
	s.Close()
	// pgx supports search_path as a connection runtime parameter in URI DSNs.
	separator := "?"
	if strings.Contains(dsn, "?") {
		separator = "&"
	}
	s, err = Open(ctx, dsn+separator+"search_path="+schema)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		s.Close()
		db, err := sql.Open("pgx", dsn)
		if err != nil {
			t.Error(err)
			return
		}
		defer db.Close()
		if _, err := db.Exec(`DROP SCHEMA ` + schema + ` CASCADE`); err != nil {
			t.Error(err)
		}
	})
	if err := s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return s
}

const profilesYAML = `users:
  - {user_id: intern, display_name: Intern, role: intern, team_id: orders, mentor_id: mentor}
  - {user_id: mentor, display_name: Mentor, role: mentor, team_id: orders}
  - {user_id: lead, display_name: Lead, role: teamlead, team_id: orders}
  - {user_id: other, display_name: Other, role: intern, team_id: payments}
`

func TestNFR_DB_1_MigrateSeedAndPreserveBinding(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if err := s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err != nil {
			t.Fatal(err)
		}
	}
	var count int
	if err := s.db.QueryRow(`SELECT count(*) FROM users`).Scan(&count); err != nil || count != 4 {
		t.Fatalf("count=%d err=%v", count, err)
	}
	for i, user := range []string{"intern", "mentor", "lead", "other"} {
		code, err := s.IssueInvitation(ctx, user, time.Hour)
		if err != nil {
			t.Fatal(err)
		}
		hash := sha256.Sum256([]byte(code))
		var stored []byte
		if err := s.db.QueryRow(`SELECT code_hash FROM invitations WHERE user_id=$1`, user).Scan(&stored); err != nil || string(stored) != string(hash[:]) {
			t.Fatal("invitation not stored as SHA-256", err)
		}
		id := int64(100 + i)
		profile, err := s.Bind(ctx, code, id, id)
		if err != nil || profile.UserID != user {
			t.Fatalf("profile=%+v err=%v", profile, err)
		}
		if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err != nil {
			t.Fatal(err)
		}
		got, err := s.TelegramProfile(ctx, id)
		if err != nil || got != profile {
			t.Fatalf("lost binding: %+v %v", got, err)
		}
		if _, err := s.Bind(ctx, code, id, id); err != ErrInvitation {
			t.Fatalf("reused code: %v", err)
		}
		if _, err := s.IssueInvitation(ctx, user, time.Hour); err != ErrNotFound {
			t.Fatalf("issued for bound user: %v", err)
		}
	}
	if _, err := s.TelegramProfile(ctx, 999); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.IssueInvitation(ctx, "missing", time.Hour); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.Bind(ctx, "unknown", 99, 99); err != ErrInvitation {
		t.Fatal(err)
	}
	expiredHash := sha256.Sum256([]byte("expired"))
	_, err := s.db.Exec(`INSERT INTO invitations(code_hash,user_id,created_at,expires_at) VALUES($1,'intern',now()-interval '2 hours',now()-interval '1 hour')`, expiredHash[:])
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Bind(ctx, "expired", 99, 99); err != ErrInvitation {
		t.Fatal(err)
	}
	// The first update must roll back when a later record attempts a team move.
	input := strings.Replace(profilesYAML, "display_name: Intern", "display_name: Changed", 1)
	input = strings.Replace(input, "team_id: payments", "team_id: orders", 1)
	if err := s.Seed(ctx, strings.NewReader(input)); err == nil {
		t.Fatal("accepted team move")
	}
	profile, _ := s.TelegramProfile(ctx, 100)
	if profile.DisplayName != "Intern" {
		t.Fatal("partial seed write")
	}
}

func TestNFR_DB_1_ReimportPreservesStoredMentorRelationships(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err != nil {
		t.Fatal(err)
	}
	// The dependent intern is deliberately omitted from this otherwise valid file.
	partial := `users:
  - {user_id: new, display_name: New, role: intern, team_id: new-team}
  - {user_id: lead, display_name: Changed, role: teamlead, team_id: orders}
  - {user_id: mentor, display_name: Mentor, role: intern, team_id: orders}
`
	err := s.Seed(ctx, strings.NewReader(partial))
	if err == nil || !strings.Contains(err.Error(), "mentor_id") {
		t.Fatalf("expected stored mentor relationship error, got %v", err)
	}
	var role, mentorID, leadName string
	if err := s.db.QueryRow(`SELECT role FROM users WHERE user_id='mentor'`).Scan(&role); err != nil || role != "mentor" {
		t.Fatalf("mentor role changed: %q, err=%v", role, err)
	}
	if err := s.db.QueryRow(`SELECT mentor_id FROM users WHERE user_id='intern'`).Scan(&mentorID); err != nil || mentorID != "mentor" {
		t.Fatalf("stored relationship changed: %q, err=%v", mentorID, err)
	}
	if err := s.db.QueryRow(`SELECT display_name FROM users WHERE user_id='lead'`).Scan(&leadName); err != nil || leadName != "Lead" {
		t.Fatalf("earlier update was not rolled back: %q, err=%v", leadName, err)
	}
	var users, teams int
	if err := s.db.QueryRow(`SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM teams)`).Scan(&users, &teams); err != nil || users != 4 || teams != 2 {
		t.Fatalf("earlier inserts were not rolled back: users=%d teams=%d err=%v", users, teams, err)
	}
	// A role change is valid if the same transaction removes its last relationship.
	updated := `users:
  - {user_id: mentor, display_name: Mentor, role: intern, team_id: orders}
  - {user_id: intern, display_name: Intern, role: intern, team_id: orders}
`
	if err := s.Seed(ctx, strings.NewReader(updated)); err != nil {
		t.Fatalf("valid final relationships rejected: %v", err)
	}
	if err := s.db.QueryRow(`SELECT role FROM users WHERE user_id='mentor'`).Scan(&role); err != nil || role != "intern" {
		t.Fatalf("valid role change was not applied: %q, err=%v", role, err)
	}
}

func TestNFR_AUTH_API_1_ConcurrentRedemption(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err != nil {
		t.Fatal(err)
	}
	code, err := s.IssueInvitation(ctx, "intern", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	results := make(chan error, 12)
	var wg sync.WaitGroup
	for i := range 12 {
		wg.Add(1)
		go func(id int64) { defer wg.Done(); _, err := s.Bind(ctx, code, id, id); results <- err }(int64(200 + i))
	}
	wg.Wait()
	close(results)
	success := 0
	for err := range results {
		if err == nil {
			success++
		} else if err != ErrInvitation {
			t.Fatal(err)
		}
	}
	if success != 1 {
		t.Fatalf("successful bindings=%d", success)
	}
}

func TestNFR_AUTH_API_1_ConflictsDoNotConsumeCodes(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err != nil {
		t.Fatal(err)
	}
	first, _ := s.IssueInvitation(ctx, "intern", time.Hour)
	second, _ := s.IssueInvitation(ctx, "intern", time.Hour)
	other, _ := s.IssueInvitation(ctx, "other", time.Hour)
	if _, err := s.Bind(ctx, first, 100, 100); err != nil {
		t.Fatal(err)
	}
	for _, c := range []string{second, other} {
		if _, err := s.Bind(ctx, c, 100, 100); err != ErrConflict {
			t.Fatalf("conflict=%v", err)
		}
		hash := sha256.Sum256([]byte(c))
		var used sql.NullTime
		if err := s.db.QueryRow(`SELECT used_at FROM invitations WHERE code_hash=$1`, hash[:]).Scan(&used); err != nil || used.Valid {
			t.Fatalf("consumed on conflict: %v %v", used, err)
		}
	}
	if _, err := s.Bind(ctx, other, 101, 101); err != nil {
		t.Fatal(err)
	}
}

func TestNFR_AUTH_API_2_ClosedDatabase(t *testing.T) {
	s := integrationStore(t)
	s.Close()
	ctx := context.Background()
	if _, err := s.Bind(ctx, "a", 1, 1); err == nil {
		t.Fatal("expected db error")
	}
	if _, err := s.TelegramProfile(ctx, 1); err == nil {
		t.Fatal("expected db error")
	}
	if _, err := s.IssueInvitation(ctx, "intern", time.Hour); err == nil {
		t.Fatal("expected db error")
	}
	if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err == nil {
		t.Fatal("expected db error")
	}
}
