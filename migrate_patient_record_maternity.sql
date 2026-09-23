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
