-- +goose Up
CREATE TABLE teams (
    team_id text PRIMARY KEY CHECK (team_id <> '')
);
CREATE TABLE users (
    user_id text PRIMARY KEY CHECK (user_id <> ''),
    display_name text NOT NULL CHECK (display_name <> ''),
    role text NOT NULL CHECK (role IN ('intern', 'mentor', 'teamlead')),
    team_id text NOT NULL REFERENCES teams(team_id),
    mentor_id text,
    telegram_user_id bigint UNIQUE CHECK (telegram_user_id > 0),
    telegram_chat_id bigint UNIQUE CHECK (telegram_chat_id > 0),
    logins jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, team_id),
    FOREIGN KEY (mentor_id, team_id) REFERENCES users(user_id, team_id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK ((telegram_user_id IS NULL) = (telegram_chat_id IS NULL)),
    CHECK (telegram_user_id = telegram_chat_id),
    CHECK (mentor_id IS NULL OR mentor_id <> user_id)
);
CREATE INDEX users_team_idx ON users(team_id);
CREATE TABLE invitations (
    code_hash bytea PRIMARY KEY CHECK (octet_length(code_hash) = 32),
    user_id text NOT NULL REFERENCES users(user_id),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);
CREATE INDEX invitations_user_idx ON invitations(user_id);

-- +goose Down
DROP TABLE invitations;
DROP TABLE users;
DROP TABLE teams;
