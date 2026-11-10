package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"
)

const cardYAML = `synthetic: true
team_id: orders
name: Учебная команда
notice: Синтетические данные
people:
  - {user_id: mentor, responsibility: Kafka}
faq: []
`
const catalogYAML = `synthetic: true
team_id: orders
notice: Синтетические данные
instruments: [github, jira, miro, google_calendar, telegram]
objects:
  - {id: repo, instrument: github, name: Repository, url: 'https://example.org/repo', request_url: 'https://example.org/request', owner_id: lead, roles: [intern, mentor, teamlead]}
  - {id: prod, instrument: github, name: Production, url: 'https://example.org/prod', owner_id: lead, roles: [intern], forbidden: true, reason: 'Стажёрам не выдаётся'}
`

func dataStore(t *testing.T) (*Store, Profile, Profile, Profile, Profile) {
	t.Helper()
	s := integrationStore(t)
	if err := s.SeedData(context.Background(), strings.NewReader(profilesYAML), strings.NewReader(cardYAML), strings.NewReader(catalogYAML)); err != nil {
		t.Fatal(err)
	}
	users := []Profile{}
	for _, id := range []string{"intern", "mentor", "lead", "other"} {
		p, err := s.User(context.Background(), id)
		if err != nil {
			t.Fatal(err)
		}
		users = append(users, p)
	}
	return s, users[0], users[1], users[2], users[3]
}
func TestST2_DATA_1_SeedVersionsRollbackAndPreservation(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	mark, err := s.MarkAccess(ctx, intern, "repo")
	if err != nil {
		t.Fatal(err)
	}
	first, err := s.TeamCard(ctx, intern, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err := s.SeedData(ctx, strings.NewReader(profilesYAML), strings.NewReader(cardYAML), strings.NewReader(catalogYAML)); err != nil {
			t.Fatal(err)
		}
	}
	same, _ := s.TeamCard(ctx, intern, "", 0)
	if same.Version != first.Version {
		t.Fatal("duplicate version")
	}
	changed := strings.Replace(cardYAML, "Учебная команда", "Новая версия", 1)
	if err := s.SeedData(ctx, strings.NewReader(profilesYAML), strings.NewReader(changed), nil); err != nil {
		t.Fatal(err)
	}
	latest, _ := s.TeamCard(ctx, intern, "people", 0)
	if latest.Version <= first.Version || len(latest.Document) != 1 || !strings.Contains(latest.SourcePath, "#people") {
		t.Fatalf("%+v", latest)
	}
	old, _ := s.TeamCard(ctx, intern, "", first.Version)
	if old.Document["name"] != "Учебная команда" {
		t.Fatal(old)
	}
	if err := s.SeedData(ctx, strings.NewReader(profilesYAML), strings.NewReader(cardYAML), nil); err != nil {
		t.Fatal(err)
	}
	reverted, _ := s.TeamCard(ctx, intern, "", 0)
	if reverted.Version <= latest.Version {
		t.Fatal("A -> B -> A must create a new version")
	}
	marks, _ := s.AccessMarks(ctx, intern)
	if len(marks) != 1 || !marks[0].RequestedAt.Equal(mark.RequestedAt) {
		t.Fatal("seed erased personal marks")
	}
	// Invalid catalog owner is detected after profile/card writes: all must roll back.
	badUsers := strings.Replace(profilesYAML, "display_name: Intern", "display_name: Changed", 1)
	badCatalog := strings.Replace(catalogYAML, "owner_id: lead", "owner_id: other", 1)
	if err := s.SeedData(ctx, strings.NewReader(badUsers), strings.NewReader(changed), strings.NewReader(badCatalog)); err == nil {
		t.Fatal("accepted foreign owner")
	}
	got, _ := s.User(ctx, "intern")
	card, _ := s.TeamCard(ctx, intern, "", 0)
	if got.DisplayName != "Intern" || card.Version != reverted.Version {
		t.Fatal("partial commit")
	}
	if _, err := s.TeamCard(ctx, intern, "missing", 0); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	if _, err := s.TeamCard(ctx, intern, "/bad", 0); !errors.Is(err, ErrInvalid) {
		t.Fatal(err)
	}
	if _, err := s.TeamCard(ctx, intern, "", 9999); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
}
func TestST2_REST_1_AccessMarksAndSnapshots(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	filtered, err := s.AccessCatalog(ctx, intern, []string{"repo"})
	if err != nil || len(filtered) != 1 || filtered[0].ID != "repo" {
		t.Fatal(filtered, err)
	}
	empty, _ := s.AccessCatalog(ctx, other, nil)
	if len(empty) != 0 {
		t.Fatal("foreign catalog")
	}
	if _, err := s.AccessCatalog(ctx, intern, []string{"/bad"}); err != ErrInvalid {
		t.Fatal(err)
	}
	for _, object := range []string{"prod", "unknown", "/bad"} {
		if _, err := s.MarkAccess(ctx, intern, object); err == nil {
			t.Fatal("invalid mark", object)
		}
	}
	// Concurrent retries retain one original request date.
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := s.MarkAccess(ctx, intern, "repo"); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	marks, _ := s.AccessMarks(ctx, intern)
	if len(marks) != 1 {
		t.Fatal(marks)
	}
	mark, _ := s.MarkAccess(ctx, intern, "repo")
	if !mark.RequestedAt.Equal(marks[0].RequestedAt) {
		t.Fatal("request timestamp reset")
	}
	if _, err := s.MarkAccess(ctx, other, "repo"); err != ErrNotFound {
		t.Fatal(err)
	}
	snap := Snapshot{ID: "snapshot-1", Objects: []AccessState{{ObjectID: "repo", Status: "missing"}, {ObjectID: "prod", Status: "forbidden"}}}
	a, err := s.SaveSnapshot(ctx, intern, snap)
	if err != nil {
		t.Fatal(err)
	}
	b, err := s.SaveSnapshot(ctx, intern, snap)
	if err != nil || !a.CreatedAt.Equal(b.CreatedAt) {
		t.Fatal(b, err)
	}
	snap.Objects[0].Status = "granted"
	if _, err := s.SaveSnapshot(ctx, intern, snap); err != ErrConflict {
		t.Fatal(err)
	}
	snap.ID = "snapshot-2"
	if _, err := s.SaveSnapshot(ctx, intern, snap); err != nil {
		t.Fatal(err)
	}
	latest, _ := s.LatestSnapshot(ctx, intern)
	if latest.ID != "snapshot-2" {
		t.Fatal(latest)
	}
	interns, err := s.Interns(ctx, mentor)
	if err != nil || len(interns) != 1 || interns[0].UserID != intern.UserID || interns[0].Snapshot.ID != "snapshot-2" {
		t.Fatal(interns, err)
	}
	if _, err := s.Interns(ctx, intern); err != ErrForbidden {
		t.Fatal(err)
	}
	if _, err := s.LatestSnapshot(ctx, other); err != ErrNotFound {
		t.Fatal(err)
	}
	// A different mentor's intern is visible only to the lead.
	if _, err := s.db.Exec(`INSERT INTO users(user_id,display_name,role,team_id) VALUES('second','Second','intern','orders')`); err != nil {
		t.Fatal(err)
	}
	interns, _ = s.Interns(ctx, mentor)
	if len(interns) != 1 {
		t.Fatal(interns)
	}
	interns, _ = s.Interns(ctx, lead)
	if len(interns) != 2 || interns[1].Snapshot != nil {
		t.Fatal(interns)
	}
	for _, bad := range []Snapshot{
		{ID: "bad", Objects: nil}, {ID: "bad", Objects: []AccessState{{ObjectID: "other", Status: "granted"}}},
		{ID: "bad", Objects: []AccessState{{ObjectID: "repo", Status: "unknown"}}},
		{ID: "bad", Objects: []AccessState{{ObjectID: "prod", Status: "granted"}}},
		{ID: "bad", Objects: []AccessState{{ObjectID: "repo", Status: "missing"}, {ObjectID: "repo", Status: "missing"}}},
	} {
		if _, err := s.SaveSnapshot(ctx, intern, bad); err != ErrInvalid {
			t.Fatal(bad, err)
		}
	}
}
func TestFR_H0_08_BriefPersistenceAndIdempotentEvents(t *testing.T) {
	s, intern, _, _, other := dataStore(t)
	ctx := context.Background()
	input := Brief{ID: "morning", Items: []BriefItem{{ID: "item", Source: "jira", Type: "jira_mention", Title: "Упоминание", URL: "https://example.org/ORD-1", PriorityClass: "people", CreatedAt: time.Now().UTC()}}}
	first, err := s.SaveBrief(ctx, intern, input)
	if err != nil || first.Status != "draft" {
		t.Fatal(first, err)
	}
	repeated, err := s.SaveBrief(ctx, intern, input)
	if err != nil || !first.CreatedAt.Equal(repeated.CreatedAt) {
		t.Fatal(repeated, err)
	}
	input.Items[0].Title = "Changed"
	if _, err := s.SaveBrief(ctx, intern, input); err != ErrConflict {
		t.Fatal(err)
	}
	if _, err := s.Brief(ctx, other, "morning"); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.BriefEvent(ctx, intern, "morning", BriefEvent{ID: "read", Kind: "read"}); err != ErrConflict {
		t.Fatal(err)
	}
	failed, err := s.BriefStatus(ctx, intern, "morning", "failed")
	if err != nil || failed.SentAt != nil {
		t.Fatal(failed, err)
	}
	sent, err := s.BriefStatus(ctx, intern, "morning", "sent")
	if err != nil || sent.SentAt == nil {
		t.Fatal(sent, err)
	}
	if _, err := s.BriefStatus(ctx, intern, "morning", "failed"); err != ErrConflict {
		t.Fatal(err)
	}
	sentAgain, err := s.BriefStatus(ctx, intern, "morning", "sent")
	if err != nil || !sentAgain.SentAt.Equal(*sent.SentAt) {
		t.Fatal(sentAgain, err)
	}
	event := BriefEvent{ID: "read", Kind: "click", ItemID: "item"}
	once, err := s.BriefEvent(ctx, intern, "morning", event)
	if err != nil {
		t.Fatal(err)
	}
	twice, err := s.BriefEvent(ctx, intern, "morning", event)
	if err != nil || !once.CreatedAt.Equal(twice.CreatedAt) {
		t.Fatal(twice, err)
	}
	event.Kind = "read"
	event.ItemID = ""
	if _, err := s.BriefEvent(ctx, intern, "morning", event); err != ErrConflict {
		t.Fatal(err)
	}
	read, _ := s.Brief(ctx, intern, "morning")
	if read.ReadAt == nil || !read.ReadAt.Equal(once.CreatedAt) {
		t.Fatal(read)
	}
	for _, kind := range []string{"read", "positive", "negative", "reply"} {
		if _, err := s.BriefEvent(ctx, intern, "morning", BriefEvent{ID: kind + "-2", Kind: kind}); err != nil {
			t.Fatal(err)
		}
	}
	after, _ := s.Brief(ctx, intern, "morning")
	if !after.ReadAt.Equal(*read.ReadAt) {
		t.Fatal("read_at changed")
	}
	for _, bad := range []BriefEvent{{ID: "x", Kind: "unknown"}, {ID: "x", Kind: "click"}, {ID: "x", Kind: "read", ItemID: "item"}, {ID: "x", Kind: "click", ItemID: "unknown"}} {
		if _, err := s.BriefEvent(ctx, intern, "morning", bad); err != ErrInvalid {
			t.Fatal(bad, err)
		}
	}
	if _, err := s.BriefEvent(ctx, other, "morning", BriefEvent{ID: "read", Kind: "read"}); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.BriefStatus(ctx, other, "morning", "sent"); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.BriefStatus(ctx, intern, "morning", "unknown"); err != ErrInvalid {
		t.Fatal(err)
	}
	input.Items[0].URL = "javascript:alert(1)"
	if _, err := s.SaveBrief(ctx, intern, input); err != ErrInvalid {
		t.Fatal(err)
	}
	input.Items = []BriefItem{}
	input.ID = "empty"
	if _, err := s.SaveBrief(ctx, intern, input); err != nil {
		t.Fatal(err)
	}
}
func TestFR_H2_06_FAQRequiresAnswerAuthorConsent(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	input := Question{ID: "q-1", Text: "Зачем Kafka?", AssignedTo: mentor.UserID}
	q, err := s.SaveQuestion(ctx, intern, input)
	if err != nil {
		t.Fatal(err)
	}
	twice, err := s.SaveQuestion(ctx, intern, input)
	if err != nil || !q.CreatedAt.Equal(twice.CreatedAt) {
		t.Fatal(twice, err)
	}
	changed := input
	changed.Text = "Changed"
	if _, err := s.SaveQuestion(ctx, intern, changed); err != ErrConflict {
		t.Fatal(err)
	}
	for _, actor := range []Profile{lead, other} {
		if _, err := s.Question(ctx, actor, q.ID); err != ErrNotFound {
			t.Fatal(err)
		}
	}
	if _, err := s.AnswerQuestion(ctx, intern, q.ID, "answer"); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "faq-1", q.ID, true); err != ErrNotFound {
		t.Fatal(err)
	}
	answered, err := s.AnswerQuestion(ctx, mentor, q.ID, "Kafka хранит учебные события")
	if err != nil || answered.Answer == "" {
		t.Fatal(answered, err)
	}
	if _, err := s.AnswerQuestion(ctx, mentor, q.ID, answered.Answer); err != nil {
		t.Fatal(err)
	}
	if _, err := s.AnswerQuestion(ctx, mentor, q.ID, "Different"); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "faq-1", q.ID, false); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := s.PublishFAQ(ctx, intern, "faq-1", q.ID, true); err != ErrNotFound {
		t.Fatal(err)
	}
	published, err := s.PublishFAQ(ctx, mentor, "faq-1", q.ID, true)
	if err != nil || published.AuthorID != mentor.UserID {
		t.Fatal(published, err)
	}
	again, err := s.PublishFAQ(ctx, mentor, "faq-1", q.ID, true)
	if err != nil || !again.CreatedAt.Equal(published.CreatedAt) {
		t.Fatal(again, err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "faq-2", q.ID, true); err != ErrConflict {
		t.Fatal(err)
	}
	found, err := s.SearchFAQ(ctx, intern, "kafka")
	if err != nil || len(found) != 1 || found[0].SourcePath != "/api/v1/faq/faq-1" {
		t.Fatal(found, err)
	}
	found, err = s.SearchFAQ(ctx, other, "kafka")
	if err != nil || len(found) != 0 {
		t.Fatal(found, err)
	}
	found, _ = s.SearchFAQ(ctx, intern, "% OR 1=1")
	if len(found) != 0 {
		t.Fatal("SQL pattern interpreted")
	}
	if _, err := s.FAQ(ctx, other, "faq-1"); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.FAQ(ctx, lead, "faq-1"); err != nil {
		t.Fatal(err)
	}
	rated, err := s.RateQuestion(ctx, intern, q.ID, true)
	if err != nil || rated.Helpful == nil || !*rated.Helpful {
		t.Fatal(rated, err)
	}
	if _, err := s.RateQuestion(ctx, mentor, q.ID, false); err != ErrNotFound {
		t.Fatal(err)
	}
	input.ID = "foreign"
	input.AssignedTo = other.UserID
	if _, err := s.SaveQuestion(ctx, intern, input); err != ErrForbidden {
		t.Fatal(err)
	}
	input.AssignedTo = "missing"
	if _, err := s.SaveQuestion(ctx, intern, input); err != ErrNotFound {
		t.Fatal(err)
	}
}
func TestST2_MCP_1_ProfileUsesStoredContext(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	if _, err := s.db.Exec(`UPDATE users SET logins='{"github":"demo"}', internship_started_on=CURRENT_DATE-14 WHERE user_id='intern'`); err != nil {
		t.Fatal(err)
	}
	p, err := s.UserProfile(ctx, intern)
	if err != nil || p.Logins["github"] != "demo" || p.TeamLeadID != "lead" || p.InternshipWeek == nil || *p.InternshipWeek != 3 {
		t.Fatal(p, err)
	}
	raw, err := json.Marshal(p)
	if err != nil || strings.Contains(string(raw), "telegram_chat_id") {
		t.Fatal(string(raw), err)
	}
	if _, err := s.User(ctx, "missing"); err != ErrNotFound {
		t.Fatal(err)
	}
}
