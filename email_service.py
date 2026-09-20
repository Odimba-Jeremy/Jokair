"""Service d'envoi d'e-mails I-HUB via l'API Resend.

Utilise HTTPS plutôt que SMTP afin d'être compatible avec les hébergements qui
bloquent les ports SMTP sortants.
"""

from __future__ import annotations
import os
import json
import urllib.error
import urllib.request

# Charger automatiquement le fichier .env si présent
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Configuration Resend. RESEND_FROM doit être une adresse/domaine vérifié dans
# Resend. La valeur par défaut permet uniquement les tests Resend autorisés.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", "I-Hub <onboarding@resend.dev>").strip()
RESEND_API_URL = "https://api.resend.com/emails"


def send_email(to_email: str, subject: str, html_body: str, text_body: str = "") -> bool:
    """Envoie un e-mail transactionnel via Resend."""
    if not RESEND_API_KEY:
        print(f"⚠️ [EmailService] RESEND_API_KEY non configurée. Envoi annulé à {to_email} : {subject}")
        return False

    try:
        payload = {
            "from": RESEND_FROM,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        if text_body:
            payload["text"] = text_body

        request = urllib.request.Request(
            RESEND_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))

        print(f"✅ [EmailService] E-mail envoyé avec succès à {to_email} (id: {result.get('id', 'inconnu')})")
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        print(f"❌ [EmailService] Erreur Resend lors de l'envoi à {to_email} : {exc}")
        return False


def build_invitation_email(invite_url: str, role_label: str = "Administrateur") -> tuple[str, str]:
    """Génère le sujet et le gabarit HTML d'invitation administrateur."""
    subject = "Invitation à rejoindre l'équipe I-Hub"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;background:#f0f9ff;margin:0;padding:24px;color:#0c4a6e;">
        <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(10,92,126,0.1);border:1px solid #bae6fd;">
            <div style="background:linear-gradient(135deg,#0a5c7e,#0284c7);padding:28px;text-align:center;color:#fff;">
                <h1 style="margin:0;font-size:24px;letter-spacing:1px;">I-Hub Hôpital</h1>
                <p style="margin:6px 0 0;font-size:13px;opacity:0.9;">Portail de gestion clinique et administrative</p>
            </div>
            <div style="padding:32px 28px;">
                <h2 style="font-size:18px;color:#075985;margin-top:0;">Invitation officielle</h2>
                <p style="font-size:14px;line-height:1.6;color:#334155;">
                    Vous avez été invité(e) par l'administrateur en tant que <strong>{role_label}</strong> sur la plateforme hospitalière I-Hub.
                </p>
                <p style="font-size:14px;line-height:1.6;color:#334155;">
                    Pour activer votre compte et définir votre mot de passe d'accès sécurisé, veuillez cliquer sur le bouton ci-dessous :
                </p>
                <div style="text-align:center;margin:32px 0;">
                    <a href="{invite_url}" style="background:#0284c7;color:#fff;text-decoration:none;padding:13px 28px;border-radius:999px;font-weight:bold;font-size:14px;display:inline-block;box-shadow:0 4px 14px rgba(2,132,199,0.35);">
                        Activer mon compte
                    </a>
                </div>
                <p style="font-size:12px;color:#64748b;line-height:1.5;">
                    Ce lien d'invitation est strictement personnel et expirera dans 72 heures.<br>
                    Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br>
                    <a href="{invite_url}" style="color:#0284c7;word-break:break-all;">{invite_url}</a>
                </p>
            </div>
            <div style="background:#f8fafc;padding:16px;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;">
                I-Hub — Kinshasa, RDC · Document système sécurisé
            </div>
        </div>
    </body>
    </html>
    """
    return subject, html


def build_reset_password_email(reset_url: str) -> tuple[str, str]:
    """Génère le sujet et le gabarit HTML de réinitialisation de mot de passe."""
    subject = "Réinitialisation de votre mot de passe I-Hub"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;background:#f0f9ff;margin:0;padding:24px;color:#0c4a6e;">
        <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(10,92,126,0.1);border:1px solid #bae6fd;">
            <div style="background:linear-gradient(135deg,#0a5c7e,#0284c7);padding:28px;text-align:center;color:#fff;">
                <h1 style="margin:0;font-size:24px;letter-spacing:1px;">I-Hub Hôpital</h1>
                <p style="margin:6px 0 0;font-size:13px;opacity:0.9;">Sécurité des accès médicaux</p>
            </div>
            <div style="padding:32px 28px;">
                <h2 style="font-size:18px;color:#075985;margin-top:0;">Demande de réinitialisation</h2>
                <p style="font-size:14px;line-height:1.6;color:#334155;">
                    Nous avons reçu une demande de réinitialisation de mot de passe pour votre compte professionnel I-Hub.
                </p>
                <div style="text-align:center;margin:32px 0;">
                    <a href="{reset_url}" style="background:#0284c7;color:#fff;text-decoration:none;padding:13px 28px;border-radius:999px;font-weight:bold;font-size:14px;display:inline-block;box-shadow:0 4px 14px rgba(2,132,199,0.35);">
                        Changer mon mot de passe
                    </a>
                </div>
                <p style="font-size:12px;color:#64748b;line-height:1.5;">
                    Ce lien expire dans 1 heure.<br>
                    Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet e-mail en toute sécurité.
                </p>
            </div>
            <div style="background:#f8fafc;padding:16px;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;">
                I-Hub — Kinshasa, RDC · Service de sécurité
            </div>
        </div>
    </body>
    </html>
    """
    return subject, html
