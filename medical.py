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

    def bill_care_session(patient_id, care_id, metadata):
        """Débite une séance validée une seule fois, au moment de la prescription.

        Les produits gardent leur devise de stock; les actes prennent le tarif
        correspondant dans la grille. La clé d'idempotence est par élément afin
        qu'une séance contenant plusieurs produits ne puisse ni être oubliée ni
        être facturée deux fois.
        """
        product_ids = []
        for item in (metadata.get("injectables") or []) + (metadata.get("consumables") or []):
            product_id = to_int(item.get("product_id"), 0)
            if product_id:
                product_ids.append(product_id)

        stock = {}
        if product_ids:
            try:
                rows = supabase.table(TABLES["pharmacy"]).select(
                    "id,medication_name,selling_price,price_currency"
                ).in_("id", product_ids).execute().data or []
            except Exception:
                # Anciennes bases où price_currency n'existe pas encore.
                rows = supabase.table(TABLES["pharmacy"]).select(
                    "id,medication_name,selling_price"
                ).in_("id", product_ids).execute().data or []
            stock = {to_int(row.get("id")): row for row in rows}

        try:
            tariffs = supabase.table(TABLES["tariffs"]).select("*").eq("is_active", True).execute().data or []
        except Exception:
            tariffs = []

        billed, missing_prices = [], []

        def add_line(kind, index, category, description, quantity, price, currency):
            quantity = max(1, to_int(quantity, 1))
            price = to_float(price, 0)
            if price <= 0:
                missing_prices.append(description)
                return
            amount = round(quantity * price, 2)
            line = add_patient_account_line(
                patient_id=patient_id,
                category=category,
                description=description,
                amount=amount,
                source="care_prescription",
                # Plusieurs lignes par séance : l'idempotence, pas source_id,
                # est la protection anti-doublon de chaque élément.
                source_id=None,
                quantity=quantity,
                unit_price=price,
                idempotency_key=f"care-{care_id}-{kind}-{index}",
                currency_origin=currency,
            )
            if line:
                billed.append(description)

        DEFAULT_ACT_PRICES = {
            "injection": 3.0, "soin_injection": 3.0, "act001": 3.0,
            "perfusion": 8.0, "soin_perfusion": 8.0, "act002": 8.0,
            "pansement": 5.0, "soin_pansement": 5.0, "act003": 5.0,
            "surveillance": 5.0, "act004": 5.0,
            "nebulisation": 6.0, "act005": 6.0,
            "suture": 10.0, "soin_suture": 10.0, "act006": 10.0,
            "platre": 20.0, "plâtre": 20.0, "soin_platre": 20.0, "act007": 20.0,
            "extraction": 15.0, "act008": 15.0,
            "reeducation": 10.0, "act009": 10.0,
            "prelevement": 5.0, "act010": 5.0,
            "transfusion": 25.0, "act011": 25.0,
            "soin de plaie": 5.0, "act012": 5.0,
            "catheterisme": 8.0, "act013": 8.0,
            "intubation": 30.0, "act014": 30.0,
            "ventilation": 25.0, "act015": 25.0,
            "soin": 5.0
        }

        for kind, items in (("injectable", metadata.get("injectables") or []),
                            ("consumable", metadata.get("consumables") or [])):
            for index, item in enumerate(items):
                product = stock.get(to_int(item.get("product_id"), 0), {})
                name = item.get("product_name") or item.get("name") or product.get("medication_name") or "Produit de soin"
                currency = item.get("price_currency") or item.get("currency") or product.get("price_currency") or "FC"
                price = item.get("unit_price") or item.get("selling_price") or product.get("selling_price")
                if not price or to_float(price, 0) <= 0:
                    price = 2500.0 if str(currency).upper() in ("FC", "CDF") else 2.0
                add_line(kind, index, "pharmacie", f"Produit soin: {name}", item.get("quantity"), price, currency)

        for index, act in enumerate(metadata.get("acts") or []):
            name = act.get("name") or act.get("product_name") or act.get("act_type") or "Acte de soin"
            act_id = str(act.get("id") or act.get("act_type") or "").strip().lower()
            name_clean = str(name).strip().lower().replace("â", "a").replace("é", "e").replace("è", "e")
            tariff = next((row for row in tariffs if str(row.get("code") or row.get("id") or "").strip().lower() in (act_id, name_clean)
                           or str(row.get("label") or "").strip().lower().replace("â", "a").replace("é", "e") in (act_id, name_clean)), None)
            fallback_act_price = DEFAULT_ACT_PRICES.get(act_id) or DEFAULT_ACT_PRICES.get(name_clean) or 5.0
            price = act.get("unit_price") or act.get("price") or (tariff or {}).get("amount") or (tariff or {}).get("price_usd") or fallback_act_price
            currency = act.get("price_currency") or act.get("currency") or (tariff or {}).get("price_currency") or "USD"
            add_line("act", index, "soins", f"Acte de soin: {name}", act.get("quantity") or act.get("repetitions"), price, currency)

        metadata["billing"] = {
            "charged_at": now_iso(),
            "charged_items": billed,
            "unpriced_items": missing_prices,
            "mode": "prescription",
        }
        return metadata

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
    
        # Même règle que pour les séances : une prestation va d'abord dans le
        # compte patient. La facture imprimable n'est créée qu'au règlement,
        # sinon facture_auto crée à la fois une facture et une seconde ligne.
        tarif_code = get_tarif_code_for_care(care_type)
        tarif = get_tarif_from_db(tarif_code)
        amount = to_float((tarif or {}).get("price_usd") or (tarif or {}).get("amount"), 0)
        if amount > 0:
            add_patient_account_line(
                patient_id=patient_id,
                category=(tarif or {}).get("category", "soins"),
                description=(tarif or {}).get("label", care_type),
                amount=amount,
                source="care",
                source_id=created_care.get("id"),
                quantity=1,
                unit_price=amount,
                currency_origin="USD",
            )
    
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
                pharm_status = desc_obj.get("pharmacy_status") or desc_obj.get("delivery_status")
                # Blocage strict : seuls les statuts 'delivered' ou 'dispensed' débloquent l'administration
                is_unblocked = pharm_status in ("delivered", "dispensed")
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
            # Un brouillon reste non facturé. Une prescription envoyée crée les
            # lignes du compte immédiatement, avant toute délivrance pharmacie.
            if data.get("status", "pending") != "draft" and created_row.get("id"):
                session_meta = bill_care_session(patient_id, created_row["id"], session_meta)
                description = json.dumps(session_meta, ensure_ascii=False)
                compatible_update(TABLES["care"], {"description": description, "updated_at": now_iso()}, "id", created_row["id"])
                created_row["description"] = description
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

            # La délivrance confirme uniquement la disponibilité des produits.
            # La facturation a déjà été faite à l'envoi de la prescription : la
            # refaire ici créait des doublons et pouvait perdre la devise FC.
            meta["delivered_and_billed"] = True

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
