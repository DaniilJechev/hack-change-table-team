package store

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
)

func mockRules(status, reason, code string) string {
	return fmt.Sprintf(`synthetic: true
access_rules:
  - {team_id: orders, object_id: repo, status: %q, reason: %q, error_code: %q}
`, status, reason, code)
}

func TestMOCK_ACCESS_1_FourOutcomesAndForbidden(t *testing.T) {
	for _, status := range []string{"granted", "pending", "denied", "error"} {
		t.Run(status, func(t *testing.T) {
			s, intern, mentor, _, other := dataStore(t)
			ctx := context.Background()
			code := ""
			if status == "error" {
				code = "service_unavailable"
			}
			if err := s.SeedMockServices(ctx, strings.NewReader(mockRules(status, "Учебная причина", code))); err != nil {
				t.Fatal(err)
			}
			one, err := s.RequestMockAccess(ctx, intern, "repo")
			if err != nil {
				t.Fatal(err)
			}
			two, err := s.RequestMockAccess(ctx, intern, "repo")
			if err != nil {
				t.Fatal(err)
			}
			if one.ID != two.ID || one.Status != status || !one.Simulated || one.ErrorCode != code || one.Reason != "Учебная причина" {
				t.Fatalf("%+v %+v", one, two)
			}
			own, err := s.MockAccessRequests(ctx, intern)
			if err != nil || len(own) != 1 {
				t.Fatal(own, err)
			}
			for _, actor := range []Profile{mentor, other} {
				list, err := s.MockAccessRequests(ctx, actor)
				if err != nil || len(list) != 0 {
					t.Fatal(list, err)
				}
			}
			for _, tc := range []struct {
				actor  Profile
				object string
				want   error
			}{
				{intern, "prod", ErrForbidden}, {other, "repo", ErrNotFound}, {intern, "absent", ErrNotFound}, {intern, "/bad", ErrInvalid},
			} {
				if _, err := s.RequestMockAccess(ctx, tc.actor, tc.object); !errors.Is(err, tc.want) {
					t.Fatal(tc, err)
				}
			}
			marks, err := s.AccessMarks(ctx, intern)
			if err != nil {
				t.Fatal(err)
			}
			want := 0
			if status == "granted" || status == "pending" {
				want = 1
			}
			if len(marks) != want {
				t.Fatal(marks)
			}
		})
	}
}

func TestMOCK_ACCESS_2_ResolveConcurrentRequestAndPersistence(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	if err := s.SeedMockServices(ctx, strings.NewReader(mockRules("pending", "Ожидает согласования", ""))); err != nil {
		t.Fatal(err)
	}
	ids := make(chan string, 8)
	errs := make(chan error, 8)
	var group sync.WaitGroup
	for i := 0; i < 8; i++ {
		group.Add(1)
		go func() { defer group.Done(); v, e := s.RequestMockAccess(ctx, intern, "repo"); ids <- v.ID; errs <- e }()
	}
	group.Wait()
	close(ids)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	id := ""
	for next := range ids {
		if id != "" && id != next {
			t.Fatal("duplicate request")
		}
		id = next
	}
	for _, actor := range []Profile{intern, mentor, other} {
		if _, err := s.ResolveMockAccess(ctx, actor, id, "granted", ""); !errors.Is(err, ErrForbidden) {
			t.Fatal(err)
		}
	}
	foreignLead := Profile{UserID: other.UserID, TeamID: other.TeamID, Role: "teamlead"}
	if _, err := s.ResolveMockAccess(ctx, foreignLead, id, "granted", ""); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	for _, tc := range []struct{ id, status, reason string }{{"/bad", "granted", ""}, {id, "pending", ""}, {id, "denied", ""}, {id, "denied", strings.Repeat("x", 1001)}} {
		if _, err := s.ResolveMockAccess(ctx, lead, tc.id, tc.status, tc.reason); !errors.Is(err, ErrInvalid) {
			t.Fatal(tc, err)
		}
	}
	result, err := s.ResolveMockAccess(ctx, lead, id, "denied", "Учебный отказ")
	if err != nil {
		t.Fatal(err)
	}
	repeated, err := s.ResolveMockAccess(ctx, lead, id, "denied", "Учебный отказ")
	if err != nil || !repeated.UpdatedAt.Equal(result.UpdatedAt) {
		t.Fatal(repeated, err)
	}
	if _, err := s.ResolveMockAccess(ctx, lead, id, "granted", ""); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	complete, err := s.MarkMockProgress(ctx, intern, EquatorItem, true)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.SeedDataWithMocks(ctx, strings.NewReader(profilesYAML), nil, strings.NewReader(catalogYAML), strings.NewReader(mockRules("granted", "Выдано", ""))); err != nil {
		t.Fatal(err)
	}
	// A new connection pool models a restarted backend process; no in-memory state is reused.
	var schema string
	if err := s.db.QueryRow(`SELECT current_schema()`).Scan(&schema); err != nil {
		t.Fatal(err)
	}
	dsn := os.Getenv("TEST_DATABASE_URL")
	sep := "?"
	if strings.Contains(dsn, "?") {
		sep = "&"
	}
	restarted, err := Open(ctx, dsn+sep+"search_path="+schema)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
	stored, err := restarted.MockAccessRequests(ctx, intern)
	if err != nil || len(stored) != 1 || stored[0].Status != "denied" {
		t.Fatal(stored, err)
	}
	progress, err := restarted.MockProgress(ctx, intern)
	if err != nil || !progress[0].Completed || !progress[0].CompletedAt.Equal(*complete.CompletedAt) {
		t.Fatal(progress, err)
	}
	again, err := restarted.MarkMockProgress(ctx, intern, EquatorItem, true)
	if err != nil || !again.UpdatedAt.Equal(*complete.UpdatedAt) {
		t.Fatal(again, err)
	}
	empty, err := s.MockProgress(ctx, other)
	if err != nil || empty[0].Completed || empty[0].CompletedAt != nil {
		t.Fatal(empty, err)
	}
	reset, err := s.MarkMockProgress(ctx, intern, EquatorItem, false)
	if err != nil || reset.Completed || reset.CompletedAt != nil {
		t.Fatal(reset, err)
	}
	if _, err := s.MarkMockProgress(ctx, intern, "unknown", true); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
}

func TestMOCK_DB_1_UnconfiguredRetryAndAtomicSeed(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	missing, err := s.RequestMockAccess(ctx, intern, "repo")
	if err != nil || missing.ErrorCode != "service_unconfigured" {
		t.Fatal(missing, err)
	}
	for _, raw := range []string{
		"[", "synthetic: false", mockRules("bogus", "", ""), mockRules("denied", "", ""),
		mockRules("error", "", "secret"), mockRules("granted", "", "service_unavailable"),
		strings.Replace(mockRules("pending", "", ""), "object_id: repo", "object_id: absent", 1),
		strings.Replace(mockRules("pending", "", ""), "team_id: orders", "team_id: other", 1),
	} {
		if err := s.SeedMockServices(ctx, strings.NewReader(raw)); err == nil {
			t.Fatal("accepted invalid rules", raw)
		}
	}
	invalid := strings.Replace(mockRules("pending", "", ""), "object_id: repo", "object_id: absent", 1)
	changed := strings.Replace(profilesYAML, "display_name: Intern", "display_name: Changed", 1)
	if err := s.SeedDataWithMocks(ctx, strings.NewReader(changed), nil, nil, strings.NewReader(invalid)); err == nil {
		t.Fatal("accepted unknown object")
	}
	user, err := s.User(ctx, intern.UserID)
	if err != nil || user.DisplayName != "Intern" {
		t.Fatal(user, err)
	}
	if err := s.SeedDataWithMocks(ctx, strings.NewReader(changed), nil, nil, strings.NewReader("[")); err == nil {
		t.Fatal("accepted invalid JSON/YAML")
	}
	if err := s.SeedMockServices(ctx, strings.NewReader(mockRules("granted", "Выдано", ""))); err != nil {
		t.Fatal(err)
	}
	retried, err := s.RequestMockAccess(ctx, intern, "repo")
	if err != nil || retried.ID != missing.ID || retried.Status != "granted" {
		t.Fatal(retried, err)
	}
}

func TestMOCK_DB_1_RuleValidation(t *testing.T) {
	withStep := func(step, status string) string {
		return strings.Replace(mockRules("pending", "", ""), "}\n", ", demo_steps: {"+step+": "+status+"}}\n", 1)
	}
	for _, raw := range []string{
		strings.Replace(mockRules("pending", "", ""), "team_id: orders", "team_id: /bad", 1),
		mockRules("pending", strings.Repeat("x", 1001), ""),
		mockRules("pending", "", "") + "  - {team_id: orders, object_id: repo, status: pending}",
		withStep("step_9", "granted"),
		withStep("step_2", "bogus"),
	} {
		if _, err := decodeMockSeed(strings.NewReader(raw)); err == nil {
			t.Fatal("accepted invalid rule")
		}
	}
	if _, err := decodeMockSeed(strings.NewReader(withStep("step_2", "granted"))); err != nil {
		t.Fatal(err)
	}
}
