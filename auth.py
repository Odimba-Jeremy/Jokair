import os
import time
import secrets
import requests
from html import escape
from flask import Blueprint, jsonify, request, g
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
        try:
            password_valid = bool(user) and check_password_hash(user.get("password_hash", ""), password)
        except (ValueError, TypeError):
            # Un hash mal enregistré ne doit jamais faire tomber l'endpoint
            # de connexion. L'administrateur doit le corriger en base.
            password_valid = False
        if not password_valid:
            return jsonify({"error": "Email ou mot de passe incorrect"}), 401
        token = create_token(user)
        # Mettre à jour last_login
        try:
            supabase.table(tables["users"]).update({"last_login": now_iso()}).eq("id", user["id"]).execute()
        except Exception:
            pass
        add_audit("LOGIN", "user", f"Connexion: {email}", user["id"])
        return jsonify({
            "user": {k: v for k, v in user.items() if k != "password_hash"},
            "token": token,
        })

    @auth.post("/api/auth/register")
    def register():
        # Vérifier si les inscriptions publiques sont autorisées
        is_reg_enabled_fn = app.config.get("IS_REGISTRATION_ENABLED_FN")
        if is_reg_enabled_fn and not is_reg_enabled_fn():
            return jsonify({
                "error": "Les inscriptions publiques sont actuellement désactivées. Contactez l'administration pour recevoir une invitation."
            }), 403

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

    # ==================== RATE LIMITING & RÉINITIALISATION MOT DE PASSE ====================
    _reset_rate_limits = {}  # { key: [timestamp, ...] }
    _reset_codes = {}        # { email: { "code": "...", "expires_at": ..., "attempts": 0 } }

    def _mask_email(email: str) -> str:
        if not email or "@" not in email:
            return "***"
        user_part, domain = email.split("@", 1)
        if len(user_part) <= 2:
            return user_part[0] + "***@" + domain
        return user_part[0] + "***" + user_part[-1] + "@" + domain

    def _is_rate_limited(key: str, max_requests: int = 3, window_seconds: int = 900) -> bool:
        now = time.time()
        timestamps = _reset_rate_limits.get(key, [])
        # Garder uniquement les timestamps dans la fenêtre
        timestamps = [t for t in timestamps if now - t < window_seconds]
        if len(timestamps) >= max_requests:
            _reset_rate_limits[key] = timestamps
            return True
        timestamps.append(now)
        _reset_rate_limits[key] = timestamps
        return False

    def _send_reset_email(to_email: str, code: str, reset_url: str):
        email_craft_url = os.getenv("EMAIL_CRAFT_URL", "https://email-craft-90.lovable.app/api/public/v1/send")
        api_key = os.getenv("EMAIL_CRAFT_API_KEY", "")

        subject = "Réinitialisation de votre mot de passe I-HUB"
        text_content = (
            f"Bonjour,\n\n"
            f"Vous avez demandé la réinitialisation de votre mot de passe pour votre compte I-HUB.\n\n"
            f"Votre code de sécurité à 6 chiffres est : {code}\n"
            f"(Ce code est valable pendant 15 minutes).\n\n"
            f"Vous pouvez également cliquer directement sur ce lien pour réinitialiser votre mot de passe :\n"
            f"{reset_url}\n\n"
            f"Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet e-mail en toute sécurité.\n\n"
            f"Cordialement,\n"
            f"L'équipe I-HUB"
        )

        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reset_password_email.html")
        html_content = None
        if os.path.exists(template_path):
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    raw_html = f.read()
                    raw_html = raw_html.replace("{{code}}", escape(str(code)))
                    raw_html = raw_html.replace("{{reset_url}}", escape(str(reset_url), quote=True))
                    raw_html = raw_html.replace("{{to_email}}", escape(str(to_email)))
                    raw_html = raw_html.replace("{{year}}", "2026")
                    html_content = raw_html
            except Exception as e:
                print(f"[AUTH] Lecture template externe ({template_path}): {e}")

        # Modèle HTML de secours propre si le fichier séparé n'est pas encore téléversé sur Render
        if not html_content:
            html_content = (
                '<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f8fafc;padding:24px;color:#0f172a;">'
                '<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:16px;padding:32px;box-shadow:0 4px 12px rgba(0,0,0,0.06);">'
                '<h2 style="color:#0f766e;margin-top:0;">I-HUB — Réinitialisation de mot de passe</h2>'
                '<p>Bonjour,</p>'
                '<p>Vous avez demandé la réinitialisation de votre mot de passe I-HUB. Voici votre code sécurisé à 6 chiffres (valable 15 minutes) :</p>'
                '<div style="background:#f0fdfa;border:2px dashed #0f766e;border-radius:12px;padding:20px;text-align:center;margin:24px 0;">'
                f'<span style="font-size:36px;font-weight:bold;letter-spacing:8px;color:#0f766e;font-family:monospace;">{escape(str(code))}</span>'
                '</div>'
                '<div style="text-align:center;margin:24px 0;">'
                f'<a href="{escape(str(reset_url), quote=True)}" style="display:inline-block;background:#0f766e;color:#fff;text-decoration:none;font-weight:bold;padding:12px 28px;border-radius:8px;">Réinitialiser mon mot de passe</a>'
                '</div>'
                '<p style="font-size:12px;color:#64748b;">Si vous n\'êtes pas à l\'origine de cette demande, vous pouvez ignorer cet e-mail.</p>'
                '<hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;">'
                '<p style="font-size:11px;color:#94a3b8;text-align:center;">&copy; 2026 I-HUB — Plateforme Médicale Hospitalière</p>'
                '</div></body></html>'
            )

        print(f"[AUTH] Code de réinitialisation pour {_mask_email(to_email)}: {code}")

        if not api_key:
            print(f"[WARN] EMAIL_CRAFT_API_KEY non configurée pour {_mask_email(to_email)}")
            return {"success": False, "notice": "Clé API absente"}

        payload = {
            "to": to_email,
            "subject": subject,
            "text": text_content,
            "from_name": "I-HUB"
        }
        if html_content:
            payload["html"] = html_content

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        try:
            resp = requests.post(email_craft_url, json=payload, headers=headers, timeout=10)
            is_ok = resp.status_code in (200, 201, 202)
            if is_ok:
                print(f"[AUTH] Envoi Email Craft réussi pour {_mask_email(to_email)} (HTTP {resp.status_code})")
                return {"success": True, "status_code": resp.status_code}
            else:
                err_msg = resp.text[:200]
                print(f"[ERROR] Email Craft HTTP {resp.status_code} pour {_mask_email(to_email)}: {err_msg}")
                return {"success": False, "status_code": resp.status_code, "error": err_msg}
        except Exception as e:
            print(f"[ERROR] Exception Email Craft pour {_mask_email(to_email)}: {e}")
            return {"success": False, "error": str(e)}

    @auth.post("/api/auth/forgot-password")
    def forgot_password():
        data = fast_json()
        email = data.get("email", "").lower().strip()
        if not email or "@" not in email:
            return jsonify({"error": "Adresse e-mail valide requise"}), 422

        # Rate limiting : max 3 demandes par 15 min par IP/Email
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        rate_key = f"{client_ip}:{email}"
        if _is_rate_limited(rate_key, max_requests=3, window_seconds=900):
            return jsonify({"error": "Trop de tentatives de réinitialisation. Veuillez patienter 15 minutes."}), 429

        # Vérifier si l'utilisateur existe (insensible à la casse)
        user = None
        try:
            res = supabase.table(tables["users"]).select("id, name, email").ilike("email", email).execute()
            user = res.data[0] if res.data else None
        except Exception:
            try:
                res = supabase.table(tables["users"]).select("id, name, email").eq("email", email).execute()
                user = res.data[0] if res.data else None
            except Exception as e:
                print(f"[ERROR] Recherche utilisateur pour reset: {e}")

        if user:
            # Générer code 6 chiffres
            code = f"{secrets.randbelow(900000) + 100000}"
            _reset_codes[email] = {
                "code": code,
                "expires_at": time.time() + 900,  # 15 minutes
                "attempts": 0
            }
            app_frontend_url = os.getenv("APP_FRONTEND_URL", "https://okito2shop.web.app").rstrip("/")
            reset_url = f"{app_frontend_url}/index.html?reset_email={email}&reset_code={code}#/reset-password"
            mail_res = _send_reset_email(email, code, reset_url)
            add_audit("REQUEST_PASSWORD_RESET", "user", f"Demande réinitialisation mot de passe: {email} (envoi: {'OK' if mail_res.get('success') else 'ECHEC'})", user["id"])

        # Toujours répondre avec succès générique pour ne pas divulguer si l'email existe
        return jsonify({
            "message": "Si cette adresse est enregistrée dans le système, un e-mail avec un code de vérification à 6 chiffres a été envoyé."
        })

    @auth.post("/api/auth/reset-password")
    def reset_password():
        data = fast_json()
        email = data.get("email", "").lower().strip()
        code = str(data.get("code", "")).strip()
        new_password = str(data.get("password", "")).strip()

        if not email or not code or not new_password:
            return jsonify({"error": "Email, code et nouveau mot de passe requis"}), 422
        if len(new_password) < 8:
            return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères"}), 422

        record = _reset_codes.get(email)
        if not record or time.time() > record.get("expires_at", 0):
            return jsonify({"error": "Code expiré ou inexistant. Veuillez demander un nouveau code."}), 400

        # Protection anti-bruteforce (Rate limit sur les essais de code : max 5 essais)
        if record.get("attempts", 0) >= 5:
            _reset_codes.pop(email, None)
            return jsonify({"error": "Nombre maximal d'essais dépassé. Ce code a été annulé par sécurité."}), 429

        if record.get("code") != code:
            record["attempts"] = record.get("attempts", 0) + 1
            remaining = max(0, 5 - record["attempts"])
            return jsonify({"error": f"Code incorrect ({remaining} tentative(s) restante(s))."}), 400

        # Code valide -> Mise à jour du mot de passe
        supabase.table(tables["users"]).update({
            "password_hash": generate_password_hash(new_password),
            "updated_at": now_iso()
        }).eq("email", email).execute()

        # Nettoyer le code utilisé
        _reset_codes.pop(email, None)
        add_audit("PASSWORD_RESET", "user", f"Mot de passe réinitialisé pour: {email}")
        invalidate_cache()

        return jsonify({"message": "Votre mot de passe a été réinitialisé avec succès ! Vous pouvez maintenant vous connecter."})

    app.register_blueprint(auth)
