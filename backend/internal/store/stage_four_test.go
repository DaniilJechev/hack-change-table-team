package store

import (
	"context"
	"database/sql"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/migrations"
	"github.com/pressly/goose/v3"
)

func stageFourBrief(id string) Brief {
	return Brief{ID: id, Items: []BriefItem{{ID: "same-change", Source: "jira", Type: "mention", Title: "Synthetic mention", URL: "https://example.org/issue#comment", PriorityClass: "people", CreatedAt: time.Now().UTC()}}}
}
func TestST4_REDIRECT_1_TokensDeliveryConcurrentClicksAndHead(t *testing.T) {
	s, intern, _, _, other := dataStore(t)
	ctx := context.Background()
	first, err := s.SaveBrief(ctx, intern, stageFourBrief("one"))
	if err != nil {
		t.Fatal(err)
	}
	token := first.Links["same-change"]
	if len(token) != 64 {
		t.Fatal(first)
	}
	again, err := s.SaveBrief(ctx, intern, stageFourBriefWithItems("one", first.Items))
	if err != nil || again.Links["same-change"] != token {
		t.Fatal(again, err)
	}
	otherBrief, err := s.SaveBrief(ctx, other, stageFourBriefWithItems("one", first.Items))
	if err != nil || otherBrief.Links["same-change"] == token {
		t.Fatal(otherBrief, err)
	}
	for _, bad := range []string{"bad", strings.Repeat("0", 64), token} {
		if _, err := s.ResolveBriefLink(ctx, bad, true); err != ErrNotFound {
			t.Fatal(err)
		}
	}
	if _, err := s.BriefStatus(ctx, intern, "one", "sent"); err != nil {
		t.Fatal(err)
	}
	if url, err := s.ResolveBriefLink(ctx, token, false); err != nil || url != first.Items[0].URL {
		t.Fatal(url, err)
	}
	head, _ := s.Brief(ctx, intern, "one")
	if head.ReadAt != nil {
		t.Fatal("HEAD counted as a read")
	}
	var wg sync.WaitGroup
	errs := make(chan error, 12)
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, err := s.ResolveBriefLink(ctx, token, true); errs <- err }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	read, _ := s.Brief(ctx, intern, "one")
	if read.ReadAt == nil {
		t.Fatal(read)
	}
	if _, err := s.ResolveBriefLink(ctx, token, true); err != nil {
		t.Fatal(err)
	}
	after, _ := s.Brief(ctx, intern, "one")
	if !after.ReadAt.Equal(*read.ReadAt) {
		t.Fatal("read_at moved")
	}
	var count int
	if err := s.db.QueryRow(`SELECT count(*) FROM brief_events`).Scan(&count); err != nil || count != 1 {
		t.Fatal(count, err)
	}
	if _, err := s.Brief(ctx, other, "missing"); err != ErrNotFound {
		t.Fatal(err)
	}
}
func stageFourBriefWithItems(id string, items []BriefItem) Brief { return Brief{ID: id, Items: items} }

func TestST4_BRIEF_1_AtomicCalendarAndPersistentSettings(t *testing.T) {
	s, intern, _, _, other := dataStore(t)
	ctx := context.Background()
	state, err := s.BriefState(ctx, intern)
	if err != nil || state.LastSent != nil || state.Calendar != nil || len(state.Seen) != 0 {
		t.Fatal(state, err)
	}
	b, err := s.SaveBrief(ctx, intern, stageFourBrief("first"))
	if err != nil {
		t.Fatal(err)
	}
	calendar := map[string]any{"events": map[string]any{"e": map[string]any{"start": "12:00"}}}
	first, err := s.BriefDelivery(ctx, intern, b.ID, "sent", calendar)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveBrief(ctx, intern, stageFourBrief("failed")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.BriefDelivery(ctx, intern, "failed", "failed", calendar); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := s.BriefStatus(ctx, intern, "failed", "failed"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveBrief(ctx, intern, stageFourBrief("calendar-down")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.BriefDelivery(ctx, intern, "calendar-down", "sent", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := s.BriefDelivery(ctx, intern, b.ID, "sent", map[string]any{"overwrite": true}); err != nil {
		t.Fatal(err)
	}
	again, _ := s.Brief(ctx, intern, b.ID)
	if !again.SentAt.Equal(*first.SentAt) {
		t.Fatal("retry moved sent_at")
	}
	state, err = s.BriefState(ctx, intern)
	if err != nil || state.Calendar["events"] == nil || len(state.Seen) != 1 {
		t.Fatal(state, err)
	}
	foreign, err := s.BriefState(ctx, other)
	if err != nil || foreign.LastSent != nil || foreign.Calendar != nil {
		t.Fatal(foreign, err)
	}
	settings, err := s.BriefSettings(ctx, intern)
	if err != nil || settings.Time != nil || settings.Quiet {
		t.Fatal(settings, err)
	}
	at, quiet := "08:15", true
	if _, err := s.SetBriefSettings(ctx, intern, &at, &quiet); err != nil {
		t.Fatal(err)
	}
	quiet = false
	if _, err := s.SetBriefSettings(ctx, intern, nil, &quiet); err != nil {
		t.Fatal(err)
	}
	settings, err = s.BriefSettings(ctx, intern)
	if err != nil || settings.Quiet || *settings.Time != "08:15" {
		t.Fatal(settings, err)
	}
	at = ""
	settings, err = s.SetBriefSettings(ctx, intern, &at, nil)
	if err != nil || settings.Time != nil || settings.Quiet {
		t.Fatal(settings, err)
	}
	for _, at := range []string{"25:00", "9:30", "12:61"} {
		if _, err := s.SetBriefSettings(ctx, intern, &at, nil); err != ErrInvalid {
			t.Fatal(err)
		}
	}
	if _, err := s.SetBriefSettings(ctx, intern, nil, nil); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := s.BriefDelivery(ctx, intern, b.ID, "sent", map[string]any{"too_big": strings.Repeat("x", (512<<10)+1)}); err != ErrInvalid {
		t.Fatal(err)
	}
}

func TestST4_STATS_1_RolesAndDistinctBriefs(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	for _, p := range []Profile{intern, other} {
		for _, id := range []string{"sent", "draft", "failed"} {
			if _, err := s.SaveBrief(ctx, p, stageFourBrief(id)); err != nil {
				t.Fatal(err)
			}
		}
		if _, err := s.BriefStatus(ctx, p, "sent", "sent"); err != nil {
			t.Fatal(err)
		}
		if _, err := s.BriefStatus(ctx, p, "failed", "failed"); err != nil {
			t.Fatal(err)
		}
	}
	for _, event := range []BriefEvent{{ID: "read", Kind: "read"}, {ID: "click1", Kind: "click", ItemID: "same-change"}, {ID: "click2", Kind: "click", ItemID: "same-change"}, {ID: "positive1", Kind: "positive"}, {ID: "positive2", Kind: "positive"}, {ID: "negative", Kind: "negative"}} {
		if _, err := s.BriefEvent(ctx, intern, "sent", event); err != nil {
			t.Fatal(err)
		}
	}
	for _, actor := range []Profile{intern, mentor, lead} {
		rows, err := s.BriefStats(ctx, actor)
		if err != nil || len(rows) != 1 || rows[0].UserID != intern.UserID || rows[0].Sent != 1 || rows[0].Read != 1 || rows[0].Clicked != 1 || rows[0].Positive != 1 || rows[0].Negative != 1 {
			t.Fatal(rows, err)
		}
	}
	rows, err := s.BriefStats(ctx, other)
	if err != nil || len(rows) != 1 || rows[0].Read != 0 {
		t.Fatal(rows, err)
	}
	var percent float64
	if err := s.db.QueryRow(`SELECT read_percent FROM metrics_briefs`).Scan(&percent); err != nil || percent != 50 {
		t.Fatal(percent, err)
	}
}

func TestST4_METRICS_1_HelpfulAccessAndAudit(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	var empty sql.NullFloat64
	if err := s.db.QueryRow(`SELECT read_percent FROM metrics_briefs`).Scan(&empty); err != nil || empty.Valid {
		t.Fatal(empty, err)
	}
	for _, capability := range []string{"h2", "h3"} {
		if _, err := s.SaveQuestion(ctx, intern, Question{ID: capability, Text: "Synthetic?", Capability: capability}); err != nil {
			t.Fatal(err)
		}
		if _, err := s.SaveBotAnswer(ctx, intern, capability, "Synthetic answer", []string{"https://example.org/source"}); err != nil {
			t.Fatal(err)
		}
		if _, err := s.RateBotAnswer(ctx, intern, capability, false); err != nil {
			t.Fatal(err)
		}
		if _, err := s.RateBotAnswer(ctx, intern, capability, true); err != nil {
			t.Fatal(err)
		}
		var rated, helpful int
		if err := s.db.QueryRow(`SELECT rated,helpful FROM metrics_helpful WHERE capability=$1 AND answer_kind='bot'`, capability).Scan(&rated, &helpful); err != nil || rated != 1 || helpful != 1 {
			t.Fatal(rated, helpful, err)
		}
	}
	if _, err := s.SaveQuestion(ctx, intern, Question{ID: "h3", Text: "Synthetic?", Capability: "h2"}); err != ErrConflict {
		t.Fatal(err)
	}
	if _, err := s.SaveQuestion(ctx, intern, Question{ID: "bad", Text: "Synthetic?", Capability: "h4"}); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := s.MarkAccess(ctx, intern, "repo"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveSnapshot(ctx, intern, Snapshot{ID: "error", Objects: []AccessState{{ObjectID: "repo", Status: "granted", Error: "source_error"}}}); err != nil {
		t.Fatal(err)
	}
	var completed int
	var hours sql.NullFloat64
	if err := s.db.QueryRow(`SELECT completed,mean_hours FROM metrics_access WHERE instrument='github'`).Scan(&completed, &hours); err != nil || completed != 0 || hours.Valid {
		t.Fatal(completed, hours, err)
	}
	if _, err := s.SaveSnapshot(ctx, intern, Snapshot{ID: "granted", Objects: []AccessState{{ObjectID: "repo", Status: "granted"}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.db.QueryRow(`SELECT completed,mean_hours FROM metrics_access WHERE instrument='github'`).Scan(&completed, &hours); err != nil || completed != 1 || !hours.Valid || hours.Float64 < 0 {
		t.Fatal(completed, hours, err)
	}
	call := MCPCall{ID: "c1", Case: "brief_now", Server: "github", Tool: "list_pull_requests", Outcome: "ok"}
	for i := 0; i < 2; i++ {
		if err := s.SaveMCPCall(ctx, intern, call); err != nil {
			t.Fatal(err)
		}
	}
	call.Outcome = "tool_denied"
	if err := s.SaveMCPCall(ctx, intern, call); err != ErrConflict {
		t.Fatal(err)
	}
	call.ID = "c2"
	if err := s.SaveMCPCall(ctx, intern, call); err != nil {
		t.Fatal(err)
	}
	call.Tool = "token=secret"
	if err := s.SaveMCPCall(ctx, intern, call); err != ErrInvalid {
		t.Fatal(err)
	}
	var calls int
	if err := s.db.QueryRow(`SELECT sum(calls) FROM metrics_mcp`).Scan(&calls); err != nil || calls != 2 {
		t.Fatal(calls, err)
	}
	if err := s.ProvisionMetricsRole(ctx, ""); err != nil {
		t.Fatal(err)
	}
}

func TestST4_RAG_1_MigrationPreservesExistingIndex(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	provider, err := goose.NewProvider(goose.DialectPostgres, s.db, migrations.Files)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := provider.DownTo(ctx, 4); err != nil {
		t.Fatal(err)
	}
	// This table now predates migration 5, as an index created by the old agent did.
	if err := s.Seed(ctx, strings.NewReader(profilesYAML)); err != nil {
		t.Fatal(err)
	}
	if _, err := s.db.Exec(`INSERT INTO questions(team_id,question_id,user_id,text) VALUES('orders','legacy','intern','Synthetic legacy question')`); err != nil {
		t.Fatal(err)
	}
	if _, err := s.db.Exec(`INSERT INTO rag_chunks VALUES('c','sha','f.md','H','https://example.org/f#h','Text',array_fill(0.1::real,ARRAY[384])::public.vector)`); err != nil {
		t.Fatal(err)
	}
	if err := s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	actor, err := s.User(ctx, "intern")
	if err != nil {
		t.Fatal(err)
	}
	legacy, err := s.SaveQuestion(ctx, actor, Question{ID: "legacy", Text: "Synthetic legacy question"})
	if err != nil || legacy.Capability != "unknown" {
		t.Fatal(legacy, err)
	}
	var count int
	if err := s.db.QueryRow(`SELECT count(*) FROM rag_chunks`).Scan(&count); err != nil || count != 1 {
		t.Fatal(count, err)
	}
}

func TestST4_METRICS_1_SQLRoleHasOnlyAggregateAccess(t *testing.T) {
	s, _, _, _, _ := dataStore(t)
	ctx := context.Background()
	for i := 0; i < 2; i++ {
		if err := s.ProvisionMetricsRole(ctx, "synthetic'quoted-password"); err != nil {
			t.Fatal(err)
		}
	}
	var rawAllowed, viewAllowed, super bool
	if err := s.db.QueryRow(`SELECT has_table_privilege('buddy_metrics',current_schema()||'.users','SELECT'),has_table_privilege('buddy_metrics',current_schema()||'.metrics_briefs','SELECT'),(SELECT rolsuper FROM pg_roles WHERE rolname='buddy_metrics')`).Scan(&rawAllowed, &viewAllowed, &super); err != nil || rawAllowed || !viewAllowed || super {
		t.Fatal(rawAllowed, viewAllowed, super, err)
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`SET LOCAL ROLE buddy_metrics`); err != nil {
		t.Fatal(err)
	}
	var count int
	if err := tx.QueryRow(`SELECT sent FROM metrics_briefs`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Exec(`SELECT text FROM questions`); err == nil {
		t.Fatal("raw question access allowed")
	}
}

func TestST4_BRIEF_1_ClosedDatabaseErrors(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	s.Close()
	ctx := context.Background()
	quiet := true
	if _, err := s.SaveBrief(ctx, intern, stageFourBrief("closed")); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.Brief(ctx, intern, "closed"); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.ResolveBriefLink(ctx, strings.Repeat("0", 64), true); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.BriefState(ctx, intern); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.BriefStats(ctx, intern); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.BriefSettings(ctx, intern); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.SetBriefSettings(ctx, intern, nil, &quiet); err == nil {
		t.Fatal("expected error")
	}
	if err := s.SaveMCPCall(ctx, intern, MCPCall{ID: "closed", Case: "brief_now", Server: "github", Tool: "list_pull_requests", Outcome: "ok"}); err == nil {
		t.Fatal("expected error")
	}
	if err := s.ProvisionMetricsRole(ctx, "synthetic"); err == nil {
		t.Fatal("expected error")
	}
}

func TestST4_BRIEF_1_CatalogTargetsValidatedAtomically(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	valid := strings.Replace(catalogYAML, "id: repo,", "id: repo, target: {owner: synthetic, repo: synthetic-repo, keywords: [repository]},", 1)
	if err := s.SeedData(ctx, strings.NewReader(profilesYAML), nil, strings.NewReader(valid)); err != nil {
		t.Fatal(err)
	}
	objects, err := s.AccessCatalog(ctx, intern, []string{"repo"})
	if err != nil || len(objects) != 1 || objects[0].Target["repo"] != "synthetic-repo" {
		t.Fatal(objects, err)
	}
	for _, target := range []string{"{token: secret}", "{owner: ''}", "{keywords: [7]}", "{keywords: x}", "{chat_id: 7}"} {
		bad := strings.Replace(catalogYAML, "id: repo,", "id: repo, target: "+target+",", 1)
		if err := s.SeedData(ctx, strings.NewReader(profilesYAML), nil, strings.NewReader(bad)); err == nil {
			t.Fatal("invalid target accepted")
		}
	}
	for _, target := range []map[string]any{{"chat_id": 0}, {"chat_id": "secret"}} {
		if err := validateTarget("telegram", target); err == nil {
			t.Fatal("invalid chat_id accepted")
		}
	}
	if err := validateTarget("telegram", map[string]any{"chat_id": -100123}); err != nil {
		t.Fatal(err)
	}
}
