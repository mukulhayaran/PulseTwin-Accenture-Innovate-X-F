-- 016_feedback.sql
-- Supervisor feedback moves into Postgres (ADR-052).
--
-- It used to live in a tempdir SQLite file even when the API ran on Postgres
-- (serve/db.py::open_feedback_db), so every Render restart erased it. On Postgres,
-- serve/main.py now points STATE["feedback"] at the ops handle; this is its table. Same
-- columns as the SQLite feedback file, so the INSERT/SELECT in serve/main.py work on both.

CREATE TABLE IF NOT EXISTS feedback (
    id          BIGSERIAL PRIMARY KEY,
    plant_id    TEXT,
    unit_id     TEXT,
    verdict     TEXT,
    created_at  TEXT
);
