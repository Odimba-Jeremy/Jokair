"""Routes patients, identifiants hospitaliers et résultats liés au patient."""
import base64
import re
from datetime import datetime, timezone

from flask import Blueprint, Response, g, jsonify, request


def register_patient_routes(
    app, *, supabase, tables, roles, fast_json, to_int, optional_date,
    now_iso, cached, roles_required, compatible_insert, invalidate_cache,
    add_audit, hospital_patient_id, enrich_patient_identifier,
    enrich_patient_identifiers, add_pregnancy_flags, is_female,
    filter_patients_for_role, can_access_patient_record, allowed_statuses,
    generate_barcode_svg, generate_qr_code_data, linked_patient_ids_for_user=None,
):
    patients = Blueprint("patients", __name__)

    @patients.get("/api/patients")
    @roles_required(*roles["staff"])
    def get_patients():
        search = request.args.get("search", "").strip().lower()
        context = request.args.get("context", "").strip().lower()
        numeric_search = re.fullmatch(r"(?:hb-ushd-|ih-ushd-|ih-usd-)?0*(\d+)", search, re.IGNORECASE)

        page_arg = request.args.get("page")
        limit_arg = request.args.get("limit")
        paged_request = (page_arg is not None) or (limit_arg is not None)
        page = max(1, to_int(page_arg, 1))
        limit = min(max(to_int(limit_arg, 20), 1), 200) if paged_request else 0

        role = g.current_user.get("role")
        doctor_patient_ids = None
        if role == "docteur" and callable(linked_patient_ids_for_user):
            linked_ids = list(linked_patient_ids_for_user())
            doctor_patient_ids = linked_ids if linked_ids else ["0"]

        if search:
            id_clause = f",id.eq.{int(numeric_search.group(1))}" if numeric_search else ""
            query = supabase.table(tables["patients"]).select("*", count="exact").or_(
                f"full_name.ilike.%{search}%,phone.ilike.%{search}%,email.ilike.%{search}%{id_clause}"
            ).order("created_at", desc=True)
        else:
            query = supabase.table(tables["patients"]).select("*", count="exact").order("created_at", desc=True)

        if doctor_patient_ids is not None:
            query = query.in_("id", doctor_patient_ids)

        if paged_request and limit:
            start = (page - 1) * limit
            end = start + limit - 1
            query = query.range(start, end)
        elif limit:
            query = query.limit(limit)

        res = query.execute()
        raw_patients = res.data or []
        total_count = res.count if res.count is not None else len(raw_patients)
        patients_list = enrich_patient_identifiers(add_pregnancy_flags(raw_patients))

        role = g.current_user.get("role")
        if context in ("maternity", "pregnancy", "prenatal", "delivery") and role == "super_admin":
            filtered = [patient for patient in patients_list if is_female(patient)]
        elif context == "pharmacy" and role in ("super_admin", "pharmacie"):
            filtered = patients_list
        else:
            filtered = filter_patients_for_role(patients_list)

        if paged_request:
            return jsonify({
                "data": filtered,
                "total": total_count,
                "page": page,
                "limit": limit
            })
        return jsonify(filtered)

    @patients.post("/api/patients")
    @roles_required("super_admin", "reception")
    def create_patient():
        data = fast_json()
        full_name = data.get("full_name", "").strip()
        if not full_name:
            return jsonify({"error": "Nom requis"}), 422
        patient = {
            "full_name": full_name, "phone": data.get("phone", ""), "email": data.get("email", ""),
            "date_of_birth": optional_date(data.get("date_of_birth")), "gender": data.get("gender", ""),
            "blood_type": data.get("blood_type", ""), "address": data.get("address", ""),
            "status": data.get("status", "waiting"), "allergies": data.get("allergies", ""),
            "medical_history": data.get("medical_history", ""),
            "emergency_contact": data.get("emergency_contact", ""), "insurance": data.get("insurance", ""),
            "priority": data.get("priority", "normal"), "doctor_notes": data.get("doctor_notes", ""),
            "room_number": data.get("room_number", ""), "is_pregnant": data.get("is_pregnant", False),
            "created_by": g.current_user.get("id"),
            "created_by_name": g.current_user.get("name") or g.current_user.get("email"),
            "created_at": now_iso(), "updated_at": now_iso(),
        }
        result = compatible_insert(tables["patients"], patient)
        created_patient = result.data[0]
        created_patient["hospital_id"] = hospital_patient_id(created_patient.get("id"))
        try:
            last = supabase.table("patient_queue").select("arrival_order").order("arrival_order", desc=True).limit(1).execute().data or []
            arrival_order = to_int(last[0].get("arrival_order"), 0) + 1 if last else 1
            compatible_insert("patient_queue", {
                "patient_id": created_patient["id"], "status": "waiting", "arrival_order": arrival_order,
                "arrival_time": now_iso(), "created_by": g.current_user.get("id"),
                "created_by_name": g.current_user.get("name") or g.current_user.get("email"),
                "created_at": now_iso(), "updated_at": now_iso(),
            })
        except Exception as exc:
            print(f"File d'attente non créée pour patient #{created_patient.get('id')}: {exc}")
        if data.get("is_pregnant") and is_female(created_patient):
            compatible_insert("pregnancies", {
                "patient_id": created_patient["id"],
                "last_menstrual_period": optional_date(data.get("pregnancy_lmp")) or datetime.now(timezone.utc).date().isoformat(),
                "expected_delivery_date": optional_date(data.get("expected_delivery_date")),
                "risk_level": data.get("risk_level", "normal"),
                "medical_history": data.get("pregnancy_notes", "Grossesse signalée à la réception, DDR à compléter en maternité."),
                "status": "active", "created_by": g.current_user.get("id"),
                "created_by_name": g.current_user.get("name") or g.current_user.get("email"),
                "created_at": now_iso(), "updated_at": now_iso(),
            })
            created_patient["is_pregnant"] = True
        add_audit("CREATE", "patient", f"Patient: {full_name}", created_patient["id"])
        invalidate_cache()
        return jsonify(created_patient), 201

    @patients.get("/api/patients/<int:patient_id>")
    @roles_required(*roles["staff"])
    @cached(timeout=60)
    def get_patient(patient_id: int):
        if patient_id <= 0:
            return jsonify({"error": "ID patient invalide"}), 400
        result = supabase.table(tables["patients"]).select("*").eq("id", patient_id).execute()
        if not result.data:
            return jsonify({"error": "Patient introuvable"}), 404
        patient = enrich_patient_identifier(add_pregnancy_flags(result.data)[0])
        if not can_access_patient_record(patient):
            return jsonify({"error": "Acces patient non autorise"}), 403
        return jsonify(patient)

    @patients.route("/api/patients/<int:patient_id>", methods=["PUT", "PATCH"])
    @roles_required("super_admin", "docteur", "infirmier", "reception")
    def update_patient(patient_id: int):
        data = fast_json()
        existing = supabase.table(tables["patients"]).select("*").eq("id", patient_id).execute()
        if not existing.data:
            return jsonify({"error": "Patient introuvable"}), 404
        if not can_access_patient_record(add_pregnancy_flags(existing.data)[0]):
            return jsonify({"error": "Acces patient non autorise"}), 403
        allowed_fields = ["full_name", "phone", "email", "date_of_birth", "gender", "blood_type", "address", "status", "allergies", "medical_history", "emergency_contact", "insurance", "priority", "doctor_notes", "room_number", "is_pregnant"]
        updates = {key: value for key, value in data.items() if key in allowed_fields and value is not None}
        if "status" in updates and updates["status"] not in allowed_statuses:
            return jsonify({"error": f"Statut invalide: {updates['status']}. Valeurs autorisées: {', '.join(allowed_statuses)}"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table(tables["patients"]).update(updates).eq("id", patient_id).execute()
        if not result.data:
            return jsonify({"error": "Patient introuvable"}), 404
        add_audit("UPDATE", "patient", f"Patient #{patient_id} modifié", patient_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @patients.delete("/api/patients/<int:patient_id>")
    @roles_required("super_admin")
    def delete_patient(patient_id: int):
        patient = supabase.table(tables["patients"]).select("full_name").eq("id", patient_id).execute()
        if not patient.data:
            return jsonify({"error": "Patient introuvable"}), 404
        supabase.table(tables["patients"]).delete().eq("id", patient_id).execute()
        add_audit("DELETE", "patient", f"Patient: {patient.data[0]['full_name']}", patient_id)
        invalidate_cache()
        return jsonify({"message": "Patient supprimé"})

    @patients.get("/api/patients/<int:patient_id>/appointments")
    @roles_required(*roles["staff"])
    def get_patient_appointments(patient_id: int):
        result = supabase.table(tables["appointments"]).select("*").eq("patient_id", patient_id).order("date", desc=True).execute()
        return jsonify(result.data)

    @patients.get("/api/patients/<int:patient_id>/prescriptions")
    @roles_required(*roles["staff"])
    def get_patient_prescriptions(patient_id: int):
        result = supabase.table(tables["prescriptions"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute()
        return jsonify(result.data)

    @patients.get("/api/patients/<int:patient_id>/lab-results")
    @roles_required(*roles["staff"])
    def get_patient_lab_results(patient_id: int):
        result = supabase.table(tables["lab_tests"]).select("*").eq("patient_id", patient_id).eq("status", "completed").order("completed_date", desc=True).execute()
        return jsonify(result.data)

    @patients.post("/api/patients/<int:patient_id>/lab-results")
    @roles_required("super_admin", "laboratoire")
    def link_lab_result_to_patient(patient_id: int):
        data = fast_json()
        if not data.get("test_id") or not data.get("test_type"):
            return jsonify({"error": "test_id et test_type requis"}), 422
        test = supabase.table(tables["lab_tests"]).select("*").eq("id", data["test_id"]).execute()
        if not test.data:
            return jsonify({"error": "Analyse introuvable"}), 404
        lab_result = {
            "patient_id": patient_id, "test_id": data["test_id"], "test_type": data["test_type"],
            "result": data.get("result", ""), "observations": data.get("observations", ""),
            "linked_at": now_iso(), "linked_by": g.current_user.get("id"),
            "linked_by_name": g.current_user.get("name") or g.current_user.get("email"),
        }
        result = compatible_insert("patient_lab_results", lab_result)
        add_audit("CREATE", "patient_lab_result", f"Résultat lié au patient #{patient_id}", patient_id)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else lab_result), 201

    @patients.get("/api/patients/<int:patient_id>/barcode")
    @roles_required(*roles["staff"])
    def get_patient_barcode(patient_id: int):
        patient = supabase.table(tables["patients"]).select("id,full_name").eq("id", patient_id).execute()
        if not patient.data:
            return jsonify({"error": "Patient introuvable"}), 404
        return Response(generate_barcode_svg(patient_id, patient.data[0].get("full_name", "Patient")), mimetype="image/svg+xml")

    @patients.post("/api/patients/<int:patient_id>/barcode")
    @roles_required("super_admin", "reception")
    def regenerate_patient_barcode(patient_id: int):
        patient = supabase.table(tables["patients"]).select("id,full_name").eq("id", patient_id).execute()
        if not patient.data:
            return jsonify({"error": "Patient introuvable"}), 404
        add_audit("UPDATE", "patient", f"Code-barres régénéré pour patient #{patient_id}", patient_id)
        return Response(generate_barcode_svg(patient_id, patient.data[0].get("full_name", "Patient")), mimetype="image/svg+xml")

    @patients.get("/api/patients/<int:patient_id>/qr-code")
    @roles_required(*roles["staff"])
    def get_patient_qr_code(patient_id: int):
        patient = supabase.table(tables["patients"]).select("id,full_name,phone").eq("id", patient_id).execute()
        if not patient.data:
            return jsonify({"error": "Patient introuvable"}), 404
        patient_data = patient.data[0]
        qr_data = generate_qr_code_data(patient_id, patient_data.get("full_name", ""), patient_data.get("phone", ""))
        try:
            import qrcode
            from io import BytesIO
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(qr_data)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            image_data = base64.b64encode(buffer.getvalue()).decode()
            return jsonify({"patient_id": patient_id, "qr_code": f"data:image/png;base64,{image_data}", "data": qr_data})
        except ImportError:
            return jsonify({"patient_id": patient_id, "qr_code": None, "data": qr_data, "error": "Bibliothèque qrcode non installée"})

    @patients.get("/api/patients/<int:patient_id>/care-history")
    @roles_required(*roles["staff"])
    def get_patient_care_history(patient_id: int):
        """Historique complet des soins prescrits et administrés pour le patient."""
        care_rows = supabase.table(tables["care"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []
        admin_rows = supabase.table("medication_administrations").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []
        return jsonify({
            "prescriptions_care": care_rows,
            "administrations": admin_rows
        })

    @patients.get("/api/patients/<int:patient_id>/full-record")
    @roles_required(*roles["staff"])
    def get_patient_full_record(patient_id: int):
        """Dossier patient médical complet agrégeant toutes les étapes du parcours clinique."""
        if patient_id <= 0:
            return jsonify({"error": "ID patient invalide"}), 400

        # 1. Infos patient de base
        p_res = supabase.table(tables["patients"]).select("*").eq("id", patient_id).execute()
        if not p_res.data:
            return jsonify({"error": "Patient introuvable"}), 404
        patient = enrich_patient_identifier(add_pregnancy_flags(p_res.data)[0])
        if not can_access_patient_record(patient):
            return jsonify({"error": "Accès patient non autorisé"}), 403

        # 2. Consultations médicales (symptômes, diagnostic, observations, notes, etc.)
        consultations = supabase.table("medical_consultations").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 3. Prescriptions médicamenteuses
        prescriptions = supabase.table(tables["prescriptions"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 4. Soins infirmiers prescrits et séances de soins
        care_logs = supabase.table(tables["care"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 5. Administrations effectives de soins
        administrations = supabase.table("medication_administrations").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 6. Résultats d'analyses de laboratoire
        lab_results = supabase.table(tables["lab_tests"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 7. Signes vitaux (constantes)
        vitals = supabase.table("vital_signs").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 8. Hospitalisations et lits
        hospitalizations = supabase.table("hospitalizations").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 9. Suivis médicaux
        followups = supabase.table("medical_followups").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        # 10. Factures
        invoices = supabase.table(tables["billing"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        return jsonify({
            "patient": patient,
            "consultations": consultations,
            "prescriptions": prescriptions,
            "care_logs": care_logs,
            "administrations": administrations,
            "lab_results": lab_results,
            "vitals": vitals,
            "hospitalizations": hospitalizations,
            "followups": followups,
            "invoices": invoices
        })

    app.register_blueprint(patients)
