package store

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"testing"
)

func TestDESKTOP_02_DeviceAndSession(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	call := func(op string, in ChannelInput) any {
		t.Helper()
		v, e := s.Channel(ctx, op, in)
		if e != nil {
			t.Fatal(op, e)
		}
		return v
	}
	device := call("device", ChannelInput{}).(map[string]any)
	secret, code := device["device_secret"].(string), device["user_code"].(string)
	if call("token", ChannelInput{Secret: secret}).(map[string]any)["pending"] != true {
		t.Fatal("not pending")
	}
	if _, e := s.Channel(ctx, "approve", ChannelInput{UserID: intern.UserID, Code: code}); !errors.Is(e, ErrNotFound) {
		t.Fatal("unbound", e)
	}
	if _, e := s.db.Exec(`UPDATE users SET telegram_user_id=985,telegram_chat_id=985 WHERE user_id=$1`, intern.UserID); e != nil {
		t.Fatal(e)
	}
	call("approve", ChannelInput{UserID: intern.UserID, Code: code})
	if _, e := s.Channel(ctx, "approve", ChannelInput{UserID: intern.UserID, Code: code}); !errors.Is(e, ErrNotFound) {
		t.Fatal("double approval")
	}
	token := call("token", ChannelInput{Secret: secret}).(map[string]any)["token"].(string)
	p := call("session", ChannelInput{Secret: token}).(Profile)
	if p.UserID != intern.UserID || p.TeamID != intern.TeamID {
		t.Fatal(p)
	}
	if _, e := s.Channel(ctx, "token", ChannelInput{Secret: secret}); !errors.Is(e, ErrNotFound) {
		t.Fatal("code reused")
	}
	call("logout", ChannelInput{Secret: token})
	if _, e := s.Channel(ctx, "session", ChannelInput{Secret: token}); !errors.Is(e, ErrNotFound) {
		t.Fatal("session not revoked")
	}
	d2 := call("device", ChannelInput{}).(map[string]any)
	if _, e := s.db.Exec(`UPDATE desktop_devices SET expires_at=now()-interval '1 second' WHERE code=$1`, d2["user_code"]); e != nil {
		t.Fatal(e)
	}
	if _, e := s.Channel(ctx, "token", ChannelInput{Secret: d2["device_secret"].(string)}); !errors.Is(e, ErrNotFound) {
		t.Fatal("expired")
	}
	call("device", ChannelInput{})
	for _, in := range []ChannelInput{{Secret: "bad"}, {Code: "bad"}} {
		if _, e := s.Channel(ctx, "token", in); !errors.Is(e, ErrInvalid) {
			t.Fatal(e)
		}
	}
	if _, e := s.Channel(ctx, "approve", ChannelInput{}); !errors.Is(e, ErrInvalid) {
		t.Fatal(e)
	}
}

func TestDESKTOP_03_04_ScopedHistoryAndReplay(t *testing.T) {
	s, intern, _, _, other := dataStore(t)
	ctx := context.Background()
	in := ChannelInput{UserID: intern.UserID, Kind: "message", ID: "m1", Payload: json.RawMessage(`{"role":"assistant","text":"synthetic"}`)}
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := s.Channel(ctx, "create", in); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	v, e := s.Channel(ctx, "list", ChannelInput{UserID: intern.UserID, Kind: "message"})
	if e != nil || len(v.(map[string]any)["items"].([]map[string]any)) != 1 {
		t.Fatal(v, e)
	}
	v, e = s.Channel(ctx, "events", ChannelInput{UserID: intern.UserID})
	if e != nil || len(v.(map[string]any)["items"].([]map[string]any)) != 1 {
		t.Fatal(v, e)
	}
	event := v.(map[string]any)["items"].([]map[string]any)[0]
	seq := event["seq"].(int64)
	v, e = s.Channel(ctx, "events", ChannelInput{UserID: intern.UserID, After: seq})
	if e != nil || len(v.(map[string]any)["items"].([]map[string]any)) != 0 {
		t.Fatal(v, e)
	}
	if _, e = s.Channel(ctx, "get", ChannelInput{UserID: other.UserID, Kind: "message", ID: "m1"}); !errors.Is(e, ErrNotFound) {
		t.Fatal("foreign message", e)
	}
	in.Payload = json.RawMessage(`{"role":"assistant","text":"updated"}`)
	if _, e = s.Channel(ctx, "put", in); e != nil {
		t.Fatal(e)
	}
	for _, kind := range []string{"file", "read", "state"} {
		in.Kind = kind
		if _, e = s.Channel(ctx, "put", in); e != nil {
			t.Fatal(e)
		}
	}
	v, e = s.Channel(ctx, "events", ChannelInput{UserID: intern.UserID})
	if e != nil || len(v.(map[string]any)["items"].([]map[string]any)) != 2 {
		t.Fatal("private file/state leaked to events", v, e)
	}
	in.Kind = "request"
	in.Payload = json.RawMessage(`{"status":"running"}`)
	if _, e = s.Channel(ctx, "put", in); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Channel(ctx, "interrupt", ChannelInput{}); e != nil {
		t.Fatal(e)
	}
	got, e := s.Channel(ctx, "get", in)
	if e != nil {
		t.Fatal(e)
	}
	var payload map[string]any
	json.Unmarshal(got.(json.RawMessage), &payload)
	if payload["status"] != "interrupted" {
		t.Fatal(payload)
	}
	for _, bad := range []ChannelInput{{}, {UserID: "missing", Kind: "message"}, {UserID: intern.UserID, Kind: "bad"}, {UserID: intern.UserID, Kind: "message"}, {UserID: intern.UserID, Kind: "message", ID: "x", Payload: json.RawMessage("{")}} {
		if _, e = s.Channel(ctx, "put", bad); e == nil {
			t.Fatal("accepted invalid")
		}
	}
	if _, e = s.Channel(ctx, "unknown", ChannelInput{UserID: intern.UserID, Kind: "message"}); !errors.Is(e, ErrInvalid) {
		t.Fatal(e)
	}
}
