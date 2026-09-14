"""Endpoints d'assistance IA clinique et opérationnelle."""

from flask import Blueprint


def register_ai_routes(app, *, runtime):
    globals().update(runtime)
    ai = Blueprint("ai", __name__)

    @ai.route("/api/ai/health", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def ai_health():
        configured = bool(GROQ_API_KEY and str(GROQ_API_KEY).startswith("gsk_"))
        if not configured:
            return jsonify({"ok": False, "model": GROQ_MODEL, "message": "Cle Groq absente ou invalide dans app.py"}), 500
        answer = groq_chat("Reponds uniquement par OK.", "Test de connexion IA.")
        ok = answer.strip().lower().startswith("ok")
        return jsonify({"ok": ok, "model": str(GROQ_MODEL), "message": answer}), 200 if ok else 502

    @ai.route("/api/ai/chat", methods=["POST"])
    @roles_required("super_admin", "docteur")
    def ai_chat_compat():
        data = fast_json()
        message = str(data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Message requis"}), 422
        response = groq_chat(
            "Assistant médical de soutien. Réponds en français, de façon structurée et prudente. "
            "Ne pose pas de diagnostic définitif et rappelle la nécessité d'une validation clinique.",
            message[:12000]
        )
        return ai_payload("response", response)

    @ai.route("/api/ai/patient-summary", methods=["POST"])
    @roles_required("super_admin", "docteur")
    def ai_patient_summary():
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        patient_result = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
        if not patient_result.data:
            return jsonify({"error": "Patient introuvable"}), 404
        patient = filter_patients_for_role(patient_result.data)
        if not patient:
            return jsonify({"error": "Accès patient interdit pour ce rôle"}), 403
        summary = groq_chat("Résume un dossier patient de façon structurée et prudente, sans diagnostic final.", json.dumps(patient[0], ensure_ascii=False))
        return ai_payload("summary", summary)

    @ai.route("/api/ai/clinical-decision", methods=["POST"])
    @roles_required("super_admin", "docteur")
    def ai_clinical_decision():
        context = fast_json().get("context", "")
        analysis = groq_chat("Aide à l'orientation clinique. Donne priorités, signes d'alerte, examens utiles et limites. Ne pose pas de diagnostic final.", context)
        return ai_payload("analysis", analysis)

    @ai.route("/api/ai/hospital-flow", methods=["POST"])
    @roles_required(*ROLES["staff"])
    def ai_hospital_flow():
        data = fast_json()
        prediction = groq_chat("Prédis l'affluence hospitalière à court terme et propose des recommandations opérationnelles.", json.dumps(data, ensure_ascii=False)[:12000])
        return ai_payload("prediction", prediction)

    app.register_blueprint(ai)
    return

    # ==================== RAPPORTS ====================

    # ==================== MODULES ROUTES ====================
    # Les routes métier sont enregistrées ici. Les modules reçoivent le contexte
    # courant afin de préserver les mêmes tables, helpers et contrôles d'accès.
    try:
        from .auth import register_auth_routes
        from .patients import register_patient_routes
        from .workflow import register_workflow_routes
        from .pharmacy import register_pharmacy_routes
        from .laboratory import register_laboratory_routes
        from .billing_api import register_billing_routes
        from .maternity import register_maternity_routes
        from .reports import register_reports_routes
        from .doctor import register_doctor_routes
        from .medical import register_medical_routes
        from .admin import register_admin_routes
        from .pediatrics import register_pediatrics_routes
    except ImportError:
        from auth import register_auth_routes
        from patients import register_patient_routes
        from workflow import register_workflow_routes
        from pharmacy import register_pharmacy_routes
        from laboratory import register_laboratory_routes
        from billing_api import register_billing_routes
        from maternity import register_maternity_routes
        from reports import register_reports_routes
        from doctor import register_doctor_routes
        from medical import register_medical_routes
        from admin import register_admin_routes
        from pediatrics import register_pediatrics_routes

    register_auth_routes(app, fast_json=fast_json, supabase=supabase, tables=TABLES,
                         roles=ROLES, now_iso=now_iso, create_token=create_token,
                         token_required=token_required, add_audit=add_audit,
                         invalidate_cache=invalidate_cache)
    register_patient_routes(app, supabase=supabase, tables=TABLES, roles=ROLES,
                            fast_json=fast_json, to_int=to_int, optional_date=optional_date,
                            now_iso=now_iso, cached=cached, roles_required=roles_required,
                            compatible_insert=compatible_insert, invalidate_cache=invalidate_cache,
                            add_audit=add_audit, hospital_patient_id=hospital_patient_id,
                            enrich_patient_identifier=enrich_patient_identifier,
                            enrich_patient_identifiers=enrich_patient_identifiers,
                            add_pregnancy_flags=add_pregnancy_flags, is_female=is_female,
                            filter_patients_for_role=filter_patients_for_role,
                            can_access_patient_record=can_access_patient_record,
                            allowed_statuses=ALLOWED_STATUSES,
                            generate_barcode_svg=generate_barcode_svg,
                            generate_qr_code_data=generate_qr_code_data)
    register_workflow_routes(app, runtime=globals())
    register_pharmacy_routes(app, runtime=globals())
    register_laboratory_routes(app, runtime=globals())
    register_billing_routes(app, runtime=globals())
    register_maternity_routes(app, runtime=globals())
    register_reports_routes(app, runtime=globals())
    register_doctor_routes(app, runtime=globals())
    register_medical_routes(app, runtime=globals())
    register_admin_routes(app, runtime=globals())
    register_pediatrics_routes(app, runtime=globals())


    if __name__ == "__main__":
        app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True)

    app.register_blueprint(ai)
