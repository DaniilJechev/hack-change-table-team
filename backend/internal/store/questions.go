package store

import (
	"context"
	"database/sql"
	"errors"
	"net/url"
	"strings"
	"time"
)

type Question struct {
	ID         string    `json:"id"`
	UserID     string    `json:"user_id"`
	Text       string    `json:"text"`
	AssignedTo string    `json:"assigned_to,omitempty"`
	Answer     string    `json:"answer"`
	Helpful    *bool     `json:"helpful"`
	CreatedAt  time.Time `json:"created_at"`
}

func scanQuestion(row *sql.Row) (Question, error) {
	var q Question
	err := row.Scan(&q.ID, &q.UserID, &q.Text, &q.AssignedTo, &q.Answer, &q.Helpful, &q.CreatedAt)
	return q, noRows(err)
}

const questionColumns = `question_id,user_id,text,coalesce(assigned_to,''),answer,helpful,created_at`

func (s *Store) SaveQuestion(ctx context.Context, actor Profile, input Question) (Question, error) {
	if !ValidID(input.ID) || !validText(input.Text, 8000) {
		return input, ErrInvalid
	}
	if input.AssignedTo != "" {
		recipient, err := s.User(ctx, input.AssignedTo)
		if err != nil {
			return input, err
		}
		if recipient.TeamID != actor.TeamID {
			return input, ErrForbidden
		}
	}
	q, err := scanQuestion(s.db.QueryRowContext(ctx, `INSERT INTO questions(team_id,question_id,user_id,text,assigned_to) VALUES($1,$2,$3,$4,nullif($5,''))
 ON CONFLICT(team_id,question_id) DO UPDATE SET text=questions.text
 WHERE questions.user_id=excluded.user_id AND questions.text=excluded.text AND questions.assigned_to IS NOT DISTINCT FROM excluded.assigned_to
 RETURNING `+questionColumns, actor.TeamID, input.ID, actor.UserID, input.Text, input.AssignedTo))
	if errors.Is(err, ErrNotFound) {
		return input, ErrConflict
	}
	return q, err
}
func (s *Store) Question(ctx context.Context, actor Profile, id string) (Question, error) {
	return scanQuestion(s.db.QueryRowContext(ctx, `SELECT `+questionColumns+` FROM questions WHERE team_id=$1 AND question_id=$2 AND (user_id=$3 OR assigned_to=$3)`, actor.TeamID, id, actor.UserID))
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
