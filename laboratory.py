"""Module laboratoire I-HUB : examens, résultats, prélèvements et stock."""
from flask import Blueprint


def register_laboratory_routes(app, *, runtime):
    globals().update(runtime)
    laboratory = Blueprint("laboratory", __name__)

    @laboratory.route("/lab/report/pdf/<int:test_id>", methods=["GET"])
    @token_required
    @roles_required("super_admin", "laboratoire", "docteur")
    def generate_lab_pdf(test_id: int):
        """Génère un PDF pour le résultat de laboratoire indiqué."""
        test_res = supabase.table("lab_tests").select("*").eq("id", test_id).execute()
        if not test_res.data:
            return jsonify({"error": "Test de laboratoire introuvable"}), 404
        test = test_res.data[0]
        try:
            from io import BytesIO
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=A4)
            c.setFont("Helvetica", 12)
            c.drawString(50, 800, f"Rapport de laboratoire – Test ID: {test_id}")
            c.drawString(50, 780, f"Patient ID: {test.get('patient_id')}")
            c.drawString(50, 760, f"Type: {test.get('test_type')}")
            c.drawString(50, 740, f"Résultat: {test.get('result')}")
            c.drawString(50, 720, f"Observations: {test.get('observations')}")
            c.showPage()
            c.save()
            pdf = buffer.getvalue()
            buffer.close()
            return Response(pdf, mimetype='application/pdf',
                            headers={"Content-Disposition": f"inline; filename=lab_report_{test_id}.pdf"})
        except Exception as e:
            return jsonify({"error": f"Génération PDF échouée: {e}"}), 500

    # ==================== APPOINTMENTS ====================

    @laboratory.route("/api/laboratory/tests", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(60)
    def get_lab_tests():
        status = request.args.get("status")
        patient_id = request.args.get("patient_id")
        query = supabase.table(TABLES["lab_tests"]).select("*")
        if g.current_user.get("role") == "docteur":
            query = query.eq("requested_by", g.current_user.get("id"))
        if status:
            query = query.eq("status", status)
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        result = query.order("request_date", desc=True).execute()
        tests = result.data
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        for t in tests:
            t["patient_name"] = patient_map.get(t.get("patient_id"), "Inconnu")
        return jsonify(tests)

    @laboratory.route("/api/laboratory/tests", methods=["POST"])
    @roles_required("super_admin", "docteur", "laboratoire")
    def create_lab_test():
        data = fast_json()
        if not data.get("patient_id") or not data.get("test_type"):
            return jsonify({"error": "Patient et type d'analyse requis"}), 422
        test = {
            "patient_id": to_int(data.get("patient_id")),
            "test_type": data.get("test_type"),
            "notes": data.get("notes", ""),
            "status": "pending",
            "priority": normalize_status(data.get("priority", "normal"), ["normal", "urgent"], "normal"),
            "request_date": now_iso(),
            "requested_by": g.current_user["id"],
            "requested_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["lab_tests"], test)
        lab_fee = to_float(data.get("amount"), get_tariff_amount("analyse", data.get("test_type", ""), 0))
        invoice = None
        if lab_fee > 0:
            line = add_patient_account_line(to_int(data.get("patient_id")), "analyse", f"Analyse: {data.get('test_type')}", lab_fee, "lab_test", result.data[0].get("id"))
            invoice = create_service_invoice(to_int(data.get("patient_id")), f"Analyse: {data.get('test_type')}", lab_fee, "lab_test", result.data[0].get("id"), line)
        add_audit("CREATE", "lab_test", f"Analyse #{result.data[0]['id']}", result.data[0]["id"])
        invalidate_cache()
        response = dict(result.data[0])
        response["invoice"] = invoice
        return jsonify(response), 201

    @laboratory.route("/api/laboratory/tests/<int:test_id>", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_lab_test(test_id: int):
        result = supabase.table(TABLES["lab_tests"]).select("*").eq("id", test_id).execute()
        if not result.data:
            return jsonify({"error": "Analyse introuvable"}), 404
        return jsonify(result.data[0])

    @laboratory.route("/api/laboratory/tests/<int:test_id>", methods=["PUT"])
    @roles_required("super_admin", "laboratoire")
    def update_lab_test(test_id: int):
        data = fast_json()
        allowed = ["test_type", "notes", "priority"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["lab_tests"]).update(updates).eq("id", test_id).execute()
        if not result.data:
            return jsonify({"error": "Analyse introuvable"}), 404
        add_audit("UPDATE", "lab_test", f"Analyse #{test_id} modifiée", test_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @laboratory.route("/api/laboratory/tests/<int:test_id>", methods=["PATCH"])
    @roles_required("super_admin", "laboratoire")
    def patch_lab_test(test_id: int):
        data = fast_json()
        allowed = ["status", "num_prelevement", "preleve_at"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        if updates.get("status") == "preleve":
            updates["num_prelevement"] = updates.get("num_prelevement") or f"PR-{datetime.now().strftime('%Y-%m-%d')}-{str(test_id).zfill(4)}"
            updates["preleve_at"] = updates.get("preleve_at") or now_iso()
        result = supabase.table(TABLES["lab_tests"]).update(updates).eq("id", test_id).execute()
        if not result.data:
            return jsonify({"error": "Analyse introuvable"}), 404
        add_audit("UPDATE", "lab_test", f"Analyse #{test_id} patchée", test_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @laboratory.route("/api/laboratory/tests/<int:test_id>/result", methods=["PUT"])
    @roles_required("super_admin", "laboratoire")
    def save_test_result(test_id: int):
        data = fast_json()
        updates = {
            "result": data.get("result", ""),
            "observations": data.get("observations", ""),
            "status": "completed",
            "completed_date": now_iso(),
            "technician_name": g.current_user["name"],
            "updated_at": now_iso()
        }
        result = supabase.table(TABLES["lab_tests"]).update(updates).eq("id", test_id).execute()
        if not result.data:
            return jsonify({"error": "Analyse introuvable"}), 404
        add_audit("UPDATE", "lab_test", f"Résultat ajouté analyse #{test_id}", test_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @laboratory.route("/api/laboratory/tests/<int:test_id>/result", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_test_result(test_id: int):
        result = supabase.table(TABLES["lab_tests"]).select("*").eq("id", test_id).execute()
        if not result.data:
            return jsonify({"error": "Analyse introuvable"}), 404
        return jsonify(result.data[0])

    @laboratory.route("/api/laboratory/results", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(120)
    def get_lab_results():
        date_from = request.args.get("from_date")
        date_to = request.args.get("to_date")
        patient_id = to_int(request.args.get("patient_id"))
        query = supabase.table(TABLES["lab_tests"]).select("*").eq("status", "completed")
        if patient_id:
            query = query.eq("patient_id", patient_id)
        if date_from:
            query = query.gte("completed_date", date_from)
        if date_to:
            query = query.lte("completed_date", date_to)
        result = query.order("completed_date", desc=True).execute()
        tests = result.data
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        for t in tests:
            t["patient_name"] = patient_map.get(t.get("patient_id"), "Inconnu")
        return jsonify(tests)

    @laboratory.route("/api/laboratory/tests/<int:test_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_lab_test(test_id: int):
        supabase.table(TABLES["lab_tests"]).delete().eq("id", test_id).execute()
        add_audit("DELETE", "lab_test", f"Analyse #{test_id} supprimée", test_id)
        invalidate_cache()
        return jsonify({"message": "Analyse supprimée"})

    # ==================== LABORATORY RESULT PDF ====================

    @laboratory.route("/api/laboratory/tests/<int:test_id>/result-pdf", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_lab_result_pdf(test_id: int):
        """Génère un PDF du résultat d'analyse"""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from io import BytesIO
            
            test = supabase.table(TABLES["lab_tests"]).select("*").eq("id", test_id).execute()
            if not test.data:
                return jsonify({"error": "Analyse introuvable"}), 404
            
            t = test.data[0]
            patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", t.get("patient_id")).execute()
            patient_name = patient.data[0]["full_name"] if patient.data else "Inconnu"
            
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=A4)
            width, height = A4
            
            c.setFont("Helvetica-Bold", 16)
            c.drawString(50, height - 50, "RÉSULTAT D'ANALYSE")
            c.setFont("Helvetica", 12)
            c.drawString(50, height - 80, f"Patient: {patient_name}")
            c.drawString(50, height - 100, f"Type: {t.get('test_type', '')}")
            c.drawString(50, height - 120, f"Résultat: {t.get('result', '')}")
            c.drawString(50, height - 140, f"Observations: {t.get('observations', '')}")
            c.drawString(50, height - 160, f"Technicien: {t.get('technician_name', '')}")
            c.drawString(50, height - 180, f"Date: {t.get('completed_date', '')[:10]}")
            
            c.save()
            buffer.seek(0)
            
            return Response(buffer.getvalue(), mimetype='application/pdf',
                           headers={"Content-Disposition": f"attachment;filename=resultat_{test_id}.pdf"})
        except ImportError:
            return jsonify({"error": "Bibliothèque reportlab non installée"}), 500

    # ==================== LABORATORY STOCK ====================

    @laboratory.route("/api/laboratory/stock", methods=["GET"])
    @roles_required("super_admin", "laboratoire")
    def get_lab_stock():
        result = supabase.table("laboratory_stock").select("*").order("name").execute()
        return jsonify(result.data or [])

    @laboratory.route("/api/laboratory/stock", methods=["POST"])
    @roles_required("super_admin", "laboratoire")
    def create_lab_stock_item():
        data = fast_json()
        if not data.get("name"):
            return jsonify({"error": "Nom du produit requis"}), 422
        item = {
            "name": data.get("name"),
            "category": data.get("category", "Réactif"),
            "quantity": max(0, to_int(data.get("quantity"), 0)),
            "threshold": max(0, to_int(data.get("threshold"), 5)),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("laboratory_stock", item)
        add_audit("CREATE", "lab_stock", f"Produit: {data['name']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @laboratory.route("/api/laboratory/stock/<int:item_id>", methods=["PUT"])
    @roles_required("super_admin", "laboratoire")
    def update_lab_stock_item(item_id: int):
        data = fast_json()
        allowed = ["name", "category", "quantity", "threshold"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        updates["updated_at"] = now_iso()
        result = supabase.table("laboratory_stock").update(updates).eq("id", item_id).execute()
        if not result.data:
            return jsonify({"error": "Produit introuvable"}), 404
        add_audit("UPDATE", "lab_stock", f"Produit #{item_id} modifié", item_id)
        invalidate_cache()
        return jsonify(result.data[0])

    # ==================== LABORATORY PRELEVEMENTS ====================

    @laboratory.route("/api/laboratory/prelevements", methods=["GET"])
    @roles_required("super_admin", "laboratoire")
    def get_lab_prelevements():
        test_id = to_int(request.args.get("test_id"))
        patient_id = to_int(request.args.get("patient_id"))
        query = supabase.table("laboratory_prelevements").select("*")
        if test_id:
            query = query.eq("test_id", test_id)
        if patient_id:
            query = query.eq("patient_id", patient_id)
        result = query.order("created_at", desc=True).execute()
        prelevements = result.data or []
        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
        for p in prelevements:
            p["patient_name"] = patient_map.get(p.get("patient_id"), "Inconnu")
        return jsonify(prelevements)

    @laboratory.route("/api/laboratory/prelevements", methods=["POST"])
    @roles_required("super_admin", "laboratoire")
    def create_lab_prelevement():
        data = fast_json()
        if not data.get("test_id") or not data.get("patient_id"):
            return jsonify({"error": "test_id et patient_id requis"}), 422
        prelevement = {
            "test_id": data.get("test_id"),
            "patient_id": data.get("patient_id"),
            "num_prelevement": data.get("num_prelevement", f"PR-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"),
            "type": data.get("type") or data.get("type_prelevement", ""),
            "type_prelevement": data.get("type_prelevement") or data.get("type", ""),
            "contenant": data.get("contenant", ""),
            "volume": data.get("volume"),
            "nombre_echantillons": max(1, to_int(data.get("nombre_echantillons"), 1)),
            "observations": data.get("observations") or data.get("notes", ""),
            "notes": data.get("notes") or data.get("observations", ""),
            "preleve_at": data.get("preleve_at") or now_iso(),
            "preleve_par": g.current_user.get("id"),
            "preleve_par_name": g.current_user.get("name") or g.current_user.get("email"),
            "created_at": now_iso()
        }
        result = compatible_insert("laboratory_prelevements", prelevement)
        add_audit("CREATE", "lab_prelevement", f"Prélèvement #{result.data[0]['id']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    # ==================== LABORATORY PARAMS ====================
    LAB_PARAMS = {
        "Hémogramme": [
            {"name": "GB", "label": "Globules blancs", "unit": "G/L", "ref_min": 4.0, "ref_max": 10.0, "decimal": 1},
            {"name": "GR", "label": "Globules rouges", "unit": "T/L", "ref_min": 4.2, "ref_max": 5.8, "decimal": 2},
            {"name": "Hb", "label": "Hémoglobine", "unit": "g/dL", "ref_min": 12.0, "ref_max": 16.0, "decimal": 1},
            {"name": "Ht", "label": "Hématocrite", "unit": "%", "ref_min": 37, "ref_max": 47, "decimal": 0},
            {"name": "VGM", "label": "VGM", "unit": "fL", "ref_min": 80, "ref_max": 96, "decimal": 0},
            {"name": "TCMH", "label": "TCMH", "unit": "pg", "ref_min": 27, "ref_max": 32, "decimal": 1},
            {"name": "CCMH", "label": "CCMH", "unit": "g/dL", "ref_min": 32, "ref_max": 36, "decimal": 1},
            {"name": "Plaquettes", "label": "Plaquettes", "unit": "G/L", "ref_min": 150, "ref_max": 400, "decimal": 0}
        ],
        "Bilan hépatique": [
            {"name": "ALAT", "label": "ALAT", "unit": "U/L", "ref_min": 5, "ref_max": 45, "decimal": 0},
            {"name": "ASAT", "label": "ASAT", "unit": "U/L", "ref_min": 5, "ref_max": 40, "decimal": 0},
            {"name": "GGT", "label": "Gamma-GT", "unit": "U/L", "ref_min": 5, "ref_max": 50, "decimal": 0},
            {"name": "PAL", "label": "Phosphatases alcalines", "unit": "U/L", "ref_min": 30, "ref_max": 120, "decimal": 0},
            {"name": "Bilirubine_T", "label": "Bilirubine totale", "unit": "mg/dL", "ref_min": 0.2, "ref_max": 1.2, "decimal": 1},
            {"name": "Bilirubine_D", "label": "Bilirubine directe", "unit": "mg/dL", "ref_min": 0, "ref_max": 0.3, "decimal": 1}
        ],
        "Bilan rénal": [
            {"name": "Uree", "label": "Urée", "unit": "g/L", "ref_min": 0.2, "ref_max": 0.5, "decimal": 2},
            {"name": "Creatinine", "label": "Créatinine", "unit": "mg/L", "ref_min": 6, "ref_max": 13, "decimal": 1},
            {"name": "Acide_urique", "label": "Acide urique", "unit": "mg/L", "ref_min": 25, "ref_max": 80, "decimal": 1}
        ],
        "Bilan lipidique": [
            {"name": "Cholest_total", "label": "Cholestérol total", "unit": "g/L", "ref_min": 1.4, "ref_max": 2.5, "decimal": 2},
            {"name": "Triglycerides", "label": "Triglycérides", "unit": "g/L", "ref_min": 0.4, "ref_max": 1.8, "decimal": 2},
            {"name": "HDL", "label": "HDL-Cholestérol", "unit": "g/L", "ref_min": 0.4, "ref_max": 0.7, "decimal": 2},
            {"name": "LDL", "label": "LDL-Cholestérol", "unit": "g/L", "ref_min": 0.6, "ref_max": 1.6, "decimal": 2}
        ],
        "Analyse d'urine": [
            {"name": "pH", "label": "pH", "unit": "", "ref_min": 4.5, "ref_max": 8.0, "decimal": 1},
            {"name": "Densite", "label": "Densité", "unit": "", "ref_min": 1.005, "ref_max": 1.030, "decimal": 3},
            {"name": "Proteines", "label": "Protéines", "unit": "g/L", "ref_min": 0, "ref_max": 0.15, "decimal": 2},
            {"name": "Glucose", "label": "Glucose", "unit": "mmol/L", "ref_min": 0, "ref_max": 0.8, "decimal": 1},
            {"name": "Cetones", "label": "Cétones", "unit": "", "ref_min": None, "ref_max": None, "decimal": 0},
            {"name": "Nitrites", "label": "Nitrites", "unit": "", "ref_min": None, "ref_max": None, "decimal": 0},
            {"name": "Leucocytes", "label": "Leucocytes", "unit": "/µL", "ref_min": 0, "ref_max": 5, "decimal": 0},
            {"name": "Hematies", "label": "Hématies", "unit": "/µL", "ref_min": 0, "ref_max": 3, "decimal": 0}
        ]
    }

    @laboratory.route("/api/laboratory/params/<string:test_type>", methods=["GET"])
    @roles_required("super_admin", "laboratoire")
    def get_lab_params(test_type: str):
        return jsonify(LAB_PARAMS.get(test_type, []))

    # ==================== CARE LOGS ====================

    app.register_blueprint(laboratory)
