"""Rapports, dashboard, notifications et supervision I-HUB."""
from flask import Blueprint


def register_reports_routes(app, *, runtime):
    globals().update(runtime)
    reports = Blueprint("reports", __name__)

    @reports.route("/api/reports/patients", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(300)
    def report_patients():
        date_from = request.args.get("from_date")
        date_to = request.args.get("to_date")
        status = request.args.get("status")
        gender = request.args.get("gender")
        query = supabase.table(TABLES["patients"]).select("*")
        if date_from:
            query = query.gte("created_at", date_from)
        if date_to:
            query = query.lte("created_at", date_to)
        if status:
            query = query.eq("status", status)
        if gender:
            query = query.eq("gender", gender)
        result = query.execute().data or []
        result = filter_patients_for_role(result)
        stats = {"total": len(result), "by_status": {}, "by_gender": {}, "by_age_group": {}, "admissions_by_month": {}}
        for patient in result:
            status_key = patient.get("status", "unknown")
            stats["by_status"][status_key] = stats["by_status"].get(status_key, 0) + 1
            gender_key = patient.get("gender", "unknown")
            stats["by_gender"][gender_key] = stats["by_gender"].get(gender_key, 0) + 1
            age = age_years(patient)
            if age is not None:
                group = "0-5" if age < 5 else "6-12" if age < 12 else "13-18" if age < 18 else "19-30" if age < 30 else "31-50" if age < 50 else "51-70" if age < 70 else "70+"
                stats["by_age_group"][group] = stats["by_age_group"].get(group, 0) + 1
            if patient.get("created_at"):
                month = patient["created_at"][:7]
                stats["admissions_by_month"][month] = stats["admissions_by_month"].get(month, 0) + 1
        return jsonify({"data": result, "stats": stats, "generated_at": now_iso()})

    @reports.route("/api/reports/financial", methods=["GET"])
    @roles_required("super_admin", "reception")
    @cached(300)
    def report_financial():
        date_from = request.args.get("from_date")
        date_to = request.args.get("to_date")
        query = supabase.table(TABLES["billing"]).select("*")
        if date_from:
            query = query.gte("created_at", date_from)
        if date_to:
            query = query.lte("created_at", date_to)
        invoices = query.execute().data or []
        total = sum(inv.get("amount", 0) for inv in invoices)
        paid = sum(inv.get("amount", 0) for inv in invoices if inv.get("status") == "paid")
        unpaid = total - paid
        by_category = {}
        for inv in invoices:
            items = normalize_invoice_lines(inv)
            for item in items:
                category = item.get("category", "autres")
                amount = item.get("amount", 0)
                by_category[category] = by_category.get(category, 0) + amount
        payments_by_month = {}
        for inv in invoices:
            if inv.get("status") == "paid" and inv.get("paid_at"):
                month = inv["paid_at"][:7]
                payments_by_month[month] = payments_by_month.get(month, 0) + inv.get("amount", 0)
        return jsonify({
            "summary": {"total_invoices": len(invoices), "total_amount": round(total, 2), "paid_amount": round(paid, 2), "unpaid_amount": round(unpaid, 2), "payment_rate": round((paid / total * 100) if total > 0 else 0, 2)},
            "by_category": by_category,
            "payments_by_month": payments_by_month,
            "invoices": invoices,
            "generated_at": now_iso()
        })

    @reports.route("/api/reports/pharmacy", methods=["GET"])
    @roles_required("super_admin", "pharmacie")
    @cached(300)
    def report_pharmacy():
        result = supabase.table(TABLES["pharmacy"]).select("*").execute().data or []
        total_items = len(result)
        total_value = sum(item.get("quantity", 0) * item.get("purchase_price", 0) for item in result)
        total_selling_value = sum(item.get("quantity", 0) * item.get("selling_price", 0) for item in result)
        low_stock = [item for item in result if item.get("quantity", 0) <= item.get("threshold", 10)]
        out_of_stock = [item for item in result if item.get("quantity", 0) == 0]
        return jsonify({
            "summary": {"total_items": total_items, "total_value": round(total_value, 2), "total_selling_value": round(total_selling_value, 2), "potential_profit": round(total_selling_value - total_value, 2), "low_stock_count": len(low_stock), "out_of_stock_count": len(out_of_stock)},
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "all_items": result,
            "generated_at": now_iso()
        })

    @reports.route("/api/reports/activity", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(300)
    def report_activity():
        date_from = request.args.get("from_date")
        date_to = request.args.get("to_date")
        tables = {"patients": TABLES["patients"], "appointments": TABLES["appointments"], "prescriptions": TABLES["prescriptions"], "lab_tests": TABLES["lab_tests"], "care": TABLES["care"], "billing": TABLES["billing"]}
        activity = {}
        for name, table in tables.items():
            query = supabase.table(table).select("*")
            if date_from:
                query = query.gte("created_at", date_from)
            if date_to:
                query = query.lte("created_at", date_to)
            result = query.execute().data or []
            activity[name] = {"count": len(result), "data": result}
        return jsonify({"activity": activity, "period": {"from": date_from, "to": date_to}, "generated_at": now_iso()})

    # ==================== DASHBOARD ====================

    @reports.route("/api/dashboard/stats", methods=["GET"])
    @roles_required(*ROLES["staff"])
    @cached(120)
    def dashboard_stats():
        patients = supabase.table(TABLES["patients"]).select("*").execute().data or []
        appointments = supabase.table(TABLES["appointments"]).select("*").execute().data or []
        prescriptions = supabase.table(TABLES["prescriptions"]).select("*").execute().data or []
        lab_tests = supabase.table(TABLES["lab_tests"]).select("*").execute().data or []
        pharmacy = supabase.table(TABLES["pharmacy"]).select("*").execute().data or []
        invoices = supabase.table(TABLES["billing"]).select("*").execute().data or []
        patients = filter_patients_for_role(patients)
        appointments = filter_appointments_for_role(appointments)
        today = datetime.now(timezone.utc).date().isoformat()
        today_appointments = [a for a in appointments if a.get("date", "")[:10] == today]
        today_patients = [p for p in patients if p.get("created_at", "")[:10] == today]
        low_stock = [i for i in pharmacy if i.get("quantity", 0) <= i.get("threshold", 10)]
        pending_tests = [t for t in lab_tests if t.get("status") == "pending"]
        unpaid_invoices = [i for i in invoices if i.get("status") != "paid"]
        total_revenue = sum(i.get("amount", 0) for i in invoices)
        paid_revenue = sum(i.get("amount", 0) for i in invoices if i.get("status") == "paid")
        return jsonify({
            "patients": {"total": len(patients), "today": len(today_patients)},
            "appointments": {"total": len(appointments), "today": len(today_appointments)},
            "prescriptions": {"total": len(prescriptions)},
            "laboratory": {"total": len(lab_tests), "pending": len(pending_tests)},
            "pharmacy": {"total": len(pharmacy), "low_stock": len(low_stock)},
            "billing": {"total": len(invoices), "unpaid": len(unpaid_invoices), "total_revenue": round(total_revenue, 2), "paid_revenue": round(paid_revenue, 2)},
            "generated_at": now_iso()
        })

    # ==================== NOTIFICATIONS ====================

    @reports.route("/api/notifications", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_notifications():
        user_id = g.current_user.get("id")
        result = supabase.table("notifications").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        return jsonify(result.data)

    @reports.route("/api/notifications", methods=["POST"])
    @roles_required(*ROLES["staff"])
    def create_notification():
        data = fast_json()
        
        # Vérification d'idempotence - éviter les doublons
        idempotency_key = data.get("idempotency_key")
        if idempotency_key:
            existing = supabase.table("notifications").select("*").eq("idempotency_key", idempotency_key).execute()
            if existing.data:
                return jsonify(existing.data[0]), 200
        
        notification = {
            "user_id": g.current_user.get("id"),
            "title": data.get("title", ""),
            "message": data.get("message", ""),
            "type": data.get("type", "info"),
            "read": False,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("notifications", notification)
        return jsonify(result.data[0]), 201

    @reports.route("/api/notifications/<int:notification_id>/read", methods=["PUT"])
    @roles_required(*ROLES["staff"])
    def mark_notification_read(notification_id: int):
        result = supabase.table("notifications").update({"read": True, "updated_at": now_iso()}).eq("id", notification_id).execute()
        if not result.data:
            return jsonify({"error": "Notification introuvable"}), 404
        return jsonify(result.data[0])

    @reports.route("/api/notifications/mark-all-read", methods=["PUT"])
    @roles_required(*ROLES["staff"])
    def mark_all_notifications_read():
        user_id = g.current_user.get("id")
        supabase.table("notifications").update({"read": True, "updated_at": now_iso()}).eq("user_id", user_id).execute()
        return jsonify({"message": "Toutes les notifications marquées comme lues"})

    @reports.route("/api/notifications/unread-count", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_unread_notifications_count():
        user_id = g.current_user.get("id")
        result = supabase.table("notifications").select("id").eq("user_id", user_id).eq("read", False).execute()
        return jsonify({"count": len(result.data)})

    # ==================== ATTACHMENTS ====================

    @reports.route("/api/attachments", methods=["POST"])
    @roles_required(*ROLES["staff"])
    def upload_attachment():
        data = fast_json()
        if not data.get("file") or not data.get("filename"):
            return jsonify({"error": "Fichier et nom requis"}), 422
        attachment = {
            "id": str(uuid.uuid4()),
            "filename": data.get("filename"),
            "entity_type": data.get("entity_type", "patient"),
            "entity_id": data.get("entity_id"),
            "file_data": data.get("file"),
            "file_size": len(data.get("file", "")),
            "content_type": data.get("content_type", "application/octet-stream"),
            "created_by": g.current_user.get("id"),
            "created_by_name": g.current_user.get("name"),
            "created_at": now_iso()
        }
        result = compatible_insert("attachments", attachment)
        add_audit("CREATE", "attachment", f"Pièce jointe: {data['filename']}", result.data[0]["id"] if result.data else None)
        return jsonify(result.data[0] if result.data else attachment), 201

    @reports.route("/api/attachments/<string:attachment_id>", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_attachment(attachment_id: str):
        result = supabase.table("attachments").select("*").eq("id", attachment_id).execute()
        if not result.data:
            return jsonify({"error": "Pièce jointe introuvable"}), 404
        return jsonify(result.data[0])

    @reports.route("/api/attachments/<string:attachment_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_attachment(attachment_id: str):
        supabase.table("attachments").delete().eq("id", attachment_id).execute()
        add_audit("DELETE", "attachment", f"Pièce jointe #{attachment_id} supprimée", None)
        return jsonify({"message": "Pièce jointe supprimée"})

    @reports.route("/api/attachments/entity/<string:entity_type>/<int:entity_id>", methods=["GET"])
    @roles_required(*ROLES["staff"])
    def get_attachments_by_entity(entity_type: str, entity_id: int):
        result = supabase.table("attachments").select("*").eq("entity_type", entity_type).eq("entity_id", entity_id).order("created_at", desc=True).execute()
        return jsonify(result.data)

    # ==================== LOGS ====================

    @reports.route("/api/logs/error", methods=["POST"])
    @token_required
    def log_error():
        data = fast_json()
        log_entry = {
            "user_id": g.current_user.get("id"),
            "user_name": g.current_user.get("name"),
            "context": data.get("context", ""),
            "error": data.get("error", ""),
            "stack": data.get("stack", ""),
            "created_at": now_iso()
        }
        compatible_insert("error_logs", log_entry)
        return jsonify({"message": "Erreur enregistrée"}), 201

    @reports.route("/api/metrics", methods=["GET"])
    @roles_required("super_admin")
    def get_metrics():
        try:
            import psutil
            return jsonify({
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_usage": psutil.disk_usage('/').percent,
                "generated_at": now_iso()
            })
        except ImportError:
            return jsonify({"error": "psutil non installé"}), 500

    @reports.route("/api/health/detailed", methods=["GET"])
    def health_detailed():
        status = {"app": "healthy", "timestamp": now_iso(), "version": "2.0.0", "services": {}}
        try:
            supabase.table(TABLES["users"]).select("id").limit(1).execute()
            status["services"]["supabase"] = "healthy"
        except:
            status["services"]["supabase"] = "unhealthy"
            status["app"] = "degraded"
        try:
            cache.get("health_check")
            status["services"]["cache"] = "healthy"
        except:
            status["services"]["cache"] = "unhealthy"
        return jsonify(status)

    # ==================== AUTO-PING ====================
    import threading
    import requests

    def auto_ping():
        """Ping l'application toutes les 12 minutes pour éviter l'endormissement"""
        # Utiliser l'URL publique si disponible
        ping_url = f"{BASE_URL}/api/health"
        if "localhost" in ping_url or "127.0.0.1" in ping_url:
            # En développement, utiliser localhost
            ping_url = f"http://localhost:{PORT}/api/health"
        
        retry_count = 0
        max_retries = 3
        
        while True:
            try:
                response = requests.get(ping_url, timeout=10)
                if response.status_code == 200:
                    print(f"[AUTO-PING] ✅ Ping réussi à {ping_url} - {datetime.now().strftime('%H:%M:%S')}")
                    retry_count = 0  # Réinitialiser le compteur
                else:
                    print(f"[AUTO-PING] ⚠️ Réponse inattendue: {response.status_code}")
                    retry_count += 1
            except requests.exceptions.Timeout:
                print(f"[AUTO-PING] ⏰ Timeout sur {ping_url}")
                retry_count += 1
            except requests.exceptions.ConnectionError:
                print(f"[AUTO-PING] ❌ Connexion impossible à {ping_url}")
                retry_count += 1
            except Exception as e:
                print(f"[AUTO-PING] ❌ Erreur: {e}")
                retry_count += 1
            
            # Si trop d'erreurs, attendre plus longtemps
            if retry_count >= max_retries:
                print(f"[AUTO-PING] 🔄 Trop d'échecs, nouvelle tentative dans 60s...")
                time.sleep(60)
                retry_count = 0
            else:
                # 720 secondes = 12 minutes
                time.sleep(720)

    def ping_supabase():
        """Ping Supabase toutes les 12 minutes pour maintenir la connexion"""
        while True:
            try:
                result = supabase.table(TABLES["users"]).select("id").limit(1).execute()
                if result.data is not None:
                    print(f"[SUPABASE-PING] ✅ Connexion Supabase OK - {datetime.now().strftime('%H:%M:%S')}")
            except Exception as e:
                print(f"[SUPABASE-PING] ❌ Erreur: {e}")
            # 720 secondes = 12 minutes
            time.sleep(720)

    # ==================== SEED ====================
    def seed_admin():
        existing = supabase.table(TABLES["users"]).select("id").eq("email", "jeremyodimba322@gmail.com").execute()
        if not existing.data:
            supabase.table(TABLES["users"]).insert({
                "name": "Administrateur",
                "email": "jeremyodimba322@gmail.com",
                "password_hash": generate_password_hash("admin123"),
                "role": "super_admin",
                "is_active": True,
                "created_at": now_iso(),
                "updated_at": now_iso()
            }).execute()
            print(" Super admin créé (email: jeremyodimba322@gmail.com, mot de passe: admin123)")

    # ==================== INITIALISATION TABLES ====================
    def init_workflow_tables():
        tables = [
            "patient_queue", "patient_dispatches", "medical_consultations",
            "vital_signs", "patient_account_lines", "patient_accounts",
            "patient_account_transactions", "hospitalizations", "medical_followups",
            "medication_administrations", "pharmacy_movements", "prescription_dispenses"
        ]
        for table in tables:
            try:
                supabase.table(table).select("*").limit(1).execute()
                print(f" Table {table} existe déjà")
            except Exception as e:
                print(f" Table {table} à créer: {str(e)[:100]}")

    def init_maternity_tables():
        tables = [
            "pregnancies", "prenatal_consultations", "deliveries",
            "maternity_rooms", "children", "vaccinations", "growth_measurements"
        ]
        for table in tables:
            try:
                supabase.table(table).select("*").limit(1).execute()
                print(f" Table {table} existe déjà")
            except Exception as e:
                print(f" Table {table} à créer: {str(e)[:100]}")

    def init_exchange_rate_table():
        try:
            supabase.table("exchange_rates").select("*").limit(1).execute()
            print(" Table exchange_rates existe déjà")
        except Exception:
            print(" Table exchange_rates à créer")
            try:
                supabase.table("exchange_rates").insert({
                    "rate": None,
                    "currency_from": "USD",
                    "currency_to": "CDF",
                    "set_by": None,
                    "set_by_name": "Systeme",
                    "created_at": now_iso()
                }).execute()
                print(" Table exchange_rates créée")
            except Exception as e:
                print(f" Impossible de créer exchange_rates: {e}")

    def init_medical_boxes_table():
        try:
            supabase.table("medical_boxes").select("*").limit(1).execute()
            print(" Table medical_boxes existe déjà")
        except Exception:
            print(" Table medical_boxes à créer")
            try:
                # Créer les 3 boxes par défaut
                for i in range(1, 4):
                    supabase.table("medical_boxes").insert({
                        "box_number": str(i),
                        "status": "free",
                        "created_at": now_iso(),
                        "updated_at": now_iso()
                    }).execute()
                print(f" Table medical_boxes créée avec {MAX_BOXES} boxes")
            except Exception as e:
                print(f" Impossible de créer medical_boxes: {e}")

    def init_hospitalization_followups_table():
        try:
            supabase.table("hospitalization_followups").select("*").limit(1).execute()
            print(" Table hospitalization_followups existe déjà")
        except Exception:
            print(" Table hospitalization_followups à créer")
            try:
                supabase.table("hospitalization_followups").insert({
                    "patient_id": 1,
                    "temperature": 36.5,
                    "general_state": "good",
                    "nurse_id": 1,
                    "nurse_name": "Systeme",
                    "created_at": now_iso()
                }).execute()
                supabase.table("hospitalization_followups").delete().eq("patient_id", 1).execute()
                print(" Table hospitalization_followups créée")
            except Exception as e:
                print(f" Impossible de créer hospitalization_followups: {e}")

    def init_notifications_table():
        try:
            supabase.table("notifications").select("*").limit(1).execute()
            print(" Table notifications existe déjà")
        except Exception:
            print(" Table notifications à créer")
            try:
                supabase.table("notifications").insert({
                    "user_id": 1,
                    "title": "Test",
                    "message": "Notification test",
                    "type": "info",
                    "read": False,
                    "created_at": now_iso()
                }).execute()
                supabase.table("notifications").delete().eq("title", "Test").execute()
                print(" Table notifications créée")
            except Exception as e:
                print(f" Impossible de créer notifications: {e}")

    # Les routes de ce module sont terminées. Le reste du bloc ci-dessous est
    # du code historique de démarrage déplacé par l'extraction initiale ; il
    # ne doit jamais réenregistrer les Blueprints du noyau.
    app.register_blueprint(reports)
    return

    # ==================== MODULES ROUTES ====================
    # Les modules enregistrent leurs blueprints en recevant explicitement les
    # dépendances du noyau. Les URLs publiques restent strictement inchangées.
    try:
        from .auth import register_auth_routes
        from .patients import register_patient_routes
        from .workflow import register_workflow_routes
        from .pharmacy import register_pharmacy_routes
        from .laboratory import register_laboratory_routes
        from .billing_api import register_billing_routes
        from .maternity import register_maternity_routes
    except ImportError:  # exécution directe : python app.py
        from auth import register_auth_routes
        from patients import register_patient_routes
        from workflow import register_workflow_routes
        from pharmacy import register_pharmacy_routes
        from laboratory import register_laboratory_routes
        from billing_api import register_billing_routes
        from maternity import register_maternity_routes

    register_auth_routes(
        app,
        fast_json=fast_json,
        supabase=supabase,
        tables=TABLES,
        roles=ROLES,
        now_iso=now_iso,
        create_token=create_token,
        token_required=token_required,
        add_audit=add_audit,
        invalidate_cache=invalidate_cache,
    )

    register_patient_routes(
        app,
        supabase=supabase,
        tables=TABLES,
        roles=ROLES,
        fast_json=fast_json,
        to_int=to_int,
        optional_date=optional_date,
        now_iso=now_iso,
        cached=cached,
        roles_required=roles_required,
        compatible_insert=compatible_insert,
        invalidate_cache=invalidate_cache,
        add_audit=add_audit,
        hospital_patient_id=hospital_patient_id,
        enrich_patient_identifier=enrich_patient_identifier,
        enrich_patient_identifiers=enrich_patient_identifiers,
        add_pregnancy_flags=add_pregnancy_flags,
        is_female=is_female,
        filter_patients_for_role=filter_patients_for_role,
        can_access_patient_record=can_access_patient_record,
        allowed_statuses=ALLOWED_STATUSES,
        generate_barcode_svg=generate_barcode_svg,
        generate_qr_code_data=generate_qr_code_data,
    )

    register_workflow_routes(app, runtime=globals())
    register_pharmacy_routes(app, runtime=globals())
    register_laboratory_routes(app, runtime=globals())
    register_billing_routes(app, runtime=globals())
    register_maternity_routes(app, runtime=globals())

    # ==================== LANCEMENT ====================
    if __name__ == "__main__":
        print("=" * 50)
        print(" I HUB HOSPITAL API - VERSION COMPLÈTE")
        print("=" * 50)
        print(f" Cache: {CACHE_TYPE}")
        print(f" Box maximum: {MAX_BOXES}")
        print(f" URL de base: {BASE_URL}")
        print("=" * 50)
        
        # Seed et initialisation
        seed_admin()
        init_workflow_tables()
        init_maternity_tables()
        init_exchange_rate_table()
        init_medical_boxes_table()
        init_hospitalization_followups_table()
        init_notifications_table()
        
        # Démarrer l'auto-ping dans un thread daemon
        ping_thread = threading.Thread(target=auto_ping, daemon=True)
        ping_thread.start()
        print("✅ Auto-ping démarré (toutes les 12 minutes)")
        
        # Démarrer le ping Supabase dans un autre thread
        supabase_ping_thread = threading.Thread(target=ping_supabase, daemon=True)
        supabase_ping_thread.start()
        print("✅ Supabase-ping démarré (toutes les 12 minutes)")
        
        print(f"🚀 Serveur démarré sur http://{HOST}:{PORT}")
        print("=" * 50)
        
        app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True)

    app.register_blueprint(reports)
