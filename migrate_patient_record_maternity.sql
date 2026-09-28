-- ==========================================================
-- Migration I-HUB : Dossier Médical Complet, Maternité & ID
-- ==========================================================

-- 1. Champs complets de consultation médicale
ALTER TABLE medical_consultations ADD COLUMN IF NOT EXISTS motif TEXT;
ALTER TABLE medical_consultations ADD COLUMN IF NOT EXISTS physical_exam TEXT;
ALTER TABLE medical_consultations ADD COLUMN IF NOT EXISTS care_plan TEXT;
ALTER TABLE medical_consultations ADD COLUMN IF NOT EXISTS treatment_plan TEXT;
ALTER TABLE medical_consultations ADD COLUMN IF NOT EXISTS recommendations TEXT;

-- 2. Champs maternité / suivi de grossesse
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS blood_type TEXT;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS reference_weight NUMERIC;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS reference_bp TEXT;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS vat_done BOOLEAN DEFAULT FALSE;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS complications TEXT;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS bio_results JSONB;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS gravidity INTEGER;
ALTER TABLE pregnancies ADD COLUMN IF NOT EXISTS parity INTEGER;

-- 3. Identifiant hospitalier IH-USHD-00001
ALTER TABLE patients ADD COLUMN IF NOT EXISTS hospital_id TEXT;
UPDATE patients
SET hospital_id = 'IH-USHD-' || LPAD(id::text, 5, '0')
WHERE hospital_id IS NULL OR hospital_id NOT LIKE 'IH-USHD-%';

-- 4. Nouveau-nés : un vrai dossier patient I-HUB, lié sans ambiguïté à la
-- mère, à la grossesse et à l'accouchement. Les informations de naissance
-- sont écrites une fois à la validation puis lues en lecture seule.
ALTER TABLE patients ADD COLUMN IF NOT EXISTS mother_id BIGINT REFERENCES patients(id);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS pregnancy_id BIGINT REFERENCES pregnancies(id);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS delivery_id BIGINT;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS is_newborn BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS birth_time TIMESTAMPTZ;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS birth_weight NUMERIC;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS delivery_mode TEXT;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS apgar TEXT;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS birth_observations TEXT;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS newborn_name_locked BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS delivery_time TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_patients_newborn ON patients (is_newborn) WHERE is_newborn = TRUE;
CREATE INDEX IF NOT EXISTS idx_patients_mother_id ON patients (mother_id);

-- 5. Carnet vaccinal volontairement simple : une dose effectuée est unique et
-- ne peut donc jamais être validée deux fois.
CREATE TABLE IF NOT EXISTS newborn_vaccinations (
  id BIGSERIAL PRIMARY KEY,
  baby_patient_id BIGINT NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
  vaccine_code TEXT NOT NULL,
  vaccine_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'completed',
  administered_at TIMESTAMPTZ NOT NULL,
  administered_by BIGINT,
  administered_by_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT newborn_vaccinations_once UNIQUE (baby_patient_id, vaccine_code)
);
