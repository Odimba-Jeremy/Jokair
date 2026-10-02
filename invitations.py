"""Gestion complète des invitations et paramètres d'inscription I-HUB.

Ce module implémente :
- Création, liste, renvoi et annulation des invitations (super_admin).
- Vérification et acceptation sécurisée d'une invitation (public).
- Envoi d'e-mails d'invitation via le service Email Craft.
- Consultation et modification du paramètre des inscriptions publiques.
"""

import os
import json
import secrets
import requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash


# ==================== HELPERS UTILITAIRES ====================

def mask_email(email: str) -> str:
    """Masque une adresse e-mail pour affichage public sécurisé."""
    if not email or "@" not in email:
        return "***"
    user_part, domain = email.split("@", 1)
    if len(user_part) <= 2:
        masked_user = user_part[0] + "***"
    else:
        masked_user = user_part[0] + "***" + user_part[-1]
    return f"{masked_user}@{domain}"


def send_invitation_email(
    to_email: str,
    invite_url: str,
    role: str,
    inviter_name: str = "L'administration I-HUB",
    custom_message: Optional[str] = None
) -> Dict[str, Any]:
    """Envoie un e-mail d'invitation via le service Email Craft.

    Règles de sécurité :
    - Ne journalise JAMAIS la clé API ni le token complet.
    - Timeout réseau de 10 secondes.
    - Retourne un résultat structuré {'success': bool, 'status_code': int, 'error': str|None}.
    """
    email_craft_url = os.getenv("EMAIL_CRAFT_URL", "https://email-craft-90.lovable.app/api/public/v1/send")
    api_key = os.getenv("EMAIL_CRAFT_API_KEY", "")

    if not api_key:
        print(f"[WARN] EMAIL_CRAFT_API_KEY non configurée pour l'envoi d'e-mail à {mask_email(to_email)}")
        return {
            "success": False,
            "status_code": None,
            "error": "Clé EMAIL_CRAFT_API_KEY non configurée sur le serveur"
        }

    role_labels = {
        "super_admin": "Super Administrateur",
        "docteur": "Médecin",
        "infirmier": "Infirmier(ère)",
        "laboratoire": "Technicien de Laboratoire",
        "pharmacie": "Pharmacien(ne)",
        "reception": "Réceptionniste"
    }
    role_label = role_labels.get(role, role.capitalize())

    msg_part = f"\n\nMessage de l'administrateur :\n\"{custom_message}\"" if custom_message else ""

    text_content = (
        f"Bonjour,\n\n"
        f"Vous avez été invité(e) par {inviter_name} à rejoindre la plateforme médicale I-HUB "
        f"avec le rôle : {role_label}.{msg_part}\n\n"
        f"Pour activer votre compte et définir votre mot de passe, veuillez cliquer sur le lien sécurisé suivant (valable 48 heures) :\n"
        f"{invite_url}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet e-mail en toute sécurité.\n\n"
        f"Cordialement,\n"
        f"L'équipe I-HUB"
    )

    payload = {
        "to": to_email,
        "subject": "Invitation à rejoindre I-HUB",
        "text": text_content,
        "from_name": "I-HUB"
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(email_craft_url, json=payload, headers=headers, timeout=10)
        # Succès attendu : HTTP 202 (ou 200/201)
        if resp.status_code in (200, 201, 202):
            return {"success": True, "status_code": resp.status_code, "error": None}
        else:
            err_msg = resp.text[:200]
            print(f"[ERROR] Email Craft HTTP {resp.status_code} pour {mask_email(to_email)}: {err_msg}")
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": f"Email Craft HTTP {resp.status_code}: {err_msg}"
            }
    except Exception as e:
        print(f"[ERROR] Email Craft exception pour {mask_email(to_email)}: {type(e).__name__}")
        return {
            "success": False,
            "status_code": None,
            "error": f"Erreur réseau lors de l'envoi de l'e-mail: {str(e)}"
        }


# ==================== GESTIONNAIRE DE STOCKAGE ====================

class InvitationsStore:
    """Accès aux tables invitations et system_settings avec résilience.

    Si la table Supabase n'est pas encore créée via SQL, un stockage local
    de secours (backend/data/) assure la continuité sans interruption de service.
    """

    def __init__(self, supabase, tables_config: dict):
        self.supabase = supabase
        self.tables_config = tables_config
        self.local_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(self.local_dir, exist_ok=True)
        self.invitations_file = os.path.join(self.local_dir, "invitations.json")
        self.settings_file = os.path.join(self.local_dir, "settings.json")
        self._supabase_invitations_ok = True
        self._supabase_settings_ok = True

    # ---------- Fallback local ----------
    def _read_local_invitations(self) -> list:
        if os.path.exists(self.invitations_file):
            try:
                with open(self.invitations_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _write_local_invitations(self, items: list):
        try:
            with open(self.invitations_file, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ERROR] Écriture invitations locales: {e}")

    # ---------- Invitations ----------
    def list_invitations(self, status: Optional[str] = None, email: Optional[str] = None, role: Optional[str] = None) -> list:
        now = datetime.now(timezone.utc).isoformat()
        if self._supabase_invitations_ok:
            try:
                q = self.supabase.table("invitations").select("*")
                if status:
                    q = q.eq("status", status)
                if email:
                    q = q.eq("email", email.lower().strip())
                if role:
                    q = q.eq("role", role)
                res = q.order("created_at", desc=True).execute()
                items = res.data or []
                # Auto-expirer celles en attente dont la date est dépassée
                for item in items:
                    if item.get("status") == "pending" and item.get("expires_at") and item.get("expires_at") < now:
                        item["status"] = "expired"
                        try:
                            self.supabase.table("invitations").update({"status": "expired"}).eq("id", item["id"]).execute()
                        except Exception:
                            pass
                return items
            except Exception as e:
                err_str = str(e)
                if "PGRST205" in err_str or "invitations" in err_str:
                    print("[INFO] Table Supabase 'invitations' absente, utilisation du stockage local")
                    self._supabase_invitations_ok = False
                else:
                    print(f"[ERROR] list_invitations Supabase: {e}")

        # Fallback local
        items = self._read_local_invitations()
        # Auto-expiration
        changed = False
        for item in items:
            if item.get("status") == "pending" and item.get("expires_at") and item.get("expires_at") < now:
                item["status"] = "expired"
                changed = True
        if changed:
            self._write_local_invitations(items)

        filtered = items
        if status:
            filtered = [i for i in filtered if i.get("status") == status]
        if email:
            filtered = [i for i in filtered if i.get("email") == email.lower().strip()]
        if role:
            filtered = [i for i in filtered if i.get("role") == role]
        return sorted(filtered, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_by_id(self, invitation_id: int) -> Optional[dict]:
        if self._supabase_invitations_ok:
            try:
                res = self.supabase.table("invitations").select("*").eq("id", invitation_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                if "PGRST205" in str(e):
                    self._supabase_invitations_ok = False
                else:
                    print(f"[ERROR] get_by_id Supabase: {e}")

        items = self._read_local_invitations()
        for i in items:
            if int(i.get("id", 0)) == int(invitation_id):
                return i
        return None

    def get_by_token(self, token: str) -> Optional[dict]:
        if not token:
            return None
        if self._supabase_invitations_ok:
            try:
                res = self.supabase.table("invitations").select("*").eq("token", token).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                if "PGRST205" in str(e):
                    self._supabase_invitations_ok = False
                else:
                    print(f"[ERROR] get_by_token Supabase: {e}")

        items = self._read_local_invitations()
        for i in items:
            if i.get("token") == token:
                return i
        return None

    def create_invitation(self, inv_data: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        # Annuler d'abord toute invitation pending existante pour cet email
        email = inv_data.get("email", "").lower().strip()
        self.cancel_pending_for_email(email)

        if self._supabase_invitations_ok:
            try:
                res = self.supabase.table("invitations").insert(inv_data).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                if "PGRST205" in str(e):
                    self._supabase_invitations_ok = False
                else:
                    print(f"[ERROR] create_invitation Supabase: {e}")

        # Fallback local
        items = self._read_local_invitations()
        new_id = max([int(i.get("id", 0)) for i in items] or [0]) + 1
        inv_data["id"] = new_id
        items.append(inv_data)
        self._write_local_invitations(items)
        return inv_data

    def update_invitation(self, invitation_id: int, updates: dict) -> Optional[dict]:
        if self._supabase_invitations_ok:
            try:
                res = self.supabase.table("invitations").update(updates).eq("id", invitation_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                if "PGRST205" in str(e):
                    self._supabase_invitations_ok = False
                else:
                    print(f"[ERROR] update_invitation Supabase: {e}")

        items = self._read_local_invitations()
        for i in items:
            if int(i.get("id", 0)) == int(invitation_id):
                i.update(updates)
                self._write_local_invitations(items)
                return i
        return None

    def cancel_pending_for_email(self, email: str):
        if not email:
            return
        now = datetime.now(timezone.utc).isoformat()
        if self._supabase_invitations_ok:
            try:
                self.supabase.table("invitations").update({
                    "status": "cancelled",
                    "cancelled_at": now
                }).eq("email", email).eq("status", "pending").execute()
            except Exception:
                pass
        items = self._read_local_invitations()
        changed = False
        for i in items:
            if i.get("email") == email and i.get("status") == "pending":
                i["status"] = "cancelled"
                i["cancelled_at"] = now
                changed = True
        if changed:
            self._write_local_invitations(items)

    # ---------- Paramètres (system_settings) ----------
    def get_registration_setting(self) -> bool:
        if self._supabase_settings_ok:
            try:
                res = self.supabase.table("system_settings").select("value").eq("key", "registrations").execute()
                if res.data:
                    val = res.data[0].get("value")
                    if isinstance(val, dict):
                        return bool(val.get("enabled", True))
                    if isinstance(val, bool):
                        return val
            except Exception as e:
                self._supabase_settings_ok = False
                print(f"[INFO] system_settings Supabase non accessible, utilisation stockage local: {e}")

        # Fallback local
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return bool(data.get("registrations", {}).get("enabled", True))
            except Exception:
                pass
        return True

    def set_registration_setting(self, enabled: bool, user_id: Optional[int] = None, user_name: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        val = {"enabled": bool(enabled)}

        # Toujours écrire dans le fichier local
        data = {}
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["registrations"] = val
        data["updated_at"] = now
        data["updated_by"] = user_id
        data["updated_by_name"] = user_name
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ERROR] Écriture settings local: {e}")

        # Synchroniser vers Supabase si disponible
        if self._supabase_settings_ok:
            try:
                self.supabase.table("system_settings").upsert({
                    "key": "registrations",
                    "value": val,
                    "updated_at": now,
                    "updated_by": user_id,
                    "updated_by_name": user_name
                }).execute()
            except Exception as e:
                self._supabase_settings_ok = False
                print(f"[INFO] Upsert system_settings Supabase indisponible: {e}")


# ==================== ENREGISTREMENT DES ROUTES ====================

def register_invitations_routes(app, *, runtime: dict):
    """Enregistre les routes de gestion des invitations et des inscriptions."""
    globals().update(runtime)

    supabase = runtime.get("supabase")
    tables = runtime.get("TABLES", {"users": "app_users", "audit": "audit_logs"})
    roles = runtime.get("ROLES", {"staff": ["super_admin", "docteur", "infirmier", "laboratoire", "pharmacie", "reception"]})
    roles_required = runtime.get("roles_required")
    fast_json = runtime.get("fast_json", lambda: request.get_json(silent=True) or {})
    now_iso = runtime.get("now_iso", lambda: datetime.now(timezone.utc).isoformat())
    add_audit = runtime.get("add_audit", lambda *a, **kw: None)
    invalidate_cache = runtime.get("invalidate_cache", lambda: None)

    store = InvitationsStore(supabase, tables)

    # Exposer la méthode de vérification pour auth.py
    app.config["IS_REGISTRATION_ENABLED_FN"] = store.get_registration_setting

    inv_bp = Blueprint("invitations_routes", __name__)

    # -------------------------------------------------------------
    # 1. GET /api/invitations (super_admin)
    # -------------------------------------------------------------
    @app.route("/api/invitations", methods=["GET"])
    @roles_required("super_admin")
    def get_invitations():
        status = request.args.get("status")
        email = request.args.get("email")
        role = request.args.get("role")

        items = store.list_invitations(status=status, email=email, role=role)
        # RÈGLE OBLIGATOIRE : Ne JAMAIS retourner le token
        safe_items = [{k: v for k, v in i.items() if k != "token"} for i in items]
        return jsonify(safe_items)

    # -------------------------------------------------------------
    # 2. POST /api/invitations (super_admin)
    # -------------------------------------------------------------
    @app.route("/api/invitations", methods=["POST"])
    @roles_required("super_admin")
    def create_invitation():
        data = fast_json()
        email = str(data.get("email") or "").lower().strip()
        role = str(data.get("role") or "").strip()
        message = str(data.get("message") or "").strip() or None
        # Diagnostic Render : ne révèle ni clé API ni token.
        print(f"[INVITATION] Demande reçue pour {mask_email(email)} (rôle: {role or 'absent'})")

        if not email or "@" not in email:
            return jsonify({"error": "Adresse e-mail invalide"}), 422

        allowed_roles = roles.get("staff", ["super_admin", "docteur", "infirmier", "laboratoire", "pharmacie", "reception"])
        if role not in allowed_roles:
            return jsonify({"error": f"Rôle invalide. Rôles autorisés : {', '.join(allowed_roles)}"}), 422

        # Vérifier si un compte existe déjà avec cet email
        try:
            user_check = supabase.table(tables["users"]).select("id").eq("email", email).execute()
            if user_check.data:
                return jsonify({"error": "Un utilisateur existe déjà avec cette adresse e-mail"}), 422
        except Exception as e:
            print(f"[WARN] Vérification utilisateur existant: {e}")

        # Générer token sécurisé et expiration (48 heures)
        token = secrets.token_urlsafe(32)
        created_at = now_iso()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()

        inv_data = {
            "email": email,
            "role": role,
            "token": token,
            "message": message,
            "status": "pending",
            "created_by": g.current_user.get("id"),
            "created_by_name": g.current_user.get("name", "Administrateur"),
            "created_at": created_at,
            "expires_at": expires_at,
            "send_count": 1,
            "last_sent_at": created_at
        }

        created = store.create_invitation(inv_data)
        inv_id = created.get("id")

        # Construire le lien d'invitation
        app_frontend_url = os.getenv("APP_FRONTEND_URL", "https://okito2shop.web.app").rstrip("/")
        invite_url = f"{app_frontend_url}/accept-invitation.html?token={token}"

        # Envoi de l'e-mail via Email Craft
        inviter_name = g.current_user.get("name", "L'administration I-HUB")
        email_res = send_invitation_email(email, invite_url, role, inviter_name, message)
        print(f"[INVITATION] Envoi Email Craft pour {mask_email(email)}: {'OK' if email_res.get('success') else 'ECHEC'}")

        # Audit
        add_audit("CREATE", "invitation", f"Invitation envoyée à {email} (rôle {role})", inv_id)
        invalidate_cache()

        # Réponse sans le token
        safe_response = {k: v for k, v in created.items() if k != "token"}
        if not email_res.get("success"):
            safe_response["warning"] = f"Invitation enregistrée, mais l'e-mail n'a pas pu être délivré: {email_res.get('error')}"

        return jsonify(safe_response), 201

    # -------------------------------------------------------------
    # 3. PATCH /api/invitations/<int:invitation_id> (super_admin)
    # -------------------------------------------------------------
    @app.route("/api/invitations/<int:invitation_id>", methods=["PATCH"])
    @roles_required("super_admin")
    def cancel_invitation(invitation_id: int):
        inv = store.get_by_id(invitation_id)
        if not inv:
            return jsonify({"error": "Invitation introuvable"}), 404

        if inv.get("status") != "pending":
            return jsonify({"error": f"Impossible d'annuler une invitation avec le statut '{inv.get('status')}'"}), 422

        now = now_iso()
        updated = store.update_invitation(invitation_id, {
            "status": "cancelled",
            "cancelled_at": now
        })

        add_audit("UPDATE", "invitation", f"Invitation #{invitation_id} annulée ({inv.get('email')})", invitation_id)
        invalidate_cache()

        safe_res = {k: v for k, v in (updated or inv).items() if k != "token"}
        return jsonify(safe_res)

    # -------------------------------------------------------------
    # 4. POST /api/invitations/<int:invitation_id>/resend (super_admin)
    # -------------------------------------------------------------
    @app.route("/api/invitations/<int:invitation_id>/resend", methods=["POST"])
    @roles_required("super_admin")
    def resend_invitation(invitation_id: int):
        inv = store.get_by_id(invitation_id)
        if not inv:
            return jsonify({"error": "Invitation introuvable"}), 404

        if inv.get("status") != "pending":
            return jsonify({"error": f"Seule une invitation en attente peut être renvoyée (statut actuel : {inv.get('status')})"}), 422

        # Vérifier expiration
        now_dt = datetime.now(timezone.utc)
        if inv.get("expires_at"):
            try:
                exp_dt = datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00"))
                if now_dt > exp_dt:
                    store.update_invitation(invitation_id, {"status": "expired"})
                    return jsonify({"error": "Cette invitation a expiré. Veuillez créer une nouvelle invitation."}), 422
            except Exception:
                pass

        # Limite anti-spam : au moins 60 secondes entre deux envois
        last_sent = inv.get("last_sent_at")
        if last_sent:
            try:
                last_dt = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
                diff = (now_dt - last_dt).total_seconds()
                if diff < 60:
                    wait_sec = int(60 - diff)
                    return jsonify({"error": f"Veuillez patienter encore {wait_sec} seconde(s) avant de renvoyer l'invitation."}), 429
            except Exception:
                pass

        token = inv.get("token")
        if not token:
            return jsonify({"error": "Token d'invitation manquant"}), 500

        app_frontend_url = os.getenv("APP_FRONTEND_URL", "https://okito2shop.web.app").rstrip("/")
        invite_url = f"{app_frontend_url}/accept-invitation.html?token={token}"

        inviter_name = g.current_user.get("name", "L'administration I-HUB")
        email_res = send_invitation_email(inv["email"], invite_url, inv["role"], inviter_name, inv.get("message"))

        new_count = int(inv.get("send_count") or 1) + 1
        now_str = now_dt.isoformat()
        store.update_invitation(invitation_id, {
            "last_sent_at": now_str,
            "send_count": new_count
        })

        add_audit("UPDATE", "invitation", f"Invitation #{invitation_id} renvoyée à {inv['email']}", invitation_id)
        invalidate_cache()

        res_payload = {
            "message": "Invitation renvoyée avec succès",
            "send_count": new_count
        }
        if not email_res.get("success"):
            res_payload["warning"] = f"E-mail non délivré : {email_res.get('error')}"

        return jsonify(res_payload)

    # -------------------------------------------------------------
    # 5. GET /api/invitations/verify/<token> (public)
    # -------------------------------------------------------------
    @app.route("/api/invitations/verify/<token>", methods=["GET"])
    def verify_invitation(token: str):
        if not token or len(token) < 10:
            return jsonify({"error": "Lien d'invitation invalide"}), 404

        inv = store.get_by_token(token)
        if not inv:
            return jsonify({"error": "Invitation introuvable ou inexistante"}), 404

        status = inv.get("status")
        if status == "cancelled":
            return jsonify({"error": "Cette invitation a été annulée par l'administration"}), 410
        if status == "accepted":
            return jsonify({"error": "Cette invitation a déjà été acceptée et utilisée"}), 410

        now_dt = datetime.now(timezone.utc)
        if inv.get("expires_at"):
            try:
                exp_dt = datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00"))
                if now_dt > exp_dt:
                    store.update_invitation(inv["id"], {"status": "expired"})
                    return jsonify({"error": "Cette invitation a expiré (validité 48 heures)"}), 410
            except Exception:
                pass

        if status != "pending":
            return jsonify({"error": f"Cette invitation n'est plus valide (statut : {status})"}), 410

        # RÈGLE OBLIGATOIRE : Ne JAMAIS retourner le token
        return jsonify({
            "valid": True,
            "email_masked": mask_email(inv.get("email")),
            "role": inv.get("role"),
            "expires_at": inv.get("expires_at")
        })

    # -------------------------------------------------------------
    # 6. POST /api/invitations/accept (public)
    # -------------------------------------------------------------
    @app.route("/api/invitations/accept", methods=["POST"])
    def accept_invitation():
        data = fast_json()
        token = str(data.get("token") or "").strip()
        password = str(data.get("password") or "")

        if not token:
            return jsonify({"error": "Jeton d'invitation requis"}), 422

        if len(password) < 8:
            return jsonify({"error": "Le mot de passe doit comporter au moins 8 caractères"}), 422

        inv = store.get_by_token(token)
        if not inv:
            return jsonify({"error": "Invitation introuvable"}), 404

        if inv.get("status") != "pending":
            return jsonify({"error": "Cette invitation n'est plus en attente (déjà utilisée ou annulée)"}), 410

        now_dt = datetime.now(timezone.utc)
        if inv.get("expires_at"):
            try:
                exp_dt = datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00"))
                if now_dt > exp_dt:
                    store.update_invitation(inv["id"], {"status": "expired"})
                    return jsonify({"error": "Cette invitation a expiré. Contactez l'administration."}), 410
            except Exception:
                pass

        email = inv["email"].lower().strip()

        # Vérifier qu'un utilisateur n'existe pas déjà
        try:
            existing = supabase.table(tables["users"]).select("id").eq("email", email).execute()
            if existing.data:
                return jsonify({"error": "Un compte existe déjà pour cette adresse e-mail"}), 422
        except Exception as e:
            print(f"[WARN] Vérification doublon utilisateur: {e}")

        # Déduire un nom présentable
        user_name = email.split("@")[0].replace(".", " ").replace("-", " ").title()

        now_str = now_iso()
        user_data = {
            "name": user_name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": inv["role"],
            "is_active": True,
            "created_at": now_str,
            "updated_at": now_str
        }

        # Insérer l'utilisateur
        created_user_id = None
        try:
            user_insert = supabase.table(tables["users"]).insert(user_data).execute()
            if user_insert.data:
                created_user_id = user_insert.data[0].get("id")
        except Exception as e:
            print(f"[ERROR] Création utilisateur via invitation: {e}")
            return jsonify({"error": f"Erreur lors de la création du compte utilisateur : {str(e)}"}), 500

        # Marquer l'invitation comme acceptée
        store.update_invitation(inv["id"], {
            "status": "accepted",
            "accepted_at": now_str
        })

        # Audits
        add_audit("CREATE", "user", f"Compte activé via invitation ({inv['role']}) : {email}", created_user_id or 0)
        add_audit("UPDATE", "invitation", f"Invitation #{inv['id']} acceptée", inv["id"])
        invalidate_cache()

        return jsonify({
            "success": True,
            "message": "Votre compte a été activé avec succès ! Vous pouvez maintenant vous connecter.",
            "email": email
        }), 200

    # -------------------------------------------------------------
    # 7. GET /api/settings/registrations (public)
    # -------------------------------------------------------------
    @app.route("/api/settings/registrations", methods=["GET"])
    def get_registration_setting_route():
        enabled = store.get_registration_setting()
        return jsonify({"enabled": enabled})

    # -------------------------------------------------------------
    # 8. PATCH /api/settings/registrations (super_admin)
    # -------------------------------------------------------------
    @app.route("/api/settings/registrations", methods=["PATCH"])
    @roles_required("super_admin")
    def update_registration_setting_route():
        data = fast_json()
        if "enabled" not in data:
            return jsonify({"error": "Paramètre 'enabled' requis (booléen)"}), 422

        enabled = bool(data["enabled"])
        user_id = g.current_user.get("id")
        user_name = g.current_user.get("name", "Administrateur")

        store.set_registration_setting(enabled, user_id=user_id, user_name=user_name)

        status_text = "activées" if enabled else "désactivées"
        add_audit("UPDATE", "settings", f"Inscriptions publiques {status_text}", 0)
        invalidate_cache()

        return jsonify({"enabled": enabled, "message": f"Inscriptions publiques {status_text}"})

    app.register_blueprint(inv_bp)
