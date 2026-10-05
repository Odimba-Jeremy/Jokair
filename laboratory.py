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

        patient_id = to_int(data.get("patient_id"))
        test_type = str(data.get("test_type") or "").strip()

        # Verrou Anti-Doublon Analyse : vérifier si l'examen n'a pas déjà été demandé dans les 24h
        yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        existing_test = supabase.table(TABLES["lab_tests"]).select("id,status,created_at").eq("patient_id", patient_id).eq("test_type", test_type).gte("created_at", yesterday).in_("status", ["pending", "in_progress", "preleve", "completed"]).execute().data or []
        if existing_test:
            return jsonify({"error": f"L'analyse '{test_type}' a déjà été prescrite pour ce patient au cours des dernières 24h (#{existing_test[0]['id']})"}), 409

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
        # L'analyse est débitée au compte patient. La facture officielle est
        # créée uniquement au règlement du compte, jamais à la prescription.
        invoice = None
        if lab_fee > 0:
            add_patient_account_line(to_int(data.get("patient_id")), "analyse", f"Analyse: {data.get('test_type')}", lab_fee, "lab_test", result.data[0].get("id"))
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
        # Verrou Lecture Seule : interdire la modification d'un résultat déjà validé sauf super_admin
        existing = supabase.table(TABLES["lab_tests"]).select("status,result").eq("id", test_id).execute().data or []
        if existing and existing[0].get("status") == "completed" and g.current_user.get("role") != "super_admin":
            return jsonify({"error": "Ce résultat d'analyse a déjà été validé et est verrouillé en lecture seule"}), 403
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

    # ==================== CATALOGUE LABORATOIRE 5 DÉPARTEMENTS ====================
    LAB_CATALOG = {
        "hematologie": {
            "label": "1. Hématologie",
            "exams": {
                "VS": {
                    "code": "VS",
                    "name": "VS",
                    "full_name": "VS (Vitesse de sédimentation)",
                    "aliases": ["vs", "vitesse de sedimentation", "vitesse de sédimentation"],
                    "sample": "Sang total citraté",
                    "params": [
                        {"code": "vs_1h", "name": "VS 1ère heure", "unit": "mm", "ref_min": 0, "ref_max": 15, "ref_text": "< 15 mm (H) / < 20 mm (F)", "type": "number"},
                        {"code": "vs_2h", "name": "VS 2ème heure", "unit": "mm", "ref_min": 0, "ref_max": 30, "ref_text": "< 25 mm (H) / < 30 mm (F)", "type": "number"}
                    ]
                },
                "GB": {
                    "code": "GB",
                    "name": "GB",
                    "full_name": "GB (Globules blancs)",
                    "aliases": ["gb", "globules blancs", "leucocytes"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "gb", "name": "GB", "unit": "G/L", "ref_min": 4.0, "ref_max": 10.0, "ref_text": "4.0 - 10.0 G/L (4000 - 10000 /mm³)", "type": "number", "decimal": 1}
                    ]
                },
                "Hb": {
                    "code": "Hb",
                    "name": "Hb",
                    "full_name": "Hb (Hémoglobine)",
                    "aliases": ["hb", "hemoglobine", "hémoglobine"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "hb", "name": "Hb", "unit": "g/dL", "ref_min": 12.0, "ref_max": 17.5, "ref_text": "12.0 - 16.0 (F) / 13.0 - 17.5 (H) g/dL", "type": "number", "decimal": 1}
                    ]
                },
                "NFS": {
                    "code": "NFS — Formule leucocytaire",
                    "name": "NFS — Formule leucocytaire",
                    "full_name": "NFS — Formule leucocytaire",
                    "aliases": ["nfs — formule leucocytaire", "nfs", "formule leucocytaire", "nfs formule leucocytaire"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "neutrophiles", "name": "Polynucléaires neutrophiles", "unit": "%", "ref_min": 40.0, "ref_max": 75.0, "ref_text": "40 - 75 %", "type": "number", "decimal": 1},
                        {"code": "lymphocytes", "name": "Lymphocytes", "unit": "%", "ref_min": 20.0, "ref_max": 45.0, "ref_text": "20 - 45 %", "type": "number", "decimal": 1},
                        {"code": "monocytes", "name": "Monocytes", "unit": "%", "ref_min": 2.0, "ref_max": 10.0, "ref_text": "2 - 10 %", "type": "number", "decimal": 1},
                        {"code": "eosinophiles", "name": "Polynucléaires éosinophiles", "unit": "%", "ref_min": 1.0, "ref_max": 5.0, "ref_text": "1 - 5 %", "type": "number", "decimal": 1},
                        {"code": "basophiles", "name": "Polynucléaires basophiles", "unit": "%", "ref_min": 0.0, "ref_max": 1.0, "ref_text": "0 - 1 %", "type": "number", "decimal": 1}
                    ]
                },
                "HCT": {
                    "code": "HCT",
                    "name": "HCT",
                    "full_name": "HCT (Hématocrite)",
                    "aliases": ["hct", "hematocrite", "hématocrite"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "hct", "name": "HCT", "unit": "%", "ref_min": 36.0, "ref_max": 52.0, "ref_text": "36 - 46 % (F) / 40 - 52 % (H)", "type": "number", "decimal": 1}
                    ]
                },
                "VGM": {
                    "code": "VGM",
                    "name": "VGM",
                    "full_name": "VGM (Volume globulaire moyen)",
                    "aliases": ["vgm"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "vgm", "name": "VGM", "unit": "fL", "ref_min": 80.0, "ref_max": 100.0, "ref_text": "80 - 100 fL", "type": "number", "decimal": 1}
                    ]
                },
                "TCMH": {
                    "code": "TCMH",
                    "name": "TCMH",
                    "full_name": "TCMH (Teneur corpusculaire moyenne)",
                    "aliases": ["tcmh"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "tcmh", "name": "TCMH", "unit": "pg", "ref_min": 27.0, "ref_max": 32.0, "ref_text": "27 - 32 pg", "type": "number", "decimal": 1}
                    ]
                },
                "CCMH": {
                    "code": "CCMH",
                    "name": "CCMH",
                    "full_name": "CCMH (Concentration corpusculaire moyenne)",
                    "aliases": ["ccmh"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "ccmh", "name": "CCMH", "unit": "g/dL", "ref_min": 32.0, "ref_max": 36.0, "ref_text": "32 - 36 g/dL", "type": "number", "decimal": 1}
                    ]
                },
                "IDR-CV": {
                    "code": "IDR-CV",
                    "name": "IDR-CV",
                    "full_name": "IDR-CV",
                    "aliases": ["idr-cv", "idr_cv", "rdw"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "idr_cv", "name": "IDR-CV", "unit": "%", "ref_min": 11.5, "ref_max": 14.5, "ref_text": "11.5 - 14.5 %", "type": "number", "decimal": 1}
                    ]
                },
                "PLT": {
                    "code": "PLT",
                    "name": "PLT",
                    "full_name": "PLT (Plaquettes)",
                    "aliases": ["plt", "plaquettes"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "plt", "name": "PLT", "unit": "G/L", "ref_min": 150.0, "ref_max": 450.0, "ref_text": "150 - 450 G/L (150 000 - 450 000 /mm³)", "type": "number", "decimal": 0}
                    ]
                },
                "VPM": {
                    "code": "VPM",
                    "name": "VPM",
                    "full_name": "VPM (Volume plaquettaire moyen)",
                    "aliases": ["vpm", "mpv"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "vpm", "name": "VPM", "unit": "fL", "ref_min": 7.4, "ref_max": 10.4, "ref_text": "7.4 - 10.4 fL", "type": "number", "decimal": 1}
                    ]
                },
                "IDP": {
                    "code": "IDP",
                    "name": "IDP",
                    "full_name": "IDP (Indice de distribution plaquettaire)",
                    "aliases": ["idp", "pdw"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "idp", "name": "IDP", "unit": "%", "ref_min": 10.0, "ref_max": 18.0, "ref_text": "10 - 18 %", "type": "number", "decimal": 1}
                    ]
                },
                "PCT": {
                    "code": "PCT",
                    "name": "PCT",
                    "full_name": "PCT (Plaquettocrite)",
                    "aliases": ["pct"],
                    "sample": "Sang total EDTA",
                    "params": [
                        {"code": "pct", "name": "PCT", "unit": "%", "ref_min": 0.15, "ref_max": 0.50, "ref_text": "0.15 - 0.50 %", "type": "number", "decimal": 2}
                    ]
                },
                "P-TGP": {
                    "code": "P-TGP",
                    "name": "P-TGP",
                    "full_name": "P-TGP (Transaminases ALAT)",
                    "aliases": ["p-tgp", "tgp", "alat", "alt", "transaminases"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "tgp", "name": "P-TGP", "unit": "U/L", "ref_min": 0, "ref_max": 45, "ref_text": "< 45 U/L (H) / < 35 U/L (F)", "type": "number", "decimal": 0}
                    ]
                }
            }
        },
        "parasitologie": {
            "label": "2. Parasitologie",
            "exams": {
                "UC": {
                    "code": "UC",
                    "name": "UC",
                    "full_name": "UC (Uroculture)",
                    "aliases": ["uc", "uroculture", "examen cytobacteriologique des urines", "ecbu"],
                    "sample": "Urine",
                    "params": [
                        {"code": "aspect", "name": "Aspect de l'urine", "unit": "", "ref_text": "Limpide", "type": "select", "options": ["Limpide", "Trouble", "Légèrement trouble", "Hématique"]},
                        {"code": "leucocytes", "name": "Leucocyturie", "unit": "/champ", "ref_text": "< 5 / champ (< 10 000/mL)", "type": "text"},
                        {"code": "hematies", "name": "Hématurie", "unit": "/champ", "ref_text": "< 3 / champ (< 5 000/mL)", "type": "text"},
                        {"code": "germes", "name": "Culture & Germe", "unit": "", "ref_text": "Stérile (Absence de germes pathogènes)", "type": "text"},
                        {"code": "antibiogramme", "name": "Antibiogramme", "unit": "", "ref_text": "Sensibilité", "type": "textarea"}
                    ]
                },
                "SELLE": {
                    "code": "SELLE",
                    "name": "Selle à frais",
                    "full_name": "Selle à frais",
                    "aliases": ["selle à frais", "selle a frais", "selle", "examen parasitologique des selles"],
                    "sample": "Selles fraîches",
                    "params": [
                        {"code": "aspect", "name": "Consistance / Aspect", "unit": "", "ref_text": "Moulée", "type": "select", "options": ["Moulée", "Pâteuse", "Liquide", "Glaireuse", "Hémorragique"]},
                        {"code": "parasites", "name": "Kystes & Parasites", "unit": "", "ref_text": "Absence de kystes ni trophozoïtes", "type": "text"},
                        {"code": "oeufs", "name": "Œufs d'helminthes", "unit": "", "ref_text": "Absence d'œufs", "type": "text"},
                        {"code": "levures", "name": "Levures / Mycètes", "unit": "", "ref_text": "Absence", "type": "text"}
                    ]
                },
                "GE": {
                    "code": "GE",
                    "name": "GE",
                    "full_name": "GE (Goutte épaisse / Frottis)",
                    "aliases": ["ge", "goutte epaisse", "goutte épaisse", "frottis sanguin", "recherche hematozoaires"],
                    "sample": "Sang total",
                    "params": [
                        {"code": "resultat", "name": "Recherche d'hématozoaires", "unit": "", "ref_text": "Négatif (Absence de trophozoïtes)", "type": "select", "options": ["Négatif", "Positif"]},
                        {"code": "densite", "name": "Densité parasitaire", "unit": "trophozoïtes/µL", "ref_text": "0 / µL", "type": "text"},
                        {"code": "espece", "name": "Espèce plasmodiale", "unit": "", "ref_text": "—", "type": "select", "options": ["—", "Plasmodium falciparum", "Plasmodium vivax", "Plasmodium malariae", "Plasmodium ovale", "Infection mixte"]}
                    ]
                }
            }
        },
        "serologie": {
            "label": "3. Sérologie",
            "exams": {
                "CRP": {
                    "code": "CRP",
                    "name": "Protéine C-Réactive (CRP)",
                    "full_name": "Dosage quantitatif de la CRP",
                    "aliases": ["crp", "proteine c reactive", "protéine c-réactive"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "crp", "name": "CRP", "unit": "mg/L", "ref_min": 0.0, "ref_max": 6.0, "ref_text": "< 6.0 mg/L", "type": "number", "decimal": 1}
                    ]
                },
                "TDR_PALU": {
                    "code": "TDR_PALU",
                    "name": "Test Rapide Paludisme (TDR)",
                    "full_name": "Test de Diagnostic Rapide du Paludisme (Ag HRP2/pLDH)",
                    "aliases": ["tdr palu", "tdr paludisme", "tdr"],
                    "sample": "Sang total",
                    "params": [
                        {"code": "resultat", "name": "Résultat TDR", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Positif (P. falciparum)", "Positif (Pan-malaria)"]}
                    ]
                },
                "HIV": {
                    "code": "HIV",
                    "name": "HIV",
                    "full_name": "HIV (Test rapide VIH 1/2)",
                    "aliases": ["hiv", "vih", "serologie vih", "sérologie vih", "hiv 1/2"],
                    "sample": "Sérum / Sang total",
                    "params": [
                        {"code": "resultat", "name": "Sérologie VIH 1/2", "unit": "", "ref_text": "Non réactif", "type": "select", "options": ["Non réactif", "Réactif", "Indéterminé"]}
                    ]
                },
                "HBS": {
                    "code": "HBS",
                    "name": "HBS",
                    "full_name": "HBS (Ag HBs - Hépatite B)",
                    "aliases": ["hbs", "ag hbs", "hepatite b", "hépatite b"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "resultat", "name": "Antigène HBs", "unit": "", "ref_text": "Négatif (Non réactif)", "type": "select", "options": ["Négatif", "Positif"]}
                    ]
                },
                "HCV": {
                    "code": "HCV",
                    "name": "HCV",
                    "full_name": "HCV (Ac anti-VHC - Hépatite C)",
                    "aliases": ["hcv", "vhc", "ac anti-vhc", "hepatite c", "hépatite c"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "resultat", "name": "Anticorps anti-VHC", "unit": "", "ref_text": "Négatif (Non réactif)", "type": "select", "options": ["Négatif", "Positif"]}
                    ]
                },
                "SYPHILIS": {
                    "code": "Syphilis",
                    "name": "Syphilis",
                    "full_name": "Syphilis (RPR / VDRL / TPHA)",
                    "aliases": ["syphilis", "rpr", "vdrl", "tpha", "bw"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "resultat", "name": "Sérologie syphilitique", "unit": "", "ref_text": "Négatif (Non réactif)", "type": "select", "options": ["Négatif", "Positif"]},
                        {"code": "titre", "name": "Titre (si réactif)", "unit": "", "ref_text": "< 1/2", "type": "text"}
                    ]
                },
                "HCG": {
                    "code": "HCG",
                    "name": "HCG",
                    "full_name": "HCG (Test de grossesse)",
                    "aliases": ["hcg", "test de grossesse", "beta-hcg", "b-hcg", "grossesse"],
                    "sample": "Urine / Sérum",
                    "params": [
                        {"code": "resultat", "name": "Recherche b-HCG", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Positif"]}
                    ]
                }
            }
        },
        "immunologie": {
            "label": "4. Immunologie",
            "exams": {
                "WIDAL": {
                    "code": "Widal",
                    "name": "Widal",
                    "full_name": "Widal (Sérodiagnostic de Widal & Félix)",
                    "aliases": ["widal", "sero-diagnostic de widal", "fievre typhoide"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "to", "name": "Antigène O (S. typhi TO)", "unit": "", "ref_text": "< 1/100 (Négatif)", "type": "select", "options": ["Négatif (< 1/100)", "1/100", "1/200", "1/400", "1/800 et +"]},
                        {"code": "th", "name": "Antigène H (S. typhi TH)", "unit": "", "ref_text": "< 1/100 (Négatif)", "type": "select", "options": ["Négatif (< 1/100)", "1/100", "1/200", "1/400", "1/800 et +"]},
                        {"code": "ao", "name": "Antigène AO (S. paratyphi A)", "unit": "", "ref_text": "< 1/100 (Négatif)", "type": "select", "options": ["Négatif (< 1/100)", "1/100", "1/200", "1/400", "1/800 et +"]},
                        {"code": "ah", "name": "Antigène AH (S. paratyphi A)", "unit": "", "ref_text": "< 1/100 (Négatif)", "type": "select", "options": ["Négatif (< 1/100)", "1/100", "1/200", "1/400", "1/800 et +"]},
                        {"code": "bo", "name": "Antigène BO (S. paratyphi B)", "unit": "", "ref_text": "< 1/100 (Négatif)", "type": "select", "options": ["Négatif (< 1/100)", "1/100", "1/200", "1/400", "1/800 et +"]},
                        {"code": "bh", "name": "Antigène BH (S. paratyphi B)", "unit": "", "ref_text": "< 1/100 (Négatif)", "type": "select", "options": ["Négatif (< 1/100)", "1/100", "1/200", "1/400", "1/800 et +"]}
                    ]
                },
                "GS": {
                    "code": "GS",
                    "name": "GS",
                    "full_name": "GS (Groupe Sanguin & Rhésus)",
                    "aliases": ["gs", "groupe sanguin", "groupage", "abo-rh", "groupage sanguin abo et rhesus"],
                    "sample": "Sang total",
                    "params": [
                        {"code": "groupe", "name": "Groupe ABO", "unit": "", "ref_text": "A, B, AB ou O", "type": "select", "options": ["A", "B", "AB", "O"]},
                        {"code": "rhesus", "name": "Facteur Rhésus (Rh)", "unit": "", "ref_text": "Positif (+) ou Négatif (-)", "type": "select", "options": ["Positif (+)", "Négatif (-)"]}
                    ]
                },
                "COMPAT": {
                    "code": "COMPAT",
                    "name": "Test de compatibilité",
                    "full_name": "Test de compatibilité (Crossmatch)",
                    "aliases": ["test de compatibilité", "test de compatibilite", "compatibilite", "crossmatch"],
                    "sample": "Sérum receveur + Culot donneur",
                    "params": [
                        {"code": "resultat", "name": "Épreuve de compatibilité majeure", "unit": "", "ref_text": "Compatible (Absence d'agglutination)", "type": "select", "options": ["Compatible", "Incompatible"]}
                    ]
                }
            }
        },
        "biochimie": {
            "label": "5. Biochimie",
            "exams": {
                "GLYC": {
                    "code": "GLYC",
                    "name": "Glycémie à jeun",
                    "full_name": "Glycémie veineuse à jeun",
                    "aliases": ["glycemie", "glyc", "glucose sanguin", "glycémie"],
                    "sample": "Sérum / Plasma fluoré",
                    "params": [
                        {"code": "glycemie", "name": "Glycémie", "unit": "mg/dL", "ref_min": 70.0, "ref_max": 110.0, "ref_text": "70 - 110 mg/dL", "type": "number", "decimal": 0}
                    ]
                },
                "UREE": {
                    "code": "UREE",
                    "name": "Urée sanguine",
                    "full_name": "Urée sérique",
                    "aliases": ["uree", "urée", "urée sanguine"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "uree", "name": "Urée", "unit": "mg/dL", "ref_min": 15.0, "ref_max": 45.0, "ref_text": "15 - 45 mg/dL", "type": "number", "decimal": 1}
                    ]
                },
                "CREAT": {
                    "code": "CREAT",
                    "name": "Créatinine sérique",
                    "full_name": "Créatininémie",
                    "aliases": ["creatinine", "creat", "créatinine"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "creatinine", "name": "Créatinine", "unit": "mg/dL", "ref_min": 0.6, "ref_max": 1.2, "ref_text": "0.6 - 1.2 mg/dL", "type": "number", "decimal": 2}
                    ]
                },
                "ALAT": {
                    "code": "ALAT",
                    "name": "Transaminases ALAT (TGP)",
                    "full_name": "Alanine aminotransférase (ALAT/TGP)",
                    "aliases": ["alat", "tgp", "transaminases alat", "p-tgp"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "alat", "name": "ALAT (TGP)", "unit": "UI/L", "ref_min": 0.0, "ref_max": 45.0, "ref_text": "< 45 UI/L", "type": "number", "decimal": 0}
                    ]
                },
                "ASAT": {
                    "code": "ASAT",
                    "name": "Transaminases ASAT (TGO)",
                    "full_name": "Aspartate aminotransférase (ASAT/TGO)",
                    "aliases": ["asat", "tgo", "transaminases asat"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "asat", "name": "ASAT (TGO)", "unit": "UI/L", "ref_min": 0.0, "ref_max": 40.0, "ref_text": "< 40 UI/L", "type": "number", "decimal": 0}
                    ]
                },
                "BILI": {
                    "code": "BILI",
                    "name": "Bilirubine (Totale & Directe)",
                    "full_name": "Bilirubinémie fractionnée",
                    "aliases": ["bilirubine", "bili", "bilirubine totale"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "bili_totale", "name": "Bilirubine Totale", "unit": "mg/dL", "ref_min": 0.2, "ref_max": 1.2, "ref_text": "0.2 - 1.2 mg/dL", "type": "number", "decimal": 2},
                        {"code": "bili_directe", "name": "Bilirubine Directe", "unit": "mg/dL", "ref_min": 0.0, "ref_max": 0.3, "ref_text": "< 0.3 mg/dL", "type": "number", "decimal": 2}
                    ]
                },
                "IONO": {
                    "code": "IONO",
                    "name": "Ionogramme sanguin",
                    "full_name": "Ionogramme sérique (Na+, K+, Cl-)",
                    "aliases": ["iono", "ionogramme", "electrolytes"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "sodium", "name": "Sodium (Na+)", "unit": "mEq/L", "ref_min": 135.0, "ref_max": 145.0, "ref_text": "135 - 145 mEq/L", "type": "number", "decimal": 1},
                        {"code": "potassium", "name": "Potassium (K+)", "unit": "mEq/L", "ref_min": 3.5, "ref_max": 5.1, "ref_text": "3.5 - 5.1 mEq/L", "type": "number", "decimal": 2},
                        {"code": "chlore", "name": "Chlore (Cl-)", "unit": "mEq/L", "ref_min": 98.0, "ref_max": 107.0, "ref_text": "98 - 107 mEq/L", "type": "number", "decimal": 1}
                    ]
                },
                "CHOL": {
                    "code": "CHOL",
                    "name": "Cholestérol total",
                    "full_name": "Cholestérolémie totale",
                    "aliases": ["cholesterol", "chol", "cholestérol"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "cholesterol", "name": "Cholestérol Total", "unit": "mg/dL", "ref_min": 120.0, "ref_max": 200.0, "ref_text": "< 200 mg/dL", "type": "number", "decimal": 0}
                    ]
                },
                "TRIGLY": {
                    "code": "TRIGLY",
                    "name": "Triglycérides",
                    "full_name": "Triglycéridémie",
                    "aliases": ["triglycerides", "trigly", "triglycérides"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "triglycerides", "name": "Triglycérides", "unit": "mg/dL", "ref_min": 40.0, "ref_max": 150.0, "ref_text": "< 150 mg/dL", "type": "number", "decimal": 0}
                    ]
                },
                "AC_URIQ": {
                    "code": "AC_URIQ",
                    "name": "Acide urique",
                    "full_name": "Uricémie",
                    "aliases": ["acide urique", "uricemie", "urates"],
                    "sample": "Sérum",
                    "params": [
                        {"code": "acide_urique", "name": "Acide Urique", "unit": "mg/dL", "ref_min": 2.5, "ref_max": 7.0, "ref_text": "2.5 - 7.0 mg/dL", "type": "number", "decimal": 1}
                    ]
                },
                "BU": {
                    "code": "BU",
                    "name": "Bandelette urinaire",
                    "full_name": "Bandelette urinaire (10 paramètres)",
                    "aliases": ["bu", "bandelette urinaire", "bandelette", "analyse d'urine", "chimie des urines"],
                    "sample": "Urine fraîche",
                    "params": [
                        {"code": "leucocytes", "name": "Leucocytes", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Traces", "+", "++", "+++"]},
                        {"code": "nitrites", "name": "Nitrites", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Positif"]},
                        {"code": "urobilinogene", "name": "Urobilinogène", "unit": "", "ref_text": "Normal (0.2 mg/dL)", "type": "select", "options": ["Normal", "Traces", "+", "++", "+++"]},
                        {"code": "proteines", "name": "Protéines", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Traces", "+", "++", "+++"]},
                        {"code": "ph", "name": "pH", "unit": "", "ref_min": 5.0, "ref_max": 8.5, "ref_text": "5.0 - 8.0", "type": "number", "decimal": 1},
                        {"code": "sang", "name": "Blood / Sang", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Traces", "+", "++", "+++"]},
                        {"code": "densite", "name": "Specific Gravity / Densité", "unit": "", "ref_min": 1.005, "ref_max": 1.030, "ref_text": "1.005 - 1.030", "type": "number", "decimal": 3},
                        {"code": "cetones", "name": "Ketone / Cétones", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Traces", "+", "++", "+++"]},
                        {"code": "bilirubine", "name": "Bilirubine", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "+", "++", "+++"]},
                        {"code": "glucose", "name": "Glucose", "unit": "", "ref_text": "Négatif", "type": "select", "options": ["Négatif", "Traces", "+", "++", "+++"]}
                    ]
                }
            }
        }
    }

    # Custom lab references store in memory / persistent table
    _CUSTOM_LAB_REFS = {}

    def find_exam_spec(test_type_str):
        """Trouve la spécification de l'examen dans le catalogue par code ou alias."""
        clean = str(test_type_str or "").strip().lower()
        if not clean:
            return None
        for dept_key, dept in LAB_CATALOG.items():
            for exam_key, exam in dept["exams"].items():
                if clean == exam["code"].lower() or clean == exam["name"].lower() or clean == exam["full_name"].lower():
                    return exam
                for alias in exam.get("aliases", []):
                    if clean == alias.lower() or alias.lower() in clean or clean in alias.lower():
                        return exam
        return None

    @laboratory.route("/api/laboratory/catalog", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_lab_catalog():
        """Retourne le catalogue officiel des 5 départements d'analyses de laboratoire."""
        return jsonify(LAB_CATALOG)

    @laboratory.route("/api/laboratory/params/<path:test_type>", methods=["GET"])
    @roles_required("super_admin", "laboratoire", "docteur", "infirmier")
    def get_lab_params_v2(test_type: str):
        """Retourne les paramètres structurés et références actives d'un examen."""
        exam = find_exam_spec(test_type)
        if exam:
            # Injecter les éventuelles références personnalisées du laboratoire
            params = []
            for p in exam["params"]:
                p_copy = dict(p)
                custom_key = f"{exam['code']}:{p['code']}"
                if custom_key in _CUSTOM_LAB_REFS:
                    p_copy.update(_CUSTOM_LAB_REFS[custom_key])
                params.append(p_copy)
            return jsonify({
                "exam": exam["name"],
                "full_name": exam["full_name"],
                "sample": exam["sample"],
                "params": params
            })
        return jsonify({"exam": test_type, "full_name": test_type, "sample": "Biologique", "params": []})

    @laboratory.route("/api/laboratory/references", methods=["GET", "PUT"])
    @roles_required("super_admin", "laboratoire")
    def manage_lab_references():
        """Consulter ou modifier les valeurs de référence personnalisées du laboratoire."""
        if request.method == "GET":
            return jsonify(_CUSTOM_LAB_REFS)
        data = fast_json()
        if isinstance(data, dict):
            _CUSTOM_LAB_REFS.update(data)
            return jsonify({"status": "success", "references": _CUSTOM_LAB_REFS})
        return jsonify({"error": "Format invalide"}), 422

    app.register_blueprint(laboratory)
