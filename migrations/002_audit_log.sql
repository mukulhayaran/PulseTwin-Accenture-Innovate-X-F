-- 002_audit_log.sql
-- Immutable action log for RBAC (Phase F4). Every mutating endpoint writes one row here
-- via serve/middleware/audit.py. Never contains raw sensor data or operator identity --
-- see docs/DATA_GOVERNANCE.md and the "Operator privacy" contract in CLAUDE.md.

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    user_role   TEXT NOT NULL,      -- supervisor, manager, leadership, system
    user_id     TEXT,               -- populated once OIDC is wired in (docs/SECURITY.md)
    action      TEXT NOT NULL,      -- alert_acknowledged, feedback_submitted, whatif_run,
                                     -- model_promoted, config_changed, retrain_triggered, ...
    resource_type TEXT,             -- unit, alert, model, station
    resource_id   TEXT,
    payload     JSONB,              -- sanitised action details -- IDs and outcomes only
    ip_address  TEXT,
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_role      ON audit_log (user_role);
