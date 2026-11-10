-- +goose Up
-- Preserve the original creation payload for idempotent retries after assignment.
ALTER TABLE questions ADD COLUMN initial_assigned_to text;
UPDATE questions SET initial_assigned_to=assigned_to;

-- +goose Down
ALTER TABLE questions DROP COLUMN initial_assigned_to;
