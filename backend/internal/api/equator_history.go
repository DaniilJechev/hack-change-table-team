package api

import (
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	"net/http"
)

func equatorRoutes(mux *http.ServeMux, data *store.Store) {
	mux.HandleFunc("GET /api/v1/equator/checks", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.EquatorHistory(r.Context(), auth.Actor(r.Context()))
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("PUT /api/v1/equator/checks/{id}", func(w http.ResponseWriter, r *http.Request) {
		var v map[string]any
		if !decode(w, r, &v) {
			return
		}
		if v == nil || v["id"] != r.PathValue("id") {
			invalidRequest(w)
			return
		}
		saved, e := data.SaveEquator(r.Context(), auth.Actor(r.Context()), v)
		result(w, saved, e)
	})
	mux.HandleFunc("POST /api/v1/equator/invalidate", func(w http.ResponseWriter, r *http.Request) {
		n, e := data.InvalidateEquator(r.Context(), auth.Actor(r.Context()), false)
		result(w, map[string]any{"generation": n}, e)
	})
	mux.HandleFunc("DELETE /api/v1/equator/checks", func(w http.ResponseWriter, r *http.Request) {
		n, e := data.InvalidateEquator(r.Context(), auth.Actor(r.Context()), true)
		result(w, map[string]any{"generation": n}, e)
	})
	mux.HandleFunc("POST /api/v1/equator/checks/{id}/claim", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Part         string `json:"part"`
			RetryUnknown bool   `json:"retry_unknown"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.EquatorDelivery(r.Context(), auth.Actor(r.Context()), r.PathValue("id"), input.Part, "", true, input.RetryUnknown)
		result(w, v, e)
	})
	mux.HandleFunc("PATCH /api/v1/equator/checks/{id}/delivery", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Part  string `json:"part"`
			State string `json:"state"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.EquatorDelivery(r.Context(), auth.Actor(r.Context()), r.PathValue("id"), input.Part, input.State, false)
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/equator/mentor-chat", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.MentorChat(r.Context(), auth.Actor(r.Context()))
		result(w, map[string]any{"chat_id": v}, e)
	})
}
