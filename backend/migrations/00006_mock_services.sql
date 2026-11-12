-- +goose Up
CREATE TABLE mock_access_rules (
    team_id text NOT NULL,
    object_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('granted','pending','denied','error')),
    reason text NOT NULL DEFAULT '',
    error_code text NOT NULL DEFAULT '',
    PRIMARY KEY (team_id, object_id),
    FOREIGN KEY (team_id, object_id) REFERENCES access_catalog(team_id, object_id),
    CHECK ((status = 'error' AND error_code IN ('service_unavailable','service_unconfigured','rate_limited'))
        OR (status <> 'error' AND error_code = '')),
    CHECK (status <> 'denied' OR length(trim(reason)) > 0)
);
CREATE TABLE mock_access_requests (
    request_id text NOT NULL UNIQUE,
    user_id text NOT NULL,
    team_id text NOT NULL,
    object_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('granted','pending','denied','error')),
    reason text NOT NULL DEFAULT '',
    error_code text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, object_id),
    FOREIGN KEY (user_id, team_id) REFERENCES users(user_id, team_id),
    FOREIGN KEY (team_id, object_id) REFERENCES access_catalog(team_id, object_id),
    CHECK ((status = 'error' AND error_code IN ('service_unavailable','service_unconfigured','rate_limited'))
        OR (status <> 'error' AND error_code = '')),
    CHECK (status <> 'denied' OR length(trim(reason)) > 0)
);
CREATE TABLE mock_progress (
    user_id text NOT NULL,
    team_id text NOT NULL,
    item_id text NOT NULL CHECK (item_id = 'equator_presentation'),
    completed boolean NOT NULL,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY (user_id, team_id) REFERENCES users(user_id, team_id),
    CHECK (completed = (completed_at IS NOT NULL))
);
CREATE INDEX mock_access_requests_team_idx ON mock_access_requests(team_id, request_id);

-- +goose Down
DROP TABLE mock_progress, mock_access_requests, mock_access_rules;
