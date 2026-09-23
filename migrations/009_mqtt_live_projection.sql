-- MQTT publishers are external producers. Keeping their ownership explicit prevents a
-- backend restart from incorrectly stopping a run still being produced by ingestion.
ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS producer TEXT NOT NULL DEFAULT 'backend';
CREATE INDEX IF NOT EXISTS idx_simulation_runs_active_producer
    ON simulation_runs (plant_id, producer, started_at DESC);
