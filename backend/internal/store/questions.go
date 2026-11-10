package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type Question struct {
	ID         string          `json:"id"`
	UserID     string          `json:"user_id"`
	Text       string          `json:"text"`
	AssignedTo string          `json:"assigned_to,omitempty"`
	Answer     string          `json:"answer"`
	Helpful    *bool           `json:"helpful"`
	CreatedAt  time.Time       `json:"created_at"`
	Context    QuestionContext `json:"context"`
	BotAnswer  string          `json:"bot_answer"`
	BotSources []string        `json:"bot_sources"`
	BotHelpful *bool           `json:"bot_helpful"`
}

type QuestionContext struct {
	CurrentTask    string   `json:"current_task,omitempty"`
	CheckedSources []string `json:"checked_sources"`
	TeamID         string   `json:"team_id"`
	InternshipWeek *int     `json:"internship_week"`
}

func scanQuestion(row *sql.Row) (Question, error) {
	var q Question
	var raw, sources []byte
	err := row.Scan(&q.ID, &q.UserID, &q.Text, &q.AssignedTo, &q.Answer, &q.Helpful, &q.CreatedAt, &raw, &q.BotAnswer, &sources, &q.BotHelpful)
	if err != nil {
		return q, noRows(err)
	}
	err = json.Unmarshal(raw, &q.Context)
	if err == nil {
		err = json.Unmarshal(sources, &q.BotSources)
	}
	return q, err
}

const questionColumns = `question_id,user_id,text,coalesce(assigned_to,''),answer,helpful,created_at,context,bot_answer,bot_sources,bot_helpful`

func (s *Store) SaveQuestion(ctx context.Context, actor Profile, input Question) (Question, error) {
	if !ValidID(input.ID) || !validText(input.Text, 8000) {
		return input, ErrInvalid
	}
	if input.AssignedTo != "" {
		if err := s.requireFlag(ctx, actor, "team_escalation"); err != nil {
			return input, err
		}
		recipient, err := s.User(ctx, input.AssignedTo)
		if err != nil {
			return input, err
		}
		if recipient.TeamID != actor.TeamID {
			return input, ErrForbidden
		}
	}
	if len(input.Context.CurrentTask) > 500 || len(input.Context.CheckedSources) > 20 {
		return input, ErrInvalid
	}
	for _, source := range input.Context.CheckedSources {
		if !validText(source, 500) {
			return input, ErrInvalid
		}
	}
	if input.Context.CheckedSources == nil {
		input.Context.CheckedSources = []string{}
	}
	checked, _ := json.Marshal(input.Context.CheckedSources)
	q, err := scanQuestion(s.db.QueryRowContext(ctx, `INSERT INTO questions(team_id,question_id,user_id,text,assigned_to,initial_assigned_to,context)
 SELECT $1,$2,$3,$4,nullif($5,''),nullif($5,''), jsonb_build_object('current_task',$6::text,'checked_sources',$7::jsonb,'team_id',team_id,
 'internship_week',CASE WHEN internship_started_on IS NULL THEN NULL ELSE greatest(1,(CURRENT_DATE-internship_started_on)/7+1) END)
 FROM users WHERE user_id=$3 AND team_id=$1
 ON CONFLICT(team_id,question_id) DO UPDATE SET text=questions.text
 WHERE questions.user_id=excluded.user_id AND questions.text=excluded.text AND questions.initial_assigned_to IS NOT DISTINCT FROM excluded.initial_assigned_to
 AND coalesce(questions.context->>'current_task','')=$6 AND coalesce(questions.context->'checked_sources','[]'::jsonb)=$7::jsonb
 RETURNING `+questionColumns, actor.TeamID, input.ID, actor.UserID, input.Text, input.AssignedTo, input.Context.CurrentTask, string(checked)))
	if errors.Is(err, ErrNotFound) {
		return input, ErrConflict
	}
	return q, err
}
func (s *Store) Question(ctx context.Context, actor Profile, id string) (Question, error) {
	return scanQuestion(s.db.QueryRowContext(ctx, `SELECT `+questionColumns+` FROM questions WHERE team_id=$1 AND question_id=$2 AND (user_id=$3 OR assigned_to=$3)`, actor.TeamID, id, actor.UserID))
}

func (s *Store) EscalateQuestion(ctx context.Context, actor Profile, id, recipientID string) (Question, error) {
	if !ValidID(id) || !ValidID(recipientID) {
		return Question{}, ErrInvalid
	}
	if err := s.requireFlag(ctx, actor, "team_escalation"); err != nil {
		return Question{}, err
	}
	recipient, err := s.User(ctx, recipientID)
	if err != nil {
		return Question{}, err
	}
	if recipient.TeamID != actor.TeamID {
		return Question{}, ErrForbidden
	}
	q, err := scanQuestion(s.db.QueryRowContext(ctx, `UPDATE questions SET assigned_to=$4 WHERE team_id=$1 AND question_id=$2 AND user_id=$3 AND (assigned_to IS NULL OR assigned_to=$4) RETURNING `+questionColumns, actor.TeamID, id, actor.UserID, recipientID))
	if errors.Is(err, ErrNotFound) {
		// Only the author may distinguish an assignment conflict from an unknown ID.
		var exists bool
		if e := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM questions WHERE team_id=$1 AND question_id=$2 AND user_id=$3)`, actor.TeamID, id, actor.UserID).Scan(&exists); e != nil {
			return Question{}, e
		}
		if exists {
			return Question{}, ErrConflict
		}
	}
	return q, err
}
func (s *Store) AnswerQuestion(ctx context.Context, actor Profile, id, text string) (Question, error) {
	if !validText(text, 8000) {
		return Question{}, ErrInvalid
	}
	// Freeze the first answer; publication and retries can never silently rewrite it.
	q, err := scanQuestion(s.db.QueryRowContext(ctx, `UPDATE questions SET answer=$4 WHERE team_id=$1 AND question_id=$2 AND assigned_to=$3 AND (answer='' OR answer=$4) RETURNING `+questionColumns, actor.TeamID, id, actor.UserID, text))
	return q, err
}
func (s *Store) RateQuestion(ctx context.Context, actor Profile, id string, helpful bool) (Question, error) {
	return scanQuestion(s.db.QueryRowContext(ctx, `UPDATE questions SET helpful=$4 WHERE team_id=$1 AND question_id=$2 AND user_id=$3 AND answer<>'' RETURNING `+questionColumns, actor.TeamID, id, actor.UserID, helpful))
}

func (s *Store) RateBotAnswer(ctx context.Context, actor Profile, id string, helpful bool) (Question, error) {
	return scanQuestion(s.db.QueryRowContext(ctx, `UPDATE questions SET bot_helpful=$4 WHERE team_id=$1 AND question_id=$2 AND user_id=$3 AND bot_answer<>'' RETURNING `+questionColumns, actor.TeamID, id, actor.UserID, helpful))
}

// A bot response is stored independently and can never supply FAQ authorship.
func (s *Store) SaveBotAnswer(ctx context.Context, actor Profile, id, text string, sources []string) (Question, error) {
	if !validText(text, 8000) || len(sources) == 0 || len(sources) > 20 {
		return Question{}, ErrInvalid
	}
	for _, source := range sources {
		if len(source) > 2048 || strings.ContainsAny(source, " \t\r\n") {
			return Question{}, ErrInvalid
		}
		// Internal source paths are resolved by the agent after authorization.
		u, err := url.Parse(source)
		internal := err == nil && u.Scheme == "" && u.Host == "" && u.Path == "/api/v1/team-card" && len(u.Query()) == 1
		if internal {
			version, parseErr := strconv.ParseInt(u.Query().Get("version"), 10, 64)
			if parseErr != nil || version <= 0 {
				return Question{}, ErrInvalid
			}
			if _, err := s.TeamCard(ctx, actor, u.Fragment, version); err != nil {
				if errors.Is(err, ErrNotFound) || errors.Is(err, ErrInvalid) {
					return Question{}, ErrInvalid
				}
				return Question{}, err
			}
		}
		faq := strings.HasPrefix(source, "/api/v1/faq/") && ValidID(strings.TrimPrefix(source, "/api/v1/faq/"))
		if faq {
			if _, err := s.FAQ(ctx, actor, strings.TrimPrefix(source, "/api/v1/faq/")); err != nil {
				if errors.Is(err, ErrNotFound) {
					return Question{}, ErrInvalid
				}
				return Question{}, err
			}
		}
		if !validURL(source) && !internal && !faq {
			return Question{}, ErrInvalid
		}
	}
	raw, _ := json.Marshal(sources)
	return scanQuestion(s.db.QueryRowContext(ctx, `UPDATE questions SET bot_answer=$4,bot_sources=$5 WHERE team_id=$1 AND question_id=$2 AND user_id=$3 AND answer='' AND (bot_answer='' OR (bot_answer=$4 AND bot_sources=$5::jsonb)) RETURNING `+questionColumns, actor.TeamID, id, actor.UserID, text, string(raw)))
}

type FAQ struct {
	ID         string    `json:"id"`
	QuestionID string    `json:"question_id"`
	Question   string    `json:"question"`
	Answer     string    `json:"answer"`
	AuthorID   string    `json:"author_id"`
	CreatedAt  time.Time `json:"created_at"`
	SourcePath string    `json:"source_path"`
}

func scanFAQ(row *sql.Row) (FAQ, error) {
	var f FAQ
	err := row.Scan(&f.ID, &f.QuestionID, &f.Question, &f.Answer, &f.AuthorID, &f.CreatedAt)
	f.SourcePath = "/api/v1/faq/" + url.PathEscape(f.ID)
	return f, noRows(err)
}

const faqColumns = `faq_id,question_id,question,answer,author_id,created_at`

func (s *Store) PublishFAQ(ctx context.Context, actor Profile, id, questionID string, consent bool) (FAQ, error) {
	if !ValidID(id) || !ValidID(questionID) || !consent {
		return FAQ{}, ErrInvalid
	}
	if err := s.requireFlag(ctx, actor, "faq_publication"); err != nil {
		return FAQ{}, err
	}
	f, err := scanFAQ(s.db.QueryRowContext(ctx, `INSERT INTO faq_entries(team_id,faq_id,question_id,question,answer,author_id)
 SELECT team_id,$3,question_id,text,answer,assigned_to FROM questions
 WHERE team_id=$1 AND question_id=$2 AND assigned_to=$4 AND answer<>''
 ON CONFLICT(team_id,faq_id) DO UPDATE SET faq_id=faq_entries.faq_id
 WHERE faq_entries.question_id=excluded.question_id AND faq_entries.author_id=excluded.author_id
 RETURNING `+faqColumns, actor.TeamID, questionID, id, actor.UserID))
	if err != nil {
		return f, constraintError(err)
	}
	return f, nil
}
func constraintError(err error) error {
	// Only uniqueness conflicts are public; driver messages never reach transports.
	var pg interface{ SQLState() string }
	if errors.As(err, &pg) && pg.SQLState() == "23505" {
		return ErrConflict
	}
	return err
}
func (s *Store) FAQ(ctx context.Context, actor Profile, id string) (FAQ, error) {
	return scanFAQ(s.db.QueryRowContext(ctx, `SELECT `+faqColumns+` FROM faq_entries WHERE team_id=$1 AND faq_id=$2`, actor.TeamID, id))
}
func (s *Store) SearchFAQ(ctx context.Context, actor Profile, text string) ([]FAQ, error) {
	text = strings.TrimSpace(text)
	if !validText(text, 500) {
		return nil, ErrInvalid
	}
	rows, err := s.db.QueryContext(ctx, `SELECT `+faqColumns+` FROM faq_entries WHERE team_id=$1
 AND (strpos(lower(question),lower($2))>0 OR strpos(lower(answer),lower($2))>0) ORDER BY created_at DESC,faq_id LIMIT 20`, actor.TeamID, text)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []FAQ{}
	for rows.Next() {
		var f FAQ
		if err := rows.Scan(&f.ID, &f.QuestionID, &f.Question, &f.Answer, &f.AuthorID, &f.CreatedAt); err != nil {
			return nil, err
		}
		f.SourcePath = "/api/v1/faq/" + url.PathEscape(f.ID)
		out = append(out, f)
	}
	return out, rows.Err()
}
