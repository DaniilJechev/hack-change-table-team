-- +goose Up
ALTER TABLE users ADD COLUMN internship_started_on date;
CREATE TABLE team_cards (
    team_id text NOT NULL REFERENCES teams,
    version bigint GENERATED ALWAYS AS IDENTITY,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, version)
);
CREATE TABLE access_catalog (
    team_id text NOT NULL REFERENCES teams,
    object_id text NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    PRIMARY KEY (team_id, object_id)
);
CREATE TABLE access_marks (
    user_id text NOT NULL REFERENCES users,
    team_id text NOT NULL,
    object_id text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, object_id),
    FOREIGN KEY (user_id, team_id) REFERENCES users(user_id, team_id),
    FOREIGN KEY (team_id, object_id) REFERENCES access_catalog(team_id, object_id)
);
CREATE TABLE access_snapshots (
    user_id text NOT NULL REFERENCES users,
    snapshot_id text NOT NULL,
    objects jsonb NOT NULL CHECK (jsonb_typeof(objects) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    PRIMARY KEY (user_id, snapshot_id)
);
CREATE TABLE briefs (
    user_id text NOT NULL REFERENCES users,
    brief_id text NOT NULL,
    items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array'),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    read_at timestamptz,
    PRIMARY KEY (user_id, brief_id)
);
CREATE TABLE brief_events (
    user_id text NOT NULL,
    event_id text NOT NULL,
    brief_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('read', 'click', 'positive', 'negative', 'reply')),
    item_id text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, event_id),
    FOREIGN KEY (user_id, brief_id) REFERENCES briefs(user_id, brief_id)
);
CREATE TABLE questions (
    team_id text NOT NULL REFERENCES teams,
    question_id text NOT NULL,
    user_id text NOT NULL,
    text text NOT NULL,
    assigned_to text,
    answer text NOT NULL DEFAULT '',
    helpful boolean,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, question_id),
    FOREIGN KEY (user_id, team_id) REFERENCES users(user_id, team_id),
    FOREIGN KEY (assigned_to, team_id) REFERENCES users(user_id, team_id)
);
CREATE TABLE faq_entries (
    team_id text NOT NULL,
    faq_id text NOT NULL,
    question_id text NOT NULL,
    question text NOT NULL,
    answer text NOT NULL,
    author_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, faq_id),
    UNIQUE (team_id, question_id),
    FOREIGN KEY (team_id, question_id) REFERENCES questions(team_id, question_id),
    FOREIGN KEY (author_id, team_id) REFERENCES users(user_id, team_id)
);
CREATE INDEX access_snapshots_latest_idx ON access_snapshots(user_id, sequence DESC);
CREATE INDEX questions_owner_idx ON questions(user_id);

-- +goose Down
DROP TABLE faq_entries, questions, brief_events, briefs, access_snapshots, access_marks, access_catalog, team_cards;
ALTER TABLE users DROP COLUMN internship_started_on;
