package api

import (
	"encoding/json"
	"net/http"
)

// Handler exposes only the stage-0 liveness endpoint.
func Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok", "service": "backend", "stage": "0"})
	})
	return mux
}
