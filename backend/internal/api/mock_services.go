package api

import (
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	"net/http"
)

func mockServicesRoutes(mux *http.ServeMux, data *store.Store) {
	mux.HandleFunc("POST /api/v1/mock/access-requests", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			ObjectID string `json:"object_id"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.RequestMockAccess(r.Context(), auth.Actor(r.Context()), input.ObjectID)
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/mock/access-requests", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.MockAccessRequests(r.Context(), auth.Actor(r.Context()))
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("PATCH /api/v1/mock/access-requests/{id}", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Status string `json:"status"`
			Reason string `json:"reason"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.ResolveMockAccess(r.Context(), auth.Actor(r.Context()), r.PathValue("id"), input.Status, input.Reason)
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/mock/progress", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.MockProgress(r.Context(), auth.Actor(r.Context()))
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("PUT /api/v1/mock/progress/{item}", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Completed *bool `json:"completed"`
		}
		if !decode(w, r, &input) {
			return
		}
		if input.Completed == nil {
			invalidRequest(w)
			return
		}
		v, e := data.MarkMockProgress(r.Context(), auth.Actor(r.Context()), r.PathValue("item"), *input.Completed)
		result(w, v, e)
	})
}
