// Package auth applies the shared service and request-local user boundary to REST and MCP.
package auth

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

type actorKey struct{}
type Users interface {
	User(context.Context, string) (store.Profile, error)
}

func Actor(ctx context.Context) store.Profile {
	p, _ := ctx.Value(actorKey{}).(store.Profile)
	return p
}
func reject(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"code": code, "message": message}})
}
func Service(key string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if key == "" {
			reject(w, 503, "unavailable", "Сервис временно недоступен")
			return
		}
		expected, supplied := sha256.Sum256([]byte(key)), sha256.Sum256([]byte(r.Header.Get("X-Buddy-Key")))
		if subtle.ConstantTimeCompare(expected[:], supplied[:]) != 1 {
			reject(w, 401, "unauthorized", "Требуется авторизация сервиса")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
func User(users Users, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Buddy-User")
		if !store.ValidID(id) {
			reject(w, 401, "unauthorized", "Требуется авторизация пользователя")
			return
		}
		actor, err := users.User(r.Context(), id)
		if errors.Is(err, store.ErrNotFound) {
			reject(w, 401, "unauthorized", "Требуется авторизация пользователя")
			return
		}
		if err != nil {
			reject(w, 503, "unavailable", "Сервис временно недоступен")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), actorKey{}, actor)))
	})
}
