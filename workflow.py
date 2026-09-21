"""Module complet du workflow clinique I-HUB.

Routes migrées depuis app.py : file, SV, dispatch, boxes, consultations,
soins, hospitalisation, suivis, administrations et tarifs workflow.
"""
from datetime import datetime, timezone
from flask import Blueprint
import re
import uuid


def register_workflow_routes(app, *, runtime):
    # Les helpers restent fournis par le noyau app.py durant la migration afin
    # d'éviter les imports circulaires et de préserver les routes existantes.
    globals().update(runtime)
    workflow = Blueprint("workflow", __name__)

    @workflow.route("/api/workflow/doctors", methods=["GET"])
    @roles_required("super_admin", "infirmier", "reception", "docteur")
    @cached(timeout=120)
    def get_workflow_doctors():
        users = supabase.table(TABLES["users"]).select("id,name,email,role").eq("role", "docteur").execute()
        return jsonify(users.data or [])

    # ==================== FILE D'ATTENTE CENTRALE ====================
    # Cette table est la source de vérité commune à la réception, à l'infirmier
    # et au médecin. Les interfaces ne doivent jamais recréer une file locale.
    ACTIVE_QUEUE_STATUSES = ("waiting", "with_nurse", "vitals_done", "assigned", "in_consultation")
    QUEUE_STATUSES = ACTIVE_QUEUE_STATUSES + ("completed", "cancelled")

    def enrich_queue_rows(rows):
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), row.get("patient_name") or "Inconnu")
        return rows

    def queue_uid(data):
        supplied = str(data.get("uid") or "").strip().upper()
        if supplied and re.fullmatch(r"[A-Z0-9-]{8,80}", supplied):
            return supplied
        return f"FILE-{now_iso()[:10].replace('-', '')}-{uuid.uuid4().hex[:8].upper()}"

    @workflow.route("/api/workflow/queue", methods=["GET", "POST"])
    @roles_required("super_admin", "reception", "infirmier", "docteur")
    def workflow_queue():
        role = g.current_user.get("role")
        if request.method == "GET":
            # Après minuit, les patients non clôturés des jours précédents passent à 'completed' (sortie)
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            try:
                supabase.table("patient_queue").update({
                    "status": "completed",
                    "updated_at": now_iso()
                }).in_("status", list(ACTIVE_QUEUE_STATUSES)).lt("created_at", today_start).execute()
            except Exception:
                pass

            # ----- Construction de la requête de base -----
            status_filter = request.args.get("status")
            patient_id = to_int(request.args.get("patient_id"))
            include_history = str(request.args.get("include_history", "")).lower() in ("1", "true", "yes")
            query = supabase.table("patient_queue").select("*")

            # ----- Filtrage par patient_id (facultatif) -----
            if patient_id:
                query = query.eq("patient_id", patient_id)

            # ----- Filtrage par statut (facultatif) -----
            if status_filter:
                requested = [s.strip() for s in status_filter.split(",") if s.strip() in QUEUE_STATUSES]
                if not requested:
                    return jsonify({"error": "Statut de file invalide"}), 422
                query = query.in_("status", requested)
            else:
                # Aucun filtre explicite de statut → appliquer le filtre par rôle
                if role == "reception":
                    query = query.in_("status", ["waiting"])
                elif role == "docteur":
                    query = query.in_("status", ["assigned", "in_consultation"])
                elif role == "infirmier":
                    query = query.in_("status", ACTIVE_QUEUE_STATUSES)
                elif not include_history:
                    query = query.in_("status", ACTIVE_QUEUE_STATUSES)

            # ----- Filtrage du médecin (toujours appliqué quand le rôle est docteur) -----
            if role == "docteur":
                doctor_id = str(g.current_user.get("id") or "").strip()
                doctor_name = str(g.current_user.get("name") or "").strip().casefold()
                rows = query.order("updated_at", desc=True).execute().data or []
                filtered_rows = []
                for r in rows:
                    r_doc_id = str(r.get("assigned_doctor_id") or "").strip()
                    r_doc_name = str(r.get("assigned_doctor_name") or "").strip().casefold()
                    if doctor_id and r_doc_id == doctor_id:
                        filtered_rows.append(r)
                    elif doctor_name and r_doc_name and r_doc_name == doctor_name:
                        filtered_rows.append(r)
                rows = filtered_rows
            else:
                rows = query.order("updated_at", desc=True).execute().data or []

            return jsonify(enrich_queue_rows(rows))

        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        patient = supabase.table(TABLES["patients"]).select("id,full_name").eq("id", patient_id).execute().data or []
        if not patient:
            return jsonify({"error": "Patient introuvable"}), 404

        uid = queue_uid(data)
        uid_tag = f"[UID:{uid}]"
        same_uid = supabase.table("patient_queue").select("*").ilike("notes", f"%{uid_tag}%").execute().data or []
        if same_uid:
            return jsonify({"message": "Action déjà enregistrée", "created": False, "patient": enrich_queue_rows(same_uid)[0]}), 200

        # Idempotence : un patient ne peut avoir qu'une entrée active par défaut.
        existing = supabase.table("patient_queue").select("*").eq("patient_id", patient_id).in_("status", ACTIVE_QUEUE_STATUSES).order("updated_at", desc=True).limit(1).execute().data or []
        if existing:
            row = enrich_queue_rows(existing)[0]
            return jsonify({"message": "Patient déjà présent dans la file", "created": False, "patient": row}), 200

        last = supabase.table("patient_queue").select("arrival_order").order("arrival_order", desc=True).limit(1).execute().data or []
        arrival_order = to_int(last[0].get("arrival_order"), 0) + 1 if last else 1
        priority = normalize_status(data.get("priority", "normal"), ["normal", "urgent"], "normal")
        payload = {
            "patient_id": patient_id,
            "status": "waiting",
            "priority": priority,
            "notes": f"{str(data.get('notes') or '').strip()} {uid_tag}".strip(),
            "appointment_id": data.get("appointment_id"),
            "appointment_time": data.get("appointment_time"),
            "arrival_order": arrival_order,
            "arrival_time": now_iso(),
            "created_by": g.current_user.get("id"),
            "created_by_name": g.current_user.get("name", ""),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        result = compatible_insert("patient_queue", payload)
        row = (result.data or [payload])[0]
        row["patient_name"] = patient[0].get("full_name", "Inconnu")
        add_audit("CREATE", "patient_queue", f"Patient #{patient_id} ajouté à la file", patient_id)
        invalidate_cache()
        return jsonify({"message": "Patient ajouté à la file", "created": True, "patient": row}), 201

    @workflow.patch("/api/workflow/queue/<int:patient_id>")
    @roles_required("super_admin", "reception", "infirmier", "docteur")
    def update_workflow_queue(patient_id: int):
        data = fast_json()
        new_status = data.get("status")
        if new_status not in QUEUE_STATUSES:
            return jsonify({"error": "Statut de file invalide"}), 422
        role = g.current_user.get("role")
        current = supabase.table("patient_queue").select("*").eq("patient_id", patient_id).order("updated_at", desc=True).limit(1).execute().data or []
        if not current:
            return jsonify({"error": "Patient absent de la file"}), 404
        row = current[0]
        if role == "docteur" and str(row.get("assigned_doctor_id") or "").strip() != str(g.current_user.get("id") or "").strip():
            return jsonify({"error": "Patient non assigné à ce médecin"}), 403
        current_status = row.get("status")
        allowed_by_role = {
            "reception": {"cancelled"},
            "infirmier": {"with_nurse", "vitals_done"},
            "docteur": {"in_consultation", "completed"},
            "super_admin": set(QUEUE_STATUSES),
        }
        if new_status not in allowed_by_role.get(role, set()):
            return jsonify({"error": "Transition non autorisée pour ce rôle"}), 403
        allowed_transitions = {
            "waiting": {"with_nurse", "vitals_done", "cancelled"},
            "with_nurse": {"vitals_done", "cancelled"},
            "vitals_done": {"assigned", "cancelled"},
            "assigned": {"in_consultation", "completed"},
            "in_consultation": {"completed"},
            "completed": set(),
            "cancelled": set(),
        }
        if new_status == current_status:
            return jsonify(enrich_queue_rows([row])[0]), 200
        if new_status not in allowed_transitions.get(current_status, set()):
            return jsonify({"error": f"Transition invalide : {current_status} → {new_status}"}), 409
        updates = {"status": new_status, "updated_at": now_iso()}
        result = supabase.table("patient_queue").update(updates).eq("id", row.get("id")).eq("status", current_status).execute()
        if not result.data:
            return jsonify({"error": "La file a été modifiée par un autre utilisateur"}), 409
        updated = enrich_queue_rows(result.data)[0]
        add_audit("UPDATE", "patient_queue", f"Patient #{patient_id}: {current_status} → {new_status}", patient_id)
        invalidate_cache()
        return jsonify(updated)

    @workflow.route("/api/workflow/vitals", methods=["GET", "POST"])
    @roles_required(*ROLES["staff"])
    def workflow_vitals():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("vital_signs").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("created_at", desc=True).execute().data or []
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        if g.current_user.get("role") not in ("super_admin", "infirmier"):
            return jsonify({"error": "Signes vitaux reserves a l'infirmier"}), 403
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        
        is_pregnant = data.get("is_pregnant", False)
        
        payload = {
            "patient_id": patient_id,
            "temperature": data.get("temperature"),
            "blood_pressure": data.get("blood_pressure") or (
                f"{data.get('blood_pressure_sys')}/{data.get('blood_pressure_dia')}"
                if data.get("blood_pressure_sys") is not None and data.get("blood_pressure_dia") is not None else ""
            ),
            "blood_pressure_sys": data.get("blood_pressure_sys"),
            "blood_pressure_dia": data.get("blood_pressure_dia"),
            "weight": data.get("weight"),
            "height": data.get("height"),
            "heart_rate": data.get("heart_rate"),
            "oxygen_saturation": data.get("oxygen_saturation"),
            "notes": data.get("notes", ""),
            "author": data.get("author", g.current_user.get("name") or g.current_user.get("email")),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("vital_signs", payload)
        
        patient_updates = {}
        if data.get("blood_type"):
            patient_updates["blood_type"] = data.get("blood_type")
        if data.get("allergies"):
            patient_updates["allergies"] = data.get("allergies")

        if is_pregnant:
            patient = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
            if patient.data and is_female(patient.data[0]):
                patient_updates["is_pregnant"] = True
                existing = supabase.table("pregnancies").select("*").eq("patient_id", patient_id).eq("status", "active").execute()
                if not existing.data:
                    compatible_insert("pregnancies", {
                        "patient_id": patient_id,
                        "last_menstrual_period": None,
                        "expected_delivery_date": None,
                        "risk_level": "normal",
                        "medical_history": "Grossesse signalée par l'infirmier lors des signes vitaux",
                        "status": "active",
                        "created_by": g.current_user["id"],
                        "created_by_name": g.current_user["name"],
                        "created_at": now_iso(),
                        "updated_at": now_iso()
                    })

        if patient_updates:
            patient_updates["updated_at"] = now_iso()
            supabase.table(TABLES["patients"]).update(patient_updates).eq("id", patient_id).execute()
        
        # Un patient déjà pris en charge par l'infirmier doit rejoindre le même
        # workflow que les autres dès que ses signes vitaux sont enregistrés.
        # Sans `with_nurse` ici, il restait invisible pour le dispatch puis pour
        # la salle d'attente du médecin, qui ne lit que les patients `assigned`.
        supabase.table("patient_queue").update({"status": "vitals_done", "updated_at": now_iso()}).eq("patient_id", patient_id).in_("status", ["waiting", "with_nurse", "vitals_done"]).execute()
        add_audit("CREATE", "vital_signs", f"Signes vitaux patient #{patient_id}", patient_id)
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @workflow.route("/api/workflow/pregnancy-flag", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def flag_pregnancy_from_nurse():
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        patient = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
        if not patient.data:
            return jsonify({"error": "Patient introuvable"}), 404
        if not is_female(patient.data[0]):
            return jsonify({"error": "Signalement grossesse reserve aux patientes"}), 422
        existing = supabase.table("pregnancies").select("*").eq("patient_id", patient_id).eq("status", "active").execute()
        if existing.data:
            return jsonify(existing.data[0]), 200
        lmp = optional_date(data.get("last_menstrual_period")) or datetime.now(timezone.utc).date().isoformat()
        payload = {
            "patient_id": patient_id,
            "last_menstrual_period": lmp,
            "expected_delivery_date": data.get("expected_delivery_date"),
            "risk_level": data.get("risk_level", "normal"),
            "medical_history": data.get("notes", "Grossesse signalee par l'infirmier avant dispatch."),
            "status": "active",
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("pregnancies", payload)
        add_audit("CREATE", "pregnancy", f"Grossesse signalee patient #{patient_id}", patient_id)
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @workflow.post("/api/workflow/dispatch")
    @roles_required("super_admin", "infirmier")
    def dispatch_patient():
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        doctor_id = to_int(data.get("doctor_id"))
        box_id = data.get("box_id")
        if not patient_id or not doctor_id:
            return jsonify({"error": "Patient et medecin requis"}), 422
        latest_vitals = supabase.table("vital_signs").select("id").eq("patient_id", patient_id).limit(1).execute()
        if not latest_vitals.data:
            return jsonify({"error": "Les signes vitaux doivent etre preleves avant le dispatch"}), 422
        doctors = get_user_map()
        doctor = doctors.get(doctor_id)
        if not doctor or doctor.get("role") != "docteur":
            return jsonify({"error": "Médecin introuvable ou invalide"}), 422
        current_queue = supabase.table("patient_queue").select("id,status,assigned_doctor_id").eq("patient_id", patient_id).order("updated_at", desc=True).limit(1).execute().data or []
        if not current_queue or current_queue[0].get("status") not in ("vitals_done", "with_nurse", "assigned"):
            return jsonify({"error": "Le patient doit être dans la file avec des signes vitaux avant le dispatch"}), 409
        previous_doctor_id = data.get("previous_doctor_id")
        if not previous_doctor_id and current_queue:
            previous_doctor_id = current_queue[0].get("assigned_doctor_id")
        
        # Un dispatch vers un box doit toujours correspondre au médecin choisi :
        # cette vérification doit vivre dans l'API, pas uniquement dans l'interface.
        if box_id:
            box_check = supabase.table("medical_boxes").select("status,doctor_id").eq("id", to_int(box_id)).execute()
            if not box_check.data:
                return jsonify({"error": "Box introuvable"}), 404
            box = box_check.data[0]
            if to_int(box.get("doctor_id")) != doctor_id:
                return jsonify({"error": "Ce box n'est pas attribué au médecin sélectionné"}), 422
            if box.get("status") != "free":
                return jsonify({"error": "Ce box est déjà occupé"}), 422
            supabase.table("medical_boxes").update({
                "status": "occupied",
                "patient_id": patient_id,
                "patient_name": get_patient_map().get(patient_id, "Patient"),
                "occupied_at": now_iso(),
                "updated_at": now_iso()
            }).eq("id", to_int(box_id)).execute()
        
        payload = {
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "doctor_name": doctor.get("name") if doctor else data.get("doctor_name", ""),
            "previous_doctor_id": previous_doctor_id,
            "reason": data.get("reason", ""),
            "box": data.get("box", ""),
            "box_id": box_id,
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        }
        result = compatible_insert("patient_dispatches", payload)
        # `assigned` est le statut contractuel de la salle d'attente médecin.
        # L'ancien `dispatched` n'était lu par aucune file médecin, ce qui faisait
        # disparaître le patient après dispatch au lieu de l'y faire apparaître.
        queue_update = supabase.table("patient_queue").update({
            "status": "assigned",
            "assigned_doctor_id": doctor_id,
            "assigned_doctor_name": payload["doctor_name"],
            "updated_at": now_iso()
        }).eq("patient_id", patient_id).in_("status", ["vitals_done", "with_nurse", "assigned"]).execute()
        if not queue_update.data:
            # Éviter de laisser un box occupé ou un dispatch orphelin si la ligne de file a changé entre la vérification et la mise à jour.
            if box_id:
                supabase.table("medical_boxes").update({
                    "status": "free",
                    "patient_id": None,
                    "patient_name": None,
                    "occupied_at": None,
                    "updated_at": now_iso()
                }).eq("id", to_int(box_id)).eq("patient_id", patient_id).execute()
            if result.data:
                supabase.table("patient_dispatches").delete().eq("id", result.data[0].get("id")).execute()
            return jsonify({"error": "Patient introuvable dans un état dispatchable"}), 409
        # Mettre à jour le statut du patient dans la table principale
        supabase.table(TABLES["patients"]).update({"status": "assigned", "assigned_doctor_id": doctor_id, "updated_at": now_iso()}).eq("id", patient_id).execute()
        add_audit("CREATE", "dispatch", f"Patient #{patient_id} assigne a {payload['doctor_name']}", patient_id)
        # Compter les patients encore en attente pour mettre à jour le badge
        waiting = supabase.table("patient_queue").select("id", count="exact").in_("status", ["waiting", "vitals_done", "with_nurse"]).execute()
        waiting_count = waiting.count if waiting else 0
        # Invalidation ciblée du cache de la file d’attente
        cache.delete('patient_queue')
        return jsonify({"message": "Dispatch ok", "waiting_count": waiting_count, "dispatch": result.data[0]}), 201

    @workflow.route("/api/workflow/dispatches", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_workflow_dispatches():
        patient_id = request.args.get("patient_id")
        query = supabase.table("patient_dispatches").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("created_at", desc=True).execute().data or []
        if g.current_user.get("role") == "docteur":
            rows = [row for row in rows if str(row.get("doctor_id")) == str(g.current_user.get("id"))]
        patients = get_patient_map()
        doctors = get_user_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            previous = doctors.get(row.get("previous_doctor_id"))
            row["previous_doctor_name"] = previous.get("name") if previous else ""
        return jsonify(rows)

    @workflow.route("/api/workflow/consultations", methods=["GET", "POST"])
    @roles_required("super_admin", "docteur")
    def workflow_consultations():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("medical_consultations").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("created_at", desc=True).execute().data or []
            if g.current_user.get("role") == "docteur":
                rows = [row for row in rows if str(row.get("doctor_id")) == str(g.current_user.get("id"))]
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        payload = {
            "patient_id": patient_id,
            "symptoms": data.get("symptoms", ""),
            "diagnosis": data.get("diagnosis", ""),
            "diagnostics": data.get("diagnostics", []),
            "observations": data.get("observations", ""),
            "medical_history": data.get("medical_history", ""),
            "doctor_id": g.current_user["id"],
            "doctor_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("medical_consultations", payload)
        consultation_fee = to_float(data.get("consultation_fee"), get_tariff_amount("consultation", "Consultation", 0))
        consultation_line = None
        if consultation_fee > 0:
            consultation_line = add_patient_account_line(patient_id, "consultation", "Consultation medicale", consultation_fee, "consultation", result.data[0].get("id"))
        supabase.table("patient_queue").update({"status": "completed", "updated_at": now_iso()}).eq("patient_id", patient_id).in_("status", ["assigned", "in_consultation", "vitals_done"]).execute()
        current_patient = supabase.table(TABLES["patients"]).select("status").eq("id", patient_id).execute().data or []
        if not current_patient or current_patient[0].get("status") not in ("admitted", "discharged"):
            supabase.table(TABLES["patients"]).update({"status": "active", "updated_at": now_iso()}).eq("id", patient_id).execute()
        
        # Libérer le box médical automatiquement
        try:
            supabase.table("medical_boxes").update({
                "status": "free",
                "patient_id": None,
                "patient_name": None,
                "occupied_at": None,
                "updated_at": now_iso()
            }).eq("patient_id", patient_id).execute()
        except Exception as e:
            print(f"Erreur libération box: {e}")
        
        consultation_invoice = None
        if consultation_fee > 0:
            consultation_invoice = facture_auto(patient_id, "CONSULT", 1, "consultation", result.data[0].get("id"))
            if consultation_line and consultation_invoice and consultation_invoice.get("id"):
                compatible_update("patient_account_lines", {"status": "invoiced", "invoice_id": consultation_invoice["id"], "updated_at": now_iso()}, "id", consultation_line.get("id"))
        
        add_audit("CREATE", "consultation", f"Consultation patient #{patient_id}", patient_id)
        invalidate_cache()
        response = dict(result.data[0])
        response["invoice"] = consultation_invoice
        return jsonify(response), 201

    @workflow.route("/api/workflow/account-lines", methods=["GET", "POST"])
    @roles_required(*ROLES["staff"])
    def workflow_account_lines():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("patient_account_lines").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("created_at", desc=True).execute().data or []
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        if g.current_user.get("role") not in ("super_admin", "reception"):
            return jsonify({"error": "Ajout manuel reserve a l'accueil/admin"}), 403
        data = fast_json()
        line = add_patient_account_line(to_int(data.get("patient_id")), data.get("category", "manuel"), data.get("description", data.get("motif", "Frais manuel")), to_float(data.get("amount")), "manual")
        if not line:
            return jsonify({"error": "Ligne invalide"}), 422
        invalidate_cache()
        return jsonify(line), 201

    @workflow.route("/api/workflow/final-invoice", methods=["POST"])
    @roles_required("super_admin", "reception")
    def create_final_invoice_from_account():
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        lines = supabase.table("patient_account_lines").select("*").eq("patient_id", patient_id).eq("status", "pending").execute().data or []
        if not lines:
            return jsonify({"error": "Aucun frais en attente pour ce patient"}), 422
        items = [{
            "code": line.get("category", "FRAIS"),
            "description": line.get("description", ""),
            "quantity": to_int(line.get("quantity"), 1),
            "unit_price": to_float(line.get("unit_price"), line.get("amount")),
            "amount": to_float(line.get("amount")),
            "date": line.get("created_at")
        } for line in lines]
        total = round(sum(to_float(item.get("amount")) for item in items), 2)
        paid = round(to_float(data.get("paid_amount"), 0), 2)
        invoice = {
            "invoice_number": f"FINAL-{int(time.time())}-{secrets.token_hex(2).upper()}",
            "patient_id": patient_id,
            "amount": total,
            "paid_amount": paid,
            "balance_due": max(0, total - paid),
            "description": "Facture finale du compte patient",
            "status": "paid" if paid >= total else "unpaid",
            "line_items": items,
            "items": items,
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["billing"], invoice)
        created_invoice = result.data[0] if result.data else invoice
        if paid > 0:
            add_invoice_payment(created_invoice.get("id"), patient_id, paid, "Acompte facture finale")
        for line in lines:
            if line.get("id"):
                compatible_update("patient_account_lines", {"status": "invoiced", "invoice_id": created_invoice.get("id"), "updated_at": now_iso()}, "id", line["id"])
        add_audit("CREATE", "billing", f"Facture finale patient #{patient_id}: {total}", created_invoice.get("id"))
        invalidate_cache()
        return jsonify({"invoice": created_invoice}), 201

    @workflow.route("/api/hospitalization/rooms", methods=["GET", "POST"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def hospitalization_rooms():
        if request.method == "GET":
            return jsonify(hardcoded_hospitalization_rooms())
        return jsonify({"error": "Les chambres sont définies dans le catalogue ROOMS et ne peuvent pas être créées via l'API"}), 405

    @workflow.route("/api/workflow/hospitalizations", methods=["GET", "POST"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def workflow_hospitalizations():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("hospitalizations").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("admission_date", desc=True).execute().data or []
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        if g.current_user.get("role") == "reception":
            return jsonify({"error": "La réception peut uniquement consulter les hospitalisations"}), 403
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        role = g.current_user.get("role")
        patient_row = supabase.table(TABLES["patients"]).select("id,assigned_doctor_id").eq("id", patient_id).execute().data or []
        if not patient_row:
            return jsonify({"error": "Patient introuvable"}), 404
        # Lorsqu'un infirmier admet directement un patient, conserver le médecin
        # déjà assigné au patient. Sans cela l'admission n'était visible dans
        # aucun écran « Mes patients hospitalisés » du médecin.
        assigned_doctor_id = data.get("doctor_id") or (
            g.current_user["id"] if role == "docteur" else patient_row[0].get("assigned_doctor_id")
        )
        assigned_doctor = get_user_map().get(assigned_doctor_id) if assigned_doctor_id else None
        # Toute création est une demande. L'admission est exclusivement réalisée via
        # PATCH par l'infirmier, après sélection obligatoire d'une chambre et d'un lit.
        status = "pending"
        bed_val = data.get("bed_id") or data.get("bed")
        bed_id_clean = to_int(bed_val, None) if str(bed_val or "").strip() else None
        room_id_clean = to_int(data.get("room_id"), None) if str(data.get("room_id") or "").strip() else None
        doctor_id_clean = to_int(assigned_doctor_id, None) if str(assigned_doctor_id or "").strip() else None
        payload = {
            "patient_id": patient_id,
            "admission_date": None,
            "discharge_date": data.get("discharge_date"),
            "status": status,
            "reason": data.get("reason", ""),
            "room": data.get("room", ""),
            "bed": str(data.get("bed") or data.get("bed_id") or ""),
            "bed_id": bed_id_clean,
            "room_id": room_id_clean,
            "doctor_id": doctor_id_clean,
            "doctor_name": data.get("doctor_name", "") or (assigned_doctor.get("name", "") if assigned_doctor else (g.current_user["name"] if role == "docteur" else "")),
            "daily_rate": to_float(data.get("daily_rate"), get_tariff_amount("hospitalisation", "Hospitalisation", 0)),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("hospitalizations", payload)
        add_audit("CREATE", "hospitalization", f"Demande d’hospitalisation patient #{patient_id}", patient_id)
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @workflow.route("/api/workflow/hospitalizations/<int:hosp_id>/discharge", methods=["POST"])
    @roles_required("super_admin", "infirmier", "docteur")
    def discharge_workflow_hospitalization(hosp_id: int):
        return _discharge_workflow_hospitalization(hosp_id, fast_json())

    def _discharge_workflow_hospitalization(hosp_id: int, data: dict):
        hosp = supabase.table("hospitalizations").select("*").eq("id", hosp_id).execute()
        if not hosp.data:
            return jsonify({"error": "Hospitalisation introuvable"}), 404
        row = hosp.data[0]
        if row.get("status") == "discharged":
            return jsonify({"error": "Cette hospitalisation est déjà clôturée"}), 422
        discharge_date = data.get("discharge_date") or now_iso()
        start = parse_date(row.get("admission_date")) or datetime.now(timezone.utc).date()
        end = parse_date(discharge_date) or datetime.now(timezone.utc).date()
        days = max(1, (end - start).days + 1)
        daily_rate = to_float(data.get("daily_rate"), row.get("daily_rate") or 0)
        updates = {"discharge_date": discharge_date, "status": "discharged", "days_count": days, "daily_rate": daily_rate, "updated_at": now_iso()}
        result = supabase.table("hospitalizations").update(updates).eq("id", hosp_id).execute()
        
        if daily_rate > 0:
            amount = days * daily_rate
            add_patient_account_line(to_int(row.get("patient_id")), "hospitalisation", f"Hospitalisation {days} jour(s)", amount, "hospitalization", hosp_id, days, daily_rate)
            facture_auto(to_int(row.get("patient_id")), "HOSPI_JOUR", days, "hospitalization", hosp_id)
        supabase.table(TABLES["patients"]).update({"status": "discharged", "updated_at": now_iso()}).eq("id", row.get("patient_id")).execute()
        add_audit("UPDATE", "hospitalization", f"Sortie hospitalisation #{hosp_id}", hosp_id)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else updates)

    @workflow.route("/api/workflow/hospitalizations/<int:hosp_id>", methods=["PATCH"])
    @roles_required("super_admin", "infirmier", "docteur")
    def patch_workflow_hospitalization(hosp_id: int):
        data = fast_json()
        if data.get("status") == "discharged":
            discharge_data = dict(data)
            discharge_data["discharge_date"] = data.get("discharge_date") or data.get("discharged_at") or now_iso()
            return _discharge_workflow_hospitalization(hosp_id, discharge_data)
        existing = supabase.table("hospitalizations").select("*").eq("id", hosp_id).execute()
        if not existing.data:
            return jsonify({"error": "Hospitalisation introuvable"}), 404
        current = existing.data[0]
        if data.get("status") in ("admitted", "hospitalized") and current.get("status") == "pending":
            if g.current_user.get("role") not in ("super_admin", "infirmier"):
                return jsonify({"error": "Seul l'infirmier peut accepter une hospitalisation"}), 403
            room_id = to_int(data.get("room_id"))
            if not room_id:
                return jsonify({"error": "Chambre requise pour l'admission"}), 422
            if not str(data.get("bed_id") or data.get("bed") or "").strip():
                return jsonify({"error": "Lit requis pour l'admission"}), 422
            room = next((item for item in hardcoded_hospitalization_rooms() if item["id"] == room_id), None)
            if not room:
                return jsonify({"error": "Chambre introuvable"}), 404
            requested_bed = str(data.get("bed_id") or data.get("bed"))
            if to_int(requested_bed) < 1 or to_int(requested_bed) > to_int(room.get("total_beds"), 1):
                return jsonify({"error": "Lit invalide pour cette chambre"}), 422
            if requested_bed in room.get("occupied_bed_ids", []):
                return jsonify({"error": "Ce lit est déjà occupé"}), 422
            if to_int(room.get("occupied_beds"), 0) >= to_int(room.get("total_beds"), 1):
                return jsonify({"error": "Aucun lit libre dans cette chambre"}), 422
            data = {**data, "room": room.get("room_number"), "status": "admitted", "admission_date": data.get("admission_date") or now_iso(),
                    "admitted_by": g.current_user["id"], "admitted_by_name": g.current_user["name"]}
            supabase.table(TABLES["patients"]).update({"status": "admitted", "updated_at": now_iso()}).eq("id", current.get("patient_id")).execute()
        allowed = ("admission_date", "discharge_date", "room", "bed", "bed_id", "room_id", "reason", "doctor_id", "doctor_name", "daily_rate", "notes", "status", "admitted_by", "admitted_by_name")
        updates = {key: value for key, value in data.items() if key in allowed and value is not None}
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table("hospitalizations").update(updates).eq("id", hosp_id).execute()
        if not result.data:
            return jsonify({"error": "Hospitalisation introuvable"}), 404
        add_audit("UPDATE", "hospitalization", f"Hospitalisation #{hosp_id} modifiée", hosp_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @workflow.route("/api/workflow/hospitalizations/<int:hosp_id>", methods=["DELETE"])
    @roles_required("super_admin", "docteur")
    def delete_workflow_hospitalization(hosp_id: int):
        existing = supabase.table("hospitalizations").select("patient_id,status").eq("id", hosp_id).execute()
        if not existing.data:
            return jsonify({"error": "Hospitalisation introuvable"}), 404
        supabase.table("hospitalizations").delete().eq("id", hosp_id).execute()
        add_audit("DELETE", "hospitalization", f"Hospitalisation #{hosp_id} annulée", hosp_id)
        invalidate_cache()
        return jsonify({"message": "Hospitalisation annulée"})

    @workflow.route("/api/workflow/hospitalizations/patient/<int:patient_id>/discharge", methods=["POST"])
    @roles_required("super_admin", "infirmier", "docteur")
    def discharge_patient_hospitalization_by_id(patient_id: int):
        hosp = supabase.table("hospitalizations").select("*").eq("patient_id", patient_id).in_("status", ["admitted", "hospitalized"]).execute()
        if not hosp.data:
            return jsonify({"error": "Aucune hospitalisation en cours pour ce patient"}), 404
        hosp_id = hosp.data[0]["id"]
        return discharge_workflow_hospitalization(hosp_id)

    @workflow.route("/api/workflow/followups", methods=["GET", "POST"])
    @roles_required("super_admin", "docteur", "infirmier")
    def workflow_followups():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("medical_followups").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("followup_date", desc=True).execute().data or []
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        payload = {
            "patient_id": patient_id,
            "instructions": data.get("instructions", ""),
            "medication": data.get("medication", ""),
            "dose": data.get("dose", ""),
            "frequency": data.get("frequency", ""),
            "observations": data.get("observations", ""),
            "status": data.get("status", "pending"),
            "followup_date": data.get("followup_date") or now_iso(),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("medical_followups", payload)
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @workflow.route("/api/workflow/followups/<int:followup_id>", methods=["PATCH"])
    @roles_required("super_admin", "docteur", "infirmier")
    def patch_followup(followup_id: int):
        data = fast_json()
        allowed = ["status"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table("medical_followups").update(updates).eq("id", followup_id).execute()
        if not result.data:
            return jsonify({"error": "Suivi introuvable"}), 404
        invalidate_cache()
        return jsonify(result.data[0])

    @workflow.route("/api/workflow/administrations", methods=["GET", "POST"])
    @roles_required("super_admin", "infirmier")
    def workflow_administrations():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("medication_administrations").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("created_at", desc=True).execute().data or []
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        amount = to_float(data.get("amount"), 0)
        followup = None
        if data.get("followup_id"):
            followup_rows = supabase.table("medical_followups").select("*").eq("id", to_int(data.get("followup_id"))).execute().data or []
            followup = followup_rows[0] if followup_rows else None
        payload = {
            "patient_id": patient_id,
            "followup_id": data.get("followup_id"),
            "medication": data.get("medication") or (followup.get("medication") if followup else ""),
            "dose": data.get("dose") or (followup.get("dose") if followup else ""),
            "status": normalize_status(data.get("status"), ["administered", "pending", "not_administered", "given"], "pending"),
            "observations": data.get("observations", ""),
            "nurse_name": data.get("nurse_name", g.current_user.get("name") or g.current_user.get("email")),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("medication_administrations", payload)
        if payload["status"] in ("administered", "given") and amount > 0:
            add_patient_account_line(patient_id, "medicament", f"Administration: {payload['medication']}", amount, "medication_administration", result.data[0].get("id"))
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @workflow.route("/api/care/administrations", methods=["GET", "POST"])
    @roles_required("super_admin", "infirmier")
    def care_administrations_compat():
        return workflow_administrations.__wrapped__()

    @workflow.route("/api/workflow/administrations/<int:admin_id>", methods=["PATCH"])
    @roles_required("super_admin", "infirmier")
    def patch_administration(admin_id: int):
        data = fast_json()
        allowed = ["status"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table("medication_administrations").update(updates).eq("id", admin_id).execute()
        if not result.data:
            return jsonify({"error": "Administration introuvable"}), 404
        invalidate_cache()
        return jsonify(result.data[0])

    @workflow.route("/api/workflow/medical-record/<int:patient_id>", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_workflow_medical_record(patient_id: int):
        patient_result = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
        if not patient_result.data:
            return jsonify({"error": "Patient introuvable"}), 404
        patient = add_pregnancy_flags(patient_result.data)[0]
        if not can_access_patient_record(patient):
            return jsonify({"error": "Acces patient non autorise"}), 403
        sources = [
            ("consultation", "Consultation", "medical_consultations", "created_at", "diagnosis"),
            ("prescription", "Prescription", TABLES["prescriptions"], "created_at", "medication"),
            ("examen", "Examen", TABLES["lab_tests"], "request_date", "test_type"),
            ("resultat", "Resultat", TABLES["lab_tests"], "completed_date", "result"),
            ("hospitalisation", "Hospitalisation", "hospitalizations", "admission_date", "reason"),
            ("facture", "Facture", TABLES["billing"], "created_at", "description"),
            ("constantes", "Signes vitaux", "vital_signs", "created_at", "notes"),
            ("suivi", "Suivi medical", "medical_followups", "followup_date", "observations"),
            ("administration", "Administration", "medication_administrations", "created_at", "medication"),
            ("frais", "Compte patient", "patient_account_lines", "created_at", "description"),
        ]
        timeline = []
        for source_type, label, table, date_field, title_field in sources:
            try:
                rows = supabase.table(table).select("*").eq("patient_id", patient_id).execute().data or []
            except Exception:
                rows = []
            for row in rows:
                if source_type == "resultat" and row.get("status") != "completed":
                    continue
                event_date = row.get(date_field) or row.get("created_at") or row.get("updated_at")
                title = row.get(title_field) or row.get("description") or label
                timeline.append({
                    "type": source_type,
                    "label": label,
                    "date": event_date,
                    "title": title,
                    "data": row
                })
        timeline.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
        return jsonify({"patient": patient, "timeline": timeline})

    @workflow.route("/api/workflow/tariffs", methods=["GET", "POST"])
    @roles_required("super_admin")
    def workflow_tariffs():
        if request.method == "GET":
            category = request.args.get("category")
            query = supabase.table(TABLES["tariffs"]).select("*")
            if category:
                query = query.eq("category", category)
            rows = query.order("category").execute().data or []
            return jsonify(rows)
        data = fast_json()
        category = data.get("category", "").strip()
        label = data.get("label", "").strip()
        amount = round(to_float(data.get("amount"), 0), 2)
        if not category or not label or amount < 0:
            return jsonify({"error": "Categorie, libelle et montant requis"}), 422
        payload = {
            "category": category,
            "label": label,
            "amount": amount,
            "is_active": data.get("is_active", True),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["tariffs"], payload)
        tariff = result.data[0] if result.data else payload
        compatible_insert(TABLES["tariff_history"], {
            "tariff_id": tariff.get("id"),
            "category": category,
            "label": label,
            "old_amount": 0,
            "new_amount": amount,
            "action": "CREATE",
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        })
        add_audit("CREATE", "tariff", f"Tarif {category}: {label} = {amount}", tariff.get("id"))
        invalidate_cache()
        return jsonify(tariff), 201

    @workflow.route("/api/workflow/tariffs/<int:tariff_id>", methods=["PUT"])
    @roles_required("super_admin")
    def update_workflow_tariff(tariff_id: int):
        data = fast_json()
        existing = supabase.table(TABLES["tariffs"]).select("*").eq("id", tariff_id).execute().data or []
        if not existing:
            return jsonify({"error": "Tarif introuvable"}), 404
        current = existing[0]
        updates = {
            "category": data.get("category", current.get("category")),
            "label": data.get("label", current.get("label")),
            "amount": round(to_float(data.get("amount"), current.get("amount")), 2),
            "is_active": data.get("is_active", current.get("is_active", True)),
            "updated_at": now_iso()
        }
        result = compatible_update(TABLES["tariffs"], updates, "id", tariff_id)
        compatible_insert(TABLES["tariff_history"], {
            "tariff_id": tariff_id,
            "category": updates["category"],
            "label": updates["label"],
            "old_amount": to_float(current.get("amount"), 0),
            "new_amount": updates["amount"],
            "action": "UPDATE",
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        })
        add_audit("UPDATE", "tariff", f"Tarif #{tariff_id} modifie", tariff_id)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else updates)

    @workflow.route("/api/workflow/tariffs/history", methods=["GET"])
    @roles_required("super_admin")
    def workflow_tariff_history():
        rows = supabase.table(TABLES["tariff_history"]).select("*").order("created_at", desc=True).execute().data or []
        return jsonify(rows)

    # ==================== HOSPITALIZATION FOLLOWUPS ====================

    @workflow.route("/api/workflow/hospitalization-followups", methods=["GET", "POST"])
    @roles_required("super_admin", "infirmier", "docteur")
    def workflow_hospitalization_followups():
        if request.method == "GET":
            patient_id = request.args.get("patient_id")
            query = supabase.table("hospitalization_followups").select("*")
            if patient_id:
                query = query.eq("patient_id", to_int(patient_id))
            rows = query.order("created_at", desc=True).execute().data or []
            patients = get_patient_map()
            for row in rows:
                row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
            return jsonify(rows)
        
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        
        payload = {
            "patient_id": patient_id,
            "temperature": data.get("temperature"),
            "blood_pressure_sys": data.get("blood_pressure_sys"),
            "blood_pressure_dia": data.get("blood_pressure_dia"),
            "heart_rate": data.get("heart_rate"),
            "respiratory_rate": data.get("respiratory_rate"),
            "pain_level": data.get("pain_level"),
            "general_state": data.get("general_state", "good"),
            "notes": data.get("notes", ""),
            "nurse_id": g.current_user.get("id"),
            "nurse_name": g.current_user.get("name") or g.current_user.get("email"),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        
        result = compatible_insert("hospitalization_followups", payload)
        add_audit("CREATE", "hospitalization_followup", f"Suivi patient #{patient_id}", result.data[0]["id"] if result.data else None)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else payload), 201

    # ==================== EXCHANGE RATE ====================

    @workflow.route("/api/medical/boxes", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    @cached(timeout=30)
    def get_medical_boxes():
        try:
            result = supabase.table("medical_boxes").select("*").order("box_number").execute()
            return jsonify(result.data or [])
        except Exception as e:
            print(f"Erreur récupération box: {e}")
            return jsonify([])

    @workflow.route("/api/medical/boxes", methods=["POST"])
    @roles_required("super_admin", "infirmier")
    def create_medical_box():
        data = fast_json()
        if not data.get("box_number"):
            return jsonify({"error": "Numéro de box requis"}), 422
        
        # Vérifier le nombre maximum de box
        try:
            count_result = supabase.table("medical_boxes").select("id", count="exact").execute()
            current_count = len(count_result.data) if count_result.data else 0
            if current_count >= MAX_BOXES:
                return jsonify({"error": f"Nombre maximum de box atteint ({MAX_BOXES})"}), 422
        except Exception:
            pass
        
        box = {
            "box_number": data.get("box_number"),
            "status": "free",
            "doctor_id": None,
            "doctor_name": None,
            "patient_id": None,
            "patient_name": None,
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("medical_boxes", box)
        add_audit("CREATE", "medical_box", f"Box {data['box_number']} créé", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @workflow.route("/api/medical/boxes/<int:box_id>", methods=["GET"])
    @roles_required("super_admin", "infirmier", "docteur", "reception")
    def get_medical_box(box_id: int):
        """Récupère les détails d'un box médical"""
        result = supabase.table("medical_boxes").select("*").eq("id", box_id).execute()
        if not result.data:
            return jsonify({"error": "Box introuvable"}), 404
        return jsonify(result.data[0])

    @workflow.route("/api/medical/boxes/<int:box_id>/assign", methods=["PUT"])
    @roles_required("super_admin", "infirmier")
    def assign_medical_box(box_id: int):
        data = fast_json()
        doctor_id = data.get("doctor_id")
        if not doctor_id:
            return jsonify({"error": "Médecin requis"}), 422
        
        doctor = supabase.table(TABLES["users"]).select("id,name").eq("id", doctor_id).execute()
        if not doctor.data:
            return jsonify({"error": "Médecin introuvable"}), 404
        
        result = supabase.table("medical_boxes").update({
            "doctor_id": doctor_id,
            "doctor_name": doctor.data[0].get("name"),
            "status": "free",
            "updated_at": now_iso()
        }).eq("id", box_id).execute()
        
        add_audit("UPDATE", "medical_box", f"Box #{box_id} assigné au médecin #{doctor_id}", box_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @workflow.route("/api/medical/boxes/<int:box_id>/free", methods=["PUT"])
    @roles_required("super_admin", "infirmier")
    def free_medical_box(box_id: int):
        result = supabase.table("medical_boxes").update({
            "status": "free",
            "patient_id": None,
            "patient_name": None,
            "occupied_at": None,
            "updated_at": now_iso()
        }).eq("id", box_id).execute()
        if not result.data:
            return jsonify({"error": "Box introuvable"}), 404
        
        add_audit("UPDATE", "medical_box", f"Box #{box_id} libéré", box_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @workflow.route("/api/medical/boxes/<int:box_id>/occupy", methods=["PUT"])
    @roles_required("super_admin", "infirmier", "docteur")
    def occupy_medical_box(box_id: int):
        data = fast_json()
        patient_id = data.get("patient_id")
        if not patient_id:
            return jsonify({"error": "Patient requis"}), 422
        
        patient = supabase.table(TABLES["patients"]).select("id,full_name").eq("id", patient_id).execute()
        if not patient.data:
            return jsonify({"error": "Patient introuvable"}), 404
        
        box = supabase.table("medical_boxes").select("*").eq("id", box_id).execute()
        if not box.data:
            return jsonify({"error": "Box introuvable"}), 404
        
        if not box.data[0].get("doctor_id"):
            return jsonify({"error": "Aucun médecin assigné à ce box"}), 422
        
        result = supabase.table("medical_boxes").update({
            "status": "occupied",
            "patient_id": patient_id,
            "patient_name": patient.data[0].get("full_name"),
            "occupied_at": now_iso(),
            "updated_at": now_iso()
        }).eq("id", box_id).execute()
        
        add_audit("UPDATE", "medical_box", f"Box #{box_id} occupé par patient #{patient_id}", box_id)
        invalidate_cache()
        return jsonify(result.data[0])

    # ==================== PHARMACY ====================
    def infer_pharmacy_category(item: dict) -> str:
        cat = str(item.get("category") or "").lower().strip()
        if cat in ("medicament", "médicament", "medicaments"):
            return "medicament"
        if cat in ("injectable", "injectables"):
            return "injectable"
        if cat in ("consommable", "consommables", "consumable"):
            return "consommable"
        if item.get("route") or "injectable" in str(item.get("form") or "").lower() or "ampoule" in str(item.get("form") or "").lower():
            return "injectable"
        if item.get("consumable_type") or item.get("size"):
            return "consommable"
        name = str(item.get("medication_name") or item.get("product_name") or item.get("name") or "").lower()
        if any(k in name for k in ("seringue", "aiguille", "compresse", "gant", "sparadrap", "coton", "pansement", "catheter", "cathéter", "sonde", "masque", "perfuseur", "tubulure", "bistouri", "garrot")):
            return "consommable"
        if any(k in name for k in ("injectable", "inj", "ampoule", "perfusion", "perf")):
            return "injectable"
        return "medicament"

    app.register_blueprint(workflow)
