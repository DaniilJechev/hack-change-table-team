package store

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
)

// FieldError contains a schema path and a safe message, never the supplied value.
type FieldError struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}
type ValidationError struct{ Fields []FieldError }

func (e *ValidationError) Error() string { return "invalid team card" }

type cardRule struct {
	kind     string
	required bool
}

func rules(required, optional map[string]string) map[string]cardRule {
	out := map[string]cardRule{}
	for key, kind := range required {
		out[key] = cardRule{kind, true}
	}
	for key, kind := range optional {
		out[key] = cardRule{kind, false}
	}
	return out
}

var cardSchema = rules(map[string]string{"synthetic": "true", "team_id": "id", "name": "text"}, map[string]string{
	"stage": "stage", "notice": "text", "source_url": "url", "people": "people", "routing": "routing", "do_not_disturb": "dnd", "tools": "texts", "links": "links", "faq": "empty",
})
var stageSchema = rules(map[string]string{"epic_key": "id", "source_url": "url"}, nil)
var personSchema = rules(map[string]string{"user_id": "id", "name": "text", "role": "text", "areas": "texts", "contact_url": "url"}, map[string]string{"first_contact": "bool", "about": "text"})
var routeSchema = rules(map[string]string{"situation": "text", "go_to": "text", "owner_id": "id", "wait_minutes": "minutes", "then_id": "id", "source_url": "url"}, nil)
var dndSchema = rules(map[string]string{"person_id": "id", "direct_allowed": "bool", "reason": "text", "via_id": "id"}, nil)
var linkSchema = rules(map[string]string{"service": "text", "url": "url"}, nil)

func validateCardFields(document map[string]any) []FieldError {
	issues := []FieldError{}
	var check func(map[string]any, map[string]cardRule, string)
	check = func(object map[string]any, schema map[string]cardRule, prefix string) {
		keys := []string{}
		for key := range object {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if _, exists := schema[key]; !exists {
				// Do not echo arbitrary YAML keys (which may contain sensitive values).
				issues = append(issues, FieldError{prefix + "*", "неизвестное поле"})
			}
		}
		keys = keys[:0]
		for key := range schema {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			rule := schema[key]
			value, exists := object[key]
			path := prefix + key
			if !exists {
				if rule.required {
					issues = append(issues, FieldError{path, "обязательное поле"})
				}
				continue
			}
			valid := false
			text, isText := value.(string)
			switch rule.kind {
			case "text":
				valid = isText && validText(text, 2000)
			case "id":
				valid = isText && ValidID(text)
			case "url":
				valid = isText && len(text) <= 2048 && !strings.ContainsAny(text, " \t\r\n") && validURL(text)
			case "true":
				valid = value == true
			case "bool":
				_, valid = value.(bool)
			case "stage":
				obj, ok := value.(map[string]any)
				valid = ok
				if ok {
					check(obj, stageSchema, path+".")
				}
			case "minutes":
				n, ok := value.(int)
				valid = ok && n >= 0 && n <= 10080
			default:
				list, ok := value.([]any)
				valid = ok && len(list) <= 100
				if rule.kind == "empty" {
					valid = ok && len(list) == 0
				}
				if valid {
					seen := map[string]bool{}
					for i, item := range list {
						itemPath := fmt.Sprintf("%s[%d]", path, i)
						if rule.kind == "texts" {
							s, ok := item.(string)
							if !ok || !validText(s, 200) {
								issues = append(issues, FieldError{itemPath, "ожидается непустая строка"})
							}
							continue
						}
						obj, ok := item.(map[string]any)
						if !ok {
							issues = append(issues, FieldError{itemPath, "ожидается объект"})
							continue
						}
						switch rule.kind {
						case "people":
							check(obj, personSchema, itemPath+".")
							if id, ok := obj["user_id"].(string); ok {
								if seen[id] {
									issues = append(issues, FieldError{itemPath + ".user_id", "повтор участника"})
								}
								seen[id] = true
							}
						case "routing":
							check(obj, routeSchema, itemPath+".")
						case "dnd":
							check(obj, dndSchema, itemPath+".")
						case "links":
							check(obj, linkSchema, itemPath+".")
						}
					}
				}
			}
			if !valid {
				issues = append(issues, FieldError{path, "неверный тип или значение поля"})
			}
		}
	}
	check(document, cardSchema, "")
	return issues
}

// UploadTeamCard retains the seed importer's idempotency and lock ordering.
// Legacy minimal seed imports remain compatible; uploads use the complete schema.
func (s *Store) UploadTeamCard(ctx context.Context, actor Profile, source io.Reader) (TeamCard, error) {
	if actor.Role != "mentor" && actor.Role != "teamlead" {
		return TeamCard{}, ErrForbidden
	}
	var document map[string]any
	if err := decodeYAML(source, &document); err != nil {
		return TeamCard{}, &ValidationError{[]FieldError{{"document", "ожидается один YAML-документ размером до 1 МиБ"}}}
	}
	if fields := validateCardFields(document); len(fields) > 0 {
		return TeamCard{}, &ValidationError{fields}
	}
	if document["team_id"] != actor.TeamID {
		return TeamCard{}, ErrForbidden
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return TeamCard{}, err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE`); err != nil {
		return TeamCard{}, err
	}
	var role, team string
	if err = tx.QueryRowContext(ctx, `SELECT role,team_id FROM users WHERE user_id=$1`, actor.UserID).Scan(&role, &team); err != nil {
		return TeamCard{}, noRows(err)
	}
	if team != actor.TeamID || role != "mentor" && role != "teamlead" {
		return TeamCard{}, ErrForbidden
	}
	fields := []FieldError{}
	for _, section := range []string{"people", "routing", "do_not_disturb"} {
		list, _ := document[section].([]any)
		for i, item := range list {
			obj := item.(map[string]any)
			for _, key := range []string{"user_id", "owner_id", "then_id", "person_id", "via_id"} {
				id, exists := obj[key]
				if !exists {
					continue
				}
				var found bool
				if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM users WHERE user_id=$1 AND team_id=$2)`, id, team).Scan(&found); err != nil {
					return TeamCard{}, err
				}
				if !found {
					fields = append(fields, FieldError{fmt.Sprintf("%s[%d].%s", section, i, key), "участник своей команды не найден"})
				}
			}
		}
	}
	if len(fields) > 0 {
		return TeamCard{}, &ValidationError{fields}
	}
	raw, err := json.Marshal(document)
	if err != nil {
		return TeamCard{}, ErrInvalid
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO team_cards(team_id,document) SELECT $1,$2::jsonb WHERE NOT EXISTS(SELECT 1 FROM (SELECT document FROM team_cards WHERE team_id=$1 ORDER BY version DESC LIMIT 1) latest WHERE document=$2::jsonb)`, team, string(raw)); err != nil {
		return TeamCard{}, err
	}
	card := TeamCard{TeamID: team, Document: document}
	if err = tx.QueryRowContext(ctx, `SELECT version FROM team_cards WHERE team_id=$1 ORDER BY version DESC LIMIT 1`, team).Scan(&card.Version); err != nil {
		return TeamCard{}, err
	}
	card.SourcePath = "/api/v1/team-card?version=" + strconv.FormatInt(card.Version, 10)
	return card, tx.Commit()
}
