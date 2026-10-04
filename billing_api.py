"""Module facturation I-HUB : factures, comptes patients et paiements."""

from flask import Blueprint





def register_billing_routes(app, *, runtime):

    globals().update(runtime)

    billing = Blueprint("billing", __name__)



    PAYMENT_METHOD_LABELS = {

        "especes": "Espèces (Cash)",

        "mpesa": "M-Pesa (Vodacom)",

        "orange_money": "Orange Money",

        "airtel_money": "Airtel Money",

        "afrimoney": "Afrimoney",

        "carte": "Carte Bancaire",

        "virement": "Virement Bancaire"

    }



    def create_paid_account_invoice(patient_id, exchange_rate, payment_currency, amount_received, payment_key=None, payment_method="especes", payment_reference=""):

        """Crée la facture imprimable des lignes du compte qui viennent d'être réglées."""

        method_label = PAYMENT_METHOD_LABELS.get(str(payment_method).lower(), str(payment_method).capitalize())

        all_lines = supabase.table("patient_account_lines").select("*").eq(

            "patient_id", patient_id

        ).execute().data or []

        pending = [l for l in all_lines if str(l.get("status", "")).lower() not in ("invoiced", "cancelled")]

        if not pending:

            pending = [l for l in all_lines if str(l.get("status", "")).lower() != "cancelled"]

        if not pending:

            return None



        items, total_usd = [], 0.0

        for line in pending:

            amount_usd = round(to_float(line.get("amount_usd"), to_float(line.get("amount"), 0)), 2)

            origin = str(line.get("currency_origin") or "USD").upper()

            if origin == "CDF":

                origin = "FC"

            amount_origin = round(to_float(line.get("amount_origin"), amount_usd if origin == "USD" else amount_usd * exchange_rate), 2)

            quantity = max(1, to_int(line.get("quantity"), 1))

            unit_origin = round(to_float(line.get("unit_price_origin"), amount_origin / quantity), 2)

            total_usd += amount_usd

            items.append({

                "account_line_id": line.get("id"),

                "code": line.get("source") or line.get("category") or "PRESTATION",

                # La clé technique d'idempotence reste dans le compte, jamais

                # sur le document remis au patient.

                "description": str(line.get("description", "Prestation")).split(" [idemp:", 1)[0],

                "quantity": quantity,

                "unit_price": unit_origin,

                "amount": amount_origin,

                "currency": origin,

                "price_currency": origin,

                "unit_price_usd": round(amount_usd / quantity, 2),

                "amount_usd": amount_usd,

                "amount_fc": round(amount_origin if origin == "FC" else amount_usd * exchange_rate, 2),

            })



        now = now_iso()

        invoice = {

            "invoice_number": f"FAC-{int(time.time())}-{secrets.token_hex(2).upper()}",

            "patient_id": patient_id,

            "amount": round(total_usd, 2),

            "amount_usd": round(total_usd, 2),

            "amount_cdf": round(total_usd * exchange_rate, 2),

            "exchange_rate": exchange_rate,

            "description": f"Prestations réglées ({method_label})" + (f" [payment:{payment_key}]" if payment_key else ""),

            "status": "paid",

            "paid_amount": round(total_usd, 2),

            "paid_at": now,

            "payment_currency": payment_currency,

            "amount_received": amount_received,

            "payment_method": payment_method,

            "payment_mode": method_label,

            "payment_reference": payment_reference,

            "payment_note": f"Règlement par {method_label}" + (f" (Réf: {payment_reference})" if payment_reference else ""),

            "line_items": items,

            "items": items,

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "paid_by_user_id": g.current_user["id"],

            "paid_by_name": g.current_user["name"],

            "created_at": now,

            "updated_at": now,

        }

        result = compatible_insert(TABLES["billing"], invoice)

        created = result.data[0] if result.data else invoice

        if created.get("id"):

            for line in pending:

                compatible_update("patient_account_lines", {

                    "status": "invoiced", "invoice_id": created["id"], "updated_at": now

                }, "id", line.get("id"))

            add_invoice_payment(

                created["id"], patient_id, round(total_usd, 2),

                f"Règlement {method_label}" + (f" (Réf: {payment_reference})" if payment_reference else ""),

                payment_method=payment_method, payment_reference=payment_reference

            )

        return created



    @billing.route("/api/tariffs", methods=["GET", "POST"])
    @roles_required("super_admin", "reception")
    def tariffs_compat():
        if request.method == "GET":
            category = request.args.get("category")
            query = supabase.table(TABLES["tariffs"]).select("*")
            if category:
                query = query.eq("category", category)
            rows = query.order("category").execute().data or []
            for r in rows:
                amt = to_float(r.get("amount") or r.get("price_usd") or r.get("price"), 0)
                r["amount"] = amt
                r["price_usd"] = amt
            return jsonify(rows)

        # POST - creation (admin seulement)
        if g.current_user.get("role") != "super_admin":
            return jsonify({"error": "Modification reservee a l'administration"}), 403
        data = fast_json()
        category = str(data.get("category", "")).strip()
        label = str(data.get("label", "")).strip()
        code = str(data.get("code") or "").strip().upper()
        amount = round(to_float(data.get("amount") or data.get("price_usd") or data.get("price"), 0), 2)
        if not category or not label or amount < 0:
            return jsonify({"error": "Categorie, libelle et montant requis"}), 422
        payload = {
            "code": code, "category": category, "label": label, "amount": amount,
            "is_active": data.get("is_active", True),
            "created_by": g.current_user["id"], "created_by_name": g.current_user["name"],
            "created_at": now_iso(), "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["tariffs"], payload)
        tariff = result.data[0] if result.data else payload
        try:
            compatible_insert(TABLES["tariff_history"], {
                "tariff_id": tariff.get("id"), "category": category, "label": label,
                "old_amount": 0, "new_amount": amount, "action": "CREATE",
                "created_by": g.current_user["id"], "created_by_name": g.current_user["name"],
                "created_at": now_iso()
            })
        except Exception:
            pass
        add_audit("CREATE", "tariff", f"Tarif {category}: {label} = {amount}", tariff.get("id"))
        invalidate_cache()
        return jsonify(tariff), 201

    @billing.route("/api/tariffs/<int:tariff_id>", methods=["PUT", "DELETE"])
    @roles_required("super_admin")
    def tariff_item_compat(tariff_id: int):
        """Compatibilité de la grille Admin avec la table tariff_grid."""
        existing = supabase.table(TABLES["tariffs"]).select("*").eq("id", tariff_id).execute().data or []
        if not existing:
            return jsonify({"error": "Tarif introuvable"}), 404
        current = existing[0]

        if request.method == "DELETE":
            supabase.table(TABLES["tariffs"]).delete().eq("id", tariff_id).execute()
            try:
                compatible_insert(TABLES["tariff_history"], {
                    "tariff_id": tariff_id, "category": current.get("category"), "label": current.get("label"),
                    "old_amount": to_float(current.get("amount"), 0), "new_amount": None,
                    "action": "DELETE", "created_by": g.current_user["id"],
                    "created_by_name": g.current_user["name"], "created_at": now_iso()
                })
            except Exception:
                pass
            add_audit("DELETE", "tariff", f"Tarif #{tariff_id} supprimé", tariff_id)
            invalidate_cache()
            return jsonify({"message": "Tarif supprimé"})

        data = fast_json()
        amount = round(to_float(data.get("amount", data.get("price_usd", current.get("amount"))), 0), 2)
        if amount < 0:
            return jsonify({"error": "Montant invalide"}), 422
        updates = {
            "code": str(data.get("code", current.get("code") or "")).strip().upper(),
            "category": str(data.get("category", current.get("category") or "")).strip(),
            "label": str(data.get("label", current.get("label") or "")).strip(),
            "amount": amount,
            "is_active": data.get("is_active", current.get("is_active", True)),
            "updated_at": now_iso()
        }
        if not updates["category"] or not updates["label"]:
            return jsonify({"error": "Catégorie et libellé requis"}), 422
        result = compatible_update(TABLES["tariffs"], updates, "id", tariff_id)
        try:
            compatible_insert(TABLES["tariff_history"], {
                "tariff_id": tariff_id, "category": updates["category"], "label": updates["label"],
                "old_amount": to_float(current.get("amount"), 0), "new_amount": amount,
                "action": "UPDATE", "created_by": g.current_user["id"],
                "created_by_name": g.current_user["name"], "created_at": now_iso()
            })
        except Exception:
            pass
        add_audit("UPDATE", "tariff", f"Tarif #{tariff_id} modifié", tariff_id)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else updates)


    @billing.route("/api/exchange-rate", methods=["GET"])

    @billing.route("/api/billing/exchange-rate", methods=["GET"])

    @roles_required(*ROLES["staff"])

    @cached(timeout=60)

    def get_exchange_rate():

        try:

            result = supabase.table("exchange_rates").select("*").order("created_at", desc=True).limit(1).execute()

            if result.data:

                return jsonify(result.data[0])

        except Exception:

            pass

        return jsonify({"rate": None, "currency_from": "USD", "currency_to": "CDF", "created_at": now_iso()})



    @billing.route("/api/exchange-rate", methods=["POST"])

    @roles_required("super_admin", "reception")

    def set_exchange_rate():

        data = fast_json()

        rate = to_float(data.get("rate"))

        if rate <= 0:

            return jsonify({"error": "Le taux doit être supérieur à 0"}), 422

        

        currency_from = data.get("from", "USD")

        currency_to = data.get("to", "CDF")

        

        result = compatible_insert("exchange_rates", {

            "rate": rate,

            "currency_from": currency_from,

            "currency_to": currency_to,

            "set_by": g.current_user.get("id"),

            "set_by_name": g.current_user.get("name") or g.current_user.get("email"),

            "created_at": now_iso()

        })

        

        invalidate_cache()

        add_audit("CREATE", "exchange_rate", f"Taux: 1 {currency_from} = {rate} {currency_to}", None)

        return jsonify(result.data[0] if result.data else {"rate": rate, "currency_from": currency_from, "currency_to": currency_to, "created_at": now_iso()}), 201



    @billing.route("/api/exchange-rate/history", methods=["GET"])

    @roles_required("super_admin", "reception")

    @cached(timeout=300)

    def get_exchange_rate_history():

        limit = to_int(request.args.get("limit"), 50)

        result = supabase.table("exchange_rates").select("*").order("created_at", desc=True).limit(min(limit, 100)).execute()

        return jsonify(result.data or [])



    @billing.route("/api/billing/stats", methods=["GET"])

    @roles_required(*ROLES["staff"])

    def get_billing_stats():

        rate = get_current_rate() or 2250.0

        today_local = datetime.now().strftime("%Y-%m-%d")

        today_utc = now_iso().split("T")[0]

        

        # 1. Transactions de paiements (comptes patients)

        transactions = supabase.table("patient_account_transactions").select("amount, type, created_at").execute().data or []

        

        total_encaisse = 0.0

        total_today = 0.0

        

        for tx in transactions:

            amt = to_float(tx.get("amount"), 0)

            if str(tx.get("type", "")).lower() == "credit" or amt < 0:

                # Les transactions du compte patient sont stockées en USD

                # canonique. Ne jamais déduire la devise depuis la taille du

                # nombre : 1 500 USD ne doit pas être traité comme 1 500 FC.

                val_usd = abs(amt)

                total_encaisse += val_usd

                

                tx_date = str(tx.get("created_at") or "")

                if tx_date.startswith(today_local) or tx_date.startswith(today_utc):

                    total_today += val_usd

                    

        # 2. Total facturé et Impayés globaux (hors lignes annulées)

        lines = supabase.table("patient_account_lines").select("amount, status").execute().data or []

        total_facture = 0.0

        for l in lines:

            if str(l.get("status", "")).lower() != "cancelled":

                # Patient account lines are canonical USD. Older FC lines are

                # not reinterpreted here: migration/backfill must label them.

                total_facture += to_float(l.get("amount"), 0)

                

        total_encaisse = round(total_encaisse, 2)

        total_facture = round(total_facture, 2)

        solde_global = round(max(0.0, total_facture - total_encaisse), 2)

        

        return jsonify({

            "total_today": round(total_today, 2),

            "total_encaisse": total_encaisse,

            "total_facture": total_facture,

            "solde_global": solde_global

        })



    # ==================== MEDICAL BOXES (MAX 3) ====================



    @billing.route("/api/billing", methods=["GET"])

    @roles_required(*ROLES["staff"])

    @cached(60)

    def get_invoices():

        status = request.args.get("status")

        patient_id = request.args.get("patient_id")

        query = supabase.table(TABLES["billing"]).select("*")

        if status:

            query = query.eq("status", status)

        if patient_id:

            query = query.eq("patient_id", to_int(patient_id))

        result = query.order("created_at", desc=True).execute()

        invoices = result.data

        patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()

        patient_map = {p["id"]: p["full_name"] for p in patients_result.data}

        for inv in invoices:

            inv["patient_name"] = patient_map.get(inv.get("patient_id"), "Inconnu")

        return jsonify(invoices)



    @billing.route("/api/billing", methods=["POST"])

    @roles_required("super_admin", "reception")

    def create_invoice():

        data = fast_json()

        if not data.get("patient_id"):

            return jsonify({"error": "Patient requis"}), 422

        line_items = data.get("line_items") or data.get("items") or []

        calculated_amount = 0

        for item in line_items:

            qty = to_int(item.get("quantity"), 0)

            price = to_float(item.get("unit_price") or item.get("price"), 0)

            calculated_amount += qty * price

        amount = round(calculated_amount if calculated_amount > 0 else to_float(data.get("amount"), 0), 2)

        if amount <= 0:

            return jsonify({"error": "Montant invalide"}), 422

        invoice = {

            "invoice_number": f"FAC-{int(time.time())}-{secrets.token_hex(2).upper()}",

            "patient_id": to_int(data.get("patient_id")),

            "amount": amount,

            "description": data.get("description", ""),

            "status": "unpaid",

            "subscriber_id": data.get("subscriber_id"),

            "line_items": line_items,

            "items": line_items,

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso(),

            "updated_at": now_iso()

        }

        result = compatible_insert(TABLES["billing"], invoice)

        

        if data.get("subscriber_id"):

            subscriber = supabase.table("subscribers").select("coverage_rate").eq("id", data.get("subscriber_id")).execute()

            if subscriber.data:

                coverage = to_float(subscriber.data[0].get("coverage_rate", 0))

                patient_share = amount * (1 - coverage / 100)

                if patient_share > 0:

                    add_patient_account_line(to_int(data.get("patient_id")), "facture", f"Facture #{result.data[0]['id']} (part patient)", patient_share, "billing", result.data[0]["id"])

        else:

            add_patient_account_line(to_int(data.get("patient_id")), "facture", f"Facture #{result.data[0]['id']}", amount, "billing", result.data[0]["id"])

        

        add_audit("CREATE", "billing", f"Facture #{result.data[0]['id']}: {amount}", result.data[0]["id"])

        invalidate_cache()

        return jsonify(result.data[0]), 201



    @billing.route("/api/billing/grouped", methods=["POST"])

    @roles_required("super_admin", "pharmacie", "reception")

    def create_grouped_invoice():

        data = fast_json()

        items = data.get("items") or data.get("line_items") or []

        if not data.get("patient_id"):

            return jsonify({"error": "Patient requis"}), 422

        if not items:

            return jsonify({"error": "Aucun article à facturer"}), 422

        batch_key = str(data.get("idempotency_key") or data.get("uid") or "").strip()
        if not batch_key:
            return jsonify({"error": "Clé d'idempotence requise pour la facture groupée"}), 422
        previous = supabase.table(TABLES["billing"]).select("*").eq("patient_id", to_int(data.get("patient_id"))).ilike(
            "description", f"%[idemp:{batch_key}]%"
        ).execute().data or []
        if previous:
            return jsonify({"invoice": previous[0], "idempotent": True}), 200

        normalized_items = []

        total_usd = 0.0

        rate = get_current_rate() or 2250.0

        for item in items:

            qty = max(1, to_int(item.get("quantity"), 1))

            unit_price = max(0, to_float(item.get("unit_price") or item.get("price"), 0))

            amount = round(to_float(item.get("amount"), qty * unit_price), 2)

            currency = "USD" if str(item.get("currency") or item.get("price_currency") or "USD").upper() == "USD" else "FC"

            amount_usd = amount if currency == "USD" else round(amount / rate, 2)

            total_usd += amount_usd

            normalized_items.append({

                "medication_id": item.get("medication_id"),

                "code": item.get("code", ""),

                "description": item.get("description", ""),

                "quantity": qty,

                "unit_price": unit_price,

                "amount": amount,

                "currency": currency,

                "price_currency": currency,

                "amount_usd": amount_usd,

                "amount_fc": amount if currency == "FC" else round(amount * rate, 2)

            })

        if total_usd <= 0:

            return jsonify({"error": "Montant invalide"}), 422

        # Les ventes pharmacie à crédit vont exclusivement dans le compte
        # patient. Une facture officielle ne sera créée qu'au règlement du
        # compte, ce qui supprime les doublons de factures impayées.
        if data.get("source") == "pharmacy" and data.get("status", "unpaid") != "paid":
            prior_lines = supabase.table("patient_account_lines").select("id").eq(
                "patient_id", to_int(data.get("patient_id"))
            ).ilike("description", f"%[idemp:{batch_key}-0]%").execute().data or []
            if prior_lines:
                return jsonify({"invoice": None, "idempotent": True, "message": "Vente déjà enregistrée"}), 200
            for index, item in enumerate(normalized_items):
                med_id = to_int(item.get("medication_id"), 0)
                qty = to_int(item.get("quantity"), 0)
                current = supabase.table(TABLES["pharmacy"]).select("quantity").eq("id", med_id).execute().data or []
                if not med_id or qty <= 0 or not current or to_int(current[0].get("quantity"), 0) < qty:
                    return jsonify({"error": f"Stock insuffisant pour {item.get('description') or 'le médicament'}"}), 422
                add_patient_account_line(
                    to_int(data.get("patient_id")), "medicament", item.get("description", "Produit pharmacie"),
                    item.get("amount"), "pharmacy_invoice", None, qty, item.get("unit_price"),
                    f"{batch_key}-{index}", item.get("currency") or item.get("price_currency") or "FC"
                )
                supabase.table(TABLES["pharmacy"]).update({
                    "quantity": to_int(current[0].get("quantity"), 0) - qty, "updated_at": now_iso()
                }).eq("id", med_id).execute()
                compatible_insert("pharmacy_movements", {
                    "medication_id": med_id, "medication_name": item.get("description", ""),
                    "type": "sortie", "quantity": qty,
                    "reason": f"Vente compte patient [idemp:{batch_key}]", "patient_id": data.get("patient_id"),
                    "created_by": g.current_user["id"], "created_by_name": g.current_user["name"], "created_at": now_iso()
                })
            add_audit("CREATE", "pharmacy_account", f"Vente pharmacie ajoutée au compte patient #{data.get('patient_id')}", to_int(data.get("patient_id")))
            invalidate_cache()
            return jsonify({"invoice": None, "message": "Vente ajoutée au compte patient"}), 201

        invoice = {

            "invoice_number": f"FAC-{int(time.time())}-{secrets.token_hex(2).upper()}",

            "patient_id": to_int(data.get("patient_id")),

            "amount": round(total_usd, 2),

            "amount_usd": round(total_usd, 2),

            "amount_cdf": round(total_usd * rate, 2),

            "exchange_rate": rate,

            "description": f"{data.get('description', 'Facture groupée')} [idemp:{batch_key}]",

            "status": data.get("status", "unpaid"),

            "payment_type": data.get("payment_type"),

            "line_items": normalized_items,

            "items": normalized_items,

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso(),

            "updated_at": now_iso()

        }

        result = compatible_insert(TABLES["billing"], invoice)

        invoice_id = result.data[0].get("id") if result.data else None

        

        if data.get("status") == "paid":

            add_invoice_payment(invoice_id, to_int(data.get("patient_id")), round(total_usd, 2), "Paiement immédiat")

        

        if data.get("source") == "pharmacy":

            for index, item in enumerate(normalized_items):

                med_id = to_int(item.get("medication_id"), 0)

                qty = to_int(item.get("quantity"), 0)

                line = add_patient_account_line(

                    to_int(data.get("patient_id")),

                    "medicament",

                    item.get("description", "Produit pharmacie"),

                    item.get("amount"),

                    "pharmacy_invoice",

                    None,

                    qty,

                    item.get("unit_price"),

                    f"{batch_key}-{index}",

                    item.get("currency") or item.get("price_currency") or "FC"

                )

                if line and invoice_id:

                    compatible_update("patient_account_lines", {"status": "invoiced", "invoice_id": invoice_id, "updated_at": now_iso()}, "id", line.get("id"))

                if not med_id or not qty:

                    continue

                current = supabase.table(TABLES["pharmacy"]).select("quantity").eq("id", med_id).execute()

                if current.data:

                    new_qty = max(0, to_int(current.data[0].get("quantity"), 0) - qty)

                    supabase.table(TABLES["pharmacy"]).update({"quantity": new_qty, "updated_at": now_iso()}).eq("id", med_id).execute()

                    compatible_insert("pharmacy_movements", {

                        "medication_id": med_id,

                        "medication_name": item.get("description", ""),

                        "type": "sortie",

                        "quantity": qty,

                        "reason": f"Facturation groupée #{invoice_id}",

                        "patient_id": data.get("patient_id"),

                        "created_by": g.current_user["id"],

                        "created_by_name": g.current_user["name"],

                        "created_at": now_iso()

                    })

        

        add_audit("CREATE", "billing", f"Facture groupée: {round(total_usd, 2)} USD", result.data[0]["id"])

        invalidate_cache()

        return jsonify({"invoice": result.data[0]}), 201



    @billing.route("/api/billing/<int:invoice_id>", methods=["GET"])
    @billing.route("/api/billing/invoices/<int:invoice_id>", methods=["GET"])

    @roles_required(*ROLES["staff"])

    def get_invoice(invoice_id: int):

        result = supabase.table(TABLES["billing"]).select("*").eq("id", invoice_id).execute()

        if not result.data:

            return jsonify({"error": "Facture introuvable"}), 404

        return jsonify(result.data[0])



    @billing.route("/api/billing/<int:invoice_id>", methods=["PUT", "PATCH"])

    @roles_required("super_admin", "reception")

    def update_invoice(invoice_id: int):

        data = fast_json()

        allowed = ["amount", "description", "status"]

        updates = {k: v for k, v in data.items() if k in allowed and v is not None}

        if "status" in updates:

            updates["status"] = "paid" if updates["status"] == "paid" else "unpaid"

            if updates["status"] == "paid":

                updates["paid_at"] = now_iso()

                updates["paid_by_user_id"] = g.current_user["id"]

                updates["paid_by_name"] = g.current_user["name"]

        updates["updated_at"] = now_iso()

        result = supabase.table(TABLES["billing"]).update(updates).eq("id", invoice_id).execute()

        if not result.data:

            return jsonify({"error": "Facture introuvable"}), 404

        add_audit("UPDATE", "billing", f"Facture #{invoice_id} modifiée", invoice_id)

        invalidate_cache()

        return jsonify(result.data[0])



    @billing.route("/api/billing/<int:invoice_id>/pay", methods=["PUT"])

    @roles_required("super_admin", "reception")

    def mark_invoice_paid(invoice_id: int):

        data = fast_json()

        amount = round(to_float(data.get("amount"), 0), 2)

        

        invoice = supabase.table(TABLES["billing"]).select("*").eq("id", invoice_id).execute()

        if not invoice.data:

            return jsonify({"error": "Facture introuvable"}), 404

        invoice_data = invoice.data[0]

        

        updates = {

            "status": "paid",

            "paid_at": now_iso(),

            "paid_by_user_id": g.current_user["id"],

            "paid_by_name": g.current_user["name"],

            "updated_at": now_iso()

        }

        

        if amount > 0 and amount < invoice_data.get("amount", 0):

            updates["paid_amount"] = amount

            updates["balance_due"] = invoice_data.get("amount", 0) - amount

            updates["status"] = "partial"

            add_invoice_payment(invoice_id, invoice_data.get("patient_id"), amount, "Paiement partiel")

        else:

            updates["paid_amount"] = invoice_data.get("amount", 0)

            updates["balance_due"] = 0

            add_invoice_payment(invoice_id, invoice_data.get("patient_id"), invoice_data.get("amount", 0), "Paiement complet")

        

        result = supabase.table(TABLES["billing"]).update(updates).eq("id", invoice_id).execute()

        if not result.data:

            return jsonify({"error": "Facture introuvable"}), 404

        

        compatible_insert("patient_account_transactions", {

            "patient_id": invoice_data.get("patient_id"),

            "amount": -amount if amount > 0 else -invoice_data.get("amount", 0),

            "type": "credit",

            "description": f"Paiement facture #{invoice_id}",

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso()

        })

        

        add_audit("UPDATE", "billing", f"Facture #{invoice_id} payée", invoice_id)

        invalidate_cache()

        return jsonify(result.data[0])



    @billing.route("/api/billing/<int:invoice_id>/partial", methods=["PATCH"])

    @roles_required("super_admin", "reception")

    def partial_pay_invoice(invoice_id: int):

        data = fast_json()

        amount = round(to_float(data.get("amount"), 0), 2)

        if amount <= 0:

            return jsonify({"error": "Montant invalide"}), 422

        

        invoice = supabase.table(TABLES["billing"]).select("*").eq("id", invoice_id).execute()

        if not invoice.data:

            return jsonify({"error": "Facture introuvable"}), 404

        invoice_data = invoice.data[0]

        

        paid_so_far = to_float(invoice_data.get("paid_amount", 0))

        total = to_float(invoice_data.get("amount", 0))

        new_paid = paid_so_far + amount

        

        updates = {

            "paid_amount": new_paid,

            "balance_due": max(0, total - new_paid),

            "status": "paid" if new_paid >= total else "partial",

            "updated_at": now_iso()

        }

        

        result = supabase.table(TABLES["billing"]).update(updates).eq("id", invoice_id).execute()

        add_invoice_payment(invoice_id, invoice_data.get("patient_id"), amount, f"Acompte / Paiement partiel")

        compatible_insert("patient_account_transactions", {

            "patient_id": invoice_data.get("patient_id"),

            "amount": -amount,

            "type": "credit",

            "description": f"Acompte facture #{invoice_id}",

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso()

        })

        

        add_audit("UPDATE", "billing", f"Paiement partiel #{invoice_id}: {amount}", invoice_id)

        invalidate_cache()

        return jsonify(result.data[0])



    @billing.route("/api/billing/merge", methods=["POST"])

    @roles_required("super_admin")

    def merge_invoices():

        data = fast_json()

        invoice_ids = data.get("invoice_ids", [])

        patient_id = to_int(data.get("patient_id"))

        if len(invoice_ids) < 2:

            return jsonify({"error": "Au moins 2 factures à fusionner"}), 422

        if not patient_id:

            return jsonify({"error": "Patient requis"}), 422

        

        total = 0

        merged_items = []

        for inv_id in invoice_ids:

            inv = supabase.table(TABLES["billing"]).select("*").eq("id", inv_id).execute()

            if not inv.data:

                continue

            inv_data = inv.data[0]

            total += to_float(inv_data.get("amount", 0))

            items = normalize_invoice_lines(inv_data)

            merged_items.extend(items)

            supabase.table(TABLES["billing"]).update({"status": "merged", "updated_at": now_iso()}).eq("id", inv_id).execute()

        

        new_invoice = {

            "invoice_number": f"MERGED-{int(time.time())}-{secrets.token_hex(2).upper()}",

            "patient_id": patient_id,

            "amount": round(total, 2),

            "description": f"Fusion de {len(invoice_ids)} factures",

            "status": "unpaid",

            "line_items": merged_items,

            "items": merged_items,

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso(),

            "updated_at": now_iso(),

            "merged_from": invoice_ids

        }

        result = compatible_insert(TABLES["billing"], new_invoice)

        add_audit("CREATE", "billing", f"Fusion de {len(invoice_ids)} factures", result.data[0]["id"])

        invalidate_cache()

        return jsonify(result.data[0]), 201



    @billing.route("/api/billing/<int:invoice_id>", methods=["DELETE"])

    @roles_required("super_admin")

    def delete_invoice(invoice_id: int):

        # 🛡️ Protection : interdire la suppression d'une facture déjà payée

        existing = supabase.table(TABLES["billing"]).select("id,status,invoice_number").eq("id", invoice_id).execute()

        if not existing.data:

            return jsonify({"error": "Facture introuvable"}), 404

        inv = existing.data[0]

        if inv.get("status") == "paid":

            return jsonify({

                "error": f"Impossible de supprimer la facture #{inv.get('invoice_number', invoice_id)} : elle a déjà été réglée.",

                "status": "paid"

            }), 403

        supabase.table(TABLES["billing"]).delete().eq("id", invoice_id).execute()

        add_audit("DELETE", "billing", f"Facture #{invoice_id} supprimée", invoice_id)

        invalidate_cache()

        return jsonify({"message": "Facture supprimée"})



    # ==================== BILLING PDF ====================



    @billing.route("/api/billing/<int:invoice_id>/pdf", methods=["GET"])

    @roles_required(*ROLES["staff"])

    def get_invoice_pdf(invoice_id: int):

        """Génère un PDF de la facture"""

        try:

            from reportlab.lib.pagesizes import A4

            from reportlab.pdfgen import canvas

            from reportlab.lib import colors

            from io import BytesIO

            

            invoice = supabase.table(TABLES["billing"]).select("*").eq("id", invoice_id).execute()

            if not invoice.data:

                return jsonify({"error": "Facture introuvable"}), 404

            

            inv = invoice.data[0]

            patient = supabase.table(TABLES["patients"]).select("full_name,phone,email").eq("id", inv.get("patient_id")).execute()

            patient_name = patient.data[0]["full_name"] if patient.data else "Inconnu"

            

            buffer = BytesIO()

            c = canvas.Canvas(buffer, pagesize=A4)

            width, height = A4

            

            # En-tête

            c.setFont("Helvetica-Bold", 20)

            c.drawString(50, height - 50, "FACTURE")

            c.setFont("Helvetica-Bold", 14)

            c.drawString(50, height - 80, f"N° {inv.get('invoice_number', '')}")

            

            c.setFont("Helvetica", 12)

            c.drawString(50, height - 110, f"Patient: {patient_name}")

            c.drawString(50, height - 130, f"Date: {inv.get('created_at', '')[:10]}")

            c.drawString(50, height - 150, f"Statut: {inv.get('status', '')}")

            c.drawString(50, height - 170, f"Montant: {inv.get('amount', 0):.2f} CDF")

            

            c.setFont("Helvetica", 10)

            c.drawString(50, height - 200, "Description: " + inv.get('description', ''))

            

            # Ligne de séparation

            c.line(50, height - 220, width - 50, height - 220)

            

            # Détails des articles

            y = height - 250

            c.setFont("Helvetica-Bold", 10)

            c.drawString(50, y, "Articles")

            y -= 20

            

            items = normalize_invoice_lines(inv)

            for item in items:

                if y < 50:

                    c.showPage()

                    y = height - 50

                c.setFont("Helvetica", 10)

                desc = item.get('description', '')[:50]

                qty = item.get('quantity', 1)

                price = item.get('unit_price', 0)

                amount = item.get('amount', 0)

                c.drawString(50, y, f"- {desc} x{qty} @ {price:.2f} = {amount:.2f}")

                y -= 20

            

            # Total

            y -= 20

            c.setFont("Helvetica-Bold", 12)

            c.drawString(50, y, f"TOTAL: {inv.get('amount', 0):.2f} CDF")

            

            c.save()

            buffer.seek(0)

            

            return Response(buffer.getvalue(), mimetype='application/pdf',

                           headers={"Content-Disposition": f"attachment;filename=facture_{invoice_id}.pdf"})

        except ImportError:

            return jsonify({"error": "Bibliothèque reportlab non installée"}), 500



    # ==================== BILLING ACCOUNTS ====================



    @billing.route("/api/billing/accounts", methods=["GET"])

    @roles_required("super_admin", "reception")

    @cached(120)

    def get_billing_accounts():

        # Source de vérité financière : Lignes facturées - Transactions payées

        lines = supabase.table("patient_account_lines").select("patient_id, amount, status").execute().data or []

        transactions = supabase.table("patient_account_transactions").select("patient_id, amount, type").execute().data or []

        

        patient_totals = {}

        for l in lines:

            pid = l.get("patient_id")

            if pid and str(l.get("status") or "").lower() != "cancelled":

                patient_totals[pid] = patient_totals.get(pid, 0.0) + to_float(l.get("amount"), 0)

                

        patient_paid = {}

        for tx in transactions:

            pid = tx.get("patient_id")

            if pid:

                amt = to_float(tx.get("amount"), 0)

                if str(tx.get("type", "")).lower() == "credit" or amt < 0:

                    patient_paid[pid] = patient_paid.get(pid, 0.0) + abs(amt)

                    

        accounts = []

        patients = get_patient_map()

        

        for pid, total_facture in patient_totals.items():

            total_paid = patient_paid.get(pid, 0.0)

            solde = round(max(0.0, total_facture - total_paid), 2)

            

            if solde > 0:

                accounts.append({

                    "patient_id": pid,

                    "patient_name": patients.get(pid, "Inconnu"),

                    "balance": solde,

                    "total_facture": round(total_facture, 2),

                    "total_paid": round(total_paid, 2),

                    "status": "active"

                })

        

        # Tri par solde décroissant (les plus gros débiteurs d'abord)

        accounts.sort(key=lambda x: x["balance"], reverse=True)

        return jsonify(accounts)



    @billing.route("/api/billing/accounts", methods=["POST"])

    @roles_required("super_admin", "reception")

    def create_billing_account():

        data = fast_json()

        patient_id = to_int(data.get("patient_id"))

        if not patient_id:

            return jsonify({"error": "Patient requis"}), 422

        

        existing = supabase.table("patient_accounts").select("*").eq("patient_id", patient_id).execute()

        if existing.data:

            return jsonify(existing.data[0]), 200

        

        account = {

            "patient_id": patient_id,

            "balance": 0,

            "status": "active",

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso(),

            "updated_at": now_iso()

        }

        result = compatible_insert("patient_accounts", account)

        add_audit("CREATE", "billing_account", f"Compte patient #{patient_id}", result.data[0]["id"])

        invalidate_cache()

        return jsonify(result.data[0] if result.data else account), 201



    @billing.route("/api/billing/accounts/<int:patient_id>", methods=["GET"])

    @roles_required("super_admin", "reception")

    def get_billing_account(patient_id: int):

        # Vérifier que l'ID n'est pas null

        if patient_id is None or patient_id <= 0:

            return jsonify({"error": "ID patient invalide"}), 400

        result = supabase.table("patient_accounts").select("*").eq("patient_id", patient_id).execute()

        if not result.data:

            return jsonify({"patient_id": patient_id, "balance": 0, "status": "inactive"})

        return jsonify(result.data[0])



    @billing.route("/api/billing/accounts/<int:patient_id>/full", methods=["GET"])

    @roles_required("super_admin", "reception")

    def get_full_billing_account(patient_id: int):

        # La vérité financière est calculée via: Facturation globale - Paiements globaux

        account_result = supabase.table("patient_accounts").select("*").eq("patient_id", patient_id).execute()

        lines = supabase.table("patient_account_lines").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        transactions = supabase.table("patient_account_transactions").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute().data or []

        patient_result = supabase.table(TABLES["patients"]).select("id,full_name,phone,hospital_id").eq("id", patient_id).execute()

        account = account_result.data[0] if account_result.data else {"patient_id": patient_id, "balance": 0, "status": "inactive"}

        account["patient"] = patient_result.data[0] if patient_result.data else None



        # Calcul robuste

        total_facture = round(sum(
            to_float(l.get("amount"), 0)
            for l in lines if str(l.get("status") or "").lower() != "cancelled"
        ), 2)

        

        total_paid_real = 0.0

        for tx in transactions:

            amt = to_float(tx.get("amount"), 0)

            if str(tx.get("type", "")).lower() == "credit" or amt < 0:

                total_paid_real += abs(amt)

        total_paid_real = round(total_paid_real, 2)

        

        solde_restant = round(max(0.0, total_facture - total_paid_real), 2)



        # Normalisation pour l'affichage de l'interface (badges de statut et libellé de service)

        normalized_lines = []

        for l in lines:

            line_copy = dict(l)

            db_status = str(line_copy.get("status") or "pending").lower()

            line_copy["status"] = "PAID" if db_status == "invoiced" else "PENDING"

            cat = str(line_copy.get("category") or line_copy.get("source") or "Général").capitalize()

            line_copy["service"] = cat

            normalized_lines.append(line_copy)



        # Normalisation des montants de transactions (valeur absolue positive pour l'affichage)

        normalized_payments = []

        for tx in transactions:

            tx_copy = dict(tx)

            raw_amt = to_float(tx_copy.get("amount"), 0)

            tx_copy["amount"] = abs(raw_amt)

            normalized_payments.append(tx_copy)



        account["status"] = "OPEN" if solde_restant > 0 else ("PAID" if lines else "EMPTY")

        account["lines"] = normalized_lines

        account["payments"] = normalized_payments

        account["total"] = solde_restant          # Reste à payer pour le nouveau frontend

        account["total_facture"] = total_facture  # Nouveau champ

        account["total_pending"] = solde_restant  # Fallback compatibilité ancien frontend

        account["total_paid"] = total_paid_real   # Vrai montant payé

        account["total_all"] = total_facture

        account["balance"] = solde_restant

        return jsonify(account)



    @billing.route("/api/billing/accounts/update", methods=["POST"])

    @roles_required("super_admin", "reception")

    def update_billing_account():

        data = fast_json()

        patient_id = to_int(data.get("patient_id"))

        amount = round(to_float(data.get("amount"), 0), 2)

        type_operation = data.get("type", "debit")

        description = data.get("description", "")

        

        # Validation des données

        if not patient_id:

            return jsonify({"error": "Patient requis"}), 422

        if amount == 0:

            return jsonify({"error": "Montant requis et doit être différent de 0"}), 422

        if type_operation not in ["debit", "credit"]:

            return jsonify({"error": "Type d'opération invalide. Utilisez 'debit' ou 'credit'"}), 422

        

        account = supabase.table("patient_accounts").select("*").eq("patient_id", patient_id).execute()

        if not account.data:

            new_account = {

                "patient_id": patient_id,

                "balance": 0,

                "status": "active",

                "created_by": g.current_user["id"],

                "created_by_name": g.current_user["name"],

                "created_at": now_iso(),

                "updated_at": now_iso()

            }

            result = compatible_insert("patient_accounts", new_account)

            account_data = result.data[0] if result.data else new_account

        else:

            account_data = account.data[0]

        

        current_balance = to_float(account_data.get("balance", 0))

        if type_operation == "debit":

            new_balance = current_balance + amount

        else:

            if amount > current_balance:

                return jsonify({"error": f"Solde insuffisant. Solde actuel: {current_balance}"}), 422

            new_balance = current_balance - amount

        

        update_result = supabase.table("patient_accounts").update({

            "balance": round(new_balance, 2),

            "updated_at": now_iso()

        }).eq("patient_id", patient_id).execute()

        

        transaction = {

            "patient_id": patient_id,

            "amount": amount if type_operation == "debit" else -amount,

            "type": type_operation,

            "description": description or f"{'Débit' if type_operation == 'debit' else 'Crédit'} compte",

            "balance_after": round(new_balance, 2),

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso()

        }

        compatible_insert("patient_account_transactions", transaction)

        

        add_audit("UPDATE", "billing_account", f"Compte patient #{patient_id}: {type_operation} {amount}", patient_id)

        invalidate_cache()

        return jsonify(update_result.data[0] if update_result.data else {"balance": new_balance})



    @billing.route("/api/billing/accounts/<int:patient_id>/transactions", methods=["GET"])

    @roles_required("super_admin", "reception")

    def get_account_transactions(patient_id: int):

        result = supabase.table("patient_account_transactions").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute()

        return jsonify(result.data or [])



    @billing.route("/api/billing/accounts/<int:patient_id>/pay", methods=["POST"])

    @roles_required("super_admin", "reception")

    def pay_patient_account_endpoint(patient_id: int):

        data = fast_json()

        raw_amount = to_float(data.get("amount"), 0)

        currency = str(data.get("currency") or "USD").upper()

        idempotency_key = data.get("idempotency_key") or data.get("payment_uid")

        payment_method = str(data.get("payment_method") or "especes").lower().strip()

        payment_reference = str(data.get("payment_reference") or "").strip()

        method_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method.capitalize())



        # 1. Protection Idempotence : si cette clé a déjà été traitée, renvoyer le succès précédent

        if idempotency_key:

            try:

                existing_tx = supabase.table("patient_account_transactions").select("*").eq("patient_id", patient_id).ilike("description", f"%[idemp:{idempotency_key}]%").execute().data

                if existing_tx:

                    tx = existing_tx[0]

                    previous_invoice = None

                    if idempotency_key:

                        previous = supabase.table(TABLES["billing"]).select("*").eq("patient_id", patient_id).ilike(

                            "description", f"%[payment:{idempotency_key}]%"

                        ).execute().data or []

                        previous_invoice = previous[0] if previous else None

                    return jsonify({

                        "message": "Paiement déjà enregistré (idempotence)",

                        "amount_paid_usd": abs(to_float(tx.get("amount"), 0)),

                        "balance": to_float(tx.get("balance_after"), 0),

                        "invoice": previous_invoice,

                    }), 200

            except Exception:

                pass



        # 2. Récupérer le taux du jour

        rate = get_current_rate()



        # 3. Conversion en USD selon la devise choisie

        if currency == "CDF" or currency == "FC":

            if not rate or rate <= 0:

                return jsonify({"error": "Taux de change non configuré. Veuillez d'abord définir le taux du jour."}), 422

            amount_usd = round(raw_amount / rate, 2)

        else:

            currency = "USD"

            amount_usd = round(raw_amount, 2)



        # 4. Calculer dynamiquement le solde réel actuel en USD

        lines = supabase.table("patient_account_lines").select("amount, status").eq("patient_id", patient_id).execute().data or []

        transactions = supabase.table("patient_account_transactions").select("amount, type").eq("patient_id", patient_id).execute().data or []

        

        total_facture = sum(to_float(l.get("amount"), 0) for l in lines if str(l.get("status", "")).lower() != "cancelled")

        total_paid = 0.0

        for tx in transactions:

            amt = to_float(tx.get("amount"), 0)

            if str(tx.get("type", "")).lower() == "credit" or amt < 0:

                total_paid += abs(amt)

        

        solde_restant = round(max(0.0, total_facture - total_paid), 2)



        if amount_usd <= 0:

            amount_usd = solde_restant

            raw_amount = round(amount_usd * rate, 2) if (currency == "CDF" and rate) else amount_usd



        if amount_usd <= 0:

            return jsonify({"error": "Aucun solde à régler pour ce patient"}), 422



        if round(amount_usd, 2) > solde_restant + 0.01:

            curr_solde_display = f"{solde_restant} $ USD" + (f" (≈ {int(solde_restant * rate):,} Fc)" if rate else "")

            return jsonify({"error": f"Le montant ({raw_amount} {currency}) dépasse le solde restant ({curr_solde_display})"}), 422



        new_balance = round(max(0.0, solde_restant - amount_usd), 2)



        # 5. Enregistrer la transaction de paiement avec mode de règlement

        desc_parts = [f"Règlement {method_label} ({raw_amount} {currency})"]

        if payment_reference:

            desc_parts.append(f"Réf: {payment_reference}")

        if idempotency_key:

            desc_parts.append(f"[idemp:{idempotency_key}]")

        stored_tx_desc = " - ".join(desc_parts)



        tx_payload = {

            "patient_id": patient_id,

            "amount": -amount_usd,

            "type": "credit",

            "description": stored_tx_desc,

            "payment_method": payment_method,

            "payment_reference": payment_reference,

            "currency": currency,

            "amount_received": raw_amount,

            "balance_after": new_balance,

            "idempotency_key": idempotency_key,

            "created_by": g.current_user["id"],

            "created_by_name": g.current_user["name"],

            "created_at": now_iso()

        }

        compatible_insert("patient_account_transactions", tx_payload)



        # 6. Mettre à jour le cache de balance

        account_res = supabase.table("patient_accounts").select("id").eq("patient_id", patient_id).execute()

        if account_res.data:

            supabase.table("patient_accounts").update({"balance": new_balance, "updated_at": now_iso()}).eq("patient_id", patient_id).execute()

        else:

            compatible_insert("patient_accounts", {

                "patient_id": patient_id, "balance": new_balance, "status": "active",

                "created_by": g.current_user["id"], "created_by_name": g.current_user["name"],

                "created_at": now_iso(), "updated_at": now_iso()

            })



        # 7. À solde nul, convertir les lignes du compte en une facture réglée

        # visible dans l'onglet Factures et donc immédiatement imprimable.

        paid_invoice = None

        if new_balance <= 0:

            paid_invoice = create_paid_account_invoice(

                patient_id, rate or 1, currency, raw_amount, idempotency_key,

                payment_method=payment_method, payment_reference=payment_reference

            )

            unpaid_invoices = supabase.table(TABLES["billing"]).select("id,amount").eq("patient_id", patient_id).neq("status", "paid").execute().data or []

            for inv in unpaid_invoices:

                if paid_invoice and inv.get("id") == paid_invoice.get("id"):

                    continue

                compatible_update(TABLES["billing"], {

                    "status": "paid", "paid_at": now_iso(), "paid_amount": inv.get("amount", 0),

                    "paid_by_user_id": g.current_user["id"], "paid_by_name": g.current_user["name"],

                    "updated_at": now_iso()

                }, "id", inv["id"])



        add_audit("PAYMENT", "patient_account", f"Compte #{patient_id} réglé : {raw_amount} {currency} via {method_label}" + (f" (Réf: {payment_reference})" if payment_reference else ""), patient_id)

        invalidate_cache()

        return jsonify({

            "message": "Compte réglé avec succès",

            "amount_paid_usd": amount_usd,

            "amount_received": raw_amount,

            "currency": currency,

            "payment_method": payment_method,

            "payment_mode": method_label,

            "payment_reference": payment_reference,

            "rate": rate,

            "balance": new_balance,

            "invoice": paid_invoice,

        }), 200



    # ==================== SUBSCRIBERS ====================





    # ==================== CLÔTURE DE CAISSE JOURNALIÈRE ====================



    @billing.route("/api/billing/daily-closure", methods=["GET"])

    @billing.route("/api/billing/caisse/rapport", methods=["GET"])

    @roles_required("super_admin", "reception")

    def get_daily_closure():

        """Rapport journalier de caisse consolidé par mode de paiement et par caissier."""

        target_date = (request.args.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()

        cashier_id = request.args.get("cashier_id")

        rate = get_current_rate() or 2250.0



        METHOD_CONFIG = {

            "especes": {"label": "Espèces (Cash)", "icon": "fa-money-bill-wave", "category": "cash"},

            "mpesa": {"label": "M-Pesa (Vodacom)", "icon": "fa-mobile-screen", "category": "mobile"},

            "orange_money": {"label": "Orange Money", "icon": "fa-mobile-screen", "category": "mobile"},

            "airtel_money": {"label": "Airtel Money", "icon": "fa-mobile-screen", "category": "mobile"},

            "afrimoney": {"label": "Afrimoney", "icon": "fa-mobile-screen", "category": "mobile"},

            "carte": {"label": "Carte Bancaire", "icon": "fa-credit-card", "category": "bank"},

            "virement": {"label": "Virement / Chèque", "icon": "fa-building-columns", "category": "bank"},

            "autre": {"label": "Autre mode", "icon": "fa-receipt", "category": "other"}

        }



        by_method = {

            k: {

                "code": k,

                "label": v["label"],

                "icon": v["icon"],

                "category": v["category"],

                "count": 0,

                "usd": 0.0,

                "cdf": 0.0,

                "total_usd_equiv": 0.0

            }

            for k, v in METHOD_CONFIG.items()

        }



        by_cashier = {}

        detailed_transactions = []

        patient_map = get_patient_map()



        try:

            tx_res = supabase.table("patient_account_transactions").select("*").execute().data or []

        except Exception:

            tx_res = []



        for tx in tx_res:

            created_at = str(tx.get("created_at") or "")

            if not created_at.startswith(target_date):

                continue



            amt = to_float(tx.get("amount"), 0)

            tx_type = str(tx.get("type", "")).lower()

            if tx_type != "credit" and amt >= 0:

                continue



            if cashier_id and str(tx.get("created_by")) != str(cashier_id):

                continue



            val_usd = round(abs(amt), 2)

            desc = str(tx.get("description") or "")

            method = str(tx.get("payment_method") or "").lower().strip()

            ref = str(tx.get("payment_reference") or "").strip()

            curr = str(tx.get("currency") or "").upper().strip()

            raw_amt = to_float(tx.get("amount_received"), 0)



            # Heuristique si colonne payment_method non renseignée

            if not method:

                desc_lower = desc.lower()

                if "mpesa" in desc_lower or "m-pesa" in desc_lower:

                    method = "mpesa"

                elif "orange" in desc_lower:

                    method = "orange_money"

                elif "airtel" in desc_lower:

                    method = "airtel_money"

                elif "afri" in desc_lower:

                    method = "afrimoney"

                elif "carte" in desc_lower or "visa" in desc_lower or "tpe" in desc_lower:

                    method = "carte"

                elif "virement" in desc_lower or "cheque" in desc_lower or "chèque" in desc_lower:

                    method = "virement"

                else:

                    method = "especes"



            if method not in by_method:

                method = "autre"



            if not curr:

                if " CDF" in desc or " FC" in desc:

                    curr = "CDF"

                else:

                    curr = "USD"



            if raw_amt <= 0:

                raw_amt = round(val_usd * rate, 2) if curr == "CDF" else val_usd



            cashier_name = tx.get("created_by_name") or "Caissier"

            cashier_id_val = str(tx.get("created_by") or "0")



            # Ventilation mode

            m_entry = by_method[method]

            m_entry["count"] += 1

            if curr in ("CDF", "FC"):

                m_entry["cdf"] = round(m_entry["cdf"] + raw_amt, 2)

            else:

                m_entry["usd"] = round(m_entry["usd"] + raw_amt, 2)

            m_entry["total_usd_equiv"] = round(m_entry["total_usd_equiv"] + val_usd, 2)



            # Ventilation caissier

            if cashier_name not in by_cashier:

                by_cashier[cashier_name] = {

                    "cashier_id": cashier_id_val,

                    "name": cashier_name,

                    "count": 0,

                    "usd": 0.0,

                    "cdf": 0.0,

                    "total_usd_equiv": 0.0

                }

            c_entry = by_cashier[cashier_name]

            c_entry["count"] += 1

            if curr in ("CDF", "FC"):

                c_entry["cdf"] = round(c_entry["cdf"] + raw_amt, 2)

            else:

                c_entry["usd"] = round(c_entry["usd"] + raw_amt, 2)

            c_entry["total_usd_equiv"] = round(c_entry["total_usd_equiv"] + val_usd, 2)



            patient_id = tx.get("patient_id")

            p_name = patient_map.get(patient_id, f"Patient #{patient_id}")



            tx_item = {

                "id": tx.get("id"),

                "created_at": created_at,

                "time": created_at[11:16] if len(created_at) >= 16 else "--:--",

                "patient_id": patient_id,

                "patient_name": p_name,

                "method": method,

                "method_label": METHOD_CONFIG[method]["label"],

                "currency": curr,

                "amount_received": raw_amt,

                "amount_usd": val_usd,

                "reference": ref,

                "cashier": cashier_name,

                "description": desc

            }

            detailed_transactions.append(tx_item)



        # Totaux consolidés

        grand_total_usd = sum(m["total_usd_equiv"] for m in by_method.values())

        total_cash_usd = by_method["especes"]["usd"]

        total_cash_cdf = by_method["especes"]["cdf"]

        total_mobile_usd = sum(by_method[m]["usd"] for m in ["mpesa", "orange_money", "airtel_money", "afrimoney"])

        total_mobile_cdf = sum(by_method[m]["cdf"] for m in ["mpesa", "orange_money", "airtel_money", "afrimoney"])

        total_bank_usd = by_method["carte"]["usd"] + by_method["virement"]["usd"]

        total_bank_cdf = by_method["carte"]["cdf"] + by_method["virement"]["cdf"]



        closure_record = None

        try:

            c_res = supabase.table("cash_closures").select("*").eq("closure_date", target_date).order("created_at", desc=True).limit(1).execute()

            if c_res.data:

                closure_record = c_res.data[0]

        except Exception:

            pass



        return jsonify({

            "date": target_date,

            "rate": rate,

            "is_closed": bool(closure_record),

            "closure_record": closure_record,

            "summary": {

                "total_transactions": len(detailed_transactions),

                "grand_total_usd_equiv": round(grand_total_usd, 2),

                "cash": {

                    "usd": round(total_cash_usd, 2),

                    "cdf": round(total_cash_cdf, 2)

                },

                "mobile_money": {

                    "usd": round(total_mobile_usd, 2),

                    "cdf": round(total_mobile_cdf, 2)

                },

                "bank": {

                    "usd": round(total_bank_usd, 2),

                    "cdf": round(total_bank_cdf, 2)

                },

                "all_usd": round(sum(m["usd"] for m in by_method.values()), 2),

                "all_cdf": round(sum(m["cdf"] for m in by_method.values()), 2)

            },

            "by_method": by_method,

            "by_cashier": list(by_cashier.values()),

            "transactions": sorted(detailed_transactions, key=lambda x: x["created_at"], reverse=True)

        })



    @billing.route("/api/billing/daily-closure", methods=["POST"])

    @roles_required("super_admin", "reception")

    def post_daily_closure():

        """Enregistre la validation officielle de clôture de caisse avec comptage physique."""

        data = fast_json()

        target_date = (data.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()

        notes = data.get("notes") or ""

        cash_usd_physical = to_float(data.get("physical_cash_usd"), 0)

        cash_cdf_physical = to_float(data.get("physical_cash_cdf"), 0)



        closure_payload = {

            "closure_date": target_date,

            "closed_by_id": g.current_user["id"],

            "closed_by_name": g.current_user["name"],

            "physical_cash_usd": cash_usd_physical,

            "physical_cash_cdf": cash_cdf_physical,

            "notes": notes,

            "status": "closed",

            "closed_at": now_iso(),

            "created_at": now_iso()

        }

        try:
            existing = supabase.table("cash_closures").select("id").eq("closure_date", target_date).limit(1).execute().data or []
            if existing:
                res = supabase.table("cash_closures").update(closure_payload).eq("id", existing[0]["id"]).execute()
                created = res.data[0] if (res and res.data) else {**closure_payload, "id": existing[0]["id"]}
            else:
                res = compatible_insert("cash_closures", closure_payload)
                if not res.data:
                    return jsonify({"error": "La cloture n'a pas pu etre enregistree"}), 500
                created = res.data[0]
        except Exception as exc:
            # Ne jamais afficher une fausse réussite : la réception doit savoir
            # qu'une migration manquante ou une panne empêche la clôture.
            print(f"[CASH_CLOSURE] Enregistrement impossible: {exc}")
            return jsonify({"error": "Clôture non enregistrée. Vérifiez la table cash_closures."}), 503


        add_audit("CASH_CLOSURE", "billing", f"Clôture de caisse du {target_date} validée par {g.current_user['name']}")

        invalidate_cache()

        return jsonify({

            "message": f"Clôture de caisse du {target_date} validée avec succès",

            "closure": created

        }), 200



    app.register_blueprint(billing)

