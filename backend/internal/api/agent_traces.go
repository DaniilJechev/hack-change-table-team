package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

const maxAgentTraceBody = 256 << 10

// AgentTraceHandler serves FR-TR-02/04: service key only, no user actor, not exposed via buddy-mcp.
func AgentTraceHandler(data *store.Store, key string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v1/agent-traces", func(w http.ResponseWriter, r *http.Request) {
		// No created_at: the server sets it. Unknown fields are rejected by decodeLimited.
		var input struct {
			ID         string          `json:"id"`
			UserID     string          `json:"user_id"`
			TeamID     string          `json:"team_id"`
			Role       string          `json:"role"`
			Demo       bool            `json:"demo"`
			Path       string          `json:"path"`
			Fallback   bool            `json:"fallback"`
			Case       string          `json:"case"`
			DurationMS int64           `json:"duration_ms"`
			LLMCalls   int64           `json:"llm_calls"`
			ToolCalls  int64           `json:"tool_calls"`
			Turn       json.RawMessage `json:"turn"`
		}
		if !decodeLimited(w, r, &input, maxAgentTraceBody) {
			return
		}
		if err := data.SaveAgentTrace(r.Context(), store.AgentTrace{ID: input.ID, UserID: input.UserID, TeamID: input.TeamID, Role: input.Role, Demo: input.Demo, Path: input.Path, Fallback: input.Fallback, Case: input.Case, DurationMS: input.DurationMS, LLMCalls: input.LLMCalls, ToolCalls: input.ToolCalls, Turn: input.Turn}); err != nil {
			storeError(w, err)
			return
		}
		respond(w, 201, map[string]string{"id": input.ID})
	})
	mux.HandleFunc("GET /api/v1/agent-traces", func(w http.ResponseWriter, r *http.Request) {
		limit := 50
		if raw := r.URL.Query().Get("limit"); raw != "" {
			parsed, err := strconv.Atoi(raw)
			if err != nil || parsed < 1 || parsed > store.MaxAgentTraces {
				invalidRequest(w)
				return
			}
			limit = parsed
		}
		traces, err := data.AgentTraces(r.Context(), r.URL.Query().Get("user_id"), limit)
		if err != nil {
			storeError(w, err)
			return
		}
		respond(w, 200, map[string]any{"traces": traces})
	})
	mux.HandleFunc("GET /api/v1/agent-traces/{id}", func(w http.ResponseWriter, r *http.Request) {
		trace, err := data.AgentTrace(r.Context(), r.PathValue("id"))
		if errors.Is(err, store.ErrNotFound) {
			failure(w, 404, "not_found", "Трейс не найден")
			return
		}
		if err != nil {
			storeError(w, err)
			return
		}
		respond(w, 200, trace)
	})
	return auth.Service(key, mux)
}
