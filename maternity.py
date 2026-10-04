"""Module maternité I-HUB : grossesses, prénatal, accouchements et chambres."""
from datetime import date, datetime, timedelta
from flask import Blueprint
import re
import uuid


def register_maternity_routes(app, *, runtime):
    globals().update(runtime)
    maternity = Blueprint("maternity", __name__)
    VACCINE_CATALOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "vaccine_catalog.json")

    def get_vaccine_catalog_from_store():
        """Charge le catalogue dynamique des vaccins (aucun vaccin en dur)."""
        catalog = []
        if os.path.exists(VACCINE_CATALOG_FILE):
            try:
                with open(VACCINE_CATALOG_FILE, "r", encoding="utf-8") as cf:
                    catalog = json.load(cf) or []
            except Exception:
                catalog = []
        try:
            db_rows = supabase.table("vaccine_catalog").select("*").execute().data or []
            db_codes = {str(r.get("code")).upper() for r in db_rows if r.get("code")}
            for r in db_rows:
                if r.get("code") and str(r.get("code")).upper() not in {str(c.get("code")).upper() for c in catalog}:
                    catalog.append(r)
        except Exception:
            pass
        return catalog

    def save_vaccine_catalog_to_store(catalog):
        try:
            os.makedirs(os.path.dirname(VACCINE_CATALOG_FILE), exist_ok=True)
            with open(VACCINE_CATALOG_FILE, "w", encoding="utf-8") as cf:
                json.dump(catalog, cf, ensure_ascii=False, indent=2)
        except Exception as e:
            print("Error saving vaccine catalog:", e)


    NEWBORN_VACCINES = (("BCG", "BCG"), ("VPO0", "VPO 0"), ("HEPB0", "Hépatite B — naissance"))
    # Catalogue vaccinal standard étendu (PEV élargi)
    DEFAULT_VACCINE_CATALOG = [
        {"code": "BCG", "name": "BCG (Tuberculose)", "target_age_months": 0, "target_age_label": "Naissance (0 mois)"},
        {"code": "VPO0", "name": "VPO 0 (Poliomyélite)", "target_age_months": 0, "target_age_label": "Naissance (0 mois)"},
        {"code": "HEPB0", "name": "Hépatite B (Naissance)", "target_age_months": 0, "target_age_label": "Naissance (0 mois)"},
        {"code": "PENTA1", "name": "Pentavalent 1 (DTC-HepB-Hib)", "target_age_months": 1.5, "target_age_label": "6 semaines (1.5 mois)"},
        {"code": "VPO1", "name": "VPO 1 (Poliomyélite)", "target_age_months": 1.5, "target_age_label": "6 semaines (1.5 mois)"},
        {"code": "PNEUMO1", "name": "Pneumocoque 1", "target_age_months": 1.5, "target_age_label": "6 semaines (1.5 mois)"},
        {"code": "ROTA1", "name": "Rotavirus 1", "target_age_months": 1.5, "target_age_label": "6 semaines (1.5 mois)"},
        {"code": "PENTA2", "name": "Pentavalent 2 (DTC-HepB-Hib)", "target_age_months": 2.5, "target_age_label": "10 semaines (2.5 mois)"},
        {"code": "VPO2", "name": "VPO 2 (Poliomyélite)", "target_age_months": 2.5, "target_age_label": "10 semaines (2.5 mois)"},
        {"code": "PNEUMO2", "name": "Pneumocoque 2", "target_age_months": 2.5, "target_age_label": "10 semaines (2.5 mois)"},
        {"code": "ROTA2", "name": "Rotavirus 2", "target_age_months": 2.5, "target_age_label": "10 semaines (2.5 mois)"},
        {"code": "PENTA3", "name": "Pentavalent 3 (DTC-HepB-Hib)", "target_age_months": 3.5, "target_age_label": "14 semaines (3.5 mois)"},
        {"code": "VPO3", "name": "VPO 3 (Poliomyélite)", "target_age_months": 3.5, "target_age_label": "14 semaines (3.5 mois)"},
        {"code": "VPI", "name": "VPI (Polio injectable)", "target_age_months": 3.5, "target_age_label": "14 semaines (3.5 mois)"},
        {"code": "PNEUMO3", "name": "Pneumocoque 3", "target_age_months": 3.5, "target_age_label": "14 semaines (3.5 mois)"},
        {"code": "VAR", "name": "VAR (Rougeole)", "target_age_months": 9, "target_age_label": "9 mois"},
        {"code": "VAA", "name": "VAA (Fièvre Jaune)", "target_age_months": 9, "target_age_label": "9 mois"},
        {"code": "MENA", "name": "Méningite A", "target_age_months": 18, "target_age_label": "18 mois"}
    ]


    def newborn_vaccine_plan(baby_patient_id):
        """Doses de vaccins pour un enfant (doses realisees + catalogue dynamique cree par les infirmiers)."""
        try:
            rows = supabase.table("newborn_vaccinations").select("*").eq("baby_patient_id", baby_patient_id).execute().data or []
        except Exception:
            rows = []
        done = {str(row.get("vaccine_code")).upper(): row for row in rows}
        catalog = get_vaccine_catalog_from_store()
        plan = []
        for vac in catalog:
            vcode = str(vac.get("code")).upper()
            vname = vac.get("name") or vcode
            is_done = vcode in done
            plan.append({
                "code": vcode,
                "name": vname,
                "status": "completed" if is_done else "planned",
                "target_age_months": vac.get("target_age_months", 0),
                "target_age_label": vac.get("target_age_label", ""),
                "record": done.get(vcode)
            })
        for vcode, rec in done.items():
            if not any(p["code"] == vcode for p in plan):
                plan.append({
                    "code": vcode,
                    "name": rec.get("vaccine_name") or vcode,
                    "status": "completed",
                    "record": rec
                })
        return plan

    def pregnancy_timeline(last_menstrual_period):
        """Retourne une DDR normalisée et les valeurs obstétricales calculées."""
        try:
            ddr = datetime.strptime(str(last_menstrual_period), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None
        if ddr > date.today():
            return None
        days = (date.today() - ddr).days
        weeks, extra_days = divmod(max(days, 0), 7)
        stage = "précoce" if weeks < 14 else "2e trimestre" if weeks < 28 else "3e trimestre" if weeks < 37 else "à terme"
        return {
            "last_menstrual_period": ddr.isoformat(),
            "expected_delivery_date": (ddr + timedelta(days=280)).isoformat(),
            "gestational_weeks": weeks,
            "gestational_days": extra_days,
            "gestational_age": f"{weeks}+{extra_days} SA",
            "pregnancy_stage": stage,
        }

    def doctor_owns_prenatal(consultation_id):
        row = supabase.table("prenatal_consultations").select("*").eq("id", consultation_id).execute().data or []
        if not row:
            return None
        consultation = row[0]
        creator_id = str(consultation.get("doctor_id") or consultation.get("created_by") or "")
        if g.current_user.get("role") == "docteur" and creator_id and creator_id != str(g.current_user.get("id") or ""):
            return False
        return consultation

    CPN_PERIODS = {
        1: (0, 12), 2: (16, 20), 3: (20, 24), 4: (24, 28),
        5: (28, 32), 6: (32, 36), 7: (36, 40),
    }

    def cpn_uid(data):
        supplied = str(data.get("uid") or "").strip().upper()
        if supplied and re.fullmatch(r"[A-Z0-9-]{8,80}", supplied):
            return supplied
        return f"CPN-{now_iso()[:10].replace('-', '')}-{uuid.uuid4().hex[:8].upper()}"

    @maternity.route("/api/maternity/pregnancies", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def get_pregnancies():
        patient_id = request.args.get("patient_id")
        status = request.args.get("status")
        query = supabase.table("pregnancies").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        if status and str(status).strip() and str(status).lower() != "all":
            query = query.eq("status", status)
        result = query.order("created_at", desc=True).execute()
        pregnancies = result.data or []
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        for p in pregnancies:
            p["patient_name"] = patient_map.get(p.get("patient_id"), "Inconnu")
            timeline = pregnancy_timeline(p.get("last_menstrual_period"))
            if timeline:
                p.update({key: value for key, value in timeline.items() if key != "last_menstrual_period"})
        return jsonify(pregnancies)

    @maternity.route("/api/maternity/pregnancies", methods=["POST"])
    @roles_required("super_admin", "docteur", "infirmier")
    def create_pregnancy():
        data = fast_json()
        if not data.get("patient_id") or not data.get("last_menstrual_period"):
            return jsonify({"error": "Patient et DDR requis"}), 422
        patient_id = to_int(data.get("patient_id"))
        # Verrou Anti-Doublon : une patiente ne peut avoir qu'une seule grossesse active
        existing_active = supabase.table("pregnancies").select("id,created_at").eq("patient_id", patient_id).eq("status", "active").execute().data or []
        if existing_active:
            return jsonify({"error": f"Cette patiente a déjà un dossier de grossesse active (#{existing_active[0]['id']}). Veuillez clôturer le suivi précédent avant d'en créer un nouveau."}), 409

        timeline = pregnancy_timeline(data.get("last_menstrual_period"))
        if not timeline:
            return jsonify({"error": "DDR invalide ou située dans le futur"}), 422
        pregnancy = {
            "patient_id": to_int(data.get("patient_id")),
            "last_menstrual_period": timeline["last_menstrual_period"],
            "expected_delivery_date": timeline["expected_delivery_date"],
            "blood_type": data.get("blood_type", ""),
            "risk_level": data.get("risk_level", "normal"),
            "medical_history": data.get("medical_history", ""),
            "status": data.get("status", "active"),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("pregnancies", pregnancy)
        add_audit("CREATE", "pregnancy", f"Grossesse #{result.data[0]['id']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @maternity.route("/api/maternity/pregnancies/<int:pregnancy_id>", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def get_pregnancy(pregnancy_id: int):
        result = supabase.table("pregnancies").select("*").eq("id", pregnancy_id).execute()
        if not result.data:
            return jsonify({"error": "Grossesse introuvable"}), 404
        pregnancy = result.data[0]
        patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", pregnancy["patient_id"]).execute()
        if patient.data:
            pregnancy["patient_name"] = patient.data[0]["full_name"]
        timeline = pregnancy_timeline(pregnancy.get("last_menstrual_period"))
        if timeline:
            pregnancy.update({key: value for key, value in timeline.items() if key != "last_menstrual_period"})
        return jsonify(pregnancy)

    @maternity.route("/api/maternity/pregnancies/<int:pregnancy_id>", methods=["PUT"])
    @roles_required("super_admin", "docteur", "infirmier")
    def update_pregnancy(pregnancy_id: int):
        data = fast_json()
        existing = supabase.table("pregnancies").select("id,status").eq("id", pregnancy_id).execute().data or []
        if not existing:
            return jsonify({"error": "Grossesse introuvable"}), 404
        if existing[0].get("status") == "completed":
            return jsonify({"error": "Cette grossesse est terminée et définitivement verrouillée"}), 409
        allowed = ["last_menstrual_period", "expected_delivery_date", "blood_type", "risk_level", "medical_history", "status", "notes"]
        updates = {key: value for key, value in data.items() if key in allowed and value is not None}
        if updates.get("status") and updates["status"] != "completed":
            return jsonify({"error": "Une grossesse ne peut être clôturée que par l'enregistrement d'une naissance"}), 422
        if "last_menstrual_period" in updates:
            timeline = pregnancy_timeline(updates["last_menstrual_period"])
            if not timeline:
                return jsonify({"error": "DDR invalide ou située dans le futur"}), 422
            updates["last_menstrual_period"] = timeline["last_menstrual_period"]
            updates["expected_delivery_date"] = timeline["expected_delivery_date"]
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table("pregnancies").update(updates).eq("id", pregnancy_id).execute()
        if not result.data:
            return jsonify({"error": "Grossesse introuvable"}), 404
        add_audit("UPDATE", "pregnancy", f"Grossesse #{pregnancy_id} modifiée", pregnancy_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @maternity.route("/api/maternity/pregnancies/<int:pregnancy_id>/followups", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def get_pregnancy_followups(pregnancy_id: int):
        query = supabase.table("prenatal_consultations").select("*").eq("pregnancy_id", pregnancy_id)
        if g.current_user.get("role") == "docteur":
            query = query.eq("created_by", g.current_user.get("id"))
        result = query.order("visit_date", desc=True).execute()
        return jsonify(result.data or [])

    @maternity.route("/api/maternity/prenatal", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    @cached(60)
    def get_prenatal_consultations():
        patient_id = request.args.get("patient_id")
        pregnancy_id = request.args.get("pregnancy_id")
        query = supabase.table("prenatal_consultations").select("*")
        if g.current_user.get("role") == "docteur":
            query = query.eq("created_by", g.current_user.get("id"))
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        if pregnancy_id:
            query = query.eq("pregnancy_id", to_int(pregnancy_id))
        result = query.order("visit_date", desc=True).execute()
        consultations = result.data or []
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in (patients_result.data or [])}
        for c in consultations:
            c["patient_name"] = patient_map.get(c.get("patient_id"), "Inconnu")
            if not c.get("visit_number") and c.get("observations"):
                match = re.search(r"\[CPN\s*#?(\d+)\]", str(c.get("observations")))
                if match:
                    c["visit_number"] = to_int(match.group(1), 1)
        return jsonify(consultations)

    @maternity.route("/api/maternity/prenatal", methods=["POST"])
    @roles_required("super_admin", "docteur", "infirmier")
    def create_prenatal_consultation():
        data = fast_json()
        if not data.get("patient_id") or not data.get("visit_date"):
            return jsonify({"error": "Patient et date requis"}), 422
        
        patient_id = to_int(data.get("patient_id"))
        visit_num = to_int(data.get("visit_number"), 1)
        if visit_num not in CPN_PERIODS:
            return jsonify({"error": "Numéro de CPN invalide"}), 422
        pregnancy_id = to_int(data.get("pregnancy_id"))
        pregnancy_rows = supabase.table("pregnancies").select("patient_id,last_menstrual_period,status").eq("id", pregnancy_id).execute().data or []
        if not pregnancy_rows or to_int(pregnancy_rows[0].get("patient_id")) != patient_id:
            return jsonify({"error": "Grossesse introuvable pour cette patiente"}), 422
        if pregnancy_rows[0].get("status") != "active":
            return jsonify({"error": "Cette grossesse est terminée : aucun suivi ne peut être ajouté"}), 409
        timeline = pregnancy_timeline(pregnancy_rows[0].get("last_menstrual_period"))
        if not timeline:
            return jsonify({"error": "DDR invalide : CPN impossible à calculer"}), 422
        min_week, _ = CPN_PERIODS[visit_num]
        if timeline["gestational_weeks"] < min_week:
            return jsonify({"error": f"CPN {visit_num} non encore débloquée"}), 422
        duplicate = supabase.table("prenatal_consultations").select("id").eq("pregnancy_id", pregnancy_id).eq("visit_number", visit_num).execute().data or []
        if duplicate:
            return jsonify({"error": f"CPN {visit_num} déjà enregistrée"}), 409
        uid = cpn_uid(data)
        uid_tag = f"[UID:{uid}]"
        same_uid = supabase.table("prenatal_consultations").select("*").ilike("observations", f"%{uid_tag}%").execute().data or []
        if same_uid:
            return jsonify(same_uid[0]), 200
        base_obs = data.get("observations", "")
        tag = f"[CPN #{visit_num}]"
        obs_with_tag = f"{tag} {base_obs} {uid_tag}".strip()
        
        consultation = {
            "patient_id": patient_id,
            "pregnancy_id": pregnancy_id,
            "visit_date": data.get("visit_date"),
            "weight": data.get("weight"),
            "visit_number": visit_num,
            "blood_pressure": data.get("blood_pressure") or (
                f"{data.get('blood_pressure_systolic')}/{data.get('blood_pressure_diastolic')}"
                if data.get("blood_pressure_systolic") is not None and data.get("blood_pressure_diastolic") is not None else ""
            ),
            "blood_pressure_systolic": data.get("blood_pressure_systolic"),
            "blood_pressure_diastolic": data.get("blood_pressure_diastolic"),
            "fetal_heartbeat": data.get("fetal_heartbeat", ""),
            "gestational_weeks": timeline["gestational_weeks"],
            "week_amenorrhea": timeline["gestational_weeks"],
            "uterine_height": data.get("uterine_height"),
            "fetal_movements": data.get("fetal_movements"),
            "presentation": data.get("presentation"),
            "prescribed_exams": data.get("prescribed_exams"),
            "risk_assessment": data.get("risk_assessment"),
            "doctor_id": g.current_user["id"] if g.current_user.get("role") == "docteur" else (data.get("doctor_id") or g.current_user["id"]),
            "doctor_name": g.current_user["name"] if g.current_user.get("role") == "docteur" else (data.get("doctor_name") or g.current_user["name"]),
            "observations": obs_with_tag,
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("prenatal_consultations", consultation)
        res_row = result.data[0] if (result and result.data) else consultation
        if not res_row.get("visit_number"):
            res_row["visit_number"] = visit_num
        # Une CPN alimente le compte patient; la facture imprimable est créée
        # au paiement afin d'éviter une facture impayée doublée.
        cpn_fee = get_tariff_amount("maternite", "CPN", 0)
        if cpn_fee > 0:
            add_patient_account_line(
                patient_id, "maternite", f"Consultation prénatale CPN {visit_num}",
                cpn_fee, "prenatal", res_row.get("id"), 1, cpn_fee
            )
        add_audit("CREATE", "prenatal", f"Consultation prénatale #{res_row.get('id')}", res_row.get("id"))
        invalidate_cache()
        return jsonify(res_row), 201

    @maternity.route("/api/maternity/prenatal/<int:consultation_id>", methods=["GET", "PUT", "DELETE"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def get_prenatal_consultation(consultation_id: int):
        owned = doctor_owns_prenatal(consultation_id)
        if owned is None:
            return jsonify({"error": "Consultation introuvable"}), 404
        if owned is False:
            return jsonify({"error": "Consultation non attribuée à ce médecin"}), 403
        if request.method == "PUT":
            data = fast_json()
            allowed = ("visit_number", "visit_date", "weight", "blood_pressure", "blood_pressure_systolic", "blood_pressure_diastolic", "fetal_heartbeat", "gestational_weeks", "week_amenorrhea", "uterine_height", "fetal_movements", "presentation", "prescribed_exams", "risk_assessment", "observations", "doctor_id", "doctor_name")
            updates = {key: value for key, value in data.items() if key in allowed and value is not None}
            if not updates:
                return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
            updates["updated_at"] = now_iso()
            if g.current_user.get("role") == "docteur":
                updates["doctor_id"] = g.current_user["id"]
                updates["doctor_name"] = g.current_user["name"]
            result = supabase.table("prenatal_consultations").update(updates).eq("id", consultation_id).execute()
            if not result.data:
                return jsonify({"error": "Consultation introuvable"}), 404
            add_audit("UPDATE", "prenatal", f"Consultation prénatale #{consultation_id} modifiée", consultation_id)
            invalidate_cache()
            return jsonify(result.data[0])
        if request.method == "DELETE":
            supabase.table("prenatal_consultations").delete().eq("id", consultation_id).execute()
            add_audit("DELETE", "prenatal", f"Consultation prénatale #{consultation_id} supprimée", consultation_id)
            invalidate_cache()
            return jsonify({"message": "Consultation prénatale supprimée"})
        return jsonify(owned)

    @maternity.route("/api/maternity/deliveries", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    @cached(60)
    def get_deliveries():
        patient_id = request.args.get("patient_id")
        query = supabase.table("deliveries").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        result = query.order("delivery_date", desc=True).execute()
        deliveries = result.data
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        for d in deliveries:
            d["patient_name"] = patient_map.get(d.get("patient_id"), "Inconnu")
            if d.get("babies"):
                d["babies"] = json.loads(d["babies"]) if isinstance(d["babies"], str) else d["babies"]
        return jsonify(deliveries)

    @maternity.route("/api/maternity/deliveries", methods=["POST"])
    @roles_required("super_admin", "docteur", "infirmier")
    def create_delivery():
        data = fast_json()
        if not data.get("patient_id"):
            return jsonify({"error": "Patiente requise"}), 422
        
        patient_id = to_int(data.get("patient_id"))
        pregnancy_id = data.get("pregnancy_id")
        delivery_type = data.get("delivery_type", "vaginal")
        
        pregnancy = None
        if pregnancy_id:
            pregnancy_id = to_int(pregnancy_id)
            existing_del = supabase.table("deliveries").select("id").eq("pregnancy_id", pregnancy_id).execute().data or []
            if existing_del:
                return jsonify({"error": "L'accouchement a déjà été enregistré pour cette grossesse (#" + str(existing_del[0]['id']) + ")"}), 409
            pregnancies = supabase.table("pregnancies").select("id,patient_id,status").eq("id", pregnancy_id).execute().data or []
            if not pregnancies or to_int(pregnancies[0].get("patient_id")) != patient_id:
                return jsonify({"error": "Grossesse introuvable pour cette patiente"}), 422
            if pregnancies[0].get("status") != "active":
                return jsonify({"error": "Cette grossesse est déjà terminée"}), 409
            pregnancy = pregnancies[0]

        mothers = supabase.table(TABLES["patients"]).select("id,full_name,assigned_doctor_id").eq("id", patient_id).execute().data or []
        if not mothers:
            return jsonify({"error": "Patiente introuvable"}), 404
        mother = mothers[0]
        # Date et heure de naissance : saisies par le serveur au moment exact de la validation.
        birth_at = now_iso()
        birth_date = birth_at[:10]
        
        delivery = {
            "patient_id": patient_id,
            "pregnancy_id": pregnancy_id,
            "delivery_date": birth_date,
            "delivery_time": birth_at,
            "delivery_type": delivery_type,
            "baby_count": data.get("baby_count", 1),
            "babies": json.dumps([]),
            "baby_weight": data.get("baby_weight"),
            "observations": data.get("observations", ""),
            "status": "completed",
            "delivered_by": g.current_user["id"],
            "delivered_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("deliveries", delivery)
        created_delivery = result.data[0] if result.data else delivery
        # Débit unique de l'accouchement selon la grille active.
        delivery_label = "Césarienne" if str(delivery_type).lower() in ("cesarienne", "césarienne", "cesarean") else "Accouchement eutocique"
        delivery_fee = get_tariff_amount("maternite", delivery_label, 0)
        if delivery_fee > 0 and created_delivery.get("id"):
            add_patient_account_line(
                patient_id, "maternite", delivery_label, delivery_fee,
                "delivery", created_delivery.get("id"), 1, delivery_fee
            )
        baby = (data.get("babies") or [{}])[0] or {}
        baby_name = f"Enfant de {mother.get('full_name') or 'la mère'}"
        baby_patient = {
            "full_name": baby_name, "date_of_birth": birth_date,
            "gender": baby.get("gender", data.get("baby_gender", "M")), "status": "active",
            "mother_id": patient_id, "pregnancy_id": pregnancy_id, "delivery_id": created_delivery.get("id"),
            "is_newborn": True, "birth_time": birth_at,
            "birth_weight": baby.get("weight", data.get("baby_weight")), "delivery_mode": delivery_type,
            "apgar": baby.get("apgar", data.get("apgar", "")),
            "birth_observations": data.get("observations", ""), "newborn_name_locked": False,
            "created_by": g.current_user["id"], "created_by_name": g.current_user["name"],
            "created_at": birth_at, "updated_at": birth_at,
        }
        baby_result = compatible_insert(TABLES["patients"], baby_patient)
        newborn = baby_result.data[0] if baby_result.data else baby_patient
        if not newborn.get("id"):
            return jsonify({"error": "Le dossier du nouveau-né n'a pas pu être créé"}), 500
        try:
            hid = hospital_patient_id(newborn["id"])
            compatible_update(TABLES["patients"], {"hospital_id": hid, "updated_at": now_iso()}, "id", newborn["id"])
            newborn["hospital_id"] = hid
        except Exception:
            pass

        if pregnancy:
            compatible_update("pregnancies", {"status": "completed", "updated_at": now_iso()}, "id", pregnancy_id)

        # Demande normale pour la maman : aucune chambre ni lit n'est attribué ici.
        active_hosp = supabase.table("hospitalizations").select("id").eq("patient_id", patient_id).in_("status", ["pending", "admitted", "hospitalized", "active"]).execute().data or []
        if not active_hosp:
            compatible_insert("hospitalizations", {
                "patient_id": patient_id, "admission_date": None, "status": "pending",
                "reason": f"Surveillance post-accouchement — naissance #{created_delivery.get('id')}",
                "room": "", "bed": "", "bed_id": None, "room_id": None,
                "doctor_id": mother.get("assigned_doctor_id"), "doctor_name": "",
                "daily_rate": get_tariff_amount("hospitalisation", "Hospitalisation", 0),
                "created_by": g.current_user["id"], "created_by_name": g.current_user["name"],
                "created_at": now_iso(), "updated_at": now_iso(),
            })

        created_delivery.update({"newborn": newborn, "delivery_date": birth_date, "delivery_time": birth_at})
        
        add_audit("CREATE", "delivery", f"Accouchement #{created_delivery.get('id')}", created_delivery.get("id"))
        invalidate_cache()
        return jsonify(created_delivery), 201

    @maternity.route("/api/maternity/newborns", methods=["GET"])
    @roles_required("super_admin", "infirmier")
    def get_newborns():
        rows = supabase.table(TABLES["patients"]).select("*").eq("is_newborn", True).order("created_at", desc=True).execute().data or []
        mothers = get_patient_map()
        for baby in rows:
            baby["mother_name"] = mothers.get(baby.get("mother_id"), "Mère inconnue")
        return jsonify(rows)

    @maternity.route("/api/maternity/newborns/<int:baby_id>", methods=["GET"])
    @roles_required("super_admin", "infirmier")
    def get_newborn(baby_id):
        rows = supabase.table(TABLES["patients"]).select("*").eq("id", baby_id).eq("is_newborn", True).execute().data or []
        if not rows:
            return jsonify({"error": "Nouveau-né introuvable"}), 404
        baby = rows[0]
        baby["mother_name"] = get_patient_map().get(baby.get("mother_id"), "Mère inconnue")
        baby["vaccines"] = newborn_vaccine_plan(baby_id)
        return jsonify(baby)

    @maternity.route("/api/maternity/newborns/<int:baby_id>/rename", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def rename_newborn_once(baby_id):
        new_name = str(fast_json().get("full_name") or "").strip()
        if not new_name:
            return jsonify({"error": "Nom requis"}), 422
        rows = supabase.table(TABLES["patients"]).select("id,is_newborn,newborn_name_locked").eq("id", baby_id).execute().data or []
        if not rows or not rows[0].get("is_newborn"):
            return jsonify({"error": "Nouveau-né introuvable"}), 404
        if rows[0].get("newborn_name_locked"):
            return jsonify({"error": "Le nom du bébé a déjà été confirmé et ne peut plus être modifié"}), 409
        result = supabase.table(TABLES["patients"]).update({
            "full_name": new_name, "newborn_name_locked": True, "updated_at": now_iso()
        }).eq("id", baby_id).eq("newborn_name_locked", False).execute()
        if not result.data:
            return jsonify({"error": "Le nom du bébé a déjà été confirmé et ne peut plus être modifié"}), 409
        add_audit("UPDATE", "newborn", f"Nom définitif du nouveau-né #{baby_id}", baby_id)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else {"id": baby_id, "full_name": new_name, "newborn_name_locked": True})

    @maternity.route("/api/maternity/newborns/<int:baby_id>/vaccines/<vaccine_code>/administer", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def administer_newborn_vaccine(baby_id, vaccine_code):
        catalog = get_vaccine_catalog_from_store()
        catalog_item = next((v for v in catalog if str(v.get("code")).upper() == str(vaccine_code).upper()), None)
        body = fast_json() or {}
        vaccine_name = body.get("vaccine_name") or (catalog_item.get("name") if catalog_item else None) or vaccine_code
        
        baby = supabase.table(TABLES["patients"]).select("id,is_newborn,full_name").eq("id", baby_id).execute().data or []
        if not baby:
            return jsonify({"error": "Patient introuvable"}), 404
        existing = supabase.table("newborn_vaccinations").select("id").eq("baby_patient_id", baby_id).eq("vaccine_code", vaccine_code).execute().data or []
        if existing:
            return jsonify({"error": "Ce vaccin a deja ete effectue et est verrouille"}), 409
        done_at = now_iso()
        result = compatible_insert("newborn_vaccinations", {
            "baby_patient_id": baby_id, "vaccine_code": vaccine_code, "vaccine_name": vaccine_name, "status": "completed",
            "administered_at": done_at, "administered_by": g.current_user["id"], "administered_by_name": g.current_user["name"],
            "created_at": done_at, "updated_at": done_at,
        })
        baby_name = baby[0].get("full_name") or f"Patient #{baby_id}"
        add_audit("CREATE", "newborn_vaccination", f"{vaccine_name} a {baby_name}", baby_id)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else {"vaccine_code": vaccine_code, "vaccine_name": vaccine_name, "status": "completed"}), 201

    @maternity.route("/api/maternity/deliveries/<int:delivery_id>", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def get_delivery(delivery_id: int):
        result = supabase.table("deliveries").select("*").eq("id", delivery_id).execute()
        if not result.data:
            return jsonify({"error": "Accouchement introuvable"}), 404
        delivery = result.data[0]
        patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", delivery["patient_id"]).execute()
        if patient.data:
            delivery["patient_name"] = patient.data[0]["full_name"]
        if delivery.get("babies"):
            delivery["babies"] = json.loads(delivery["babies"]) if isinstance(delivery["babies"], str) else delivery["babies"]
        return jsonify(delivery)

    @maternity.route("/api/maternity/rooms", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    @cached(60)
    def get_maternity_rooms():
        result = supabase.table("maternity_rooms").select("*").order("room_number").execute()
        rooms = result.data
        for room in rooms:
            if room.get("patient_id") and room.get("status") == "occupied":
                patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", room["patient_id"]).execute()
                if patient.data:
                    room["patient_name"] = patient.data[0]["full_name"]
        return jsonify(rooms)

    @maternity.route("/api/maternity/rooms", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def create_maternity_room():
        data = fast_json()
        if not data.get("room_number"):
            return jsonify({"error": "Numéro de lit requis"}), 422
        room = {
            "room_number": data.get("room_number"),
            "type": data.get("type", "Standard"),
            "status": "available",
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("maternity_rooms", room)
        add_audit("CREATE", "maternity_room", f"Lit #{result.data[0]['room_number']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @maternity.route("/api/maternity/rooms/admit", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def admit_to_maternity():
        data = fast_json()
        if not data.get("room_id") or not data.get("patient_id"):
            return jsonify({"error": "Lit et patient requis"}), 422
        room = supabase.table("maternity_rooms").select("*").eq("id", data["room_id"]).execute()
        if not room.data:
            return jsonify({"error": "Lit introuvable"}), 404
        if room.data[0]["status"] != "available":
            return jsonify({"error": "Lit déjà occupé"}), 422
        updates = {
            "status": "occupied",
            "patient_id": to_int(data.get("patient_id")),
            "admission_date": now_iso(),
            "admission_reason": data.get("reason", ""),
            "updated_at": now_iso()
        }
        result = supabase.table("maternity_rooms").update(updates).eq("id", data["room_id"]).execute()
        add_audit("UPDATE", "maternity_room", f"Admission patient #{data['patient_id']} au lit #{room.data[0]['room_number']}", data["room_id"])
        invalidate_cache()
        return jsonify(result.data[0])

    @maternity.route("/api/maternity/rooms/<int:room_id>/discharge", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def discharge_from_maternity(room_id: int):
        updates = {
            "status": "available",
            "patient_id": None,
            "discharge_date": now_iso(),
            "admission_reason": None,
            "updated_at": now_iso()
        }
        result = supabase.table("maternity_rooms").update(updates).eq("id", room_id).execute()
        if not result.data:
            return jsonify({"error": "Lit introuvable"}), 404
        add_audit("UPDATE", "maternity_room", f"Libération du lit #{result.data[0]['room_number']}", room_id)
        invalidate_cache()
        return jsonify(result.data[0])

    # ==================== PÉDIATRIE ROUTES ====================


    @maternity.route("/api/maternity/vaccine-catalog", methods=["GET", "POST"])
    @roles_required("super_admin", "infirmier", "docteur")
    def maternity_vaccine_catalog():
        if request.method == "GET":
            # Aucun vaccin en dur : uniquement ceux crees et geres par les infirmiers
            return jsonify(get_vaccine_catalog_from_store())
        
        # POST - Creer un vaccin dynamiquement
        data = fast_json()
        code = str(data.get("code") or "").strip().upper()
        name = str(data.get("name") or "").strip()
        target_age = to_float(data.get("target_age_months"), 0)
        label = str(data.get("target_age_label") or f"{target_age} mois").strip()
        if not code or not name:
            return jsonify({"error": "Code et nom de vaccin requis"}), 422
        
        user_name = g.current_user.get("name") if hasattr(g, "current_user") and g.current_user else "Infirmier"
        new_item = {
            "code": code,
            "name": name,
            "target_age_months": target_age,
            "target_age_label": label,
            "created_by": user_name,
            "created_at": now_iso()
        }
        catalog = get_vaccine_catalog_from_store()
        catalog = [c for c in catalog if str(c.get("code")).upper() != code]
        catalog.append(new_item)
        save_vaccine_catalog_to_store(catalog)
        try:
            compatible_insert("vaccine_catalog", new_item)
        except Exception:
            pass
        add_audit("CREATE", "vaccine_catalog", f"Vaccin {name} ({code}) cree par {user_name}")
        invalidate_cache()
        return jsonify(new_item), 201

    @maternity.route("/api/maternity/vaccine-catalog/<vaccine_code>", methods=["DELETE"])
    @roles_required("super_admin", "infirmier")
    def delete_maternity_vaccine_catalog(vaccine_code):
        code = str(vaccine_code).strip().upper()
        catalog = get_vaccine_catalog_from_store()
        initial_len = len(catalog)
        catalog = [c for c in catalog if str(c.get("code")).upper() != code]
        if len(catalog) < initial_len:
            save_vaccine_catalog_to_store(catalog)
            try:
                supabase.table("vaccine_catalog").delete().eq("code", code).execute()
            except Exception:
                pass
            add_audit("DELETE", "vaccine_catalog", f"Vaccin {code} supprime")
            invalidate_cache()
            return jsonify({"success": True, "message": f"Vaccin {code} supprime"})
        return jsonify({"error": "Vaccin introuvable"}), 404

    @maternity.route("/api/maternity/children", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def create_child_manual():
        data = fast_json()
        full_name = str(data.get("full_name") or "").strip()
        dob = optional_date(data.get("date_of_birth"))
        if not full_name or not dob:
            return jsonify({"error": "Nom et date de naissance requis"}), 422
        
        mother_id = to_int(data.get("mother_id"))
        child_patient = {
            "full_name": full_name,
            "date_of_birth": dob,
            "gender": data.get("gender", "M"),
            "blood_type": data.get("blood_type", ""),
            "status": "active",
            "mother_id": mother_id,
            "is_newborn": True,
            "birth_weight": to_float(data.get("birth_weight")),
            "delivery_mode": data.get("delivery_mode", "vaginal"),
            "birth_observations": data.get("birth_observations", ""),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        res = compatible_insert(TABLES["patients"], child_patient)
        created = res.data[0] if res.data else child_patient
        if created.get("id"):
            hid = hospital_patient_id(created["id"])
            try:
                supabase.table(TABLES["patients"]).update({"hospital_id": hid}).eq("id", created["id"]).execute()
                created["hospital_id"] = hid
            except Exception:
                pass
        add_audit("CREATE", "child_patient", f"Enfant #{created.get('id')}: {full_name}", created.get("id"))
        invalidate_cache()
        return jsonify(created), 201

    app.register_blueprint(maternity)
