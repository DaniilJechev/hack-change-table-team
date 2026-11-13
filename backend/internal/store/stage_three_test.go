package store

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
)

const uploadYAML = `synthetic: true
team_id: orders
name: Учебная команда
people:
  - {user_id: mentor, name: Наставник, role: Наставник, areas: [Kafka], about: Отвечает за Kafka, contact_url: 'https://example.org/mentor', first_contact: true}
routing:
  - {situation: Kafka, go_to: Mentor, owner_id: mentor, wait_minutes: 30, then_id: lead, source_url: 'https://example.org/routing'}
do_not_disturb:
  - {person_id: mentor, direct_allowed: false, reason: Через лида, via_id: lead}
tools: [Go, PostgreSQL]
links: [{service: jira, url: 'https://example.org/ORD-1'}]
faq: []
`

func TestFR_H2_01_CardSchemaAndFieldPaths(t *testing.T) {
	raw, err := os.ReadFile("../../../seed/team_cards/demo-orders.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := decodeYAML(strings.NewReader(string(raw)), &document); err != nil {
		t.Fatal(err)
	}
	if issues := validateCardFields(document); len(issues) > 0 {
		t.Fatal(issues)
	}
	for _, tc := range []struct{ old, new, path string }{
		{"role: Наставник, ", "", "people[0].role"},
		{"name: Наставник, ", "name: '', ", "people[0].name"},
		{"areas: [Kafka]", "areas: Kafka", "people[0].areas"},
		{"areas: [Kafka]", "areas: [false]", "people[0].areas[0]"},
		{"contact_url: 'https://example.org/mentor'", "contact_url: 'javascript:secret'", "people[0].contact_url"},
		{"wait_minutes: 30", "wait_minutes: -1", "routing[0].wait_minutes"},
		{"direct_allowed: false", "direct_allowed: 'false'", "do_not_disturb[0].direct_allowed"},
		{"tools: [Go, PostgreSQL]", "tools: bad", "tools"},
		{"faq: []", "faq: [unapproved]", "faq"},
		{"first_contact: true", "first_contact: yes", "people[0].first_contact"},
		{"links: [{service: jira, url: 'https://example.org/ORD-1'}]", "links: [bad]", "links[0]"},
		{"synthetic: true", "synthetic: false", "synthetic"},
		{"first_contact: true}", "first_contact: true}\n  - {user_id: mentor, name: Второй, role: Наставник, areas: [], contact_url: 'https://example.org/mentor'}", "people[1].user_id"},
		{"name: Учебная команда", "name: Учебная команда\nsecret_value: secret", "*"},
	} {
		var card map[string]any
		if err := decodeYAML(strings.NewReader(strings.Replace(uploadYAML, tc.old, tc.new, 1)), &card); err != nil {
			t.Fatal(err)
		}
		found := false
		for _, issue := range validateCardFields(card) {
			if issue.Field == tc.path {
				found = true
			}
			if strings.Contains(issue.Message, "secret") {
				t.Fatal(issue)
			}
		}
		if !found {
			t.Fatalf("missing path %s", tc.path)
		}
	}
}

func TestFR_H2_01_UploadAuthorizationVersionsAndConcurrentRetry(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	for _, actor := range []Profile{intern, other} {
		if _, err := s.UploadTeamCard(ctx, actor, strings.NewReader(uploadYAML)); err != ErrForbidden {
			t.Fatal(err)
		}
	}
	for _, bad := range []string{"[", uploadYAML + "---\nx: y", strings.Repeat("x", (1<<20)+1), strings.Replace(uploadYAML, "role: Наставник, ", "", 1), strings.Replace(uploadYAML, "owner_id: mentor", "owner_id: other", 1)} {
		_, err := s.UploadTeamCard(ctx, mentor, strings.NewReader(bad))
		var validation *ValidationError
		if !errors.As(err, &validation) || len(validation.Fields) == 0 || err.Error() != "invalid team card" {
			t.Fatal(err)
		}
	}
	if _, err := s.UploadTeamCard(ctx, mentor, strings.NewReader(strings.Replace(uploadYAML, "team_id: orders", "team_id: other", 1))); err != ErrForbidden {
		t.Fatal(err)
	}
	first, err := s.UploadTeamCard(ctx, mentor, strings.NewReader(uploadYAML))
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			card, err := s.UploadTeamCard(ctx, lead, strings.NewReader(uploadYAML))
			if err != nil || card.Version != first.Version {
				t.Error(card, err)
			}
		}()
	}
	wg.Wait()
	changed, err := s.UploadTeamCard(ctx, lead, strings.NewReader(strings.Replace(uploadYAML, "Учебная команда", "Новая карточка", 1)))
	if err != nil || changed.Version <= first.Version {
		t.Fatal(changed, err)
	}
	old, err := s.TeamCard(ctx, intern, "people", first.Version)
	if err != nil || !strings.Contains(old.SourcePath, "#people") {
		t.Fatal(old, err)
	}
	if _, err := s.db.Exec(`UPDATE users SET role='intern' WHERE user_id='lead'`); err != nil {
		t.Fatal(err)
	}
	if _, err := s.UploadTeamCard(ctx, lead, strings.NewReader(uploadYAML)); err != ErrForbidden {
		t.Fatal(err)
	}
	s.Close()
	if _, err := s.UploadTeamCard(ctx, mentor, strings.NewReader(uploadYAML)); err == nil {
		t.Fatal("closed database accepted upload")
	}
}

func TestST3_FLAGS_1_DefaultsPermissionsAndDataRemainReadable(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	if err := s.ConfigureFlagDefaults(func(name string) string {
		if name == "FEATURE_ACCESS_MONITORING" {
			return "false"
		}
		return ""
	}); err != nil {
		t.Fatal(err)
	}
	flags, err := s.Flags(ctx, intern)
	if err != nil || flags["access_monitoring"] || !flags["team_escalation"] || len(flags) != 3 {
		t.Fatal(flags, err)
	}
	if err := s.ConfigureFlagDefaults(func(string) string { return "secret" }); err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatal(err)
	}
	for _, actor := range []Profile{intern, mentor, other} {
		if err := s.SetFlag(ctx, actor, "team_escalation", false); err != ErrForbidden {
			t.Fatal(err)
		}
	}
	if err := s.SetFlag(ctx, lead, "unknown", false); err != ErrInvalid {
		t.Fatal(err)
	}
	q, err := s.SaveQuestion(ctx, intern, Question{ID: "q", Text: "Kafka?", AssignedTo: mentor.UserID})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.AnswerQuestion(ctx, mentor, q.ID, "Учебный ответ"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "f", q.ID, true); err != nil {
		t.Fatal(err)
	}
	for _, name := range flagNames {
		if err := s.SetFlag(ctx, lead, name, false); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := s.SaveQuestion(ctx, intern, Question{ID: "new", Text: "Новый вопрос", AssignedTo: mentor.UserID}); err != ErrForbidden {
		t.Fatal(err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "f", q.ID, true); err != ErrForbidden {
		t.Fatal(err)
	}
	if _, err := s.FAQ(ctx, intern, "f"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Question(ctx, mentor, q.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveQuestion(ctx, intern, Question{ID: "local", Text: "Неэскалированный вопрос"}); err != nil {
		t.Fatal(err)
	}
	foreign, err := s.Flags(ctx, other)
	if err != nil || !foreign["team_escalation"] {
		t.Fatal(foreign, err)
	}
	if _, err := s.db.Exec(`UPDATE users SET role='intern' WHERE user_id='lead'`); err != nil {
		t.Fatal(err)
	}
	if err := s.SetFlag(ctx, lead, "access_monitoring", true); err != ErrForbidden {
		t.Fatal(err)
	}
	s.Close()
	if _, err := s.Flags(ctx, intern); err == nil {
		t.Fatal("expected unavailable")
	}
}

func TestFR_H2_05_EscalationContextAndSeparateBotAnswer(t *testing.T) {
	s, intern, mentor, _, other := dataStore(t)
	ctx := context.Background()
	if _, err := s.db.Exec(`UPDATE users SET internship_started_on=CURRENT_DATE-14 WHERE user_id='intern'`); err != nil {
		t.Fatal(err)
	}
	input := Question{ID: "q", Text: "Kafka?", AssignedTo: mentor.UserID, Context: QuestionContext{CurrentTask: "ORD-118", CheckedSources: []string{"team-card:people", "faq"}, TeamID: "forged"}}
	q, err := s.SaveQuestion(ctx, intern, input)
	if err != nil || q.Context.TeamID != "orders" || q.Context.InternshipWeek == nil || *q.Context.InternshipWeek != 3 {
		t.Fatal(q, err)
	}
	if _, err := s.db.Exec(`UPDATE users SET internship_started_on=CURRENT_DATE-28 WHERE user_id='intern'`); err != nil {
		t.Fatal(err)
	}
	retry, err := s.SaveQuestion(ctx, intern, input)
	if err != nil || *retry.Context.InternshipWeek != 3 {
		t.Fatal(retry, err)
	}
	input.Context.CurrentTask = "ORD-119"
	if _, err := s.SaveQuestion(ctx, intern, input); err != ErrConflict {
		t.Fatal(err)
	}
	card, err := s.TeamCard(ctx, intern, "people", 0)
	if err != nil {
		t.Fatal(err)
	}
	q, err = s.SaveBotAnswer(ctx, intern, "q", "Учебный ответ бота", []string{card.SourcePath})
	if err != nil || q.Answer != "" {
		t.Fatal(q, err)
	}
	if _, err := s.SaveBotAnswer(ctx, intern, "q", "Учебный ответ бота", []string{card.SourcePath}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveBotAnswer(ctx, intern, "q", "Перезаписать", []string{card.SourcePath}); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.SaveBotAnswer(ctx, other, "q", "Ответ", []string{"https://example.org/ORD-1"}); err != ErrNotFound {
		t.Fatal(err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "f", "q", true); err != ErrNotFound {
		t.Fatal("bot answer became human FAQ", err)
	}
	if _, err := s.RateBotAnswer(ctx, mentor, "q", true); err != ErrNotFound {
		t.Fatal(err)
	}
	q, err = s.RateBotAnswer(ctx, intern, "q", true)
	if err != nil || q.BotHelpful == nil || !*q.BotHelpful || q.Helpful != nil {
		t.Fatal(q, err)
	}
	if _, err := s.AnswerQuestion(ctx, mentor, "q", "Ответ человека"); err != nil {
		t.Fatal(err)
	}
	q, err = s.RateQuestion(ctx, intern, "q", false)
	if err != nil || q.Helpful == nil || *q.Helpful || !*q.BotHelpful {
		t.Fatal(q, err)
	}
	if _, err := s.PublishFAQ(ctx, mentor, "f", "q", true); err != nil {
		t.Fatal(err)
	}
	for _, source := range []string{"javascript:secret", "/api/v1/team-card?version=0#people", "/api/v1/team-card?version=-1", "/api/v1/team-card?version=9999#people", "/api/v1/faq/unknown"} {
		if _, err := s.SaveBotAnswer(ctx, intern, "q", "Ответ", []string{source}); err != ErrInvalid {
			t.Fatal(source, err)
		}
	}
	for _, bad := range []QuestionContext{{CurrentTask: strings.Repeat("x", 501)}, {CheckedSources: []string{""}}, {CheckedSources: make([]string, 21)}} {
		if _, err := s.SaveQuestion(ctx, intern, Question{ID: "bad", Text: "Вопрос", Context: bad}); err != ErrInvalid {
			t.Fatal(err)
		}
	}
	if _, err := s.MarkAccess(ctx, intern, "repo"); err != nil {
		t.Fatal(err)
	}
	interns, err := s.Interns(ctx, mentor)
	if err != nil || len(interns) != 1 || len(interns[0].Marks) != 1 || interns[0].Snapshot != nil {
		t.Fatal(interns, err)
	}
	mark := interns[0].Marks[0]
	if _, err := s.SaveSnapshot(ctx, intern, Snapshot{ID: "failed-probe", Objects: []AccessState{{ObjectID: "repo", Status: "requested", Error: "Источник временно недоступен"}}}); err != nil {
		t.Fatal(err)
	}
	interns, err = s.Interns(ctx, mentor)
	if err != nil || interns[0].Snapshot.Objects[0].Status != "requested" || interns[0].Snapshot.Objects[0].Error == "" || !interns[0].Marks[0].RequestedAt.Equal(mark.RequestedAt) {
		t.Fatal(interns, err)
	}
}

func TestST3_ESCALATE_1_SameQuestionRetainsBotAnswerAndCreationRetry(t *testing.T) {
	s, intern, mentor, lead, other := dataStore(t)
	ctx := context.Background()
	input := Question{ID: "existing", Text: "Почему Kafka?", Context: QuestionContext{CheckedSources: []string{"people", "faq"}}}
	created, err := s.SaveQuestion(ctx, intern, input)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveBotAnswer(ctx, intern, input.ID, "Учебный ответ бота", []string{"https://example.org/ORD-1"}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.RateBotAnswer(ctx, intern, input.ID, true); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Question(ctx, mentor, input.ID); err != ErrNotFound {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		q, err := s.EscalateQuestion(ctx, intern, input.ID, mentor.UserID)
		if err != nil || q.ID != created.ID || q.BotAnswer == "" || q.BotHelpful == nil || !*q.BotHelpful {
			t.Fatal(q, err)
		}
	}
	if _, err := s.Question(ctx, mentor, input.ID); err != nil {
		t.Fatal(err)
	}
	q, err := s.SaveQuestion(ctx, intern, input)
	if err != nil || q.AssignedTo != mentor.UserID || !q.CreatedAt.Equal(created.CreatedAt) {
		t.Fatal(q, err)
	}
	if _, err := s.EscalateQuestion(ctx, intern, input.ID, lead.UserID); err != ErrConflict {
		t.Fatal(err)
	}
	for _, actor := range []Profile{mentor, other} {
		if _, err := s.EscalateQuestion(ctx, actor, input.ID, actor.UserID); err != ErrNotFound {
			t.Fatal(err)
		}
	}
	if _, err := s.EscalateQuestion(ctx, intern, input.ID, other.UserID); err != ErrForbidden {
		t.Fatal(err)
	}
	if _, err := s.EscalateQuestion(ctx, intern, "/bad", mentor.UserID); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := s.EscalateQuestion(ctx, intern, input.ID, "unknown"); err != ErrNotFound {
		t.Fatal(err)
	}
	if err := s.SetFlag(ctx, lead, "team_escalation", false); err != nil {
		t.Fatal(err)
	}
	if _, err := s.EscalateQuestion(ctx, intern, input.ID, mentor.UserID); err != ErrForbidden {
		t.Fatal(err)
	}
	if _, err := s.AnswerQuestion(ctx, mentor, input.ID, "Ответ человека"); err != nil {
		t.Fatal(err)
	}
}

func TestST3_CARD_2_StageMetadataValidation(t *testing.T) {
	good := map[string]any{"synthetic": true, "team_id": "demo-orders", "name": "Demo",
		"stage": map[string]any{"epic_key": "ORD-100", "source_url": "https://example.invalid/jira/browse/ORD-100"}}
	if issues := validateCardFields(good); len(issues) > 0 {
		t.Fatal(issues)
	}
	good["stage"] = map[string]any{"epic_key": "", "source_url": "javascript:bad"}
	if issues := validateCardFields(good); len(issues) != 2 {
		t.Fatalf("expected two field errors, got %v", issues)
	}
	good["stage"] = "bad"
	if issues := validateCardFields(good); len(issues) != 1 || issues[0].Field != "stage" {
		t.Fatal(issues)
	}
}
