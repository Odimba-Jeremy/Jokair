"""Routes d'authentification I-HUB.

Le module ne possède pas de client Supabase global : l'application lui injecte
ses dépendances au démarrage. Cela évite les imports circulaires avec app.py.
"""
from flask import Blueprint, jsonify, g, request
from werkzeug.security import generate_password_hash, check_password_hash


def register_auth_routes(app, *, fast_json, supabase, tables, roles, now_iso,
                         create_token, token_required, add_audit,
                         invalidate_cache):
    auth = Blueprint("auth", __name__)

    @auth.post("/api/auth/login")
    def login():
        data = fast_json()
        email = data.get("email", "").lower().strip()
        password = data.get("password", "")
        if not email or not password:
            return jsonify({"error": "Email et mot de passe requis"}), 422
        result = supabase.table(tables["users"]).select("*").eq("email", email).execute()
        user = result.data[0] if result.data else None
        if not user or not check_password_hash(user.get("password_hash", ""), password):
            return jsonify({"error": "Email ou mot de passe incorrect"}), 401
        token = create_token(user)
        add_audit("LOGIN", "user", f"Connexion: {email}", user["id"])
        return jsonify({
            "user": {k: v for k, v in user.items() if k != "password_hash"},
            "token": token,
        })

    @auth.post("/api/auth/register")
    def register():
        data = fast_json()
        name = data.get("name", "").strip()
        email = data.get("email", "").lower().strip()
        password = data.get("password", "")
        role = data.get("role", "reception")
        if len(name) < 2:
            return jsonify({"error": "Nom trop court"}), 422
        if "@" not in email:
            return jsonify({"error": "Email invalide"}), 422
        if len(password) < 8:
            return jsonify({"error": "Mot de passe trop court"}), 422
        if role not in roles["public"]:
            return jsonify({"error": "Rôle invalide"}), 422

        # Vérification du blocage des inscriptions par l'administrateur
        global _REGISTRATION_ENABLED
        is_reg_open = True
        try:
            check_s = supabase.table("app_settings").select("value").eq("key", "registration_enabled").execute()
            if check_s.data:
                is_reg_open = str(check_s.data[0].get("value")).lower() in ("true", "1", "yes")
            else:
                is_reg_open = _REGISTRATION_ENABLED
        except Exception:
            is_reg_open = _REGISTRATION_ENABLED

        if not is_reg_open:
            return jsonify({"error": "Les inscriptions sont temporairement fermées par l'administrateur."}), 403
        existing = supabase.table(tables["users"]).select("id").eq("email", email).execute()
        if existing.data:
            return jsonify({"error": "Email déjà utilisé"}), 422
        user_data = {
            "name": name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": role,
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        result = supabase.table(tables["users"]).insert(user_data).execute()
        user = result.data[0]
        token = create_token(user)
        add_audit("CREATE", "user", f"Inscription: {email}", user["id"])
        invalidate_cache()
        return jsonify({
            "user": {k: v for k, v in user.items() if k != "password_hash"},
            "token": token,
        }), 201

    @auth.post("/api/auth/logout")
    @token_required
    def logout():
        add_audit("LOGOUT", "user", f"Déconnexion: {g.current_user.get('email')}", g.current_user.get("id"))
        return jsonify({"message": "Déconnexion réussie"})

    @auth.get("/api/auth/me")
    @token_required
    def auth_me():
        return jsonify({"user": {k: v for k, v in g.current_user.items() if k != "password_hash"}})


    # ==================== RÉGLAGE CONTRÔLE DES INSCRIPTIONS ====================
    # Variable globale d'état d'inscription (activée par défaut)
    global _REGISTRATION_ENABLED
    _REGISTRATION_ENABLED = True

    @auth.get("/api/settings/registration")
    def get_registration_status():
        """Consulte l'état d'ouverture des inscriptions."""
        try:
            res = supabase.table("app_settings").select("value").eq("key", "registration_enabled").execute()
            if res.data:
                enabled = str(res.data[0].get("value")).lower() in ("true", "1", "yes")
                return jsonify({"registration_enabled": enabled})
        except Exception:
            pass
        return jsonify({"registration_enabled": _REGISTRATION_ENABLED})

    @auth.put("/api/settings/registration")
    @token_required
    def toggle_registration():
        """Permet au super_admin d'activer ou désactiver les inscriptions."""
        if g.current_user.get("role") != "super_admin":
            return jsonify({"error": "Action réservée à l'administrateur"}), 403
        data = fast_json()
        enabled = bool(data.get("registration_enabled", True))
        global _REGISTRATION_ENABLED
        _REGISTRATION_ENABLED = enabled
        try:
            supabase.table("app_settings").upsert({"key": "registration_enabled", "value": str(enabled), "updated_at": now_iso()}).execute()
        except Exception:
            pass
        add_audit("SETTINGS", "registration", f"Inscriptions {'activées' if enabled else 'désactivées'}", g.current_user.get("id"))
        return jsonify({"registration_enabled": enabled, "message": "Statut mis à jour avec succès"})

    # ==================== MODIFICATION MOT DE PASSE ====================
    @auth.post("/api/auth/change-password")
    @token_required
    def change_password():
        """Permet à l'utilisateur connecté de modifier son mot de passe."""
        data = fast_json()
        current_password = data.get("current_password", "")
        new_password = data.get("new_password", "")
        if not current_password or not new_password:
            return jsonify({"error": "Ancien et nouveau mot de passe requis"}), 422
        if len(new_password) < 8:
            return jsonify({"error": "Le nouveau mot de passe doit comporter au moins 8 caractères"}), 422

        user_id = g.current_user.get("id")
        user_res = supabase.table(tables["users"]).select("*").eq("id", user_id).execute()
        if not user_res.data:
            return jsonify({"error": "Utilisateur introuvable"}), 404
        user = user_res.data[0]
        if not check_password_hash(user.get("password_hash", ""), current_password):
            return jsonify({"error": "Mot de passe actuel incorrect"}), 401

        supabase.table(tables["users"]).update({
            "password_hash": generate_password_hash(new_password),
            "updated_at": now_iso()
        }).eq("id", user_id).execute()

        add_audit("PASSWORD_CHANGE", "user", f"Mot de passe changé pour {user.get('email')}", user_id)
        return jsonify({"message": "Mot de passe modifié avec succès"})

    # ==================== MOT DE PASSE OUBLIÉ & RÉCUPÉRATION EMAIL ====================
    @auth.post("/api/auth/forgot-password")
    def forgot_password():
        """Génère un token de réinitialisation et envoie l'e-mail avec le lien."""
        data = fast_json()
        email = data.get("email", "").lower().strip()
        if not email or "@" not in email:
            return jsonify({"error": "Adresse e-mail valide requise"}), 422

        user_res = supabase.table(tables["users"]).select("id, name, email").eq("email", email).execute()
        if user_res.data:
            try:
                from email_service import send_email, build_reset_password_email
                from itsdangerous import URLSafeTimedSerializer
                import os
                sec_key = os.getenv("SECRET_KEY", "ihub_super_secret_key_2024")
                ser = URLSafeTimedSerializer(sec_key)
                token = ser.dumps({"email": email, "purpose": "reset_password"})
                base_url = request.host_url.rstrip("/")
                reset_url = f"{base_url}/reset-password.html?token={token}"
                subject, html = build_reset_password_email(reset_url)
                send_email(email, subject, html)
            except Exception as e:
                print(f"Erreur envoi email réinitialisation: {e}")

        # Pour des raisons de sécurité, on retourne toujours 200 sans révéler si l'email existe
        return jsonify({"message": "Si cette adresse est enregistrée, un e-mail de réinitialisation a été envoyé."})

    @auth.post("/api/auth/reset-password")
    def reset_password():
        """Valide le token signé et applique le nouveau mot de passe."""
        data = fast_json()
        token = data.get("token", "")
        new_password = data.get("new_password", "")
        if not token or not new_password:
            return jsonify({"error": "Jeton et nouveau mot de passe requis"}), 422
        if len(new_password) < 8:
            return jsonify({"error": "Le mot de passe doit comporter au moins 8 caractères"}), 422

        try:
            from itsdangerous import URLSafeTimedSerializer
            import os
            sec_key = os.getenv("SECRET_KEY", "ihub_super_secret_key_2024")
            ser = URLSafeTimedSerializer(sec_key)
            payload = ser.loads(token, max_age=3600)  # 1 heure max
            if payload.get("purpose") != "reset_password":
                return jsonify({"error": "Jeton invalide"}), 400
            email = payload.get("email")
        except Exception:
            return jsonify({"error": "Lien de réinitialisation expiré ou invalide"}), 400

        res = supabase.table(tables["users"]).update({
            "password_hash": generate_password_hash(new_password),
            "updated_at": now_iso()
        }).eq("email", email).execute()

        if not res.data:
            return jsonify({"error": "Utilisateur introuvable"}), 404

        add_audit("PASSWORD_RESET", "user", f"Mot de passe réinitialisé pour {email}", res.data[0]["id"])
        return jsonify({"message": "Votre mot de passe a été réinitialisé. Vous pouvez vous connecter."})

    # ==================== INVITATION ADMINISTRATEUR / PERSONNEL ====================
    @auth.post("/api/admin/invite")
    @token_required
    def invite_staff():
        """L'administrateur invite un collaborateur par e-mail avec confirmation."""
        if g.current_user.get("role") != "super_admin":
            return jsonify({"error": "Action réservée au super administrateur"}), 403
        data = fast_json()
        email = data.get("email", "").lower().strip()
        role = data.get("role", "admin").strip()
        if not email or "@" not in email:
            return jsonify({"error": "Adresse e-mail valide requise"}), 422

        # Vérifier si l'utilisateur existe déjà
        existing = supabase.table(tables["users"]).select("id").eq("email", email).execute()
        if existing.data:
            return jsonify({"error": "Un compte existe déjà avec cette adresse e-mail"}), 422

        from email_service import send_email, build_invitation_email
        from itsdangerous import URLSafeTimedSerializer
        import os
        sec_key = os.getenv("SECRET_KEY", "ihub_super_secret_key_2024")
        ser = URLSafeTimedSerializer(sec_key)
        token = ser.dumps({"email": email, "role": role, "purpose": "invite"})
        base_url = request.host_url.rstrip("/")
        invite_url = f"{base_url}/accept-invite.html?token={token}"

        role_labels = {
            "super_admin": "Super Administrateur",
            "admin": "Administrateur",
            "docteur": "Médecin",
            "infirmier": "Infirmier(ère)",
            "laboratoire": "Laborantin",
            "pharmacie": "Pharmacien",
            "reception": "Réceptionniste"
        }
        subject, html = build_invitation_email(invite_url, role_labels.get(role, "Membre du personnel"))
        sent = send_email(email, subject, html)

        add_audit("INVITE", "user", f"Invitation envoyée à {email} (rôle: {role})", g.current_user.get("id"))
        return jsonify({
            "message": f"Invitation envoyée avec succès à {email}",
            "email_sent": sent,
            "invite_url": invite_url
        })

    @auth.post("/api/auth/accept-invite")
    def accept_invite():
        """Le destinataire de l'invitation crée son mot de passe et active son compte."""
        data = fast_json()
        token = data.get("token", "")
        name = data.get("name", "").strip()
        password = data.get("password", "")

        if not token or len(name) < 2 or len(password) < 8:
            return jsonify({"error": "Données incomplètes (nom min 2 car., mot de passe min 8 car.)"}), 422

        try:
            from itsdangerous import URLSafeTimedSerializer
            import os
            sec_key = os.getenv("SECRET_KEY", "ihub_super_secret_key_2024")
            ser = URLSafeTimedSerializer(sec_key)
            payload = ser.loads(token, max_age=259200)  # 72 heures max
            if payload.get("purpose") != "invite":
                return jsonify({"error": "Jeton d'invitation invalide"}), 400
            email = payload.get("email")
            role = payload.get("role", "admin")
        except Exception:
            return jsonify({"error": "L'invitation est invalide ou a expiré"}), 400

        existing = supabase.table(tables["users"]).select("id").eq("email", email).execute()
        if existing.data:
            return jsonify({"error": "Ce compte est déjà activé. Veuillez vous connecter."}), 422

        user_data = {
            "name": name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": role,
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        result = supabase.table(tables["users"]).insert(user_data).execute()
        new_user = result.data[0]
        token_out = create_token(new_user)
        add_audit("ACCEPT_INVITE", "user", f"Compte activé via invitation: {email}", new_user["id"])
        invalidate_cache()
        return jsonify({
            "user": {k: v for k, v in new_user.items() if k != "password_hash"},
            "token": token_out,
            "message": "Votre compte a été activé avec succès !"
        }), 201

    # ==================== PHOTO DE PROFIL (AVATAR) ====================
    @auth.post("/api/users/<int:user_id>/avatar")
    @token_required
    def upload_avatar(user_id: int):
        """Upload et enregistrement de la photo de profil du personnel."""
        # Seul l'utilisateur lui-même ou le super_admin peut changer l'avatar
        if g.current_user.get("id") != user_id and g.current_user.get("role") != "super_admin":
            return jsonify({"error": "Action non autorisée sur ce profil"}), 403

        data = fast_json()
        avatar_data = data.get("avatar") or data.get("image") or ""
        if not avatar_data:
            return jsonify({"error": "Données d'image requises (format base64 ou URL)"}), 422

        avatar_url = avatar_data
        # Tentative d'enregistrement dans Supabase Storage bucket 'avatars'
        if avatar_data.startswith("data:image"):
            try:
                import base64
                header, encoded = avatar_data.split(",", 1)
                image_bytes = base64.b64decode(encoded)
                file_ext = "png" if "png" in header else "jpg"
                file_name = f"user_{user_id}_{int(time.time())}.{file_ext}"
                upload_res = supabase.storage.from_("avatars").upload(file_name, image_bytes, {"content-type": f"image/{file_ext}"})
                avatar_url = supabase.storage.from_("avatars").get_public_url(file_name)
            except Exception as e:
                # Fallback : stocker directement l'avatar_data base64 dans la colonne avatar_url
                print(f"Stockage Supabase avatar: {e}, fallback data URI")
                avatar_url = avatar_data

        try:
            supabase.table(tables["users"]).update({
                "avatar_url": avatar_url,
                "updated_at": now_iso()
            }).eq("id", user_id).execute()
        except Exception:
            pass

        add_audit("AVATAR", "user", f"Avatar mis à jour pour #{user_id}", user_id)
        return jsonify({"avatar_url": avatar_url, "message": "Photo de profil enregistrée"})


    app.register_blueprint(auth)
