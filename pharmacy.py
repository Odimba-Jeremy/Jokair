"""Module pharmacie I-HUB : stock, délivrance, mouvements et caisse."""
from flask import Blueprint


def register_pharmacy_routes(app, *, runtime):
    globals().update(runtime)
    pharmacy = Blueprint("pharmacy", __name__)

    # ==================== HELPERS ====================
    def infer_pharmacy_category(item: dict) -> str:
        """Déduit la catégorie pharmacie d'un item (medicament, injectable, consommable)."""
        cat = str(item.get("category") or "").lower().strip()
        if cat in ("medicament", "médicament", "medicaments"):
            return "medicament"
        if cat in ("injectable", "injectables"):
            return "injectable"
        if cat in ("consommable", "consommables", "consumable"):
            return "consommable"
        if item.get("route") or "injectable" in str(item.get("form") or "").lower() or "ampoule" in str(item.get("form") or "").lower():
            return "injectable"
        if item.get("consumable_type") or item.get("size"):
            return "consommable"
        name = str(item.get("medication_name") or item.get("product_name") or item.get("name") or "").lower()
        if any(k in name for k in ("seringue", "aiguille", "compresse", "gant", "sparadrap", "coton", "pansement", "catheter", "cathéter", "sonde", "masque", "perfuseur", "tubulure", "bistouri", "garrot")):
            return "consommable"
        if any(k in name for k in ("injectable", "inj", "ampoule", "perfusion", "perf")):
            return "injectable"
        return "medicament"

    @pharmacy.route("/api/pharmacy", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(60)
    def get_pharmacy():
        low_stock = request.args.get("low_stock", "false").lower() == "true"
        result = supabase.table(TABLES["pharmacy"]).select("*").order("medication_name").execute()
        items = result.data or []
        for item in items:
            if not item.get("category"):
                item["category"] = infer_pharmacy_category(item)
        if low_stock:
            items = [i for i in items if i.get("quantity", 0) <= i.get("threshold", 10)]
        return jsonify(items)

    @pharmacy.route("/api/pharmacy", methods=["POST"])
    @roles_required("super_admin", "pharmacie")
    def create_pharmacy_item():
        data = fast_json()
        if not data.get("medication_name"):
            return jsonify({"error": "Nom du médicament requis"}), 422
        raw_cat = str(data.get("category") or "").lower().strip()
        if raw_cat in ("medicament", "médicament", "medicaments"):
            clean_cat = "medicament"
        elif raw_cat in ("injectable", "injectables"):
            clean_cat = "injectable"
        elif raw_cat in ("consommable", "consommables", "consumable"):
            clean_cat = "consommable"
        else:
            clean_cat = infer_pharmacy_category(data)

        item = {
            "medication_name": data["medication_name"],
            "quantity": max(0, to_int(data.get("quantity"), 0)),
            "unit": data.get("unit", "comprimé(s)"),
            "purchase_price": max(0, to_float(data.get("purchase_price"), 0)),
            "selling_price": max(0, to_float(data.get("selling_price"), 0)),
            "threshold": max(0, to_int(data.get("threshold"), 10)),
            "expiry_date": optional_date(data.get("expiry_date")),
            "category": clean_cat,
            "form": data.get("form"),
            "dosage": data.get("dosage"),
            "dosage_unit": data.get("dosage_unit"),
            "route": data.get("route"),
            "consumable_type": data.get("consumable_type"),
            "size": data.get("size"),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["pharmacy"], item)
        add_audit("CREATE", "pharmacy", f"Médicament: {data['medication_name']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @pharmacy.route("/api/pharmacy/<int:item_id>", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_pharmacy_item(item_id: int):
        result = supabase.table(TABLES["pharmacy"]).select("*").eq("id", item_id).execute()
        if not result.data:
            return jsonify({"error": "Médicament introuvable"}), 404
        return jsonify(result.data[0])

    @pharmacy.route("/api/pharmacy/<int:item_id>", methods=["PUT"])
    @roles_required("super_admin", "pharmacie")
    def update_pharmacy_item(item_id: int):
        data = fast_json()
        allowed = ["medication_name", "unit", "purchase_price", "selling_price", "threshold", "expiry_date", "category", "form", "dosage", "dosage_unit", "route", "consumable_type", "size"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if "expiry_date" in updates:
            updates["expiry_date"] = optional_date(updates["expiry_date"])
        updates["updated_at"] = now_iso()
        result = supabase.table(TABLES["pharmacy"]).update(updates).eq("id", item_id).execute()
        if not result.data:
            return jsonify({"error": "Médicament introuvable"}), 404
        add_audit("UPDATE", "pharmacy", f"Médicament #{item_id} modifié", item_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @pharmacy.route("/api/pharmacy/<int:item_id>/stock", methods=["PUT"])
    @roles_required("super_admin", "pharmacie")
    def update_stock(item_id: int):
        data = fast_json()
        quantity = max(0, to_int(data.get("quantity"), 0))
        operation = data.get("operation", "set")
        reason = data.get("reason", "Ajustement manuel")
        # Champs optionnels pour traçabilité des livraisons de soins
        patient_id = data.get("patient_id")
        patient_name = data.get("patient_name")
        doctor_name = data.get("doctor_name")
        movement_type = data.get("movement_type")  # ex: "livraison_soin"

        item_result = supabase.table(TABLES["pharmacy"]).select("quantity,medication_name").eq("id", item_id).execute()
        if not item_result.data:
            return jsonify({"error": "Médicament introuvable"}), 404
        current = to_int(item_result.data[0].get("quantity"), 0)
        if operation == "add":
            new_qty = current + quantity
        elif operation == "remove":
            if quantity > current:
                return jsonify({"error": "Stock insuffisant"}), 422
            new_qty = current - quantity
        else:
            new_qty = quantity
        result = supabase.table(TABLES["pharmacy"]).update({"quantity": new_qty, "updated_at": now_iso()}).eq("id", item_id).execute()

        movement = {
            "medication_id": item_id,
            "medication_name": item_result.data[0].get("medication_name", "Médicament"),
            "type": movement_type if movement_type else ("entree" if operation == "add" else "sortie"),
            "quantity": quantity,
            "reason": reason,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        }
        # Supprimer les clés None pour éviter les erreurs Supabase
        movement = {k: v for k, v in movement.items() if v is not None}
        compatible_insert("pharmacy_movements", movement)

        add_audit("UPDATE", "pharmacy", f"Stock #{item_id}: {current} -> {new_qty}", item_id)
        invalidate_cache()
        return jsonify(result.data[0])

    @pharmacy.route("/api/pharmacy/<int:item_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_pharmacy_item(item_id: int):
        supabase.table(TABLES["pharmacy"]).delete().eq("id", item_id).execute()
        add_audit("DELETE", "pharmacy", f"Médicament #{item_id} supprimé", item_id)
        invalidate_cache()
        return jsonify({"message": "Médicament supprimé"})

    @pharmacy.route("/api/pharmacy/<int:item_id>/dispense", methods=["POST"])
    @roles_required("super_admin", "pharmacie")
    def dispense_medication(item_id: int):
        data = fast_json()
        if not data.get("patient_id"):
            return jsonify({"error": "Patient requis"}), 422
        quantity = to_int(data.get("quantity"), 0)
        if quantity <= 0:
            return jsonify({"error": "Quantité invalide"}), 422
        item_result = supabase.table(TABLES["pharmacy"]).select("*").eq("id", item_id).execute()
        if not item_result.data:
            return jsonify({"error": "Médicament introuvable"}), 404
        item = item_result.data[0]
        # 🛡️ Protection : vérifier la date de péremption
        expiry_date = item.get("expiry_date")
        if expiry_date:
            today = datetime.now(timezone.utc).date().isoformat()
            if str(expiry_date) < today:
                return jsonify({
                    "error": f"Le médicament '{item.get('medication_name')}' est périmé depuis le {expiry_date}. Dispensation impossible.",
                    "expiry_date": expiry_date
                }), 409
        # 🛡️ Protection : vérifier le stock disponible
        if item.get("quantity", 0) < quantity:
            return jsonify({"error": f"Stock insuffisant. Disponible: {item.get('quantity')}"}), 422
        unit_price = to_float(data.get("unit_price"), item.get("selling_price", 0))
        total_amount = round(quantity * unit_price, 2)
        patient_id = to_int(data.get("patient_id"))
        try:
            # ✅ Ajout direct au compte patient unique (Mode C)
            add_patient_account_line(
                patient_id,
                "pharmacie",
                f"Médicament: {item.get('medication_name')} x{quantity}",
                total_amount,
                "pharmacy_dispense",
                item_id,
                quantity,
                unit_price
            )

            new_qty = item.get("quantity", 0) - quantity
            supabase.table(TABLES["pharmacy"]).update({"quantity": new_qty, "updated_at": now_iso()}).eq("id", item_id).execute()

            compatible_insert("pharmacy_movements", {
                "medication_id": item_id,
                "medication_name": item.get("medication_name"),
                "type": "sortie",
                "quantity": quantity,
                "reason": f"Dispensation - Patient #{patient_id}",
                "patient_id": patient_id,
                "created_by": g.current_user["id"],
                "created_by_name": g.current_user["name"],
                "created_at": now_iso()
            })

            add_audit("UPDATE", "pharmacy", f"Dispensation: {item.get('medication_name')} x{quantity}", item_id)
            invalidate_cache()
            return jsonify({"message": "Médicament délivré et imputé sur le compte patient avec succès", "amount": total_amount}), 201
        except Exception as exc:
            return jsonify({"error": f"Erreur lors de la dispensation: {str(exc)}"}), 500

    @pharmacy.route("/api/pharmacy/medications/search", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def search_medications():
        search = request.args.get("q", "").strip().lower()
        if not search:
            return jsonify({"error": "Terme de recherche requis"}), 422
        result = supabase.table(TABLES["pharmacy"]).select("*").ilike("medication_name", f"%{search}%").execute()
        return jsonify(result.data)

    @pharmacy.route("/api/pharmacy/expiring", methods=["GET"])
    @roles_required("super_admin", "pharmacie")
    def get_expiring_medications():
        days = to_int(request.args.get("days"), 30)
        cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
        result = supabase.table(TABLES["pharmacy"]).select("*").lte("expiry_date", cutoff).execute()
        return jsonify(result.data)

    @pharmacy.route("/api/pharmacy/low-stock", methods=["GET"])
    @roles_required("super_admin", "pharmacie")
    def get_low_stock():
        result = supabase.table(TABLES["pharmacy"]).select("*").execute().data or []
        low_stock = [i for i in result if i.get("quantity", 0) <= i.get("threshold", 10)]
        return jsonify(low_stock)

    # ==================== PHARMACY MOVEMENTS ====================

    @pharmacy.route("/api/pharmacy/movements", methods=["GET"])
    @roles_required("super_admin", "pharmacie")
    def get_pharmacy_movements():
        result = supabase.table("pharmacy_movements").select("*").order("created_at", desc=True).execute()
        return jsonify(result.data or [])

    @pharmacy.route("/api/pharmacy/movements", methods=["POST"])
    @roles_required("super_admin", "pharmacie")
    def create_pharmacy_movement():
        data = fast_json()
        if not data.get("medication_id") or not data.get("type"):
            return jsonify({"error": "medication_id et type requis"}), 422
        movement = {
            "medication_id": data.get("medication_id"),
            "medication_name": data.get("medication_name", ""),
            "type": data.get("type"),
            "quantity": to_int(data.get("quantity"), 0),
            "reason": data.get("reason", ""),
            "patient_id": data.get("patient_id"),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        }
        result = compatible_insert("pharmacy_movements", movement)
        invalidate_cache()
        return jsonify(result.data[0] if result.data else movement), 201

    # ==================== PHARMACY PATIENT ACCOUNT ====================

    @pharmacy.route("/api/pharmacy/patient-account/add", methods=["POST"])
    @roles_required("super_admin", "pharmacie")
    def add_pharmacy_to_patient_account():
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        items = data.get("items", [])
        if not patient_id or not items:
            return jsonify({"error": "Patient et articles requis"}), 422

        total = 0
        for item in items:
            amount = to_float(item.get("amount"), to_float(item.get("unit_price"), 0) * to_int(item.get("quantity"), 1))
            total += amount
            add_patient_account_line(
                patient_id,
                "medicament",
                item.get("description", "Médicament"),
                amount,
                "pharmacy",
                item.get("medication_id")
            )

        add_audit("CREATE", "pharmacy_account", f"Ajout au compte patient #{patient_id}: {total}", patient_id)
        invalidate_cache()
        return jsonify({"message": "Montant ajouté au compte patient", "total": total}), 201

    # ==================== PHARMACY CASHIER ====================

    @pharmacy.route("/api/pharmacy/cashier", methods=["POST"])
    @roles_required("super_admin", "pharmacie")
    def pharmacy_cashier_payment():
        data = fast_json()
        patient_id = to_int(data.get("patient_id"))
        items = data.get("items", [])
        payment_type = data.get("payment_type", "cash")
        if not patient_id or not items:
            return jsonify({"error": "Patient et articles requis"}), 422

        total = 0
        item_list = []
        for item in items:
            qty = to_int(item.get("quantity"), 1)
            unit_price = to_float(item.get("unit_price"), 0)
            amount = qty * unit_price
            total += amount
            item_list.append({
                "description": item.get("description", "Médicament"),
                "quantity": qty,
                "unit_price": unit_price,
                "amount": amount
            })

        invoice = {
            "invoice_number": f"PHARMA-{int(time.time())}-{secrets.token_hex(2).upper()}",
            "patient_id": patient_id,
            "amount": total,
            "description": f"Vente pharmacie - {payment_type}",
            "status": "paid",
            "paid_at": now_iso(),
            "payment_type": payment_type,
            "line_items": item_list,
            "items": item_list,
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert(TABLES["billing"], invoice)
        add_audit("CREATE", "pharmacy_cashier", f"Vente pharmacie #{result.data[0]['id']}: {total}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0] if result.data else invoice), 201

    # ==================== BILLING ====================

    app.register_blueprint(pharmacy)
