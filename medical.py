"""Routes de soins partagées entre médecin, infirmier et pharmacie."""

from flask import Blueprint


def register_medical_routes(app, *, runtime):
    globals().update(runtime)
    medical = Blueprint("medical", __name__)

    @app.route("/api/care", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(60)
    def get_care_logs():
        result = supabase.table(TABLES["care"]).select("*").order("created_at", desc=True).execute()
        care_logs = result.data or []
        patients = get_patient_map()
        rows = []
        for c in care_logs:
            metadata = {}
            desc = c.get("description") or ""
            if isinstance(desc, str) and desc.strip().startswith("{"):
                try:
                    metadata = json.loads(desc)
                except Exception:
                    metadata = {}
            item = {**c, **metadata}
            item["patient_name"] = patients.get(item.get("patient_id"), "Inconnu")
            item["product_name"] = item.get("product_name") or item.get("medication") or item.get("care_type") or "Soin"
            rows.append(item)
        return jsonify(rows)

    @app.route("/api/care", methods=["POST"])
    @roles_required("super_admin", "docteur", "infirmier")
    def create_care_log():
        data = fast_json()
        if not data.get("patient_id") or not data.get("care_type"):
            return jsonify({"error": "Patient et type de soin requis"}), 422
    
        patient_id = to_int(data.get("patient_id"))
        care_type = data.get("care_type")
    
        care = {
            "patient_id": patient_id,
            "care_type": care_type,
            "description": data.get("description", ""),
            "priority": normalize_status(data.get("priority", "normal"), ["normal", "urgent", "high", "low"], "normal"),
            "status": data.get("status", "pending"),
            "date": now_iso(),
            "performed_by": g.current_user["id"],
            "performed_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["care"], care)
        created_care = result.data[0] if result.data else care
    
        tarif_code = get_tarif_code_for_care(care_type)
        facture_auto(patient_id, tarif_code, 1, "care", created_care.get("id"))
    
        add_audit("CREATE", "care", f"Soin #{created_care.get('id')}", created_care.get("id"))
        invalidate_cache()
        return jsonify(created_care), 201

    @app.route("/api/care/<int:care_id>", methods=["PUT"])
    @roles_required("super_admin", "docteur", "infirmier")
    def update_care_log(care_id: int):
        data = fast_json()
        updates = {}
        if "care_type" in data:
            updates["care_type"] = data["care_type"]
        if "description" in data:
            updates["description"] = data["description"]
        if "status" in data:
            updates["status"] = data["status"]
        if "priority" in data:
            updates["priority"] = data["priority"]
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["care"]).update(updates).eq("id", care_id).execute()
        if not result.data:
            return jsonify({"error": "Soin introuvable"}), 404
        add_audit("UPDATE", "care", f"Soin #{care_id} modifié", care_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/care/<int:care_id>", methods=["PATCH"])
    @roles_required("super_admin", "docteur", "infirmier")
    def patch_care_log(care_id: int):
        data = fast_json()
        allowed = ["status", "priority"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["care"]).update(updates).eq("id", care_id).execute()
        if not result.data:
            return jsonify({"error": "Soin introuvable"}), 404
        add_audit("UPDATE", "care", f"Soin #{care_id} patché", care_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/care/<int:care_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_care_log(care_id: int):
        supabase.table(TABLES["care"]).delete().eq("id", care_id).execute()
        add_audit("DELETE", "care", f"Soin #{care_id} supprimé", care_id)
        invalidate_cache()
        return jsonify({"message": "Soin supprimé"})

    # Compatibilité pharmacie : les soins injectables et consommables sont stockés
    # dans care_logs, mais le frontend historique les nomme « care prescriptions ».
    @app.route("/api/care/prescriptions", methods=["GET", "POST"])
    @roles_required("super_admin", "docteur", "infirmier", "pharmacie")
    def get_care_prescriptions_compat():
        if request.method == "POST":
            data = fast_json()
            patient_id = to_int(data.get("patient_id"))
            if not patient_id:
                return jsonify({"error": "Patient requis"}), 422
            items = []
            for category, values in (("injectable", data.get("injectables") or []), ("consommable", data.get("consumables") or []), ("acte", data.get("acts") or [])):
                for value in values:
                    item = dict(value or {})
                    item["category"] = category
                    item["patient_name"] = data.get("patient_name", "")
                    item["prescribed_by"] = data.get("doctor_name") or g.current_user.get("name", "")
                    item["doctor_id"] = data.get("doctor_id") or g.current_user.get("id")
                    items.append(item)
            if not items:
                return jsonify({"error": "Au moins un soin est requis"}), 422
            created = []
            for item in items:
                care = {
                    "patient_id": patient_id,
                    "care_type": item.get("category", "soin"),
                    "description": json.dumps(item, ensure_ascii=False),
                    "priority": "normal",
                    "status": data.get("status", "pending"),
                    "date": now_iso(),
                    "performed_by": g.current_user["id"],
                    "performed_by_name": g.current_user["name"],
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                result = compatible_insert(TABLES["care"], care)
                created.append(result.data[0] if result.data else care)
            add_audit("CREATE", "care", f"Prescription de soins ({len(created)} élément(s))", patient_id)
            invalidate_cache()
            return jsonify({"items": created}), 201
        result = supabase.table(TABLES["care"]).select("*").order("created_at", desc=True).execute()
        patients = get_patient_map()
        rows = []
        for row in result.data or []:
            try:
                metadata = json.loads(row.get("description") or "{}")
            except (TypeError, ValueError):
                metadata = {}
            category = metadata.get("category") or row.get("category") or row.get("care_type")
            if category not in ("injectable", "consommable"):
                continue
            item = {**row, **metadata}
            item["category"] = category
            item["product_name"] = item.get("product_name") or item.get("medication") or item.get("care_type")
            item["patient_name"] = patients.get(item.get("patient_id"), "Inconnu")
            rows.append(item)
        return jsonify(rows)

    @app.route("/api/care/prescriptions/<int:care_id>", methods=["PATCH"])
    @roles_required("super_admin", "pharmacie")
    def patch_care_prescription_compat(care_id: int):
        data = fast_json()
        updates = {key: value for key, value in data.items() if key in ("status",) and value is not None}
        if not updates:
            return jsonify({"error": "Statut requis"}), 422
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["care"]).update(updates).eq("id", care_id).execute()
        if not result.data:
            return jsonify({"error": "Soin introuvable"}), 404
        add_audit("UPDATE", "care", f"Soin #{care_id} délivré par la pharmacie", care_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @app.route("/api/care/pending", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_care_pending():
        result = supabase.table(TABLES["care"]).select("*").in_("status", ["pending", "scheduled", "due", "in_progress", "active"]).order("created_at", desc=True).execute()
        patients = get_patient_map()
        rows = []
        for row in (result.data or []):
            metadata = {}
            desc = row.get("description") or ""
            if isinstance(desc, str) and desc.strip().startswith("{"):
                try:
                    metadata = json.loads(desc)
                except Exception:
                    metadata = {}
            item = {**row, **metadata}
            item["patient_name"] = patients.get(item.get("patient_id"), "Inconnu")
            item["product_name"] = item.get("product_name") or item.get("medication") or item.get("care_type") or "Soin"
            rows.append(item)
        return jsonify(rows)

    @app.route("/api/care/history", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_care_history():
        result = supabase.table(TABLES["care"]).select("*").in_("status", ["completed", "cancelled", "missed", "refused", "administered"]).order("updated_at", desc=True).execute()
        patients = get_patient_map()
        rows = []
        for row in (result.data or []):
            metadata = {}
            desc = row.get("description") or ""
            if isinstance(desc, str) and desc.strip().startswith("{"):
                try:
                    metadata = json.loads(desc)
                except Exception:
                    metadata = {}
            item = {**row, **metadata}
            item["patient_name"] = patients.get(item.get("patient_id"), "Inconnu")
            item["product_name"] = item.get("product_name") or item.get("medication") or item.get("care_type") or "Soin"
            rows.append(item)
        return jsonify(rows)

    # ==================== WORKFLOW ====================

    app.register_blueprint(medical)
