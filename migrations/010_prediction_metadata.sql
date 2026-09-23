-- Preserve the exact serving contract used for each replay/live/real prediction.
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS model_version INTEGER;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS feature_set_version TEXT;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS prediction_ts TIMESTAMPTZ;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS features_available INTEGER;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS features_missing INTEGER;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS data_completeness_pct REAL;
ALTER TABLE model_predictions_log ADD COLUMN IF NOT EXISTS source TEXT;
CREATE INDEX IF NOT EXISTS idx_prediction_run ON model_predictions_log (run_id, timestamp DESC);
