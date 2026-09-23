try:
    from events import broadcast_event
except ImportError:
    broadcast_event = lambda t, p=None: None
"""Routes de soins partagées entre médecin, infirmier et pharmacie."""

from flask import Blueprint
import uuid


def register_medical_routes(app, *, runtime):
    globals().update(runtime)
    medical = Blueprint("medical", __name__)

    @app.route("/api/care", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(60)
    def get_care_logs():
        query = supabase.table(TABLES["care"]).select("*")
        if g.current_user.get("role") == "docteur":
            query = query.eq("performed_by", g.current_user.get("id"))
        result = query.order("created_at", desc=True).execute()
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
        if g.current_user.get("role") == "docteur":
            owned = supabase.table(TABLES["care"]).select("id").eq("id", care_id).eq("performed_by", g.current_user.get("id")).execute().data or []
            if not owned:
                return jsonify({"error": "Soin non attribué à ce médecin"}), 403
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
        if g.current_user.get("role") == "docteur":
            owned = supabase.table(TABLES["care"]).select("id").eq("id", care_id).eq("performed_by", g.current_user.get("id")).execute().data or []
            if not owned:
                return jsonify({"error": "Soin non attribué à ce médecin"}), 403
        allowed = ["status", "priority", "description"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}

        meta_keys = [
            "current_occurrence", "total_occurrences", "next_due_at",
            "frequency_hours", "duration_days", "last_administered_at",
            "last_administered_by", "administrations", "completed_phases",
            "current_phase"
        ]
        has_meta = any(k in data for k in meta_keys)

        if has_meta and "description" not in updates:
            row_res = supabase.table(TABLES["care"]).select("description, status").eq("id", care_id).execute()
            if row_res.data:
                desc_str = row_res.data[0].get("description") or "{}"
                try:
                    meta = json.loads(desc_str) if desc_str.strip().startswith("{") else {"raw_description": desc_str}
                except Exception:
                    meta = {"raw_description": desc_str}
                for mk in meta_keys:
                    if mk in data and data[mk] is not None:
                        meta[mk] = data[mk]

                admin_rec = data.get("admin_record")
                if admin_rec and isinstance(admin_rec, dict):
                    if "administrations" not in meta or not isinstance(meta["administrations"], list):
                        meta["administrations"] = []
                    meta["administrations"].append(admin_rec)

                cur_occ = to_int(meta.get("current_occurrence")) or 1
                tot_occ = to_int(meta.get("total_occurrences")) or 1
                if cur_occ > tot_occ:
                    updates["status"] = "completed"
                    meta["completed_at"] = now_iso()

                updates["description"] = json.dumps(meta, ensure_ascii=False)

        # Vérification du blocage pharmacie côté backend : sécurité stricte
        if g.current_user.get("role") == "infirmier" and (data.get("admin_record") or data.get("current_occurrence")):
            care_row = supabase.table(TABLES["care"]).select("description, status").eq("id", care_id).execute()
            if care_row.data:
                desc_val = care_row.data[0].get("description") or "{}"
                try:
                    desc_obj = json.loads(desc_val) if desc_val.strip().startswith("{") else {}
                except Exception:
                    desc_obj = {}
                has_products = bool(desc_obj.get("injectables") or desc_obj.get("consumables") or desc_obj.get("requires_pharmacy"))
                pharm_status = desc_obj.get("pharmacy_status") or desc_obj.get("delivery_status") or care_row.data[0].get("status")
                allowed_statuses = ("delivered", "dispensed", "completed", "scheduled", "in_progress", "active")
                is_unblocked = (pharm_status in allowed_statuses) or (care_row.data[0].get("status") in allowed_statuses)
                if has_products and not is_unblocked:
                    return jsonify({"error": "Soin bloqué : les produits pharmaceutiques doivent d'abord être livrés par la pharmacie."}), 422

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

            acts = data.get("acts") or []
            injectables = data.get("injectables") or []
            consumables = data.get("consumables") or []

            if not acts and not injectables and not consumables:
                return jsonify({"error": "Au moins un soin est requis"}), 422

            freq_h = to_int(data.get("frequency_hours"))
            dur_d = to_int(data.get("duration_days"))
            for item in (injectables + acts):
                if not freq_h and item.get("frequency_hours"):
                    freq_h = to_int(item.get("frequency_hours"))
                if not dur_d and item.get("duration_days"):
                    dur_d = to_int(item.get("duration_days"))
            freq_h = freq_h or 8
            dur_d = dur_d or 1
            tot_occ = max(1, (dur_d * 24) // freq_h)

            doc_name = g.current_user.get("name", "") if g.current_user.get("role") == "docteur" else (data.get("doctor_name") or g.current_user.get("name", ""))
            doc_id = g.current_user.get("id") if g.current_user.get("role") == "docteur" else (data.get("doctor_id") or g.current_user.get("id"))
            patient_name = data.get("patient_name", "")

            main_title = ""
            if acts:
                main_title = acts[0].get("name") or acts[0].get("product_name") or "Acte de soin"
            elif injectables:
                main_title = injectables[0].get("product_name") or injectables[0].get("name") or "Injection"
            else:
                main_title = "Séance de soins"

            supplied_uid = str(data.get("uid") or "").strip().upper()
            session_uid = supplied_uid if supplied_uid else f"SOIN-{now_iso()[:10].replace('-', '')}-{uuid.uuid4().hex[:8].upper()}"
            if supplied_uid:
                same_uid = supabase.table(TABLES["care"]).select("*").ilike("description", f"%{session_uid}%").execute().data or []
                if same_uid:
                    return jsonify({"items": [same_uid[0]]}), 200
            session_meta = {
                "uid": session_uid,
                "is_session": True,
                "title": main_title,
                "patient_id": patient_id,
                "patient_name": patient_name,
                "doctor_id": doc_id,
                "prescribed_by": doc_name,
                "acts": acts,
                "injectables": injectables,
                "consumables": consumables,
                "frequency_hours": freq_h,
                "duration_days": dur_d,
                "total_occurrences": tot_occ,
                "current_occurrence": 1,
                "next_due_at": None,
                "last_administered_at": None,
                "last_administered_by": None,
                "administrations": []
            }

            care = {
                "patient_id": patient_id,
                "care_type": "seance_soin",
                "description": json.dumps(session_meta, ensure_ascii=False),
                "priority": data.get("priority", "normal"),
                "status": data.get("status", "pending"),
                "date": now_iso(),
                "performed_by": g.current_user["id"],
                "performed_by_name": g.current_user["name"],
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            result = compatible_insert(TABLES["care"], care)
            created_row = result.data[0] if result.data else care
            add_audit("CREATE", "care", f"Prescription séance de soins #{patient_id}", patient_id)
            invalidate_cache()
            return jsonify({"items": [created_row], "session": session_meta}), 201

        query = supabase.table(TABLES["care"]).select("*")
        if g.current_user.get("role") == "docteur":
            query = query.eq("performed_by", g.current_user.get("id"))
        result = query.order("created_at", desc=True).execute()
        patients = get_patient_map()
        rows = []
        for row in result.data or []:
            try:
                metadata = json.loads(row.get("description") or "{}")
            except (TypeError, ValueError):
                metadata = {}

            if metadata.get("is_session") or row.get("care_type") == "seance_soin":
                doc_name = metadata.get("prescribed_by") or row.get("performed_by_name") or "Médecin"
                pat_name = patients.get(row.get("patient_id"), metadata.get("patient_name") or "Inconnu")
                for inj in metadata.get("injectables") or []:
                    rows.append({
                        **row,
                        **inj,
                        "category": "injectable",
                        "prescribed_by": doc_name,
                        "patient_name": pat_name,
                        "session_id": row.get("id"),
                        "product_name": inj.get("product_name") or inj.get("name") or "Injectable",
                        # L'ID de la séance doit rester celui de care_logs, jamais
                        # l'ID éventuel du produit / de l'acte dans les métadonnées.
                        "id": row.get("id")
                    })
                for cons in metadata.get("consumables") or []:
                    rows.append({
                        **row,
                        **cons,
                        "category": "consommable",
                        "prescribed_by": doc_name,
                        "patient_name": pat_name,
                        "session_id": row.get("id"),
                        "product_name": cons.get("product_name") or cons.get("name") or "Consommable",
                        "id": row.get("id")
                    })
                continue

            category = metadata.get("category") or row.get("category") or row.get("care_type")
            if category not in ("injectable", "consommable"):
                continue
            # Ne jamais laisser les métadonnées écraser l'ID de la ligne SQL.
            # Sinon un identifiant métier comme ACT006 est envoyé à la route
            # /api/care/<int:care_id> et l'administration échoue en 404.
            item = {**metadata, **row}
            item["category"] = category
            item["product_name"] = item.get("product_name") or item.get("medication") or item.get("care_type")
            item["patient_name"] = patients.get(item.get("patient_id"), "Inconnu")
            rows.append(item)
        return jsonify(rows)

    @app.route("/api/care/prescriptions/<int:care_id>", methods=["PATCH"])
    @roles_required("super_admin", "pharmacie")
    def patch_care_prescription_compat(care_id: int):
        data = fast_json()
        status_val = data.get("status")
        if not status_val:
            return jsonify({"error": "Statut requis"}), 422

        care_row = supabase.table(TABLES["care"]).select("*").eq("id", care_id).execute()
        if not care_row.data:
            return jsonify({"error": "Soin introuvable"}), 404
        
        row = care_row.data[0]
        desc_str = row.get("description") or "{}"
        try:
            meta = json.loads(desc_str) if desc_str.strip().startswith("{") else {}
        except Exception:
            meta = {}
            
        meta["pharmacy_status"] = status_val
        meta["delivery_status"] = status_val
        if status_val == "delivered":
            meta["delivered_at"] = now_iso()
            meta["delivered_by"] = g.current_user.get("id")
            meta["delivered_by_name"] = g.current_user.get("name")

        updates = {
            "status": status_val,
            "description": json.dumps(meta, ensure_ascii=False),
            "updated_at": now_iso()
        }
        result = supabase.table(TABLES["care"]).update(updates).eq("id", care_id).execute()
        add_audit("UPDATE", "care", f"Soin #{care_id} délivré par la pharmacie", care_id)
        invalidate_cache()

        # Émission de l'événement temps réel vers l'infirmier
        try:
            broadcast_event("care_delivered", {"care_id": care_id, "patient_id": row.get("patient_id")})
        except Exception:
            pass

        return jsonify(result.data[0] if result.data else updates)

    @app.route("/api/care/pending", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_care_pending():
        # Un soin livré par la pharmacie est prêt à être administré par l'infirmier.
        query = supabase.table(TABLES["care"]).select("*").in_("status", ["pending", "scheduled", "due", "in_progress", "active", "delivered"])
        if g.current_user.get("role") == "docteur":
            query = query.eq("performed_by", g.current_user.get("id"))
        result = query.order("created_at", desc=True).execute()
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
            # Garder l'ID numérique persisté pour PATCH /api/care/<int:id>.
            item = {**metadata, **row}
            item["patient_name"] = patients.get(item.get("patient_id"), metadata.get("patient_name") or "Inconnu")
            item["product_name"] = metadata.get("title") or item.get("product_name") or item.get("medication") or item.get("care_type") or "Soin"
            item["prescribed_by"] = metadata.get("prescribed_by") or row.get("performed_by_name") or "Médecin"
            rows.append(item)
        return jsonify(rows)

    @app.route("/api/care/history", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_care_history():
        query = supabase.table(TABLES["care"]).select("*").in_("status", ["completed", "cancelled", "missed", "refused", "administered"])
        if g.current_user.get("role") == "docteur":
            query = query.eq("performed_by", g.current_user.get("id"))
        result = query.order("updated_at", desc=True).execute()
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
            item["patient_name"] = patients.get(item.get("patient_id"), metadata.get("patient_name") or "Inconnu")
            item["product_name"] = metadata.get("title") or item.get("product_name") or item.get("medication") or item.get("care_type") or "Soin"
            item["prescribed_by"] = metadata.get("prescribed_by") or row.get("performed_by_name") or "Médecin"
            rows.append(item)
        return jsonify(rows)

    # ==================== WORKFLOW ====================

    app.register_blueprint(medical)
