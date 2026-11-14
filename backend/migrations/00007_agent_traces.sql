-- +goose Up
-- Agent turn traces (FR-TR-02). No FK to users: demo users are not stored in PostgreSQL.
-- Contains user text; readable only through the service-key REST routes, never by buddy_metrics.
CREATE TABLE agent_traces (
    id text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    user_id text NOT NULL,
    team_id text NOT NULL,
    role text NOT NULL,
    demo boolean NOT NULL,
    path text NOT NULL CHECK (path IN ('agent','router','followup','trigger')),
    fallback boolean NOT NULL,
    case_name text NOT NULL DEFAULT '',
    duration_ms integer NOT NULL CHECK (duration_ms >= 0),
    llm_calls integer NOT NULL CHECK (llm_calls >= 0),
    tool_calls integer NOT NULL CHECK (tool_calls >= 0),
    turn jsonb NOT NULL CHECK (jsonb_typeof(turn) = 'object')
);
CREATE INDEX agent_traces_created_idx ON agent_traces(created_at);
CREATE INDEX agent_traces_user_idx ON agent_traces(user_id, created_at DESC);
-- FR-TR-05: aggregate only; no text, user_id or turn.
CREATE VIEW metrics_agent WITH (security_barrier=true) AS
SELECT (created_at AT TIME ZONE 'UTC')::date AS day, path,
 count(*) AS turns,
 100.0*count(*) FILTER (WHERE fallback)/nullif(count(*),0) AS fallback_percent,
 avg(duration_ms)::double precision AS avg_duration_ms,
 percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_duration_ms,
 avg(tool_calls)::double precision AS avg_tool_calls
FROM agent_traces GROUP BY 1, 2;

-- +goose Down
DROP VIEW metrics_agent;
DROP TABLE agent_traces;
