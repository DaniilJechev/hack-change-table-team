-- +goose Up
CREATE TABLE desktop_devices (
    secret_hash bytea PRIMARY KEY,
    code text UNIQUE NOT NULL,
    user_id text REFERENCES users(user_id),
    expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes',
    used boolean NOT NULL DEFAULT false
);
CREATE TABLE desktop_sessions (
    token_hash bytea PRIMARY KEY,
    user_id text NOT NULL,
    team_id text NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now()+interval '30 days',
    FOREIGN KEY(user_id,team_id) REFERENCES users(user_id,team_id)
);
CREATE TABLE channel_records (
    seq bigserial PRIMARY KEY,
    user_id text NOT NULL,
    team_id text NOT NULL,
    id text NOT NULL,
    kind text NOT NULL CHECK(kind IN ('message','request','file','read','state')),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id,team_id,kind,id),
    FOREIGN KEY(user_id,team_id) REFERENCES users(user_id,team_id)
);
CREATE INDEX channel_history ON channel_records(user_id,team_id,kind,seq);
CREATE TABLE channel_events (
    seq bigserial PRIMARY KEY,
    user_id text NOT NULL,
    team_id text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    FOREIGN KEY(user_id,team_id) REFERENCES users(user_id,team_id)
);
CREATE INDEX channel_resume ON channel_events(user_id,team_id,seq);

-- +goose Down
DROP TABLE channel_events;
DROP TABLE channel_records;
DROP TABLE desktop_sessions;
DROP TABLE desktop_devices;
