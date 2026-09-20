-- 006_genai_copilot.sql
-- Conversational threads over triage runs.
--
-- A thread is a CONTAINER FOR ORDERED RUNS, not a second store of truth. Every verdict
-- still lives on `triage_runs`; every citation is still scoped to exactly ONE run's
-- transcript in `triage_tool_calls`.
--
-- WHY CITATIONS DO NOT CROSS TURNS (ADR-039)
-- ------------------------------------------
-- `genai/loop.py` assigns `call_id` per run: turn 1 and turn 3 both have a `call_0003`,
-- pointing at different tool results. Replaying turn 1's citations into turn 3 would let
-- a pointer resolve against turn 3's transcript and land, by coincidence, within the
-- verifier's rounding tolerance of an unrelated number -- a claim marked `verified` and
-- traced to evidence that has nothing to do with it. That is the worst failure this
-- feature can produce, and it is silently reachable. So the prompt forbids it outright
-- and the model must re-call a tool to restate an earlier number.
--
-- `carried_values` is the ONE thing that crosses a turn boundary: the set of numbers a
-- previous turn's verifier already passed, admitted as a prose exemption (check C3) and
-- nothing else. A CLAIM can still never be verified by it. It is derived on the server
-- from `triage_runs.finding`, never supplied by a client -- a browser that could seed the
-- exemption set could disarm the uncited-number sweep by pasting a fabricated figure.
-- It is stored per run so `python -m genai.verify_cli --run-id X` still reproduces that
-- turn's verdict byte-identically, which is the whole audit claim.

CREATE TABLE IF NOT EXISTS triage_threads (
    thread_id   TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    plant_id    TEXT NOT NULL,
    title       TEXT NOT NULL,          -- derived from turn 0; never model-written
    opened_by   TEXT NOT NULL,          -- role that opened the thread
    turn_count  INTEGER DEFAULT 0,
    last_run_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_triage_threads_plant
    ON triage_threads (plant_id, created_at DESC);

ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS thread_id          TEXT;
ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS turn_index         INTEGER DEFAULT 0;
ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS intent             TEXT DEFAULT 'ask';
ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS station_id         INTEGER;
ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS carried_values     JSONB;
ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS capability_profile TEXT;

-- One turn index per thread. Also the concurrency guard: two POSTs racing on the same
-- thread cannot both claim turn N, so a double-clicked Send cannot interleave two
-- workers writing the same heartbeat file.
CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_runs_thread_turn
    ON triage_runs (thread_id, turn_index);

-- The human-readable activity label the progress surface renders, recorded alongside the
-- call so the trail does not flicker when the UI switches from reading the live heartbeat
-- to reading the persisted ledger.
ALTER TABLE triage_tool_calls ADD COLUMN IF NOT EXISTS phase TEXT;
