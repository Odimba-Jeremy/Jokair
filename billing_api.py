"""Module facturation I-HUB : factures, comptes patients et paiements."""
from flask import Blueprint


def register_billing_routes(app, *, runtime):
    globals().update(runtime)
    billing = Blueprint("billing", __name__)

    @billing.route("/api/tariffs", methods=["GET", "POST"])
    @roles_required("super_admin")
    def tariffs_compat():
        return workflow_tariffs.__wrapped__()

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
                val = abs(amt)
                val_usd = round(val / rate, 2) if val > 1000 else val
                total_encaisse += val_usd
                
                tx_date = str(tx.get("created_at") or "")
                if tx_date.startswith(today_local) or tx_date.startswith(today_utc):
                    total_today += val_usd
                    
        # 2. Total facturé et Impayés globaux (hors lignes annulées)
        lines = supabase.table("patient_account_lines").select("amount, status").execute().data or []
        total_facture = 0.0
        for l in lines:
            if str(l.get("status", "")).lower() != "cancelled":
                amt = to_float(l.get("amount"), 0)
                amt_usd = round(amt / rate, 2) if amt > 1000 else amt
                total_facture += amt_usd
                
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
        normalized_items = []
        total = 0.0
        for item in items:
            qty = max(1, to_int(item.get("quantity"), 1))
            unit_price = max(0, to_float(item.get("unit_price") or item.get("price"), 0))
            amount = round(to_float(item.get("amount"), qty * unit_price), 2)
            total += amount
            normalized_items.append({
                "medication_id": item.get("medication_id"),
                "code": item.get("code", ""),
                "description": item.get("description", ""),
                "quantity": qty,
                "unit_price": unit_price,
                "amount": amount
            })
        if total <= 0:
            return jsonify({"error": "Montant invalide"}), 422
        invoice = {
            "invoice_number": f"FAC-{int(time.time())}-{secrets.token_hex(2).upper()}",
            "patient_id": to_int(data.get("patient_id")),
            "amount": round(total, 2),
            "description": data.get("description", "Facture groupée"),
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
            add_invoice_payment(invoice_id, to_int(data.get("patient_id")), round(total, 2), "Paiement immédiat")
        
        if data.get("source") == "pharmacy":
            for item in normalized_items:
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
                    item.get("unit_price")
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
        
        add_audit("CREATE", "billing", f"Facture groupée: {round(total, 2)}", result.data[0]["id"])
        invalidate_cache()
        return jsonify({"invoice": result.data[0]}), 201

    @billing.route("/api/billing/<int:invoice_id>", methods=["GET"])
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
        lines = supabase.table("patient_account_lines").select("patient_id, amount").execute().data or []
        transactions = supabase.table("patient_account_transactions").select("patient_id, amount, type").execute().data or []
        
        patient_totals = {}
        for l in lines:
            pid = l.get("patient_id")
            if pid:
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
        total_facture = round(sum(to_float(l.get("amount"), 0) for l in lines), 2)
        
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

        # 1. Protection Idempotence : si cette clé a déjà été traitée, renvoyer le succès précédent
        if idempotency_key:
            try:
                existing_tx = supabase.table("patient_account_transactions").select("*").eq("patient_id", patient_id).ilike("description", f"%[idemp:{idempotency_key}]%").execute().data
                if existing_tx:
                    tx = existing_tx[0]
                    return jsonify({
                        "message": "Paiement déjà enregistré (idempotence)",
                        "amount_paid_usd": abs(to_float(tx.get("amount"), 0)),
                        "balance": to_float(tx.get("balance_after"), 0)
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

        # 5. Enregistrer la transaction de paiement
        stored_tx_desc = f"Règlement ({raw_amount} {currency})" + (f" [idemp:{idempotency_key}]" if idempotency_key else "")
        tx_payload = {
            "patient_id": patient_id,
            "amount": -amount_usd,
            "type": "credit",
            "description": stored_tx_desc,
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

        # 7. Clôture complémentaire si solde intégralement payé
        if new_balance <= 0:
            unpaid_invoices = supabase.table(TABLES["billing"]).select("id,amount").eq("patient_id", patient_id).neq("status", "paid").execute().data or []
            for inv in unpaid_invoices:
                compatible_update(TABLES["billing"], {
                    "status": "paid", "paid_at": now_iso(), "paid_amount": inv.get("amount", 0),
                    "paid_by_user_id": g.current_user["id"], "paid_by_name": g.current_user["name"],
                    "updated_at": now_iso()
                }, "id", inv["id"])

            supabase.table("patient_account_lines").update({
                "status": "invoiced",
                "updated_at": now_iso()
            }).eq("patient_id", patient_id).eq("status", "pending").execute()

        add_audit("PAYMENT", "patient_account", f"Compte #{patient_id} réglé : {raw_amount} {currency} ({amount_usd} $)", patient_id)
        invalidate_cache()
        return jsonify({
            "message": "Compte réglé avec succès",
            "amount_paid_usd": amount_usd,
            "amount_received": raw_amount,
            "currency": currency,
            "rate": rate,
            "balance": new_balance
        }), 200

    # ==================== SUBSCRIBERS ====================

    app.register_blueprint(billing)
