package store

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestNFR_DB_1_InvalidSeed(t *testing.T) {
	for _, input := range []string{
		`users: []`, `users: [`, `users: [{user_id: a, display_name: A, team_id: t, role: admin}]`,
		`users: [{user_id: a, display_name: A, team_id: t, role: intern, mentor_id: missing}]`,
		`users: [{user_id: a, display_name: A, team_id: t, role: intern, mentor_id: b}, {user_id: b, display_name: B, team_id: other, role: mentor}]`,
		`users: [{user_id: a, display_name: A, team_id: t, role: intern}, {user_id: a, display_name: A, team_id: t, role: intern}]`,
		`users: [{user_id: a, display_name: A, team_id: t, role: intern, extra: x}]`,
		`users: [{user_id: a, display_name: A, team_id: t, role: intern}, {user_id: b, display_name: B, team_id: other, role: mentor, mentor_id: a}]`,
		`users: [{display_name: A, team_id: t, role: intern}]`,
		"users: []\n---\nusers: []",
	} {
		if err := (&Store{}).Seed(context.Background(), strings.NewReader(input)); err == nil {
			t.Fatalf("accepted invalid seed: %s", input)
		}
	}
}

func TestNFR_AUTH_API_1_InvalidInput(t *testing.T) {
	s := &Store{}
	for _, ttl := range []int64{0, -1, 1000000000000000000} {
		if _, err := s.IssueInvitation(context.Background(), "user", time.Duration(ttl)); err == nil {
			t.Fatal("accepted invalid TTL")
		}
	}
	for _, v := range []struct {
		code     string
		id, chat int64
	}{{"", 1, 1}, {"a", 0, 0}, {"a", 1, 2}, {strings.Repeat("a", 129), 1, 1}} {
		if _, err := s.Bind(context.Background(), v.code, v.id, v.chat); err != ErrInvitation {
			t.Fatalf("err=%v", err)
		}
	}
}
