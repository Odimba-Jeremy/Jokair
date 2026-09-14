"""Routes du module médecin : rendez-vous et ordonnances."""

from flask import Blueprint


def register_doctor_routes(app, *, runtime):
    globals().update(runtime)
    doctor = Blueprint("doctor", __name__)

    @app.route("/api/appointments", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(timeout=60)
    def get_appointments():
        status = request.args.get("status")
        patient_id = request.args.get("patient_id")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        limit = min(max(to_int(request.args.get("limit"), 100), 1), 500)
        offset = to_int(request.args.get("offset"), 0)
        query = supabase.table(TABLES["appointments"]).select("*")
        if status:
            query = query.eq("status", status)
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        if date_from:
            query = query.gte("date", date_from)
        if date_to:
            query = query.lte("date", date_to)
        result = query.order("date", desc=True).limit(limit).offset(offset).execute()
        appointments = filter_appointments_for_role(result.data or [])
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        for apt in appointments:
            apt["patient_name"] = patient_map.get(apt.get("patient_id"), "Inconnu")
        return jsonify(appointments)

    @app.route("/api/appointments", methods=["POST"])
    @roles_required("super_admin", "docteur", "infirmier", "reception")
    def create_appointment():
        data = fast_json()
        required = ["patient_id", "date", "type"]
        for field in required:
            if not data.get(field):
                return jsonify({"error": f"Champ {field} requis"}), 422
        appointment = {
            "patient_id": to_int(data.get("patient_id")),
            "date": data.get("date"),
            "type": data.get("type"),
            "duration": to_int(data.get("duration"), 30),
            "notes": data.get("notes", ""),
            "status": normalize_status(data.get("status", "scheduled"), ["scheduled", "arrived", "in_consultation", "completed", "cancelled"], "scheduled"),
            "priority": normalize_status(data.get("priority", "normal"), ["normal", "urgent"], "normal"),
            "doctor_id": data.get("doctor_id") or g.current_user["id"],
            "doctor_name": data.get("doctor_name") or g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["appointments"], appointment)
        add_audit("CREATE", "appointment", f"RDV #{result.data[0]['id']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @app.route("/api/appointments/<int:appointment_id>", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_appointment(appointment_id: int):
        result = supabase.table(TABLES["appointments"]).select("*").eq("id", appointment_id).execute()
        if not result.data:
            return jsonify({"error": "Rendez-vous introuvable"}), 404
        return jsonify(result.data[0])

    @app.route("/api/appointments/<int:appointment_id>", methods=["PUT"])
    @roles_required("super_admin", "docteur", "infirmier", "reception")
    def update_appointment(appointment_id: int):
        data = fast_json()
        allowed = ["date", "type", "duration", "status", "priority", "notes", "doctor_id", "doctor_name"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if "status" in updates:
            updates["status"] = normalize_status(updates["status"], ["scheduled", "arrived", "in_consultation", "completed", "cancelled"], "scheduled")
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["appointments"]).update(updates).eq("id", appointment_id).execute()
        if not result.data:
            return jsonify({"error": "Rendez-vous introuvable"}), 404
        add_audit("UPDATE", "appointment", f"RDV #{appointment_id} modifié", appointment_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/appointments/<int:appointment_id>", methods=["PATCH"])
    @roles_required("super_admin", "docteur", "infirmier", "reception")
    def patch_appointment(appointment_id: int):
        data = fast_json()
        allowed = ["status", "date", "type", "duration", "priority", "notes"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if "status" in updates:
            updates["status"] = normalize_status(updates["status"], ["scheduled", "arrived", "in_consultation", "completed", "cancelled"], "scheduled")
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["appointments"]).update(updates).eq("id", appointment_id).execute()
        if not result.data:
            return jsonify({"error": "Rendez-vous introuvable"}), 404
        add_audit("UPDATE", "appointment", f"RDV #{appointment_id} modifié (PATCH)", appointment_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/appointments/<int:appointment_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_appointment(appointment_id: int):
        supabase.table(TABLES["appointments"]).delete().eq("id", appointment_id).execute()
        add_audit("DELETE", "appointment", f"RDV #{appointment_id} supprimé", appointment_id)
        invalidate_cache()
        return jsonify({"message": "Rendez-vous supprimé"})

    # ==================== PRESCRIPTIONS ====================
    @app.route("/api/prescriptions", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(120)
    def get_prescriptions():
        result = supabase.table(TABLES["prescriptions"]).select("*").order("created_at", desc=True).execute()
        prescriptions = result.data
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        try:
            dispensed_rows = supabase.table("prescription_dispenses").select("prescription_id").execute().data or []
            dispensed_ids = {str(row.get("prescription_id")) for row in dispensed_rows}
        except Exception:
            dispensed_ids = set()
        for p in prescriptions:
            p["patient_name"] = patient_map.get(p.get("patient_id"), "Inconnu")
            if str(p.get("id")) in dispensed_ids:
                p["pharmacy_status"] = "dispensed"
            else:
                p["pharmacy_status"] = p.get("pharmacy_status") or "pending"
        return jsonify(prescriptions)

    @app.route("/api/prescriptions", methods=["POST"])
    @roles_required("super_admin", "docteur")
    def create_prescription():
        data = fast_json()
        if not data.get("patient_id") or not data.get("medication"):
            return jsonify({"error": "Patient et médicament requis"}), 422
        prescription = {
            "patient_id": to_int(data.get("patient_id")),
            "medication": data.get("medication"),
            "dosage": data.get("dosage", ""),
            "frequency": data.get("frequency", ""),
            "duration": data.get("duration", ""),
            "start_date": optional_date(data.get("start_date")),
            "end_date": optional_date(data.get("end_date")),
            "instructions": data.get("instructions", ""),
            "status": data.get("status", "active"),
            "pharmacy_status": data.get("pharmacy_status", "pending"),
            "invoiced": data.get("invoiced", False),
            "doctor_id": g.current_user["id"],
            "doctor_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["prescriptions"], prescription)
        add_audit("CREATE", "prescription", f"Prescription #{result.data[0]['id']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @app.route("/api/prescriptions/<int:prescription_id>", methods=["PUT"])
    @roles_required("super_admin", "docteur")
    def update_prescription(prescription_id: int):
        data = fast_json()
        allowed = ["medication", "dosage", "frequency", "duration", "start_date", "end_date", "instructions", "status", "invoiced"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        for date_field in ("start_date", "end_date"):
            if date_field in updates:
                updates[date_field] = optional_date(updates[date_field])
        updates["updated_at"] = now_iso()
        try:
            result = supabase.table(TABLES["prescriptions"]).update(updates).eq("id", prescription_id).execute()
        except Exception as exc:
            if not missing_schema_column(exc):
                raise
            result = supabase.table(TABLES["prescriptions"]).update({"status": "completed", "updated_at": now_iso()}).eq("id", prescription_id).execute()
        if not result.data:
            return jsonify({"error": "Prescription introuvable"}), 404
        add_audit("UPDATE", "prescription", f"Prescription #{prescription_id} modifiée", prescription_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/prescriptions/<int:prescription_id>", methods=["PATCH"])
    @roles_required("super_admin", "docteur", "pharmacie")
    def patch_prescription(prescription_id: int):
        data = fast_json()
        allowed = ["status", "pharmacy_status", "invoiced"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["prescriptions"]).update(updates).eq("id", prescription_id).execute()
        if not result.data:
            return jsonify({"error": "Prescription introuvable"}), 404
        add_audit("UPDATE", "prescription", f"Prescription #{prescription_id} patchée", prescription_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/prescriptions/<int:prescription_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_prescription(prescription_id: int):
        supabase.table(TABLES["prescriptions"]).delete().eq("id", prescription_id).execute()
        add_audit("DELETE", "prescription", f"Prescription #{prescription_id} supprimée", prescription_id)
        invalidate_cache()
        return jsonify({"message": "Prescription supprimée"})

    @app.route("/api/prescriptions/<int:prescription_id>/dispense", methods=["POST"])
    @roles_required("super_admin", "pharmacie")
    def dispense_prescription(prescription_id: int):
        data = fast_json()
        prescription = supabase.table(TABLES["prescriptions"]).select("*").eq("id", prescription_id).execute()
        if not prescription.data:
            return jsonify({"error": "Prescription introuvable"}), 404
        row = prescription.data[0]
        amount = round(to_float(data.get("amount"), 0), 2)
        updates = {
            "pharmacy_status": "dispensed",
            "dispensed_at": now_iso(),
            "dispensed_by": g.current_user["id"],
            "dispensed_by_name": g.current_user["name"],
            "updated_at": now_iso()
        }
        result = supabase.table(TABLES["prescriptions"]).update(updates).eq("id", prescription_id).execute()
        invoice = None
        if amount > 0:
            line = add_patient_account_line(to_int(row.get("patient_id")), "medicament", f"Prescription: {row.get('medication', '')}", amount, "prescription", prescription_id)
            invoice = create_service_invoice(to_int(row.get("patient_id")), f"Médicament: {row.get('medication', '')}", amount, "prescription", prescription_id, line)
        compatible_insert("prescription_dispenses", {
            "prescription_id": prescription_id,
            "patient_id": row.get("patient_id"),
            "amount": amount,
            "notes": data.get("notes", ""),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        })
        add_audit("UPDATE", "prescription", f"Prescription #{prescription_id} delivree", prescription_id)
        invalidate_cache()
        response = result.data[0] if result.data else updates
        response["invoice"] = invoice
        return jsonify(response)

    # ==================== PRESCRIPTION PDF ====================
    @app.route("/api/prescriptions/<int:prescription_id>/ordonnance-pdf", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_ordonnance_pdf(prescription_id: int):
        """Génère un PDF de l'ordonnance"""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from io import BytesIO
        
            prescription = supabase.table(TABLES["prescriptions"]).select("*").eq("id", prescription_id).execute()
            if not prescription.data:
                return jsonify({"error": "Prescription introuvable"}), 404
        
            p = prescription.data[0]
            patient = supabase.table(TABLES["patients"]).select("full_name,phone").eq("id", p.get("patient_id")).execute()
            patient_name = patient.data[0]["full_name"] if patient.data else "Inconnu"
        
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=A4)
            width, height = A4
        
            c.setFont("Helvetica-Bold", 16)
            c.drawString(50, height - 50, "ORDONNANCE MÉDICALE")
            c.setFont("Helvetica", 12)
            c.drawString(50, height - 80, f"Patient: {patient_name}")
            c.drawString(50, height - 100, f"Médicament: {p.get('medication', '')}")
            c.drawString(50, height - 120, f"Dosage: {p.get('dosage', '')}")
            c.drawString(50, height - 140, f"Fréquence: {p.get('frequency', '')}")
            c.drawString(50, height - 160, f"Durée: {p.get('duration', '')}")
            c.drawString(50, height - 180, f"Instructions: {p.get('instructions', '')}")
            c.drawString(50, height - 200, f"Médecin: {p.get('doctor_name', '')}")
            c.drawString(50, height - 220, f"Date: {p.get('created_at', '')[:10]}")
        
            c.save()
            buffer.seek(0)
        
            return Response(buffer.getvalue(), mimetype='application/pdf', 
                           headers={"Content-Disposition": f"attachment;filename=ordonnance_{prescription_id}.pdf"})
        except ImportError:
            return jsonify({"error": "Bibliothèque reportlab non installée"}), 500

    # ==================== LABORATORY ====================

    app.register_blueprint(doctor)
