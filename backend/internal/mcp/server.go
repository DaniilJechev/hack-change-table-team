// Package mcp exposes only our six read tools, using the official MCP Go SDK.
package mcp

import (
	"context"
	"errors"
	"net/http"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/auth"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
	sdk "github.com/modelcontextprotocol/go-sdk/mcp"
)

type Data interface {
	auth.Users
	UserProfile(context.Context, store.Profile) (store.UserProfile, error)
	TeamCard(context.Context, store.Profile, string, int64) (store.TeamCard, error)
	SearchFAQ(context.Context, store.Profile, string) ([]store.FAQ, error)
	AccessCatalog(context.Context, store.Profile, []string) ([]store.CatalogObject, error)
	AccessMarks(context.Context, store.Profile) ([]store.AccessMark, error)
	Interns(context.Context, store.Profile) ([]store.Intern, error)
}
type empty struct{}
type cardInput struct {
	Section string `json:"section,omitempty" jsonschema:"Необязательный раздел карточки своей команды"`
}
type faqInput struct {
	Text string `json:"text" jsonschema:"Текст для поиска в FAQ своей команды"`
}
type catalogInput struct {
	Objects []string `json:"objects,omitempty" jsonschema:"Необязательные идентификаторы объектов своей команды"`
}
type list[T any] struct {
	Items []T `json:"items"`
}

func tool[I, O any](server *sdk.Server, name, description string, fn func(context.Context, I) (O, error)) {
	sdk.AddTool(server, &sdk.Tool{Name: name, Description: description, Annotations: &sdk.ToolAnnotations{ReadOnlyHint: true, DestructiveHint: boolPointer(false), IdempotentHint: true, OpenWorldHint: boolPointer(false)}},
		func(ctx context.Context, _ *sdk.CallToolRequest, input I) (*sdk.CallToolResult, O, error) {
			result, err := fn(ctx, input)
			if err != nil {
				message := "Сервис временно недоступен"
				switch {
				case errors.Is(err, store.ErrForbidden):
					message = "Доступ запрещён"
				case errors.Is(err, store.ErrNotFound):
					message = "Данные не найдены"
				case errors.Is(err, store.ErrInvalid):
					message = "Некорректный запрос"
				}
				return nil, result, errors.New(message)
			}
			return nil, result, nil
		})
}
func boolPointer(v bool) *bool { return &v }
func Handler(data Data, key string) http.Handler {
	server := sdk.NewServer(&sdk.Implementation{Name: "buddy-mcp", Version: "0.2.0"}, nil)
	tool(server, "user_profile_get", "Профиль текущего пользователя: роль, команда, наставник и логины", func(ctx context.Context, _ empty) (store.UserProfile, error) {
		return data.UserProfile(ctx, auth.Actor(ctx))
	})
	tool(server, "team_card_get", "Карточка своей команды или её раздел, с версией и источником", func(ctx context.Context, in cardInput) (store.TeamCard, error) {
		return data.TeamCard(ctx, auth.Actor(ctx), in.Section, 0)
	})
	tool(server, "team_faq_search", "Поиск сохранённых с согласия автора ответов своей команды", func(ctx context.Context, in faqInput) (list[store.FAQ], error) {
		out, err := data.SearchFAQ(ctx, auth.Actor(ctx), in.Text)
		return list[store.FAQ]{out}, err
	})
	tool(server, "access_catalog_get", "Объекты доступов своей команды для роли текущего пользователя", func(ctx context.Context, in catalogInput) (list[store.CatalogObject], error) {
		out, err := data.AccessCatalog(ctx, auth.Actor(ctx), in.Objects)
		return list[store.CatalogObject]{out}, err
	})
	tool(server, "access_marks_get", "Мои отметки «я запросил» с первоначальной датой", func(ctx context.Context, _ empty) (list[store.AccessMark], error) {
		out, err := data.AccessMarks(ctx, auth.Actor(ctx))
		return list[store.AccessMark]{out}, err
	})
	tool(server, "interns_list", "Стажёры наставника или команды тимлида с последними снимками доступов", func(ctx context.Context, _ empty) (list[store.Intern], error) {
		out, err := data.Interns(ctx, auth.Actor(ctx))
		return list[store.Intern]{out}, err
	})
	transport := sdk.NewStreamableHTTPHandler(func(*http.Request) *sdk.Server { return server }, &sdk.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	return auth.Service(key, auth.User(data, http.MaxBytesHandler(transport, 1<<20)))
}
