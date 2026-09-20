-- 005_genai_triage.sql
-- Agentic triage runs and their tool ledgers (genai/).
--
-- `triage_tool_calls.result` is the EVIDENCE OF RECORD: the exact (already truncated)
-- payload the model was shown. genai/verifier.py re-checks every cited number against
-- these rows and never re-calls the API -- re-calling would verify against data the
-- model never saw, and would let a changing database silently repair a bad citation.
--
-- Unlike the retrain job (whose compare() verdict lives only in an ephemeral job file --
-- a gap RetrainingConsole.tsx's own comment records), the verdict here is persisted.
-- The ops DB is authoritative; the jobs-dir file carries only a live progress heartbeat.

CREATE TABLE IF NOT EXISTS triage_runs (
    run_id            TEXT PRIMARY KEY,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    plant_id          TEXT NOT NULL,
    question          TEXT NOT NULL,
    requested_by      TEXT NOT NULL,      -- role that triggered the run
    acting_role       TEXT NOT NULL,      -- role the tools ran AS; capped at requested_by
    mode              TEXT NOT NULL,      -- agent | deterministic
    status            TEXT NOT NULL,      -- queued|running|verified|
                                          -- verified_with_redactions|rejected|failed
    provider          TEXT,
    model_id          TEXT,
    finding           JSONB,              -- SERVED finding, post-redaction
    finding_raw       JSONB,              -- as emitted, pre-redaction
    verification      JSONB,              -- per-claim verdicts and reasons
    claims_total      INTEGER DEFAULT 0,
    claims_verified   INTEGER DEFAULT 0,
    iterations        INTEGER DEFAULT 0,
    tool_calls        INTEGER DEFAULT 0,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd          REAL,
    -- Contract #2 (CLAUDE.md §3) applies to OUR costs too, the moment they appear in an
    -- API response. Names the rate card and the date it was read.
    cost_source       TEXT,
    latency_s         REAL,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_triage_runs_plant
    ON triage_runs (plant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS triage_tool_calls (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT REFERENCES triage_runs (run_id),
    call_id     TEXT NOT NULL,          -- runner-assigned: call_0001, call_0002, ...
    seq         INTEGER NOT NULL,
    tool_name   TEXT NOT NULL,
    arguments   JSONB,
    result      JSONB,                  -- the exact bytes the model saw
    duration_ms REAL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_calls_run
    ON triage_tool_calls (run_id, call_id);
