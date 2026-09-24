"""Module maternité I-HUB : grossesses, prénatal, accouchements et chambres."""
from datetime import date, datetime, timedelta
from flask import Blueprint
import re
import uuid


def register_maternity_routes(app, *, runtime):
    globals().update(runtime)
    maternity = Blueprint("maternity", __name__)

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
        if g.current_user.get("role") == "docteur" and str(consultation.get("doctor_id") or "") != str(g.current_user.get("id") or ""):
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
        allowed = ["last_menstrual_period", "expected_delivery_date", "blood_type", "risk_level", "medical_history", "status", "notes"]
        updates = {key: value for key, value in data.items() if key in allowed and value is not None}
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
            query = query.eq("doctor_id", g.current_user.get("id"))
        result = query.order("visit_date", desc=True).execute()
        return jsonify(result.data)

    @maternity.route("/api/maternity/prenatal", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    @cached(60)
    def get_prenatal_consultations():
        patient_id = request.args.get("patient_id")
        pregnancy_id = request.args.get("pregnancy_id")
        query = supabase.table("prenatal_consultations").select("*")
        if g.current_user.get("role") == "docteur":
            query = query.eq("doctor_id", g.current_user.get("id"))
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        if pregnancy_id:
            query = query.eq("pregnancy_id", to_int(pregnancy_id))
        result = query.order("visit_date", desc=True).execute()
        consultations = result.data
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
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
        pregnancy_rows = supabase.table("pregnancies").select("patient_id,last_menstrual_period").eq("id", pregnancy_id).execute().data or []
        if not pregnancy_rows or to_int(pregnancy_rows[0].get("patient_id")) != patient_id:
            return jsonify({"error": "Grossesse introuvable pour cette patiente"}), 422
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
        facture_auto(patient_id, "CPN", 1, "prenatal", res_row.get("id"))
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
        if not data.get("patient_id") or not data.get("delivery_date"):
            return jsonify({"error": "Patient et date requis"}), 422
        
        patient_id = to_int(data.get("patient_id"))
        pregnancy_id = data.get("pregnancy_id")
        delivery_type = data.get("delivery_type", "vaginal")
        
        if pregnancy_id:
            existing_del = supabase.table("deliveries").select("id").eq("pregnancy_id", to_int(pregnancy_id)).execute().data or []
            if existing_del:
                return jsonify({"error": "L'accouchement a déjà été enregistré pour cette grossesse (#" + str(existing_del[0]['id']) + ")"}), 409
            supabase.table("pregnancies").update({"status": "completed", "updated_at": now_iso()}).eq("id", pregnancy_id).execute()
        
        delivery = {
            "patient_id": patient_id,
            "pregnancy_id": pregnancy_id,
            "delivery_date": data.get("delivery_date"),
            "delivery_type": delivery_type,
            "baby_count": data.get("baby_count", 1),
            "babies": json.dumps(data.get("babies", [])),
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
        tarif_code = "ACC_VAG" if delivery_type == "vaginal" else "ACC_CES"
        facture_auto(patient_id, tarif_code, 1, "delivery", created_delivery.get("id"))
        
        babies = data.get("babies", [])
        for idx, baby in enumerate(babies):
            child_data = {
                "full_name": f"Bébé de la patiente #{patient_id}",
                "date_of_birth": data.get("delivery_date"),
                "gender": baby.get("gender", "M"),
                "parent_id": patient_id,
                "blood_type": baby.get("blood_type", ""),
                "birth_weight": baby.get("weight"),
                "birth_height": baby.get("height"),
                "created_at": now_iso(),
                "updated_at": now_iso()
            }
            try:
                compatible_insert("children", child_data)
            except Exception as exc:
                print(f" Fiche enfant non créée après accouchement: {exc}")
        
        add_audit("CREATE", "delivery", f"Accouchement #{created_delivery.get('id')}", created_delivery.get("id"))
        invalidate_cache()
        return jsonify(created_delivery), 201

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

    app.register_blueprint(maternity)
