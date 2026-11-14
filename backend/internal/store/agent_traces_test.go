package store

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func sampleTrace(id string) AgentTrace {
	return AgentTrace{ID: id, UserID: "demo-daniil", TeamID: "demo-orders", Role: "intern", Demo: true, Path: "agent", DurationMS: 100, LLMCalls: 1, ToolCalls: 2, Turn: json.RawMessage(`{"request":{"text":"Synthetic"}}`)}
}

func TestFR_TR_03_RetentionConfig(t *testing.T) {
	s := &Store{}
	if s.retentionDays() != 30 {
		t.Fatal(s.retentionDays())
	}
	if err := s.ConfigureAgentTraceRetention(func(string) string { return "" }); err != nil || s.retentionDays() != 30 {
		t.Fatal(err, s.retentionDays())
	}
	if err := s.ConfigureAgentTraceRetention(func(string) string { return "7" }); err != nil || s.retentionDays() != 7 {
		t.Fatal(err, s.retentionDays())
	}
	for _, bad := range []string{"0", "-3", "abc", "1.5", "99999999999"} {
		err := s.ConfigureAgentTraceRetention(func(string) string { return bad })
		if err == nil || !strings.Contains(err.Error(), "AGENT_TRACE_RETENTION_DAYS") {
			t.Fatal(bad, err)
		}
	}
}

func TestFR_TR_03_OldTraceDeletedOnWrite(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if err := s.ConfigureAgentTraceRetention(func(string) string { return "30" }); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"tr-old", "tr-recent"} {
		if err := s.SaveAgentTrace(ctx, sampleTrace(id)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := s.db.Exec(`UPDATE agent_traces SET created_at=now()-interval '31 days' WHERE id='tr-old'`); err != nil {
		t.Fatal(err)
	}
	if _, err := s.db.Exec(`UPDATE agent_traces SET created_at=now()-interval '29 days' WHERE id='tr-recent'`); err != nil {
		t.Fatal(err)
	}
	if err := s.SaveAgentTrace(ctx, sampleTrace("tr-new")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.AgentTrace(ctx, "tr-old"); err != ErrNotFound {
		t.Fatal("old trace kept", err)
	}
	for _, id := range []string{"tr-recent", "tr-new"} {
		if _, err := s.AgentTrace(ctx, id); err != nil {
			t.Fatal(id, err)
		}
	}
}

func TestFR_TR_02_DuplicateIDStoresOneRow(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	first, second := sampleTrace("tr-dup"), sampleTrace("tr-dup")
	second.Path = "router"
	for _, trace := range []AgentTrace{first, second} {
		if err := s.SaveAgentTrace(ctx, trace); err != nil {
			t.Fatal(err)
		}
	}
	var count int
	var path string
	if err := s.db.QueryRow(`SELECT count(*), min(path) FROM agent_traces`).Scan(&count, &path); err != nil || count != 1 || path != "agent" {
		t.Fatal(count, path, err)
	}
	invalid := sampleTrace("tr-bad")
	invalid.LLMCalls = 1 << 40
	if err := s.SaveAgentTrace(ctx, invalid); err != ErrInvalid {
		t.Fatal(err)
	}
	if _, err := s.AgentTraces(ctx, "", 0); err != ErrInvalid {
		t.Fatal(err)
	}
	listed, err := s.AgentTraces(ctx, "demo-daniil", 10)
	if err != nil || len(listed) != 1 || listed[0].ID != "tr-dup" || listed[0].Turn != nil {
		t.Fatal(listed, err)
	}
	if listed, err = s.AgentTraces(ctx, "nobody", 10); err != nil || len(listed) != 0 {
		t.Fatal(listed, err)
	}
}

func TestFR_TR_05_AggregateWithoutText(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	var turns int
	if err := s.db.QueryRow(`SELECT count(*) FROM metrics_agent`).Scan(&turns); err != nil || turns != 0 {
		t.Fatal(turns, err)
	}
	for i, path := range []string{"agent", "agent", "router"} {
		trace := sampleTrace("tr-m" + string(rune('a'+i)))
		trace.Path = path
		trace.Fallback = i == 1
		trace.DurationMS = int64(100 * (i + 1))
		if err := s.SaveAgentTrace(ctx, trace); err != nil {
			t.Fatal(err)
		}
	}
	rows, err := s.db.Query(`SELECT * FROM metrics_agent ORDER BY path`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	columns, err := rows.Columns()
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(columns, ",") != "day,path,turns,fallback_percent,avg_duration_ms,p95_duration_ms,avg_tool_calls" {
		t.Fatal(columns)
	}
	var day, path string
	var count int
	var fallback, avg, p95, tools float64
	if !rows.Next() {
		t.Fatal("no agent row")
	}
	if err := rows.Scan(&day, &path, &count, &fallback, &avg, &p95, &tools); err != nil {
		t.Fatal(err)
	}
	if path != "agent" || count != 2 || fallback != 50 || avg != 150 || p95 != 195 || tools != 2 {
		t.Fatal(day, path, count, fallback, avg, p95, tools)
	}
}

func TestFR_TR_04_MetricsRoleCannotReadTraces(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if err := s.ProvisionMetricsRole(ctx, "synthetic"); err != nil {
		t.Fatal(err)
	}
	var raw, view bool
	if err := s.db.QueryRow(`SELECT has_table_privilege('buddy_metrics',current_schema()||'.agent_traces','SELECT'),has_table_privilege('buddy_metrics',current_schema()||'.metrics_agent','SELECT')`).Scan(&raw, &view); err != nil || raw || !view {
		t.Fatal(raw, view, err)
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`SET LOCAL ROLE buddy_metrics`); err != nil {
		t.Fatal(err)
	}
	var turns int
	if err := tx.QueryRow(`SELECT count(*) FROM metrics_agent`).Scan(&turns); err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Exec(`SELECT turn FROM agent_traces`); err == nil {
		t.Fatal("raw trace access allowed")
	}
}

func TestFR_TR_02_ClosedDatabaseErrors(t *testing.T) {
	s := integrationStore(t)
	s.Close()
	ctx := context.Background()
	if err := s.SaveAgentTrace(ctx, sampleTrace("tr-closed")); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.AgentTraces(ctx, "", 10); err == nil {
		t.Fatal("expected error")
	}
	if _, err := s.AgentTrace(ctx, "tr-closed"); err == nil {
		t.Fatal("expected error")
	}
}
