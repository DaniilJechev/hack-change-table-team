package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

type IdentityStore interface {
	Ping(context.Context) error
	Bind(context.Context, string, int64, int64) (store.Profile, error)
	TelegramProfile(context.Context, int64) (store.Profile, error)
}

type Identity struct {
	Store      IdentityStore
	ServiceKey string
}

// Handler preserves the liveness contract; identity routes require a configured store.
func Handler(identity ...Identity) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok", "service": "backend", "stage": "0"})
	})
	if len(identity) > 0 {
		id := identity[0]
		mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
			ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
			defer cancel()
			if err := id.Store.Ping(ctx); err != nil {
				failure(w, 503, "unavailable", "Сервис временно недоступен")
				return
			}
			respond(w, 200, map[string]string{"status": "ok"})
		})
		secured := http.NewServeMux()
		secured.HandleFunc("POST /api/v1/auth/bind", id.bind)
		secured.HandleFunc("GET /api/v1/auth/telegram/{id}", id.profile)
		mux.Handle("/api/v1/", id.authorize(secured))
	}
	return mux
}

func (id Identity) authorize(next http.Handler) http.Handler {
	return auth.Service(id.ServiceKey, next)
}

func (id Identity) bind(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Code           string `json:"code"`
		TelegramUserID int64  `json:"telegram_user_id"`
		TelegramChatID int64  `json:"telegram_chat_id"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		invalidRequest(w)
		return
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		invalidRequest(w)
		return
	}
	if input.Code == "" || len(input.Code) > 128 || input.TelegramUserID <= 0 || input.TelegramChatID != input.TelegramUserID {
		invalidRequest(w)
		return
	}
	profile, err := id.Store.Bind(r.Context(), input.Code, input.TelegramUserID, input.TelegramChatID)
	if err != nil {
		storeError(w, err)
		return
	}
	respond(w, 200, profile)
}

func (id Identity) profile(w http.ResponseWriter, r *http.Request) {
	telegramID, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || telegramID <= 0 {
		invalidRequest(w)
		return
	}
	profile, err := id.Store.TelegramProfile(r.Context(), telegramID)
	if err != nil {
		storeError(w, err)
		return
	}
	respond(w, 200, profile)
}

func storeError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, store.ErrInvalid):
		invalidRequest(w)
	case errors.Is(err, store.ErrForbidden):
		failure(w, 403, "forbidden", "Доступ запрещён")
	case errors.Is(err, store.ErrInvitation):
		failure(w, 400, "invalid_invitation", "Код не подошёл, спроси наставника")
	case errors.Is(err, store.ErrConflict):
		failure(w, 409, "binding_conflict", "Аккаунт уже привязан, спроси наставника")
	case errors.Is(err, store.ErrNotFound):
		failure(w, 404, "not_found", "Пользователь не найден")
	default:
		failure(w, 503, "unavailable", "Сервис временно недоступен")
	}
}

func invalidRequest(w http.ResponseWriter) {
	failure(w, 400, "invalid_request", "Некорректный запрос")
}
func failure(w http.ResponseWriter, status int, code, message string) {
	respond(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}
func respond(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
