"""Routes d'authentification I-HUB.

Le module ne possède pas de client Supabase global : l'application lui injecte
ses dépendances au démarrage. Cela évite les imports circulaires avec app.py.
"""
from flask import Blueprint, jsonify, g
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

    app.register_blueprint(auth)
