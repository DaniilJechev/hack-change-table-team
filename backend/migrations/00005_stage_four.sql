-- +goose Up
SELECT pg_advisory_xact_lock(704489103);
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE TABLE IF NOT EXISTS rag_chunks (
    id text PRIMARY KEY, commit_sha text NOT NULL, file text NOT NULL,
    heading text NOT NULL, url text NOT NULL, body text NOT NULL,
    embedding public.vector(384) NOT NULL
);
CREATE INDEX IF NOT EXISTS rag_chunks_commit_idx ON rag_chunks(commit_sha);
ALTER TABLE briefs ADD COLUMN calendar jsonb CHECK (jsonb_typeof(calendar)='object');
CREATE INDEX briefs_sent_idx ON briefs(user_id,sent_at DESC) WHERE status='sent';
CREATE TABLE brief_links (
    token text PRIMARY KEY CHECK (token ~ '^[a-f0-9]{64}$'),
    user_id text NOT NULL, brief_id text NOT NULL, item_id text NOT NULL, url text NOT NULL,
    UNIQUE(user_id,brief_id,item_id),
    FOREIGN KEY(user_id,brief_id) REFERENCES briefs(user_id,brief_id)
);
CREATE TABLE brief_settings (
    user_id text PRIMARY KEY REFERENCES users,
    time text CHECK (time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    quiet boolean NOT NULL DEFAULT false
);
-- Old records have no trustworthy category: do not count old H3 answers as H2.
ALTER TABLE questions ADD COLUMN capability text NOT NULL DEFAULT 'unknown'
    CHECK (capability IN ('h2','h3','unknown'));
ALTER TABLE questions ALTER COLUMN capability SET DEFAULT 'h2';
CREATE TABLE mcp_calls (
    user_id text NOT NULL REFERENCES users, call_id text NOT NULL,
    case_name text NOT NULL, server text NOT NULL, tool text NOT NULL, outcome text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,call_id)
);
-- Only these aggregate views are granted to the Grafana SQL role.
CREATE VIEW metrics_briefs WITH (security_barrier=true) AS
SELECT count(*) AS sent, count(read_at) AS read,
 count(*) FILTER (WHERE EXISTS(SELECT 1 FROM brief_events e WHERE e.user_id=b.user_id AND e.brief_id=b.brief_id AND e.kind='click')) AS clicked,
 100.0*count(read_at)/nullif(count(*),0) AS read_percent,
 100.0*count(*) FILTER (WHERE EXISTS(SELECT 1 FROM brief_events e WHERE e.user_id=b.user_id AND e.brief_id=b.brief_id AND e.kind='click'))/nullif(count(*),0) AS clicked_percent
FROM briefs b JOIN users u USING(user_id) WHERE status='sent' AND u.role='intern';
CREATE VIEW metrics_helpful WITH (security_barrier=true) AS
SELECT capability, 'bot'::text AS answer_kind,
 count(bot_helpful) AS rated, count(*) FILTER (WHERE bot_helpful) AS helpful,
 100.0*count(*) FILTER (WHERE bot_helpful)/nullif(count(bot_helpful),0) AS helpful_percent
FROM questions GROUP BY capability
UNION ALL
SELECT capability,'human',count(helpful),count(*) FILTER (WHERE helpful),
 100.0*count(*) FILTER (WHERE helpful)/nullif(count(helpful),0)
FROM questions GROUP BY capability;
CREATE VIEW metrics_access WITH (security_barrier=true) AS
SELECT c.document->>'instrument' AS instrument, count(g.first_granted) AS completed,
 avg(extract(epoch FROM(g.first_granted-m.requested_at))/3600.0) AS mean_hours
FROM access_marks m JOIN access_catalog c USING(team_id,object_id)
JOIN users u ON u.user_id=m.user_id AND u.role='intern'
LEFT JOIN LATERAL (
 SELECT min(s.created_at) AS first_granted FROM access_snapshots s,
 jsonb_array_elements(s.objects) o
 WHERE s.user_id=m.user_id AND s.created_at>=m.requested_at AND o->>'object_id'=m.object_id
 AND o->>'status'='granted' AND coalesce(o->>'error','')=''
) g ON true GROUP BY c.document->>'instrument';
CREATE VIEW metrics_mcp WITH (security_barrier=true) AS
SELECT case_name,server,tool,outcome,count(*) AS calls FROM mcp_calls GROUP BY case_name,server,tool,outcome;

-- +goose Down
DROP VIEW metrics_mcp,metrics_access,metrics_helpful,metrics_briefs;
DROP TABLE mcp_calls,brief_settings,brief_links;
ALTER TABLE questions DROP COLUMN capability;
DROP INDEX briefs_sent_idx;
ALTER TABLE briefs DROP COLUMN calendar;
-- rag_chunks may predate this migration; preserve its data on downgrade.
