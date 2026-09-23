-- Complete the human assignment/audit fields required by the containment workflow.
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS decided_by_user TEXT;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS due_station INTEGER;
