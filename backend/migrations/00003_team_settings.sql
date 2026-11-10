-- +goose Up
CREATE TABLE team_flags (
    team_id text NOT NULL REFERENCES teams,
    name text NOT NULL CHECK (name IN ('access_monitoring', 'team_escalation', 'faq_publication')),
    enabled boolean NOT NULL,
    updated_by text NOT NULL REFERENCES users,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, name)
);
ALTER TABLE questions ADD COLUMN context jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(context) = 'object');
ALTER TABLE questions ADD COLUMN bot_answer text NOT NULL DEFAULT '';
ALTER TABLE questions ADD COLUMN bot_helpful boolean;
ALTER TABLE questions ADD COLUMN bot_sources jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(bot_sources) = 'array');

-- +goose Down
ALTER TABLE questions DROP COLUMN context;
ALTER TABLE questions DROP COLUMN bot_answer, DROP COLUMN bot_sources, DROP COLUMN bot_helpful;
DROP TABLE team_flags;
