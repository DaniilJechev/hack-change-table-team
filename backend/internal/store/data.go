package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var (
	ErrForbidden = errors.New("forbidden")
	ErrInvalid   = errors.New("invalid request")
)
var identifier = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$`)

func ValidID(value string) bool { return identifier.MatchString(value) }
func validText(value string, max int) bool {
	return strings.TrimSpace(value) != "" && len(value) <= max
}
func validURL(value string) bool {
	u, err := url.Parse(value)
	return err == nil && len(value) <= 2048 && !strings.ContainsAny(value, " \t\r\n") && (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != "" && u.User == nil
}
func noRows(err error) error {
	if errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	}
	return err
}

// User resolves authorization from persistent data. No caller supplies team or role.
func (s *Store) User(ctx context.Context, id string) (Profile, error) {
	return scanProfile(s.db.QueryRowContext(ctx, `SELECT `+profileColumns+` FROM users WHERE user_id=$1`, id))
}

type UserProfile struct {
	Profile
	Logins         map[string]string `json:"logins"`
	TeamLeadID     string            `json:"teamlead_id,omitempty"`
	InternshipWeek *int              `json:"internship_week"`
}

func (s *Store) UserProfile(ctx context.Context, actor Profile) (UserProfile, error) {
	p := UserProfile{Profile: actor, Logins: map[string]string{}}
	var raw []byte
	err := s.db.QueryRowContext(ctx, `SELECT logins,
 coalesce((SELECT user_id FROM users WHERE team_id=$2 AND role='teamlead' ORDER BY user_id LIMIT 1), ''),
 CASE WHEN internship_started_on IS NULL THEN NULL ELSE greatest(1, (CURRENT_DATE-internship_started_on)/7+1) END
 FROM users WHERE user_id=$1 AND team_id=$2`, actor.UserID, actor.TeamID).Scan(&raw, &p.TeamLeadID, &p.InternshipWeek)
	if err != nil {
		return p, noRows(err)
	}
	err = json.Unmarshal(raw, &p.Logins)
	return p, err
}

type TeamCard struct {
	TeamID     string         `json:"team_id"`
	Version    int64          `json:"version"`
	Document   map[string]any `json:"document"`
	SourcePath string         `json:"source_path"`
}

func (s *Store) TeamCard(ctx context.Context, actor Profile, section string, version int64) (TeamCard, error) {
	card := TeamCard{TeamID: actor.TeamID}
	var raw []byte
	err := s.db.QueryRowContext(ctx, `SELECT version, document FROM team_cards WHERE team_id=$1 AND ($2=0 OR version=$2) ORDER BY version DESC LIMIT 1`, actor.TeamID, version).Scan(&card.Version, &raw)
	if err != nil {
		return card, noRows(err)
	}
	if err := json.Unmarshal(raw, &card.Document); err != nil {
		return card, err
	}
	card.SourcePath = "/api/v1/team-card?version=" + strconv.FormatInt(card.Version, 10)
	if section != "" {
		if !ValidID(section) {
			return card, ErrInvalid
		}
		value, ok := card.Document[section]
		if !ok {
			return card, ErrNotFound
		}
		card.Document = map[string]any{section: value}
		card.SourcePath += "#" + section
	}
	return card, nil
}

type CatalogObject struct {
	Target     map[string]any `json:"target,omitempty" yaml:"target,omitempty"`
	ID         string         `json:"id" yaml:"id"`
	Instrument string         `json:"instrument" yaml:"instrument"`
	Name       string         `json:"name" yaml:"name"`
	URL        string         `json:"url" yaml:"url"`
	RequestURL string         `json:"request_url,omitempty" yaml:"request_url,omitempty"`
	OwnerID    string         `json:"owner_id" yaml:"owner_id"`
	Roles      []string       `json:"roles" yaml:"roles"`
	Forbidden  bool           `json:"forbidden" yaml:"forbidden"`
	Reason     string         `json:"reason,omitempty" yaml:"reason,omitempty"`
}

func (o CatalogObject) visible(role string) bool {
	for _, r := range o.Roles {
		if r == role {
			return true
		}
	}
	return false
}
func (s *Store) AccessCatalog(ctx context.Context, actor Profile, objects []string) ([]CatalogObject, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT document FROM access_catalog WHERE team_id=$1 ORDER BY object_id`, actor.TeamID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []CatalogObject{}
	filter := map[string]bool{}
	for _, id := range objects {
		if !ValidID(id) {
			return nil, ErrInvalid
		}
		filter[id] = true
	}
	for rows.Next() {
		var raw []byte
		var obj CatalogObject
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(raw, &obj); err != nil {
			return nil, err
		}
		if obj.visible(actor.Role) && (len(objects) == 0 || filter[obj.ID]) {
			out = append(out, obj)
		}
	}
	return out, rows.Err()
}

type AccessMark struct {
	ObjectID    string    `json:"object_id"`
	RequestedAt time.Time `json:"requested_at"`
}

func (s *Store) AccessMarks(ctx context.Context, actor Profile) ([]AccessMark, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT object_id,requested_at FROM access_marks WHERE user_id=$1 ORDER BY object_id`, actor.UserID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []AccessMark{}
	for rows.Next() {
		var mark AccessMark
		if err := rows.Scan(&mark.ObjectID, &mark.RequestedAt); err != nil {
			return nil, err
		}
		out = append(out, mark)
	}
	return out, rows.Err()
}
func (s *Store) MarkAccess(ctx context.Context, actor Profile, objectID string) (AccessMark, error) {
	var mark AccessMark
	if !ValidID(objectID) {
		return mark, ErrInvalid
	}
	// One statement keeps validation and insert atomic with catalog changes.
	err := s.db.QueryRowContext(ctx, `INSERT INTO access_marks(user_id,team_id,object_id)
 SELECT $1,$2,object_id FROM access_catalog WHERE team_id=$2 AND object_id=$3
 AND document->'roles' ? $4 AND NOT (document->>'forbidden')::boolean
 ON CONFLICT (user_id,object_id) DO UPDATE SET requested_at=access_marks.requested_at
 RETURNING object_id,requested_at`, actor.UserID, actor.TeamID, objectID, actor.Role).Scan(&mark.ObjectID, &mark.RequestedAt)
	return mark, noRows(err)
}

type AccessState struct {
	ObjectID string `json:"object_id"`
	Status   string `json:"status"`
	Error    string `json:"error,omitempty"`
}
type Snapshot struct {
	ID        string        `json:"id"`
	Objects   []AccessState `json:"objects"`
	CreatedAt time.Time     `json:"created_at"`
}

func (s *Store) SaveSnapshot(ctx context.Context, actor Profile, input Snapshot) (Snapshot, error) {
	if !ValidID(input.ID) || input.Objects == nil || len(input.Objects) > 100 {
		return input, ErrInvalid
	}
	catalog, err := s.AccessCatalog(ctx, actor, nil)
	if err != nil {
		return input, err
	}
	allowed := map[string]CatalogObject{}
	for _, o := range catalog {
		allowed[o.ID] = o
	}
	seen := map[string]bool{}
	for _, o := range input.Objects {
		def, exists := allowed[o.ObjectID]
		if !exists || seen[o.ObjectID] || len(o.Error) > 256 {
			return input, ErrInvalid
		}
		seen[o.ObjectID] = true
		switch o.Status {
		case "granted", "missing", "requested", "stale", "forbidden":
		default:
			return input, ErrInvalid
		}
		if def.Forbidden && o.Status != "forbidden" {
			return input, ErrInvalid
		}
	}
	raw, _ := json.Marshal(input.Objects)
	err = s.db.QueryRowContext(ctx, `INSERT INTO access_snapshots(user_id,snapshot_id,objects) VALUES($1,$2,$3)
 ON CONFLICT(user_id,snapshot_id) DO UPDATE SET objects=access_snapshots.objects
 WHERE access_snapshots.objects=excluded.objects RETURNING created_at`, actor.UserID, input.ID, string(raw)).Scan(&input.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return input, ErrConflict
	}
	return input, err
}
func (s *Store) LatestSnapshot(ctx context.Context, actor Profile) (Snapshot, error) {
	var out Snapshot
	var raw []byte
	err := s.db.QueryRowContext(ctx, `SELECT snapshot_id,objects,created_at FROM access_snapshots WHERE user_id=$1 ORDER BY sequence DESC LIMIT 1`, actor.UserID).Scan(&out.ID, &raw, &out.CreatedAt)
	if err != nil {
		return out, noRows(err)
	}
	err = json.Unmarshal(raw, &out.Objects)
	return out, err
}

type Intern struct {
	Profile
	Snapshot *Snapshot    `json:"snapshot"`
	Marks    []AccessMark `json:"marks"`
}

func (s *Store) Interns(ctx context.Context, actor Profile) ([]Intern, error) {
	if actor.Role != "mentor" && actor.Role != "teamlead" {
		return nil, ErrForbidden
	}
	rows, err := s.db.QueryContext(ctx, `SELECT `+profileColumns+`,
 (SELECT jsonb_build_object('id',snapshot_id,'objects',objects,'created_at',created_at)
 FROM access_snapshots WHERE user_id=users.user_id ORDER BY sequence DESC LIMIT 1),
 coalesce((SELECT jsonb_agg(jsonb_build_object('object_id',object_id,'requested_at',requested_at) ORDER BY object_id)
 FROM access_marks WHERE user_id=users.user_id AND team_id=users.team_id),'[]'::jsonb)
 FROM users WHERE team_id=$1 AND role='intern' AND ($2='teamlead' OR mentor_id=$3) ORDER BY user_id`, actor.TeamID, actor.Role, actor.UserID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Intern{}
	for rows.Next() {
		var p Intern
		var raw, marks []byte
		if err := rows.Scan(&p.UserID, &p.DisplayName, &p.Role, &p.TeamID, &p.MentorID, &raw, &marks); err != nil {
			return nil, err
		}
		if raw != nil {
			if err := json.Unmarshal(raw, &p.Snapshot); err != nil {
				return nil, err
			}
		}
		if err := json.Unmarshal(marks, &p.Marks); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
