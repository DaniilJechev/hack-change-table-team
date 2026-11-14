package api

import (
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	"net/http"
)

// ChannelHandler is deliberately service-only. The public agent authenticates
// device sessions and constructs these inputs; no client may select an actor.
func ChannelHandler(data *store.Store, key string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /internal/client/{operation}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		var input store.ChannelInput
		if !decodeLimited(w, r, &input, 15<<20) {
			return
		}
		value, err := data.Channel(r.Context(), r.PathValue("operation"), input)
		result(w, value, err)
	})
	return auth.Service(key, mux)
}
