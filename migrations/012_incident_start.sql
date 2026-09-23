-- Optional simulation-time delay before a configured scenario becomes active.
ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS incident_start_s REAL NOT NULL DEFAULT 0;
