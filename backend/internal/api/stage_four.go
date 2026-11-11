package api

import (
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	"net/http"
)

func stageFourRoutes(mux *http.ServeMux, data *store.Store) {
	mux.HandleFunc("GET /api/v1/brief-state", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.BriefState(r.Context(), auth.Actor(r.Context()))
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/brief-stats", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.BriefStats(r.Context(), auth.Actor(r.Context()))
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("GET /api/v1/brief-settings", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.BriefSettings(r.Context(), auth.Actor(r.Context()))
		result(w, v, e)
	})
	mux.HandleFunc("PATCH /api/v1/brief-settings", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Time  *string `json:"time"`
			Quiet *bool   `json:"quiet"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.SetBriefSettings(r.Context(), auth.Actor(r.Context()), input.Time, input.Quiet)
		result(w, v, e)
	})
	mux.HandleFunc("POST /api/v1/mcp-calls", func(w http.ResponseWriter, r *http.Request) {
		var input store.MCPCall
		if !decode(w, r, &input) {
			return
		}
		e := data.SaveMCPCall(r.Context(), auth.Actor(r.Context()), input)
		result(w, map[string]string{"id": input.ID}, e)
	})
}
