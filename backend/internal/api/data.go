package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	buddymcp "github.com/DaniilJechev/hack-change-table-team/backend/internal/mcp"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

// DataHandler is mounted after the identity routes; all routes require an actor.
func DataHandler(data *store.Store, key string) http.Handler {
	mux := http.NewServeMux()
	actor := auth.Actor
	mux.HandleFunc("GET /api/v1/profile", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.UserProfile(r.Context(), actor(r.Context()))
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/team-card", func(w http.ResponseWriter, r *http.Request) {
		version := int64(0)
		var err error
		if raw := r.URL.Query().Get("version"); raw != "" {
			version, err = strconv.ParseInt(raw, 10, 64)
			if err != nil || version < 1 {
				invalidRequest(w)
				return
			}
		}
		v, e := data.TeamCard(r.Context(), actor(r.Context()), r.URL.Query().Get("section"), version)
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/access-catalog", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.AccessCatalog(r.Context(), actor(r.Context()), r.URL.Query()["object"])
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("GET /api/v1/access-marks", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.AccessMarks(r.Context(), actor(r.Context()))
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("PUT /api/v1/access-marks/{object}", func(w http.ResponseWriter, r *http.Request) {
		if r.ContentLength != 0 {
			invalidRequest(w)
			return
		}
		v, e := data.MarkAccess(r.Context(), actor(r.Context()), r.PathValue("object"))
		result(w, v, e)
	})
	mux.HandleFunc("POST /api/v1/access-snapshots", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			ID      string              `json:"id"`
			Objects []store.AccessState `json:"objects"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.SaveSnapshot(r.Context(), actor(r.Context()), store.Snapshot{ID: input.ID, Objects: input.Objects})
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/access-snapshots/latest", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.LatestSnapshot(r.Context(), actor(r.Context()))
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/interns", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.Interns(r.Context(), actor(r.Context()))
		result(w, map[string]any{"items": v}, e)
	})
	mux.HandleFunc("POST /api/v1/briefs", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			ID    string            `json:"id"`
			Items []store.BriefItem `json:"items"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.SaveBrief(r.Context(), actor(r.Context()), store.Brief{ID: input.ID, Items: input.Items})
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/briefs/{id}", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.Brief(r.Context(), actor(r.Context()), r.PathValue("id"))
		result(w, v, e)
	})
	mux.HandleFunc("PATCH /api/v1/briefs/{id}", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Status string `json:"status"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.BriefStatus(r.Context(), actor(r.Context()), r.PathValue("id"), input.Status)
		result(w, v, e)
	})
	mux.HandleFunc("POST /api/v1/briefs/{id}/events", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			ID     string `json:"id"`
			Kind   string `json:"kind"`
			ItemID string `json:"item_id,omitempty"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.BriefEvent(r.Context(), actor(r.Context()), r.PathValue("id"), store.BriefEvent{ID: input.ID, Kind: input.Kind, ItemID: input.ItemID})
		result(w, v, e)
	})
	mux.HandleFunc("POST /api/v1/questions", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			ID         string `json:"id"`
			Text       string `json:"text"`
			AssignedTo string `json:"assigned_to,omitempty"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.SaveQuestion(r.Context(), actor(r.Context()), store.Question{ID: input.ID, Text: input.Text, AssignedTo: input.AssignedTo})
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/questions/{id}", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.Question(r.Context(), actor(r.Context()), r.PathValue("id"))
		result(w, v, e)
	})
	mux.HandleFunc("POST /api/v1/questions/{id}/answer", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Text string `json:"text"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.AnswerQuestion(r.Context(), actor(r.Context()), r.PathValue("id"), input.Text)
		result(w, v, e)
	})
	mux.HandleFunc("PATCH /api/v1/questions/{id}", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Helpful *bool `json:"helpful"`
		}
		if !decode(w, r, &input) {
			return
		}
		if input.Helpful == nil {
			invalidRequest(w)
			return
		}
		v, e := data.RateQuestion(r.Context(), actor(r.Context()), r.PathValue("id"), *input.Helpful)
		result(w, v, e)
	})
	mux.HandleFunc("POST /api/v1/faq", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			ID         string `json:"id"`
			QuestionID string `json:"question_id"`
			Consent    bool   `json:"consent"`
		}
		if !decode(w, r, &input) {
			return
		}
		v, e := data.PublishFAQ(r.Context(), actor(r.Context()), input.ID, input.QuestionID, input.Consent)
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/faq/{id}", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.FAQ(r.Context(), actor(r.Context()), r.PathValue("id"))
		result(w, v, e)
	})
	mux.HandleFunc("GET /api/v1/faq", func(w http.ResponseWriter, r *http.Request) {
		v, e := data.SearchFAQ(r.Context(), actor(r.Context()), r.URL.Query().Get("text"))
		result(w, map[string]any{"items": v}, e)
	})
	return auth.Service(key, auth.User(data, mux))
}
func result(w http.ResponseWriter, value any, err error) {
	if errors.Is(err, store.ErrConflict) {
		failure(w, 409, "conflict", "Запись конфликтует с сохранёнными данными")
		return
	}
	if err != nil {
		storeError(w, err)
		return
	}
	respond(w, 200, value)
}
func decode(w http.ResponseWriter, r *http.Request, value any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		invalidRequest(w)
		return false
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		invalidRequest(w)
		return false
	}
	return true
}

// Application composes authenticated data and MCP routes without changing stage-0 health or identity.
func Application(data *store.Store, key string) http.Handler {
	root := http.NewServeMux()
	identity := Handler(Identity{Store: data, ServiceKey: key})
	root.Handle("/api/v1/auth/", identity)
	root.Handle("/api/v1/", DataHandler(data, key))
	root.Handle("/mcp", buddymcp.Handler(data, key))
	root.Handle("/", identity)
	return root
}
