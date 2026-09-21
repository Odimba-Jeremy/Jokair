"""Routes isolées pour le prototype de l'application patient.

Le prototype accepte l'identifiant hospitalier IH-USD-00001. Il délivre un jeton
signé limité au patient connecté, sans modifier l'authentification du personnel.
"""
from __future__ import annotations

import re
from functools import wraps

from flask import Blueprint, jsonify, request
from itsdangerous import BadSignature, SignatureExpired


def register_patient_portal_routes(app, *, supabase, tables, serializer,
                                   hospital_patient_id, enrich_patient_identifier):
    portal = Blueprint("patient_portal", __name__)

    def portal_token(patient_id: int) -> str:
        return serializer.dumps({"scope": "patient_portal", "patient_id": patient_id})

    def portal_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
            if not token:
                return jsonify({"error": "Connexion patient requise"}), 401
            try:
                payload = serializer.loads(token, max_age=8 * 60 * 60)
            except (BadSignature, SignatureExpired):
                return jsonify({"error": "Session patient expirée ou invalide"}), 401
            if payload.get("scope") != "patient_portal" or not str(payload.get("patient_id", "")).isdigit():
                return jsonify({"error": "Session patient invalide"}), 401
            request.patient_id = int(payload["patient_id"])
            return view(*args, **kwargs)
        return wrapped

    def current_patient():
        result = supabase.table(tables["patients"]).select("*").eq("id", request.patient_id).execute()
        return result.data[0] if result.data else None

    def safe_rows(table: str, patient_id: int, order_field: str = "created_at"):
        try:
            return supabase.table(table).select("*").eq("patient_id", patient_id).order(order_field, desc=True).execute().data or []
        except Exception as exc:
            print(f"Portail patient : lecture {table} impossible : {exc}")
            return []

    @portal.post("/api/patient-portal/login")
    def login():
        data = request.get_json(silent=True) or {}
        identifier = str(data.get("identifier") or "").strip().upper()
        match = re.fullmatch(r"IH-USD-0*(\d+)", identifier)
        if not match:
            return jsonify({"error": "Utilisez l'identifiant figurant sur votre fiche : IH-USD-00001"}), 422

        patient_id = int(match.group(1))
        result = supabase.table(tables["patients"]).select("*").eq("id", patient_id).execute()
        if not result.data:
            return jsonify({"error": "Dossier patient introuvable"}), 404
        patient = enrich_patient_identifier(result.data[0])
        return jsonify({
            "token": portal_token(patient_id),
            "patient": patient,
            "expires_in": 8 * 60 * 60,
        })

    @portal.get("/api/patient-portal/me")
    @portal_required
    def me():
        patient = current_patient()
        if not patient:
            return jsonify({"error": "Dossier patient introuvable"}), 404
        return jsonify(enrich_patient_identifier(patient))

    @portal.get("/api/patient-portal/dashboard")
    @portal_required
    def dashboard():
        patient = current_patient()
        if not patient:
            return jsonify({"error": "Dossier patient introuvable"}), 404
        patient_id = request.patient_id
        appointments = safe_rows(tables["appointments"], patient_id, "date")
        prescriptions = safe_rows(tables["prescriptions"], patient_id)
        labs = safe_rows(tables["lab_tests"], patient_id)
        invoices = safe_rows(tables["billing"], patient_id)
        consultations = safe_rows("medical_consultations", patient_id)
        return jsonify({
            "patient": enrich_patient_identifier(patient),
            "appointments": appointments,
            "prescriptions": prescriptions,
            "labs": labs,
            "invoices": invoices,
            "consultations": consultations,
        })

    @portal.get("/api/patient-portal/appointments")
    @portal_required
    def appointments():
        return jsonify(safe_rows(tables["appointments"], request.patient_id, "date"))

    @portal.get("/api/patient-portal/prescriptions")
    @portal_required
    def prescriptions():
        return jsonify(safe_rows(tables["prescriptions"], request.patient_id))

    @portal.get("/api/patient-portal/labs")
    @portal_required
    def labs():
        return jsonify(safe_rows(tables["lab_tests"], request.patient_id))

    @portal.get("/api/patient-portal/invoices")
    @portal_required
    def invoices():
        return jsonify(safe_rows(tables["billing"], request.patient_id))

    app.register_blueprint(portal)

