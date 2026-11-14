package store

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

func TestFR_H4_12_Config(t *testing.T) {
	for _, raw := range []string{"", "1", "7", "36500"} {
		s := &Store{}
		if err := s.ConfigureEquatorHistory(func(string) string { return raw }); err != nil || s.equatorDays() < 1 {
			t.Fatal(raw, err)
		}
	}
	for _, raw := range []string{"0", "-1", "1.5", "bad", "36501"} {
		if err := (&Store{}).ConfigureEquatorHistory(func(string) string { return raw }); err == nil {
			t.Fatal(raw)
		}
	}
	if (&Store{}).equatorDays() != 7 {
		t.Fatal("default")
	}
}

func equatorRecord(actor Profile, id string, generation int64) map[string]any {
	return map[string]any{"id": id, "user_id": actor.UserID, "team_id": actor.TeamID, "generation": float64(generation), "passed": true, "current": true, "report": "Synthetic report", "file_id": "", "demo": false}
}

func TestFR_H4_12_14_HistoryGenerationExpiryAndFile(t *testing.T) {
	s, intern, mentor, _, other := dataStore(t)
	ctx := context.Background()
	n, err := s.InvalidateEquator(ctx, intern, false)
	if err != nil {
		t.Fatal(err)
	}
	record := equatorRecord(intern, "h4-first", n)
	first, err := s.SaveEquator(ctx, intern, record)
	if err != nil || first["current"] != true {
		t.Fatal(first, err)
	}
	record["file_id"] = "telegram-file"
	record["passed"] = false
	updated, err := s.SaveEquator(ctx, intern, record)
	if err != nil || updated["file_id"] != "telegram-file" || updated["passed"] != true {
		t.Fatal(updated, err)
	}
	record["file_id"] = "replaced"
	again, err := s.SaveEquator(ctx, intern, record)
	if err != nil || again["file_id"] != "telegram-file" {
		t.Fatal(again, err)
	}
	for _, actor := range []Profile{mentor, other} {
		items, e := s.EquatorHistory(ctx, actor)
		if e != nil || len(items) != 0 {
			t.Fatal(items, e)
		}
	}
	n2, err := s.InvalidateEquator(ctx, intern, false)
	if err != nil || n2 <= n {
		t.Fatal(n2, err)
	}
	late, err := s.SaveEquator(ctx, intern, equatorRecord(intern, "h4-late", n))
	if err != nil || late["current"] != false {
		t.Fatal(late, err)
	}
	fresh, err := s.SaveEquator(ctx, intern, equatorRecord(intern, "h4-fresh", n2))
	if err != nil || fresh["current"] != true {
		t.Fatal(fresh, err)
	}
	items, err := s.EquatorHistory(ctx, intern)
	if err != nil || len(items) != 3 {
		t.Fatal(items, err)
	}
	if _, err = s.db.Exec(`UPDATE equator_checks SET expires_at=$1 WHERE check_id='h4-first'`, time.Now().Add(-time.Second)); err != nil {
		t.Fatal(err)
	}
	items, err = s.EquatorHistory(ctx, intern)
	if err != nil || len(items) != 2 {
		t.Fatal(items, err)
	}
	if _, err = s.InvalidateEquator(ctx, intern, true); err != nil {
		t.Fatal(err)
	}
	items, err = s.EquatorHistory(ctx, intern)
	if err != nil || len(items) != 0 {
		t.Fatal(items, err)
	}
}

func TestFR_H4_16_AtomicDeliveryAndValidation(t *testing.T) {
	s, intern, _, _, other := dataStore(t)
	ctx := context.Background()
	n, _ := s.InvalidateEquator(ctx, intern, false)
	if _, err := s.SaveEquator(ctx, intern, equatorRecord(intern, "h4-send", n)); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	results := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := s.EquatorDelivery(ctx, intern, "h4-send", "file", "", true)
			results <- err
		}()
	}
	wg.Wait()
	close(results)
	success, conflict := 0, 0
	for err := range results {
		if err == nil {
			success++
		} else if errors.Is(err, ErrConflict) {
			conflict++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || conflict != 1 {
		t.Fatal(success, conflict)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "file", "sent", false); err != nil {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "file", "sent", false); err != nil {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "file", "", true); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, other, "h4-send", "report", "", true); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "report", "sent", false); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "report", "", true); err != nil {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "report", "unknown", false); err != nil {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "report", "", true); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "report", "", true, true); err != nil {
		t.Fatal(err)
	}
	if _, err := s.EquatorDelivery(ctx, intern, "h4-send", "report", "", true, true); !errors.Is(err, ErrConflict) {
		t.Fatal("explicit retry must still serialize concurrent requests", err)
	}
	for _, tc := range []struct {
		id, part, state string
		claim           bool
	}{{"/bad", "file", "", true}, {"h4-send", "bad", "", true}, {"h4-send", "file", "pending", false}} {
		if _, err := s.EquatorDelivery(ctx, intern, tc.id, tc.part, tc.state, tc.claim); !errors.Is(err, ErrInvalid) {
			t.Fatal(err)
		}
	}
	for _, v := range []map[string]any{{}, {"id": "/bad"}, {"id": "valid", "user_id": "other"}, {"id": "valid", "team_id": "other"}, {"id": "valid", "demo": true}, {"id": "valid", "passed": "yes"}, {"id": "valid", "passed": true, "generation": -1.0}} {
		if _, err := s.SaveEquator(ctx, intern, v); err == nil {
			t.Fatal(v)
		}
	}
}

func TestFR_H4_15_MentorChatOnlyOwn(t *testing.T) {
	s, intern, _, _, other := dataStore(t)
	ctx := context.Background()
	chat, err := s.MentorChat(ctx, intern)
	if err != nil || chat != nil {
		t.Fatal(chat, err)
	}
	if _, err = s.db.Exec(`UPDATE users SET telegram_chat_id=111,telegram_user_id=111 WHERE user_id='mentor'`); err != nil {
		t.Fatal(err)
	}
	chat, err = s.MentorChat(ctx, intern)
	if err != nil || chat == nil || *chat != 111 {
		t.Fatal(chat, err)
	}
	chat, err = s.MentorChat(ctx, other)
	if err != nil || chat != nil {
		t.Fatal(chat, err)
	}
}
