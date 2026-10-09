"""Administration : abonnés, comptes utilisateurs et journal d'audit."""

from flask import Blueprint


def register_admin_routes(app, *, runtime):
    globals().update(runtime)
    admin = Blueprint("admin", __name__)

    @app.route("/api/subscribers", methods=["GET"])
    @roles_required("super_admin", "reception")
    @cached(300)
    def get_subscribers():
        result = supabase.table("subscribers").select("*").order("name").execute()
        return jsonify(result.data or [])

    @app.route("/api/subscribers", methods=["POST"])
    @roles_required("super_admin", "reception")
    def create_subscriber():
        data = fast_json()
        if not data.get("name"):
            return jsonify({"error": "Nom requis"}), 422
        subscriber = {
            "name": data.get("name"),
            "type": data.get("type", "Mutuelle"),
            "coverage_rate": min(100, max(0, to_float(data.get("coverage_rate"), 80))),
            "active": data.get("active", True),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = compatible_insert("subscribers", subscriber)
        add_audit("CREATE", "subscriber", f"Abonné: {data['name']}", result.data[0]["id"])
        invalidate_cache()
        return jsonify(result.data[0]), 201

    @app.route("/api/subscribers/<int:subscriber_id>", methods=["PUT"])
    @roles_required("super_admin", "reception")
    def update_subscriber(subscriber_id: int):
        data = fast_json()
        allowed = ["name", "type", "coverage_rate", "active"]
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if "coverage_rate" in updates:
            updates["coverage_rate"] = min(100, max(0, to_float(updates["coverage_rate"], 80)))
        updates["updated_at"] = now_iso()
        result = supabase.table("subscribers").update(updates).eq("id", subscriber_id).execute()
        if not result.data:
            return jsonify({"error": "Abonné introuvable"}), 404
        add_audit("UPDATE", "subscriber", f"Abonné #{subscriber_id} modifié", subscriber_id)
        invalidate_cache()
        return jsonify(result.data[0])

    # ==================== USERS ====================
    @app.route("/api/users", methods=["GET"])
    @app.route("/api/auth/users", methods=["GET"])
    @roles_required("super_admin", "reception")
    @cached(120)
    def get_users():
        requested_role = str(request.args.get("role") or "").strip()
        # La réception ne peut consulter que le répertoire des médecins pour
        # créer un rendez-vous ; la liste complète reste réservée à l'admin.
        if g.current_user.get("role") == "reception" and requested_role not in ("docteur", "doctor"):
            return jsonify({"error": "La réception peut uniquement consulter les médecins"}), 403
        query = supabase.table(TABLES["users"]).select("*")
        if requested_role:
            if requested_role in ("docteur", "doctor"):
                query = query.in_("role", ["docteur", "doctor"])
            else:
                if requested_role not in ROLES["staff"]:
                    return jsonify({"error": "Rôle invalide"}), 422
                query = query.eq("role", requested_role)
        result = query.order("created_at", desc=True).execute()
        users = [{k: v for k, v in u.items() if k != "password_hash"} for u in result.data]
        return jsonify(users)

    @app.route("/api/users", methods=["POST"])
    @app.route("/api/auth/users", methods=["POST"])
    @roles_required("super_admin")
    def create_user():
        data = fast_json()
        name = data.get("name", "").strip()
        email = data.get("email", "").lower().strip()
        password = data.get("password", "")
        role = data.get("role", "")
        if len(name) < 2:
            return jsonify({"error": "Nom trop court"}), 422
        if "@" not in email:
            return jsonify({"error": "Email invalide"}), 422
        if len(password) < 8:
            return jsonify({"error": "Mot de passe trop court"}), 422
        if role not in ROLES["staff"]:
            return jsonify({"error": "Rôle invalide"}), 422
        existing = supabase.table(TABLES["users"]).select("id").eq("email", email).execute()
        if existing.data:
            return jsonify({"error": "Email déjà utilisé"}), 422
        user_data = {
            "name": name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": role,
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = supabase.table(TABLES["users"]).insert(user_data).execute()
        add_audit("CREATE", "user", f"Compte: {email}", result.data[0]["id"])
        invalidate_cache()
        return jsonify({k: v for k, v in result.data[0].items() if k != "password_hash"}), 201

    @app.route("/api/users/<int:user_id>", methods=["PUT", "PATCH"])
    @app.route("/api/auth/users/<int:user_id>", methods=["PUT", "PATCH"])
    @roles_required("super_admin")
    def update_user(user_id: int):
        data = fast_json()
        updates = {}
        if "name" in data and data["name"]:
            updates["name"] = str(data["name"]).strip()
        if "email" in data and data["email"]:
            updates["email"] = str(data["email"]).lower().strip()
        if "role" in data and data["role"] in ROLES["staff"]:
            updates["role"] = data["role"]
        if "is_active" in data:
            updates["is_active"] = bool(data["is_active"])
        if "status" in data:
            val = str(data["status"]).strip().lower()
            updates["is_active"] = val in ("active", "actif", "true", "1")
        if "password" in data and data["password"]:
            if len(data["password"]) >= 8:
                updates["password_hash"] = generate_password_hash(data["password"])
        if not updates:
            return jsonify({"error": "Aucune donnee a mettre a jour"}), 422
        updates["updated_at"] = now_iso()
        
        # compatible_update gere la suppression des colonnes inexistantes
        result = compatible_update(TABLES["users"], updates, "id", user_id)
        if not result or not result.data:
            return jsonify({"error": "Utilisateur introuvable"}), 404
        
        user_name = updates.get("name") or result.data[0].get("name") or user_id
        action_desc = f"Compte #{user_id} ({user_name}) modifie"
        if "is_active" in updates:
            action_desc += f" [Statut: {'Actif' if updates['is_active'] else 'Desactive'}]"
        add_audit("UPDATE", "user", action_desc, user_id)
        invalidate_cache()
        
        user_res = dict(result.data[0])
        user_res.pop("password_hash", None)
        user_res["status"] = "active" if user_res.get("is_active") else "inactive"
        return jsonify(user_res)

    @app.route("/api/users/<int:user_id>", methods=["DELETE"])
    @app.route("/api/auth/users/<int:user_id>", methods=["DELETE"])
    @roles_required("super_admin")
    def delete_user(user_id: int):
        if user_id == g.current_user.get("id"):
            return jsonify({"error": "Vous ne pouvez pas supprimer votre propre compte"}), 422
        try:
            supabase.table(TABLES["users"]).delete().eq("id", user_id).execute()
            add_audit("DELETE", "user", f"Compte #{user_id} supprimé", user_id)
        except Exception:
            # Si le compte est lié à des actes médicaux ou financiers (contrainte FK), on le désactive pour préserver l'historique
            supabase.table(TABLES["users"]).update({"is_active": False, "updated_at": now_iso()}).eq("id", user_id).execute()
            add_audit("DEACTIVATE", "user", f"Compte #{user_id} désactivé (lié à des actes existants)", user_id)
        invalidate_cache()
        return jsonify({"message": "Compte supprimé ou désactivé"})

    # ==================== AUDIT ====================
    @app.route("/api/audit", methods=["GET"])
    @roles_required("super_admin")
    @cached(120)
    def get_audit_logs():
        action = request.args.get("action")
        entity = request.args.get("entity_type")
        user_id = request.args.get("user_id")
        limit = to_int(request.args.get("limit"), 500)
        query = supabase.table(TABLES["audit"]).select("*")
        if action:
            query = query.eq("action", action)
        if entity:
            query = query.eq("entity_type", entity)
        if user_id:
            query = query.eq("user_id", to_int(user_id))
        result = query.order("created_at", desc=True).limit(min(limit, 1000)).execute()
        return jsonify(result.data)

    # ==================== SETTINGS (REGISTRATION LOCK) ====================
    @app.route("/api/settings/registrations", methods=["GET"])
    def get_registration_settings():
        enabled_fn = app.config.get("IS_REGISTRATION_ENABLED_FN")
        enabled = enabled_fn() if enabled_fn else True
        return jsonify({"enabled": enabled})

    @app.route("/api/settings/registrations", methods=["PATCH", "PUT"])
    @roles_required("super_admin", "admin")
    def update_registration_settings():
        data = fast_json()
        enabled = bool(data.get("enabled", True))
        
        # Mettre à jour l'état dans l'application backend
        if "set_registration_allowed" in globals():
            set_registration_allowed(enabled)
        else:
            app.config["REGISTRATIONS_ENABLED"] = enabled
        
        action_str = "activées" if enabled else "bloquées"
        add_audit("UPDATE", "settings", f"Inscriptions publiques {action_str} par l'administrateur")
        invalidate_cache()
        return jsonify({"enabled": enabled, "message": f"Inscriptions {action_str}"})

    app.register_blueprint(admin)
