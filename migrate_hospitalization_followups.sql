-- I-HUB : trace clinique complète des surveillances infirmières hospitalières.
ALTER TABLE hospitalization_followups ADD COLUMN IF NOT EXISTS hospitalization_id BIGINT REFERENCES hospitalizations(id);
ALTER TABLE hospitalization_followups ADD COLUMN IF NOT EXISTS oxygen_saturation NUMERIC;
ALTER TABLE hospitalization_followups ADD COLUMN IF NOT EXISTS weight NUMERIC;
ALTER TABLE hospitalization_followups ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ;
UPDATE hospitalization_followups SET recorded_at = created_at WHERE recorded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_hospitalization_followups_patient_time
  ON hospitalization_followups (patient_id, recorded_at DESC);
