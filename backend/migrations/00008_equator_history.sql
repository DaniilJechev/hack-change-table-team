-- +goose Up
CREATE TABLE equator_generations (
    user_id text PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    generation bigint NOT NULL DEFAULT 0
);
CREATE TABLE equator_checks (
    user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    team_id text NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
    check_id text NOT NULL,
    generation bigint NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    current boolean NOT NULL DEFAULT false,
    delivery_file text NOT NULL DEFAULT 'pending',
    delivery_report text NOT NULL DEFAULT 'pending',
    PRIMARY KEY(user_id,check_id),
    CHECK(delivery_file IN ('pending','sending','sent','failed','unknown')),
    CHECK(delivery_report IN ('pending','sending','sent','failed','unknown'))
);
CREATE INDEX equator_checks_expiry ON equator_checks(expires_at);

-- +goose Down
DROP TABLE equator_checks;
DROP TABLE equator_generations;
