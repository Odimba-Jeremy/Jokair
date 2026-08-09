from __future__ import annotations

import json
import os
import re
import secrets
import time
import gzip
from datetime import datetime, timezone, date, timedelta
from functools import wraps
from typing import Any
import urllib.error
import urllib.request
import base64
import hashlib
import uuid

from flask import Flask, jsonify, request, g, send_file, Response
from flask_cors import CORS
from flask_caching import Cache
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash

# ==================== REDIS CACHE ====================
# Utiliser Redis si disponible, sinon SimpleCache
REDIS_URL = os.getenv("REDIS_URL", "")
if REDIS_URL:
    try:
        import redis
        redis_client = redis.from_url(REDIS_URL)
        redis_client.ping()
        print(f"✅ Redis connecté sur {REDIS_URL}")
        CACHE_TYPE = "RedisCache"
    except Exception as e:
        print(f"⚠️ Redis indisponible: {e}, utilisation SimpleCache")
        CACHE_TYPE = "SimpleCache"
else:
    print("ℹ️ REDIS_URL non définie, utilisation SimpleCache")
    CACHE_TYPE = "SimpleCache"

# ==================== CONFIGURATION ====================
SUPABASE_URL = "https://figmeixteescztmmprmi.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZpZ21laXh0ZWVzY3p0bW1wcm1pIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTM4NjA2MCwiZXhwIjoyMDkwOTYyMDYwfQ.zMIDYvm-Bwv0EUQzME3nZR8ZPoSwTMCaybHRnw_-7Ew"
SECRET_KEY = os.getenv("SECRET_KEY", "ihub_super_secret_key_2024")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
HOST = "0.0.0.0"
PORT = 10000
DEBUG = True
TOKEN_EXPIRY = 86400 * 7
CACHE_TIMEOUT = 300

# ==================== INITIALISATION ====================
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["CACHE_TYPE"] = CACHE_TYPE
app.config["CACHE_REDIS_URL"] = REDIS_URL
app.config["CACHE_DEFAULT_TIMEOUT"] = CACHE_TIMEOUT
app.config["JSON_AS_ASCII"] = False

cache = Cache(app)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

@app.before_request
def start_request_timer():
    g.request_started_at = time.perf_counter()

@app.after_request
def compress_and_measure_response(response):
    elapsed_ms = (time.perf_counter() - getattr(g, "request_started_at", time.perf_counter())) * 1000
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "").lower()
    compressible = response.mimetype in ("application/json", "text/html", "text/css", "application/javascript")
    if accepts_gzip and compressible and not response.headers.get("Content-Encoding") and len(response.get_data()) > 1024:
        response.set_data(gzip.compress(response.get_data(), compresslevel=6))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
        response.headers["Content-Length"] = str(len(response.get_data()))
    return response

@app.after_request
def optimize_response(response):
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "private, max-age=30")
        response.headers.setdefault("Vary", "Accept-Encoding, Authorization")
    if (
        response.status_code == 200
        and response.mimetype == "application/json"
        and "gzip" in request.headers.get("Accept-Encoding", "").lower()
        and not response.headers.get("Content-Encoding")
        and len(response.get_data()) > 1024
    ):
        response.set_data(gzip.compress(response.get_data(), compresslevel=6))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(response.get_data()))
    return response

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY est requis")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
serializer = URLSafeTimedSerializer(SECRET_KEY)

TABLES = {
    "users": "app_users",
    "patients": "patients",
    "appointments": "appointments",
    "prescriptions": "prescriptions",
    "lab_tests": "laboratory_tests",
    "care": "care_logs",
    "pharmacy": "pharmacy_items",
    "billing": "invoices",
    "audit": "audit_logs",
    "tariffs": "tariff_grid",
    "tariff_history": "tariff_history",
    "invoice_payments": "invoice_payments"
}

ROLES = {
    "public": ["docteur", "infirmier", "laboratoire", "pharmacie", "reception"],
    "staff": ["super_admin", "docteur", "infirmier", "laboratoire", "pharmacie", "reception"],
}

# ==================== GRILLE TARIFAIRE COMPLÈTE (BASE DE DONNÉES) ====================
# Cette constante est conservée pour compatibilité, mais facture_auto utilise la base
# Ne plus utiliser TARIFS directement dans facture_auto
TARIFS = {
    "CONSULT": {"label": "Consultation médicale générale", "price_usd": 15, "category": "Consultation"},
    "URGENCE": {"label": "Consultation d'urgence", "price_usd": 20, "category": "Consultation"},
    "CONTROLE": {"label": "Consultation de contrôle", "price_usd": 12, "category": "Consultation"},
    "SOIN_BASE": {"label": "Soin infirmier", "price_usd": 5, "category": "Soins"},
    "SOIN_PANSEMENT": {"label": "Pansement", "price_usd": 5, "category": "Soins"},
    "SOIN_INJECTION": {"label": "Injection", "price_usd": 3, "category": "Soins"},
    "SOIN_PERFUSION": {"label": "Perfusion", "price_usd": 8, "category": "Soins"},
    "SOIN_SUTURE": {"label": "Suture", "price_usd": 10, "category": "Soins"},
    "SOIN_PLATRE": {"label": "Plâtre", "price_usd": 20, "category": "Soins"},
    "HEMO": {"label": "Hémogramme complet", "price_usd": 15, "category": "Laboratoire"},
    "HEP": {"label": "Bilan hépatique", "price_usd": 15, "category": "Laboratoire"},
    "REN": {"label": "Bilan rénal", "price_usd": 15, "category": "Laboratoire"},
    "LIP": {"label": "Bilan lipidique", "price_usd": 15, "category": "Laboratoire"},
    "URINE": {"label": "Analyse d'urine", "price_usd": 10, "category": "Laboratoire"},
    "MEDIC_BASE": {"label": "Médicament", "price_usd": 5, "category": "Pharmacie"},
    "MEDIC_ANTIB": {"label": "Antibiotique", "price_usd": 8, "category": "Pharmacie"},
    "MEDIC_SPEC": {"label": "Médicament spécialisé", "price_usd": 15, "category": "Pharmacie"},
    "ADMISSION": {"label": "Admission / frais de dossier", "price_usd": 15, "category": "Hospitalisation"},
    "HOSPI_JOUR": {"label": "Hospitalisation / jour", "price_usd": 20, "category": "Hospitalisation"},
    "CHAMBRE_PRIV": {"label": "Chambre privée / jour", "price_usd": 35, "category": "Hospitalisation"},
    "HOSPI_USI": {"label": "Soins intensifs / jour", "price_usd": 50, "category": "Hospitalisation"},
    "SORTIE": {"label": "Bulletin de sortie", "price_usd": 5, "category": "Hospitalisation"},
    "ACC_VAG": {"label": "Accouchement voie basse", "price_usd": 100, "category": "Maternité"},
    "ACC_CES": {"label": "Accouchement césarienne", "price_usd": 180, "category": "Maternité"},
    "CPN": {"label": "Consultation prénatale", "price_usd": 15, "category": "Maternité"},
    "ECHO_OBST": {"label": "Échographie obstétricale", "price_usd": 25, "category": "Maternité"},
    "LIT_MAT": {"label": "Lit maternité / jour", "price_usd": 25, "category": "Maternité"},
    "RADIO_THO": {"label": "Radiographie thorax", "price_usd": 20, "category": "Imagerie"},
    "SCAN": {"label": "Scanner", "price_usd": 60, "category": "Imagerie"},
    "IRM": {"label": "IRM", "price_usd": 100, "category": "Imagerie"},
}

# ==================== UTILITAIRES ====================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def hospital_patient_id(patient_id: Any) -> str:
    return f"IH-USHD-{to_int(patient_id):03d}"

def enrich_patient_identifier(patient: dict) -> dict:
    patient = dict(patient)
    patient["hospital_id"] = hospital_patient_id(patient.get("id"))
    return patient

def enrich_patient_identifiers(patients: list) -> list:
    return [enrich_patient_identifier(patient) for patient in (patients or [])]

def fast_json() -> dict:
    return request.get_json(silent=True) or {}

def to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

def to_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def normalize_status(status: str, valid: list, default: str) -> str:
    return status if status in valid else default

def optional_date(value):
    return value or None

def missing_schema_column(exc: Exception) -> str | None:
    match = re.search(r"Could not find the '([^']+)' column", str(exc))
    return match.group(1) if match else None

def compatible_insert(table_name: str, data: dict):
    payload = dict(data)
    removed_columns = []
    while True:
        try:
            result = supabase.table(table_name).insert(payload).execute()
            if removed_columns:
                print(f" Colonnes ignorees pour {table_name}: {', '.join(removed_columns)}")
            return result
        except Exception as exc:
            column = missing_schema_column(exc)
            if not column or column not in payload:
                raise
            removed_columns.append(column)
            payload.pop(column, None)

def compatible_update(table_name: str, data: dict, field: str, value: Any):
    payload = {k: v for k, v in dict(data).items() if v is not None}
    removed_columns = []
    while True:
        try:
            result = supabase.table(table_name).update(payload).eq(field, value).execute()
            if removed_columns:
                print(f" Colonnes ignorees pour {table_name}: {', '.join(removed_columns)}")
            return result
        except Exception as exc:
            column = missing_schema_column(exc)
            if not column or column not in payload:
                raise
            removed_columns.append(column)
            payload.pop(column, None)

def invalidate_cache(pattern: str = None):
    cache.clear()

def cached(timeout=CACHE_TIMEOUT, key_prefix=None):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            cache_key = key_prefix or f"{f.__name__}:{request.full_path}"
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                return jsonify(cached_data)
            result = f(*args, **kwargs)
            if result and hasattr(result, 'get_json'):
                data = result.get_json()
                if data:
                    cache.set(cache_key, data, timeout)
            return result
        return decorated
    return decorator

def parse_date(value):
    if not value:
        return None
    try:
        if isinstance(value, str):
            if 'T' in value:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
            return datetime.fromisoformat(value).date()
        elif hasattr(value, 'date'):
            return value.date()
        return value
    except Exception:
        return None

def age_years(patient: dict):
    birth = parse_date(patient.get("date_of_birth") or patient.get("birth_date") or patient.get("dob"))
    if not birth:
        return None
    today = datetime.now(timezone.utc).date()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

def is_female(patient: dict) -> bool:
    return str(patient.get("gender", "")).lower() in ("f", "female", "feminin", "féminin", "femme")

def active_pregnant_patient_ids() -> set:
    try:
        result = supabase.table("pregnancies").select("patient_id,status").eq("status", "active").execute()
        return {str(row.get("patient_id")) for row in (result.data or []) if row.get("patient_id")}
    except Exception:
        return set()

def add_pregnancy_flags(patients: list) -> list:
    pregnant_ids = active_pregnant_patient_ids()
    for patient in patients:
        patient["is_pregnant"] = str(patient.get("id")) in pregnant_ids
    return patients

def linked_patient_ids_for_user() -> set:
    user_id = g.current_user.get("id") if hasattr(g, "current_user") else None
    if not user_id:
        return set()
    user_id = str(user_id)
    linked = set()
    sources = [
        (TABLES["patients"], "id", ("created_by",)),
        ("patient_queue", "patient_id", ("assigned_doctor_id",)),
        ("patient_dispatches", "patient_id", ("doctor_id",)),
        ("medical_consultations", "patient_id", ("doctor_id",)),
        (TABLES["appointments"], "patient_id", ("created_by", "doctor_id", "practitioner_id", "provider_id", "user_id", "assigned_to")),
        (TABLES["prescriptions"], "patient_id", ("created_by", "doctor_id", "practitioner_id", "provider_id", "user_id")),
        (TABLES["lab_tests"], "patient_id", ("created_by", "doctor_id", "requested_by", "technician_id", "user_id")),
        (TABLES["care"], "patient_id", ("created_by", "nurse_id", "caregiver_id", "user_id")),
        (TABLES["billing"], "patient_id", ("created_by", "cashier_id", "user_id")),
    ]
    for table_name, patient_field, user_fields in sources:
        for user_field in user_fields:
            try:
                rows = supabase.table(table_name).select(f"{patient_field},{user_field}").eq(user_field, user_id).execute().data or []
                linked.update(str(row.get(patient_field)) for row in rows if row.get(patient_field))
            except Exception:
                continue
    return linked

def filter_patients_for_role(patients: list) -> list:
    role = g.current_user.get("role") if hasattr(g, "current_user") else ""
    patients = add_pregnancy_flags(patients)
    if role in ("super_admin", "infirmier"):
        return patients
    if role == "docteur":
        linked_ids = linked_patient_ids_for_user()
        return [p for p in patients if str(p.get("id")) in linked_ids]
    linked_ids = linked_patient_ids_for_user()
    if role == "reception" and not linked_ids:
        return patients
    return [p for p in patients if str(p.get("id")) in linked_ids]

def can_access_patient_record(patient: dict) -> bool:
    return any(str(row.get("id")) == str(patient.get("id")) for row in filter_patients_for_role([patient]))

def filter_appointments_for_role(appointments: list) -> list:
    role = g.current_user.get("role") if hasattr(g, "current_user") else ""
    return appointments

# ==================== AUTHENTIFICATION ====================
def create_token(user: dict) -> str:
    payload = {
        "id": user["id"],
        "role": user["role"],
        "email": user["email"],
        "exp": int(time.time()) + TOKEN_EXPIRY
    }
    return serializer.dumps(payload)

def decode_token(token: str) -> dict:
    return serializer.loads(token, max_age=TOKEN_EXPIRY)

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.cookies.get("ihub_session", "")
        if not token:
            return jsonify({"error": "Token requis"}), 401
        try:
            payload = decode_token(token)
            cache_key = f"user:{payload['id']}"
            user_data = cache.get(cache_key)
            if user_data is None:
                result = supabase.table(TABLES["users"]).select("*").eq("id", payload["id"]).execute()
                if not result.data:
                    return jsonify({"error": "Utilisateur introuvable"}), 401
                user_data = result.data[0]
                cache.set(cache_key, user_data, CACHE_TIMEOUT)
            g.current_user = user_data
        except (SignatureExpired, BadSignature):
            return jsonify({"error": "Token invalide ou expiré"}), 401
        return f(*args, **kwargs)
    return decorated

def roles_required(*allowed):
    def decorator(f):
        @token_required
        @wraps(f)
        def decorated(*args, **kwargs):
            if g.current_user.get("role") not in allowed:
                return jsonify({"error": "Accès interdit"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

def add_audit(action: str, entity: str, details: str = None, entity_id: int = None):
    try:
        compatible_insert(TABLES["audit"], {
            "action": action,
            "entity_type": entity,
            "entity_id": entity_id,
            "user_id": g.current_user.get("id") if hasattr(g, 'current_user') else None,
            "user_name": g.current_user.get("name") if hasattr(g, 'current_user') else "Systeme",
            "details": details or "",
            "created_at": now_iso()
        })
    except:
        pass

def get_user_map(role: str = None) -> dict:
    try:
        query = supabase.table(TABLES["users"]).select("id,name,role")
        if role:
            query = query.eq("role", role)
        rows = query.execute().data or []
        return {row["id"]: row for row in rows}
    except Exception:
        return {}

def get_patient_map() -> dict:
    try:
        rows = supabase.table(TABLES["patients"]).select("id,full_name").execute().data or []
        return {row["id"]: row.get("full_name", "Inconnu") for row in rows}
    except Exception:
        return {}

def get_tariff_amount(category: str, label: str = "", default: float = 0.0) -> float:
    try:
        rows = supabase.table(TABLES["tariffs"]).select("*").eq("category", category).eq("is_active", True).execute().data or []
    except Exception:
        return to_float(default, 0.0)
    if not rows:
        return to_float(default, 0.0)
    label_key = str(label or "").strip().lower()
    if label_key:
        for row in rows:
            if str(row.get("label", "")).strip().lower() == label_key:
                return to_float(row.get("amount"), default)
    return to_float(rows[0].get("amount"), default)

def parse_json_array(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []

def normalize_invoice_lines(invoice: dict) -> list:
    return parse_json_array(invoice.get("items")) or parse_json_array(invoice.get("line_items"))

def add_invoice_payment(invoice_id: int, patient_id: int, amount: float, notes: str = ""):
    amount = round(to_float(amount), 2)
    if not invoice_id or amount <= 0:
        return None
    try:
        result = compatible_insert(TABLES["invoice_payments"], {
            "invoice_id": invoice_id,
            "patient_id": patient_id,
            "amount": amount,
            "notes": notes,
            "created_by": g.current_user.get("id") if hasattr(g, "current_user") else None,
            "created_by_name": g.current_user.get("name") if hasattr(g, "current_user") else "Systeme",
            "created_at": now_iso()
        })
        return result.data[0] if result.data else None
    except Exception as exc:
        print(f"Impossible d'ajouter le paiement facture: {exc}")
        return None

def add_patient_account_line(patient_id: int, category: str, description: str, amount: float,
                             source: str = "", source_id: int = None, quantity: int = 1,
                             unit_price: float = None):
    amount = round(to_float(amount), 2)
    if not patient_id or amount <= 0:
        return None
    line = {
        "patient_id": patient_id,
        "category": category,
        "description": description,
        "amount": amount,
        "quantity": max(1, to_int(quantity, 1)),
        "unit_price": round(to_float(unit_price, amount), 2),
        "source": source,
        "source_id": source_id,
        "status": "pending",
        "created_by": g.current_user.get("id") if hasattr(g, "current_user") else None,
        "created_by_name": g.current_user.get("name") if hasattr(g, "current_user") else "Systeme",
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    try:
        result = compatible_insert("patient_account_lines", line)
        return result.data[0] if result.data else None
    except Exception as exc:
        print(f"Impossible d'ajouter la ligne compte patient: {exc}")
        return None

def create_service_invoice(patient_id: int, description: str, amount: float, source: str, source_id: int = None, account_line: dict = None):
    amount = round(to_float(amount), 2)
    if not patient_id or amount <= 0:
        return None
    item = {"code": source.upper(), "description": description, "quantity": 1, "unit_price": amount, "amount": amount, "date": now_iso()}
    invoice = {
        "invoice_number": f"AUTO-{int(time.time())}-{secrets.token_hex(2).upper()}",
        "patient_id": patient_id,
        "amount": amount,
        "description": description,
        "status": "unpaid",
        "line_items": [item],
        "items": [item],
        "source": source,
        "source_id": source_id,
        "created_by": g.current_user.get("id"),
        "created_by_name": g.current_user.get("name"),
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["billing"], invoice)
    created = result.data[0] if result.data else invoice
    if account_line and created.get("id"):
        compatible_update("patient_account_lines", {"status": "invoiced", "invoice_id": created["id"], "updated_at": now_iso()}, "id", account_line.get("id"))
    return created

# ==================== FACTURATION AUTOMATIQUE (UTILISE LA BASE) ====================
def get_current_rate():
    """Récupère le taux de change actuel depuis la base"""
    try:
        result = supabase.table("exchange_rates").select("*").order("created_at", desc=True).limit(1).execute()
        if result.data:
            return float(result.data[0].get("rate", 2800))
    except Exception:
        pass
    return 2800

def get_tarif_from_db(code_tarif):
    """Récupère un tarif depuis la base de données"""
    try:
        result = supabase.table(TABLES["tariffs"]).select("*").eq("code", code_tarif).eq("is_active", True).execute()
        if result.data:
            return result.data[0]
        # Essayer par catégorie
        result = supabase.table(TABLES["tariffs"]).select("*").eq("category", code_tarif).eq("is_active", True).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception:
        return None

def facture_auto(patient_id, code_tarif, quantite=1, source="", source_id=None):
    """Génère une facture automatiquement depuis la base de données"""
    # 1. Récupérer le tarif depuis la base
    tarif = get_tarif_from_db(code_tarif)
    if not tarif:
        print(f"Tarif non trouvé pour le code: {code_tarif}")
        return None
    
    taux = get_current_rate()
    prix_usd = to_float(tarif.get("price_usd", 0)) * quantite
    prix_cdf = prix_usd * taux
    
    if prix_cdf <= 0:
        return None
    
    facture = {
        "invoice_number": f"AUTO-{int(time.time())}-{secrets.token_hex(2).upper()}",
        "patient_id": patient_id,
        "amount": prix_cdf,
        "amount_usd": prix_usd,
        "description": tarif.get("label", code_tarif),
        "status": "unpaid",
        "source": source or code_tarif,
        "source_id": source_id,
        "items": [{
            "code": code_tarif,
            "description": tarif.get("label", code_tarif),
            "quantity": quantite,
            "unit_price_usd": tarif.get("price_usd", 0),
            "unit_price_cdf": tarif.get("price_usd", 0) * taux,
            "amount_usd": prix_usd,
            "amount_cdf": prix_cdf
        }],
        "created_by": g.current_user.get("id") if hasattr(g, "current_user") else None,
        "created_by_name": g.current_user.get("name") if hasattr(g, "current_user") else "Systeme",
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    
    result = compatible_insert(TABLES["billing"], facture)
    invoice = result.data[0] if result.data else facture
    
    # Ajouter au compte patient
    add_patient_account_line(
        patient_id, 
        "auto", 
        tarif.get("label", code_tarif), 
        prix_cdf, 
        source or code_tarif, 
        source_id
    )
    
    return invoice

def get_tarif_code_for_care(care_type):
    """Retourne le code tarifaire pour un type de soin"""
    mapping = {
        "Pansement": "SOIN_PANSEMENT",
        "Injection": "SOIN_INJECTION",
        "Perfusion": "SOIN_PERFUSION",
        "Suture": "SOIN_SUTURE",
        "Plâtre": "SOIN_PLATRE",
    }
    return mapping.get(care_type, "SOIN_BASE")

# ==================== GENERATE BARCODE ====================
def generate_barcode_svg(patient_id: int, patient_name: str) -> str:
    from datetime import datetime
    
    patient_id_str = f"PAT-{patient_id:06d}"
    now = datetime.now().strftime("%d/%m/%Y")
    
    bars = []
    import random
    random.seed(patient_id)
    
    binary = bin(patient_id)[2:].zfill(20)
    pattern = binary + ''.join('1' if random.random() > 0.5 else '0' for _ in range(40))
    
    x = 0
    for bit in pattern:
        width = 1 if bit == '0' else 2
        bars.append(f'<rect x="{x}" y="10" width="{width}" height="40" fill="black"/>')
        x += width
    
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{x + 20}" height="100" viewBox="0 0 {x + 20} 100">
    <rect width="{x + 20}" height="100" fill="white"/>
    <g transform="translate(10, 0)">
        {''.join(bars)}
    </g>
    <text x="10" y="70" font-family="monospace" font-size="14" font-weight="bold" fill="#0a5c7e">{patient_id_str}</text>
    <text x="10" y="85" font-family="monospace" font-size="11" fill="#64748b">{patient_name}</text>
    <text x="10" y="95" font-family="monospace" font-size="9" fill="#94a3b8">I HUB - {now}</text>
</svg>'''
    return svg

# ==================== AUTH ROUTES ====================
@app.route("/api/auth/login", methods=["POST"])
def login():
    data = fast_json()
    email = data.get("email", "").lower().strip()
    password = data.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 422
    result = supabase.table(TABLES["users"]).select("*").eq("email", email).execute()
    user = result.data[0] if result.data else None
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401
    token = create_token(user)
    add_audit("LOGIN", "user", f"Connexion: {email}", user["id"])
    return jsonify({
        "user": {k: v for k, v in user.items() if k not in ["password_hash"]},
        "token": token
    })

@app.route("/api/auth/register", methods=["POST"])
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
    if role not in ROLES["public"]:
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
    user = result.data[0]
    token = create_token(user)
    add_audit("CREATE", "user", f"Inscription: {email}", user["id"])
    invalidate_cache()
    return jsonify({
        "user": {k: v for k, v in user.items() if k != "password_hash"},
        "token": token
    }), 201

@app.route("/api/auth/logout", methods=["POST"])
@token_required
def logout():
    add_audit("LOGOUT", "user", f"Déconnexion: {g.current_user.get('email')}", g.current_user.get("id"))
    return jsonify({"message": "Déconnexion réussie"})

@app.route("/api/auth/me", methods=["GET"])
@token_required
def auth_me():
    return jsonify({"user": {k: v for k, v in g.current_user.items() if k != "password_hash"}})

# ==================== PATIENTS ====================
@app.route("/api/patients", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(timeout=120)  # Cache 2 minutes
def get_patients():
    search = request.args.get("search", "").strip().lower()
    context = request.args.get("context", "").strip().lower()
    numeric_search = re.fullmatch(r"(?:ih-ushd-)?0*(\d+)", search, re.IGNORECASE)
    limit = min(max(to_int(request.args.get("limit"), 0), 0), 200)
    if search:
        id_clause = f",id.eq.{int(numeric_search.group(1))}" if numeric_search else ""
        query = supabase.table(TABLES["patients"]).select("*")\
            .or_(f"full_name.ilike.%{search}%,phone.ilike.%{search}%,email.ilike.%{search}%{id_clause}")\
            .order("created_at", desc=True)
    else:
        query = supabase.table(TABLES["patients"]).select("*").order("created_at", desc=True)
    if limit:
        query = query.limit(limit)
    result = query.execute()
    patients = enrich_patient_identifiers(add_pregnancy_flags(result.data or []))
    role = g.current_user.get("role")
    if context in ("maternity", "pregnancy", "prenatal", "delivery") and role == "super_admin":
        return jsonify([patient for patient in patients if is_female(patient)])
    if context == "pharmacy" and role in ("super_admin", "pharmacie"):
        return jsonify(patients)
    return jsonify(filter_patients_for_role(patients))

@app.route("/api/patients", methods=["POST"])
@roles_required("super_admin", "reception")
def create_patient():
    data = fast_json()
    full_name = data.get("full_name", "").strip()
    if not full_name:
        return jsonify({"error": "Nom requis"}), 422
    patient = {
        "full_name": full_name,
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "date_of_birth": optional_date(data.get("date_of_birth")),
        "gender": data.get("gender", ""),
        "blood_type": data.get("blood_type", ""),
        "address": data.get("address", ""),
        "status": data.get("status", "waiting"),
        "allergies": data.get("allergies", ""),
        "medical_history": data.get("medical_history", ""),
        "emergency_contact": data.get("emergency_contact", ""),
        "insurance": data.get("insurance", ""),
        "priority": data.get("priority", "normal"),
        "doctor_notes": data.get("doctor_notes", ""),
        "room_number": data.get("room_number", ""),
        "is_pregnant": data.get("is_pregnant", False),
        "created_by": g.current_user.get("id"),
        "created_by_name": g.current_user.get("name") or g.current_user.get("email"),
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["patients"], patient)
    created_patient = result.data[0]
    try:
        last = supabase.table("patient_queue").select("arrival_order").order("arrival_order", desc=True).limit(1).execute().data or []
        arrival_order = to_int(last[0].get("arrival_order"), 0) + 1 if last else 1
        compatible_insert("patient_queue", {
            "patient_id": created_patient["id"],
            "status": "waiting",
            "arrival_order": arrival_order,
            "arrival_time": now_iso(),
            "created_by": g.current_user.get("id"),
            "created_by_name": g.current_user.get("name") or g.current_user.get("email"),
            "created_at": now_iso(),
            "updated_at": now_iso()
        })
    except Exception as exc:
        print(f"File d'attente non creee pour patient #{created_patient.get('id')}: {exc}")
    if data.get("is_pregnant") and is_female(created_patient):
        pregnancy_lmp = optional_date(data.get("pregnancy_lmp")) or datetime.now(timezone.utc).date().isoformat()
        pregnancy = {
            "patient_id": created_patient["id"],
            "last_menstrual_period": pregnancy_lmp,
            "expected_delivery_date": optional_date(data.get("expected_delivery_date")),
            "risk_level": data.get("risk_level", "normal"),
            "medical_history": data.get("pregnancy_notes", "Grossesse signalee a la reception, DDR a completer en maternite."),
            "status": "active",
            "created_by": g.current_user.get("id"),
            "created_by_name": g.current_user.get("name") or g.current_user.get("email"),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        compatible_insert("pregnancies", pregnancy)
        created_patient["is_pregnant"] = True
    add_audit("CREATE", "patient", f"Patient: {full_name}", created_patient["id"])
    invalidate_cache()
    return jsonify(created_patient), 201

@app.route("/api/patients/<int:patient_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(timeout=60)
def get_patient(patient_id: int):
    result = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
    if not result.data:
        return jsonify({"error": "Patient introuvable"}), 404
    patient = enrich_patient_identifier(add_pregnancy_flags(result.data)[0])
    if not can_access_patient_record(patient):
        return jsonify({"error": "Acces patient non autorise"}), 403
    return jsonify(patient)

@app.route("/api/patients/<int:patient_id>", methods=["PUT"])
@roles_required("super_admin", "docteur", "infirmier", "reception")
def update_patient(patient_id: int):
    data = fast_json()
    existing = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
    if not existing.data:
        return jsonify({"error": "Patient introuvable"}), 404
    if not can_access_patient_record(add_pregnancy_flags(existing.data)[0]):
        return jsonify({"error": "Acces patient non autorise"}), 403
    allowed_fields = ["full_name", "phone", "email", "date_of_birth", "gender", "blood_type",
                      "address", "status", "allergies", "medical_history", "emergency_contact",
                      "insurance", "priority", "doctor_notes", "room_number", "is_pregnant"]
    updates = {k: v for k, v in data.items() if k in allowed_fields and v is not None}
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["patients"]).update(updates).eq("id", patient_id).execute()
    if not result.data:
        return jsonify({"error": "Patient introuvable"}), 404
    add_audit("UPDATE", "patient", f"Patient #{patient_id} modifié", patient_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/patients/<int:patient_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_patient(patient_id: int):
    patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", patient_id).execute()
    if not patient.data:
        return jsonify({"error": "Patient introuvable"}), 404
    supabase.table(TABLES["patients"]).delete().eq("id", patient_id).execute()
    add_audit("DELETE", "patient", f"Patient: {patient.data[0]['full_name']}", patient_id)
    invalidate_cache()
    return jsonify({"message": "Patient supprimé"})

@app.route("/api/patients/<int:patient_id>/appointments", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_patient_appointments(patient_id: int):
    result = supabase.table(TABLES["appointments"]).select("*").eq("patient_id", patient_id).order("date", desc=True).execute()
    return jsonify(result.data)

@app.route("/api/patients/<int:patient_id>/prescriptions", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_patient_prescriptions(patient_id: int):
    result = supabase.table(TABLES["prescriptions"]).select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute()
    return jsonify(result.data)

@app.route("/api/patients/<int:patient_id>/lab-results", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_patient_lab_results(patient_id: int):
    result = supabase.table(TABLES["lab_tests"]).select("*").eq("patient_id", patient_id).eq("status", "completed").order("completed_date", desc=True).execute()
    return jsonify(result.data)

@app.route("/api/patients/<int:patient_id>/lab-results", methods=["POST"])
@roles_required("super_admin", "laboratoire")
def link_lab_result_to_patient(patient_id: int):
    data = fast_json()
    if not data.get("test_id") or not data.get("test_type"):
        return jsonify({"error": "test_id et test_type requis"}), 422
    
    test = supabase.table(TABLES["lab_tests"]).select("*").eq("id", data.get("test_id")).execute()
    if not test.data:
        return jsonify({"error": "Analyse introuvable"}), 404
    
    lab_result = {
        "patient_id": patient_id,
        "test_id": data.get("test_id"),
        "test_type": data.get("test_type"),
        "result": data.get("result", ""),
        "observations": data.get("observations", ""),
        "linked_at": now_iso(),
        "linked_by": g.current_user.get("id"),
        "linked_by_name": g.current_user.get("name") or g.current_user.get("email")
    }
    result = compatible_insert("patient_lab_results", lab_result)
    add_audit("CREATE", "patient_lab_result", f"Résultat lié au patient #{patient_id}", patient_id)
    invalidate_cache()
    return jsonify(result.data[0] if result.data else lab_result), 201

# ==================== PATIENT BARCODE ====================
@app.route("/api/patients/<int:patient_id>/barcode", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_patient_barcode(patient_id: int):
    patient = supabase.table(TABLES["patients"]).select("id,full_name").eq("id", patient_id).execute()
    if not patient.data:
        return jsonify({"error": "Patient introuvable"}), 404
    
    patient_data = patient.data[0]
    svg = generate_barcode_svg(patient_id, patient_data.get("full_name", "Patient"))
    
    return Response(svg, mimetype='image/svg+xml')

@app.route("/api/patients/<int:patient_id>/barcode", methods=["POST"])
@roles_required("super_admin", "reception")
def regenerate_patient_barcode(patient_id: int):
    patient = supabase.table(TABLES["patients"]).select("id,full_name").eq("id", patient_id).execute()
    if not patient.data:
        return jsonify({"error": "Patient introuvable"}), 404
    
    patient_data = patient.data[0]
    svg = generate_barcode_svg(patient_id, patient_data.get("full_name", "Patient"))
    
    add_audit("UPDATE", "patient", f"Code-barres régénéré pour patient #{patient_id}", patient_id)
    
    return Response(svg, mimetype='image/svg+xml')

# ==================== APPOINTMENTS ====================
@app.route("/api/appointments", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(timeout=60)  # Cache 1 minute
def get_appointments():
    status = request.args.get("status")
    patient_id = request.args.get("patient_id")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    limit = min(max(to_int(request.args.get("limit"), 100), 1), 500)
    offset = to_int(request.args.get("offset"), 0)
    query = supabase.table(TABLES["appointments"]).select("*")
    if status:
        query = query.eq("status", status)
    if patient_id:
        query = query.eq("patient_id", to_int(patient_id))
    if date_from:
        query = query.gte("date", date_from)
    if date_to:
        query = query.lte("date", date_to)
    result = query.order("date", desc=True).limit(limit).offset(offset).execute()
    appointments = filter_appointments_for_role(result.data or [])
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for apt in appointments:
        apt["patient_name"] = patient_map.get(apt.get("patient_id"), "Inconnu")
    return jsonify(appointments)

@app.route("/api/appointments", methods=["POST"])
@roles_required("super_admin", "docteur", "infirmier", "reception")
def create_appointment():
    data = fast_json()
    required = ["patient_id", "date", "type"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Champ {field} requis"}), 422
    appointment = {
        "patient_id": to_int(data.get("patient_id")),
        "date": data.get("date"),
        "type": data.get("type"),
        "duration": to_int(data.get("duration"), 30),
        "notes": data.get("notes", ""),
        "status": normalize_status(data.get("status", "scheduled"), ["scheduled", "arrived", "in_consultation", "completed", "cancelled"], "scheduled"),
        "priority": normalize_status(data.get("priority", "normal"), ["normal", "urgent"], "normal"),
        "doctor_id": g.current_user["id"],
        "doctor_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["appointments"], appointment)
    add_audit("CREATE", "appointment", f"RDV #{result.data[0]['id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/appointments/<int:appointment_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_appointment(appointment_id: int):
    result = supabase.table(TABLES["appointments"]).select("*").eq("id", appointment_id).execute()
    if not result.data:
        return jsonify({"error": "Rendez-vous introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/appointments/<int:appointment_id>", methods=["PUT"])
@roles_required("super_admin", "docteur", "infirmier", "reception")
def update_appointment(appointment_id: int):
    data = fast_json()
    allowed = ["date", "type", "duration", "status", "priority", "notes"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "status" in updates:
        updates["status"] = normalize_status(updates["status"], ["scheduled", "arrived", "in_consultation", "completed", "cancelled"], "scheduled")
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["appointments"]).update(updates).eq("id", appointment_id).execute()
    if not result.data:
        return jsonify({"error": "Rendez-vous introuvable"}), 404
    add_audit("UPDATE", "appointment", f"RDV #{appointment_id} modifié", appointment_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/appointments/<int:appointment_id>", methods=["PATCH"])
@roles_required("super_admin", "docteur", "infirmier", "reception")
def patch_appointment(appointment_id: int):
    data = fast_json()
    allowed = ["status", "date", "type", "duration", "priority", "notes"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "status" in updates:
        updates["status"] = normalize_status(updates["status"], ["scheduled", "arrived", "in_consultation", "completed", "cancelled"], "scheduled")
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["appointments"]).update(updates).eq("id", appointment_id).execute()
    if not result.data:
        return jsonify({"error": "Rendez-vous introuvable"}), 404
    add_audit("UPDATE", "appointment", f"RDV #{appointment_id} modifié (PATCH)", appointment_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/appointments/<int:appointment_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_appointment(appointment_id: int):
    supabase.table(TABLES["appointments"]).delete().eq("id", appointment_id).execute()
    add_audit("DELETE", "appointment", f"RDV #{appointment_id} supprimé", appointment_id)
    invalidate_cache()
    return jsonify({"message": "Rendez-vous supprimé"})

# ==================== PRESCRIPTIONS ====================
@app.route("/api/prescriptions", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(120)
def get_prescriptions():
    result = supabase.table(TABLES["prescriptions"]).select("*").order("created_at", desc=True).execute()
    prescriptions = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    try:
        dispensed_rows = supabase.table("prescription_dispenses").select("prescription_id").execute().data or []
        dispensed_ids = {str(row.get("prescription_id")) for row in dispensed_rows}
    except Exception:
        dispensed_ids = set()
    for p in prescriptions:
        p["patient_name"] = patient_map.get(p.get("patient_id"), "Inconnu")
        if str(p.get("id")) in dispensed_ids:
            p["pharmacy_status"] = "dispensed"
        else:
            p["pharmacy_status"] = p.get("pharmacy_status") or "pending"
    return jsonify(prescriptions)

@app.route("/api/prescriptions", methods=["POST"])
@roles_required("super_admin", "docteur")
def create_prescription():
    data = fast_json()
    if not data.get("patient_id") or not data.get("medication"):
        return jsonify({"error": "Patient et médicament requis"}), 422
    prescription = {
        "patient_id": to_int(data.get("patient_id")),
        "medication": data.get("medication"),
        "dosage": data.get("dosage", ""),
        "frequency": data.get("frequency", ""),
        "duration": data.get("duration", ""),
        "start_date": optional_date(data.get("start_date")),
        "end_date": optional_date(data.get("end_date")),
        "instructions": data.get("instructions", ""),
        "status": data.get("status", "active"),
        "pharmacy_status": data.get("pharmacy_status", "pending"),
        "invoiced": data.get("invoiced", False),
        "doctor_id": g.current_user["id"],
        "doctor_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["prescriptions"], prescription)
    add_audit("CREATE", "prescription", f"Prescription #{result.data[0]['id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/prescriptions/<int:prescription_id>", methods=["PUT"])
@roles_required("super_admin", "docteur")
def update_prescription(prescription_id: int):
    data = fast_json()
    allowed = ["medication", "dosage", "frequency", "duration", "start_date", "end_date", "instructions", "status", "invoiced"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    for date_field in ("start_date", "end_date"):
        if date_field in updates:
            updates[date_field] = optional_date(updates[date_field])
    updates["updated_at"] = now_iso()
    try:
        result = supabase.table(TABLES["prescriptions"]).update(updates).eq("id", prescription_id).execute()
    except Exception as exc:
        if not missing_schema_column(exc):
            raise
        result = supabase.table(TABLES["prescriptions"]).update({"status": "completed", "updated_at": now_iso()}).eq("id", prescription_id).execute()
    if not result.data:
        return jsonify({"error": "Prescription introuvable"}), 404
    add_audit("UPDATE", "prescription", f"Prescription #{prescription_id} modifiée", prescription_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/prescriptions/<int:prescription_id>", methods=["PATCH"])
@roles_required("super_admin", "docteur", "pharmacie")
def patch_prescription(prescription_id: int):
    data = fast_json()
    allowed = ["status", "pharmacy_status", "invoiced"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["prescriptions"]).update(updates).eq("id", prescription_id).execute()
    if not result.data:
        return jsonify({"error": "Prescription introuvable"}), 404
    add_audit("UPDATE", "prescription", f"Prescription #{prescription_id} patchée", prescription_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/prescriptions/<int:prescription_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_prescription(prescription_id: int):
    supabase.table(TABLES["prescriptions"]).delete().eq("id", prescription_id).execute()
    add_audit("DELETE", "prescription", f"Prescription #{prescription_id} supprimée", prescription_id)
    invalidate_cache()
    return jsonify({"message": "Prescription supprimée"})

@app.route("/api/prescriptions/<int:prescription_id>/dispense", methods=["POST"])
@roles_required("super_admin", "pharmacie")
def dispense_prescription(prescription_id: int):
    data = fast_json()
    prescription = supabase.table(TABLES["prescriptions"]).select("*").eq("id", prescription_id).execute()
    if not prescription.data:
        return jsonify({"error": "Prescription introuvable"}), 404
    row = prescription.data[0]
    amount = round(to_float(data.get("amount"), 0), 2)
    updates = {
        "pharmacy_status": "dispensed",
        "dispensed_at": now_iso(),
        "dispensed_by": g.current_user["id"],
        "dispensed_by_name": g.current_user["name"],
        "updated_at": now_iso()
    }
    result = supabase.table(TABLES["prescriptions"]).update(updates).eq("id", prescription_id).execute()
    invoice = None
    if amount > 0:
        line = add_patient_account_line(to_int(row.get("patient_id")), "medicament", f"Prescription: {row.get('medication', '')}", amount, "prescription", prescription_id)
        invoice = create_service_invoice(to_int(row.get("patient_id")), f"Médicament: {row.get('medication', '')}", amount, "prescription", prescription_id, line)
    compatible_insert("prescription_dispenses", {
        "prescription_id": prescription_id,
        "patient_id": row.get("patient_id"),
        "amount": amount,
        "notes": data.get("notes", ""),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso()
    })
    add_audit("UPDATE", "prescription", f"Prescription #{prescription_id} delivree", prescription_id)
    invalidate_cache()
    response = result.data[0] if result.data else updates
    response["invoice"] = invoice
    return jsonify(response)

# ==================== LABORATORY ====================
@app.route("/api/laboratory/tests", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(60)
def get_lab_tests():
    status = request.args.get("status")
    patient_id = request.args.get("patient_id")
    query = supabase.table(TABLES["lab_tests"]).select("*")
    if status:
        query = query.eq("status", status)
    if patient_id:
        query = query.eq("patient_id", to_int(patient_id))
    result = query.order("request_date", desc=True).execute()
    tests = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for t in tests:
        t["patient_name"] = patient_map.get(t.get("patient_id"), "Inconnu")
    return jsonify(tests)

@app.route("/api/laboratory/tests", methods=["POST"])
@roles_required("super_admin", "docteur", "laboratoire")
def create_lab_test():
    data = fast_json()
    if not data.get("patient_id") or not data.get("test_type"):
        return jsonify({"error": "Patient et type d'analyse requis"}), 422
    test = {
        "patient_id": to_int(data.get("patient_id")),
        "test_type": data.get("test_type"),
        "notes": data.get("notes", ""),
        "status": "pending",
        "priority": normalize_status(data.get("priority", "normal"), ["normal", "urgent"], "normal"),
        "request_date": now_iso(),
        "requested_by": g.current_user["id"],
        "requested_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["lab_tests"], test)
    lab_fee = to_float(data.get("amount"), get_tariff_amount("analyse", data.get("test_type", ""), 0))
    invoice = None
    if lab_fee > 0:
        line = add_patient_account_line(to_int(data.get("patient_id")), "analyse", f"Analyse: {data.get('test_type')}", lab_fee, "lab_test", result.data[0].get("id"))
        invoice = create_service_invoice(to_int(data.get("patient_id")), f"Analyse: {data.get('test_type')}", lab_fee, "lab_test", result.data[0].get("id"), line)
    add_audit("CREATE", "lab_test", f"Analyse #{result.data[0]['id']}", result.data[0]["id"])
    invalidate_cache()
    response = dict(result.data[0])
    response["invoice"] = invoice
    return jsonify(response), 201

@app.route("/api/laboratory/tests/<int:test_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_lab_test(test_id: int):
    result = supabase.table(TABLES["lab_tests"]).select("*").eq("id", test_id).execute()
    if not result.data:
        return jsonify({"error": "Analyse introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/laboratory/tests/<int:test_id>", methods=["PUT"])
@roles_required("super_admin", "laboratoire")
def update_lab_test(test_id: int):
    data = fast_json()
    allowed = ["test_type", "notes", "priority"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["lab_tests"]).update(updates).eq("id", test_id).execute()
    if not result.data:
        return jsonify({"error": "Analyse introuvable"}), 404
    add_audit("UPDATE", "lab_test", f"Analyse #{test_id} modifiée", test_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/laboratory/tests/<int:test_id>", methods=["PATCH"])
@roles_required("super_admin", "laboratoire")
def patch_lab_test(test_id: int):
    data = fast_json()
    allowed = ["status"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    if updates.get("status") == "preleve":
        updates["num_prelevement"] = f"PR-{datetime.now().strftime('%Y-%m-%d')}-{str(test_id).zfill(4)}"
        updates["preleve_at"] = now_iso()
    result = supabase.table(TABLES["lab_tests"]).update(updates).eq("id", test_id).execute()
    if not result.data:
        return jsonify({"error": "Analyse introuvable"}), 404
    add_audit("UPDATE", "lab_test", f"Analyse #{test_id} patchée", test_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/laboratory/tests/<int:test_id>/result", methods=["PUT"])
@roles_required("super_admin", "laboratoire")
def save_test_result(test_id: int):
    data = fast_json()
    updates = {
        "result": data.get("result", ""),
        "observations": data.get("observations", ""),
        "status": "completed",
        "completed_date": now_iso(),
        "technician_name": g.current_user["name"],
        "updated_at": now_iso()
    }
    result = supabase.table(TABLES["lab_tests"]).update(updates).eq("id", test_id).execute()
    if not result.data:
        return jsonify({"error": "Analyse introuvable"}), 404
    add_audit("UPDATE", "lab_test", f"Résultat ajouté analyse #{test_id}", test_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/laboratory/tests/<int:test_id>/result", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_test_result(test_id: int):
    result = supabase.table(TABLES["lab_tests"]).select("*").eq("id", test_id).execute()
    if not result.data:
        return jsonify({"error": "Analyse introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/laboratory/results", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(120)
def get_lab_results():
    date_from = request.args.get("from_date")
    date_to = request.args.get("to_date")
    query = supabase.table(TABLES["lab_tests"]).select("*").eq("status", "completed")
    if date_from:
        query = query.gte("completed_date", date_from)
    if date_to:
        query = query.lte("completed_date", date_to)
    result = query.order("completed_date", desc=True).execute()
    tests = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for t in tests:
        t["patient_name"] = patient_map.get(t.get("patient_id"), "Inconnu")
    return jsonify(tests)

@app.route("/api/laboratory/tests/<int:test_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_lab_test(test_id: int):
    supabase.table(TABLES["lab_tests"]).delete().eq("id", test_id).execute()
    add_audit("DELETE", "lab_test", f"Analyse #{test_id} supprimée", test_id)
    invalidate_cache()
    return jsonify({"message": "Analyse supprimée"})

# ==================== LABORATORY STOCK ====================
@app.route("/api/laboratory/stock", methods=["GET"])
@roles_required("super_admin", "laboratoire")
def get_lab_stock():
    result = supabase.table("laboratory_stock").select("*").order("name").execute()
    return jsonify(result.data or [])

@app.route("/api/laboratory/stock", methods=["POST"])
@roles_required("super_admin", "laboratoire")
def create_lab_stock_item():
    data = fast_json()
    if not data.get("name"):
        return jsonify({"error": "Nom du produit requis"}), 422
    item = {
        "name": data.get("name"),
        "category": data.get("category", "Réactif"),
        "quantity": max(0, to_int(data.get("quantity"), 0)),
        "threshold": max(0, to_int(data.get("threshold"), 5)),
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("laboratory_stock", item)
    add_audit("CREATE", "lab_stock", f"Produit: {data['name']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/laboratory/stock/<int:item_id>", methods=["PUT"])
@roles_required("super_admin", "laboratoire")
def update_lab_stock_item(item_id: int):
    data = fast_json()
    allowed = ["name", "category", "quantity", "threshold"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    updates["updated_at"] = now_iso()
    result = supabase.table("laboratory_stock").update(updates).eq("id", item_id).execute()
    if not result.data:
        return jsonify({"error": "Produit introuvable"}), 404
    add_audit("UPDATE", "lab_stock", f"Produit #{item_id} modifié", item_id)
    invalidate_cache()
    return jsonify(result.data[0])

# ==================== LABORATORY PRELEVEMENTS ====================
@app.route("/api/laboratory/prelevements", methods=["GET"])
@roles_required("super_admin", "laboratoire")
def get_lab_prelevements():
    result = supabase.table("laboratory_prelevements").select("*").order("created_at", desc=True).execute()
    prelevements = result.data or []
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for p in prelevements:
        p["patient_name"] = patient_map.get(p.get("patient_id"), "Inconnu")
    return jsonify(prelevements)

@app.route("/api/laboratory/prelevements", methods=["POST"])
@roles_required("super_admin", "laboratoire")
def create_lab_prelevement():
    data = fast_json()
    if not data.get("test_id") or not data.get("patient_id"):
        return jsonify({"error": "test_id et patient_id requis"}), 422
    prelevement = {
        "test_id": data.get("test_id"),
        "patient_id": data.get("patient_id"),
        "num_prelevement": data.get("num_prelevement", f"PR-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"),
        "type": data.get("type", ""),
        "notes": data.get("notes", ""),
        "preleve_par": g.current_user.get("id"),
        "preleve_par_name": g.current_user.get("name") or g.current_user.get("email"),
        "created_at": now_iso()
    }
    result = compatible_insert("laboratory_prelevements", prelevement)
    add_audit("CREATE", "lab_prelevement", f"Prélèvement #{result.data[0]['id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

# ==================== LABORATORY PARAMS ====================
LAB_PARAMS = {
    "Hémogramme": [
        {"name": "GB", "label": "Globules blancs", "unit": "G/L", "ref_min": 4.0, "ref_max": 10.0, "decimal": 1},
        {"name": "GR", "label": "Globules rouges", "unit": "T/L", "ref_min": 4.2, "ref_max": 5.8, "decimal": 2},
        {"name": "Hb", "label": "Hémoglobine", "unit": "g/dL", "ref_min": 12.0, "ref_max": 16.0, "decimal": 1},
        {"name": "Ht", "label": "Hématocrite", "unit": "%", "ref_min": 37, "ref_max": 47, "decimal": 0},
        {"name": "VGM", "label": "VGM", "unit": "fL", "ref_min": 80, "ref_max": 96, "decimal": 0},
        {"name": "TCMH", "label": "TCMH", "unit": "pg", "ref_min": 27, "ref_max": 32, "decimal": 1},
        {"name": "CCMH", "label": "CCMH", "unit": "g/dL", "ref_min": 32, "ref_max": 36, "decimal": 1},
        {"name": "Plaquettes", "label": "Plaquettes", "unit": "G/L", "ref_min": 150, "ref_max": 400, "decimal": 0}
    ],
    "Bilan hépatique": [
        {"name": "ALAT", "label": "ALAT", "unit": "U/L", "ref_min": 5, "ref_max": 45, "decimal": 0},
        {"name": "ASAT", "label": "ASAT", "unit": "U/L", "ref_min": 5, "ref_max": 40, "decimal": 0},
        {"name": "GGT", "label": "Gamma-GT", "unit": "U/L", "ref_min": 5, "ref_max": 50, "decimal": 0},
        {"name": "PAL", "label": "Phosphatases alcalines", "unit": "U/L", "ref_min": 30, "ref_max": 120, "decimal": 0},
        {"name": "Bilirubine_T", "label": "Bilirubine totale", "unit": "mg/dL", "ref_min": 0.2, "ref_max": 1.2, "decimal": 1},
        {"name": "Bilirubine_D", "label": "Bilirubine directe", "unit": "mg/dL", "ref_min": 0, "ref_max": 0.3, "decimal": 1}
    ],
    "Bilan rénal": [
        {"name": "Uree", "label": "Urée", "unit": "g/L", "ref_min": 0.2, "ref_max": 0.5, "decimal": 2},
        {"name": "Creatinine", "label": "Créatinine", "unit": "mg/L", "ref_min": 6, "ref_max": 13, "decimal": 1},
        {"name": "Acide_urique", "label": "Acide urique", "unit": "mg/L", "ref_min": 25, "ref_max": 80, "decimal": 1}
    ],
    "Bilan lipidique": [
        {"name": "Cholest_total", "label": "Cholestérol total", "unit": "g/L", "ref_min": 1.4, "ref_max": 2.5, "decimal": 2},
        {"name": "Triglycerides", "label": "Triglycérides", "unit": "g/L", "ref_min": 0.4, "ref_max": 1.8, "decimal": 2},
        {"name": "HDL", "label": "HDL-Cholestérol", "unit": "g/L", "ref_min": 0.4, "ref_max": 0.7, "decimal": 2},
        {"name": "LDL", "label": "LDL-Cholestérol", "unit": "g/L", "ref_min": 0.6, "ref_max": 1.6, "decimal": 2}
    ],
    "Analyse d'urine": [
        {"name": "pH", "label": "pH", "unit": "", "ref_min": 4.5, "ref_max": 8.0, "decimal": 1},
        {"name": "Densite", "label": "Densité", "unit": "", "ref_min": 1.005, "ref_max": 1.030, "decimal": 3},
        {"name": "Proteines", "label": "Protéines", "unit": "g/L", "ref_min": 0, "ref_max": 0.15, "decimal": 2},
        {"name": "Glucose", "label": "Glucose", "unit": "mmol/L", "ref_min": 0, "ref_max": 0.8, "decimal": 1},
        {"name": "Cetones", "label": "Cétones", "unit": "", "ref_min": None, "ref_max": None, "decimal": 0},
        {"name": "Nitrites", "label": "Nitrites", "unit": "", "ref_min": None, "ref_max": None, "decimal": 0},
        {"name": "Leucocytes", "label": "Leucocytes", "unit": "/µL", "ref_min": 0, "ref_max": 5, "decimal": 0},
        {"name": "Hematies", "label": "Hématies", "unit": "/µL", "ref_min": 0, "ref_max": 3, "decimal": 0}
    ]
}

@app.route("/api/laboratory/params/<string:test_type>", methods=["GET"])
@roles_required("super_admin", "laboratoire")
def get_lab_params(test_type: str):
    """Récupère les paramètres pour un type d'examen"""
    return jsonify(LAB_PARAMS.get(test_type, []))

# ==================== CARE LOGS (AVEC FACTURATION AUTO) ====================
@app.route("/api/care", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(60)
def get_care_logs():
    result = supabase.table(TABLES["care"]).select("*").order("date", desc=True).execute()
    care_logs = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for c in care_logs:
        c["patient_name"] = patient_map.get(c.get("patient_id"), "Inconnu")
    return jsonify(care_logs)

@app.route("/api/care", methods=["POST"])
@roles_required("super_admin", "docteur", "infirmier")
def create_care_log():
    data = fast_json()
    if not data.get("patient_id") or not data.get("care_type"):
        return jsonify({"error": "Patient et type de soin requis"}), 422
    
    patient_id = to_int(data.get("patient_id"))
    care_type = data.get("care_type")
    
    care = {
        "patient_id": patient_id,
        "care_type": care_type,
        "description": data.get("description", ""),
        "priority": normalize_status(data.get("priority", "normal"), ["normal", "urgent", "high", "low"], "normal"),
        "status": data.get("status", "pending"),
        "date": now_iso(),
        "performed_by": g.current_user["id"],
        "performed_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["care"], care)
    created_care = result.data[0] if result.data else care
    
    # Facturation automatique du soin
    tarif_code = get_tarif_code_for_care(care_type)
    facture_auto(patient_id, tarif_code, 1, "care", created_care.get("id"))
    
    add_audit("CREATE", "care", f"Soin #{created_care.get('id')}", created_care.get("id"))
    invalidate_cache()
    return jsonify(created_care), 201

@app.route("/api/care/<int:care_id>", methods=["PUT"])
@roles_required("super_admin", "docteur", "infirmier")
def update_care_log(care_id: int):
    data = fast_json()
    updates = {}
    if "care_type" in data:
        updates["care_type"] = data["care_type"]
    if "description" in data:
        updates["description"] = data["description"]
    if "status" in data:
        updates["status"] = data["status"]
    if "priority" in data:
        updates["priority"] = data["priority"]
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["care"]).update(updates).eq("id", care_id).execute()
    if not result.data:
        return jsonify({"error": "Soin introuvable"}), 404
    add_audit("UPDATE", "care", f"Soin #{care_id} modifié", care_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/care/<int:care_id>", methods=["PATCH"])
@roles_required("super_admin", "docteur", "infirmier")
def patch_care_log(care_id: int):
    data = fast_json()
    allowed = ["status", "priority"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["care"]).update(updates).eq("id", care_id).execute()
    if not result.data:
        return jsonify({"error": "Soin introuvable"}), 404
    add_audit("UPDATE", "care", f"Soin #{care_id} patché", care_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/care/<int:care_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_care_log(care_id: int):
    supabase.table(TABLES["care"]).delete().eq("id", care_id).execute()
    add_audit("DELETE", "care", f"Soin #{care_id} supprimé", care_id)
    invalidate_cache()
    return jsonify({"message": "Soin supprimé"})

# ==================== PARCOURS PATIENT ====================
@app.route("/api/workflow/doctors", methods=["GET"])
@roles_required("super_admin", "infirmier", "reception", "docteur")
@cached(timeout=120)
def get_workflow_doctors():
    users = supabase.table(TABLES["users"]).select("id,name,email,role").eq("role", "docteur").execute()
    return jsonify(users.data or [])

@app.route("/api/workflow/queue", methods=["GET"])
@roles_required("super_admin", "reception", "infirmier", "docteur")
@cached(timeout=30)
def get_patient_queue():
    role = g.current_user.get("role")
    status = request.args.get("status", "").strip()
    search = request.args.get("search", "").strip().lower()
    query = supabase.table("patient_queue").select("*")
    if status and status != "all":
        query = query.eq("status", status)
    rows = query.order("arrival_order").execute().data or []
    patients = get_patient_map()
    doctors = get_user_map()
    if role == "docteur":
        rows = [row for row in rows if str(row.get("assigned_doctor_id")) == str(g.current_user.get("id")) and row.get("status") in ("assigned", "in_consultation")]
    elif role == "reception":
        rows = [row for row in rows if row.get("status") == "waiting"]
    elif role == "infirmier":
        rows = [row for row in rows if row.get("status") in ("waiting", "with_nurse", "vitals_done")]
    for row in rows:
        row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        doctor = doctors.get(row.get("assigned_doctor_id"))
        row["assigned_doctor_name"] = row.get("assigned_doctor_name") or (doctor.get("name") if doctor else "")
    if search:
        rows = [row for row in rows if search in str(row.get("patient_name", "")).lower() or search in str(row.get("assigned_doctor_name", "")).lower()]
    return jsonify(rows)

@app.route("/api/workflow/queue", methods=["POST"])
@roles_required("super_admin", "reception", "infirmier")
def add_patient_to_queue():
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    existing = supabase.table("patient_queue").select("*").eq("patient_id", patient_id).in_("status", ["waiting", "vitals_done", "assigned"]).execute()
    if existing.data:
        return jsonify(existing.data[0]), 200
    last = supabase.table("patient_queue").select("arrival_order").order("arrival_order", desc=True).limit(1).execute().data or []
    arrival_order = to_int(last[0].get("arrival_order"), 0) + 1 if last else 1
    payload = {
        "patient_id": patient_id,
        "status": "waiting",
        "arrival_order": arrival_order,
        "arrival_time": now_iso(),
        "priority": data.get("priority", "normal"),
        "appointment_id": data.get("appointment_id"),
        "appointment_time": data.get("appointment_time"),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("patient_queue", payload)
    supabase.table(TABLES["patients"]).update({"status": "waiting", "updated_at": now_iso()}).eq("id", patient_id).execute()
    add_audit("CREATE", "patient_queue", f"Patient #{patient_id} en attente", patient_id)
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/queue/<int:patient_id>", methods=["PATCH"])
@roles_required("super_admin", "reception", "infirmier", "docteur")
def patch_patient_queue(patient_id: int):
    data = fast_json()
    allowed = ["status", "priority", "assigned_doctor_id", "assigned_doctor_name"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table("patient_queue").update(updates).eq("patient_id", patient_id).execute()
    if not result.data:
        return jsonify({"error": "Patient introuvable dans la file"}), 404
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/workflow/vitals", methods=["GET", "POST"])
@roles_required(*ROLES["staff"])
def workflow_vitals():
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("vital_signs").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("created_at", desc=True).execute().data or []
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    if g.current_user.get("role") not in ("super_admin", "infirmier"):
        return jsonify({"error": "Signes vitaux reserves a l'infirmier"}), 403
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    
    # Vérifier si la case "Femme enceinte" est cochée
    is_pregnant = data.get("is_pregnant", False)
    
    payload = {
        "patient_id": patient_id,
        "temperature": data.get("temperature"),
        "blood_pressure": data.get("blood_pressure", ""),
        "weight": data.get("weight"),
        "height": data.get("height"),
        "heart_rate": data.get("heart_rate"),
        "oxygen_saturation": data.get("oxygen_saturation"),
        "notes": data.get("notes", ""),
        "author": data.get("author", g.current_user.get("name") or g.current_user.get("email")),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("vital_signs", payload)
    
    # Si la case enceinte est cochée, mettre à jour le patient
    if is_pregnant:
        patient = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
        if patient.data and is_female(patient.data[0]):
            supabase.table(TABLES["patients"]).update({
                "is_pregnant": True,
                "updated_at": now_iso()
            }).eq("id", patient_id).execute()
            
            # Vérifier si une grossesse existe déjà
            existing = supabase.table("pregnancies").select("*").eq("patient_id", patient_id).eq("status", "active").execute()
            if not existing.data:
                compatible_insert("pregnancies", {
                    "patient_id": patient_id,
                    "last_menstrual_period": None,
                    "expected_delivery_date": None,
                    "risk_level": "normal",
                    "medical_history": "Grossesse signalée par l'infirmier lors des signes vitaux",
                    "status": "active",
                    "created_by": g.current_user["id"],
                    "created_by_name": g.current_user["name"],
                    "created_at": now_iso(),
                    "updated_at": now_iso()
                })
    
    supabase.table("patient_queue").update({"status": "vitals_done", "updated_at": now_iso()}).eq("patient_id", patient_id).in_("status", ["waiting", "vitals_done"]).execute()
    add_audit("CREATE", "vital_signs", f"Signes vitaux patient #{patient_id}", patient_id)
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/vitals/<int:vital_id>", methods=["PUT"])
@roles_required("super_admin", "infirmier")
def update_workflow_vitals(vital_id: int):
    data = fast_json()
    allowed = ["temperature", "blood_pressure", "weight", "height", "heart_rate", "oxygen_saturation", "notes"]
    updates = {k: data.get(k) for k in allowed if k in data}
    updates["updated_at"] = now_iso()
    result = compatible_update("vital_signs", updates, "id", vital_id)
    if not result.data:
        return jsonify({"error": "Signes vitaux introuvables"}), 404
    add_audit("UPDATE", "vital_signs", f"Signes vitaux #{vital_id} modifies", vital_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/workflow/pregnancy-flag", methods=["POST"])
@roles_required("super_admin", "infirmier")
def flag_pregnancy_from_nurse():
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    patient = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
    if not patient.data:
        return jsonify({"error": "Patient introuvable"}), 404
    if not is_female(patient.data[0]):
        return jsonify({"error": "Signalement grossesse reserve aux patientes"}), 422
    existing = supabase.table("pregnancies").select("*").eq("patient_id", patient_id).eq("status", "active").execute()
    if existing.data:
        return jsonify(existing.data[0]), 200
    lmp = optional_date(data.get("last_menstrual_period")) or datetime.now(timezone.utc).date().isoformat()
    payload = {
        "patient_id": patient_id,
        "last_menstrual_period": lmp,
        "expected_delivery_date": data.get("expected_delivery_date"),
        "risk_level": data.get("risk_level", "normal"),
        "medical_history": data.get("notes", "Grossesse signalee par l'infirmier avant dispatch."),
        "status": "active",
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("pregnancies", payload)
    add_audit("CREATE", "pregnancy", f"Grossesse signalee patient #{patient_id}", patient_id)
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/dispatch", methods=["POST"])
@roles_required("super_admin", "infirmier")
def dispatch_patient():
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    doctor_id = to_int(data.get("doctor_id"))
    if not patient_id or not doctor_id:
        return jsonify({"error": "Patient et medecin requis"}), 422
    latest_vitals = supabase.table("vital_signs").select("id").eq("patient_id", patient_id).limit(1).execute()
    if not latest_vitals.data:
        return jsonify({"error": "Les signes vitaux doivent etre preleves avant le dispatch"}), 422
    doctors = get_user_map()
    doctor = doctors.get(doctor_id)
    current_queue = supabase.table("patient_queue").select("assigned_doctor_id").eq("patient_id", patient_id).order("updated_at", desc=True).limit(1).execute().data or []
    previous_doctor_id = data.get("previous_doctor_id")
    if not previous_doctor_id and current_queue:
        previous_doctor_id = current_queue[0].get("assigned_doctor_id")
    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "doctor_name": doctor.get("name") if doctor else data.get("doctor_name", ""),
        "previous_doctor_id": previous_doctor_id,
        "reason": data.get("reason", ""),
        "box": data.get("box", ""),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso()
    }
    result = compatible_insert("patient_dispatches", payload)
    supabase.table("patient_queue").update({
        "status": "assigned",
        "assigned_doctor_id": doctor_id,
        "assigned_doctor_name": payload["doctor_name"],
        "updated_at": now_iso()
    }).eq("patient_id", patient_id).in_("status", ["vitals_done", "assigned"]).execute()
    supabase.table(TABLES["patients"]).update({"status": "assigned", "assigned_doctor_id": doctor_id, "updated_at": now_iso()}).eq("id", patient_id).execute()
    add_audit("CREATE", "dispatch", f"Patient #{patient_id} assigne a {payload['doctor_name']}", patient_id)
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/dispatches", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_workflow_dispatches():
    patient_id = request.args.get("patient_id")
    query = supabase.table("patient_dispatches").select("*")
    if patient_id:
        query = query.eq("patient_id", to_int(patient_id))
    rows = query.order("created_at", desc=True).execute().data or []
    if g.current_user.get("role") == "docteur":
        rows = [row for row in rows if str(row.get("doctor_id")) == str(g.current_user.get("id"))]
    patients = get_patient_map()
    doctors = get_user_map()
    for row in rows:
        row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        previous = doctors.get(row.get("previous_doctor_id"))
        row["previous_doctor_name"] = previous.get("name") if previous else ""
    return jsonify(rows)

@app.route("/api/workflow/consultations", methods=["GET", "POST"])
@roles_required("super_admin", "docteur")
def workflow_consultations():
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("medical_consultations").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("created_at", desc=True).execute().data or []
        if g.current_user.get("role") == "docteur":
            rows = [row for row in rows if str(row.get("doctor_id")) == str(g.current_user.get("id"))]
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    payload = {
        "patient_id": patient_id,
        "symptoms": data.get("symptoms", ""),
        "diagnosis": data.get("diagnosis", ""),
        "diagnostics": data.get("diagnostics", []),
        "observations": data.get("observations", ""),
        "medical_history": data.get("medical_history", ""),
        "doctor_id": g.current_user["id"],
        "doctor_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("medical_consultations", payload)
    consultation_fee = to_float(data.get("consultation_fee"), get_tariff_amount("consultation", "Consultation", 0))
    consultation_line = None
    if consultation_fee > 0:
        consultation_line = add_patient_account_line(patient_id, "consultation", "Consultation medicale", consultation_fee, "consultation", result.data[0].get("id"))
    supabase.table("patient_queue").update({"status": "completed", "updated_at": now_iso()}).eq("patient_id", patient_id).in_("status", ["assigned", "in_consultation", "vitals_done"]).execute()
    current_patient = supabase.table(TABLES["patients"]).select("status").eq("id", patient_id).execute().data or []
    if not current_patient or current_patient[0].get("status") not in ("admitted", "discharged"):
        supabase.table(TABLES["patients"]).update({"status": "active", "updated_at": now_iso()}).eq("id", patient_id).execute()
    
    # Facturation automatique avec grille tarifaire
    consultation_invoice = None
    if consultation_fee > 0:
        consultation_invoice = facture_auto(patient_id, "CONSULT", 1, "consultation", result.data[0].get("id"))
        if consultation_line and consultation_invoice and consultation_invoice.get("id"):
            compatible_update("patient_account_lines", {"status": "invoiced", "invoice_id": consultation_invoice["id"], "updated_at": now_iso()}, "id", consultation_line.get("id"))
    
    add_audit("CREATE", "consultation", f"Consultation patient #{patient_id}", patient_id)
    invalidate_cache()
    response = dict(result.data[0])
    response["invoice"] = consultation_invoice
    return jsonify(response), 201

@app.route("/api/workflow/account-lines", methods=["GET", "POST"])
@roles_required(*ROLES["staff"])
def workflow_account_lines():
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("patient_account_lines").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("created_at", desc=True).execute().data or []
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    if g.current_user.get("role") not in ("super_admin", "reception"):
        return jsonify({"error": "Ajout manuel reserve a l'accueil/admin"}), 403
    data = fast_json()
    line = add_patient_account_line(to_int(data.get("patient_id")), data.get("category", "manuel"), data.get("description", data.get("motif", "Frais manuel")), to_float(data.get("amount")), "manual")
    if not line:
        return jsonify({"error": "Ligne invalide"}), 422
    invalidate_cache()
    return jsonify(line), 201

@app.route("/api/workflow/final-invoice", methods=["POST"])
@roles_required("super_admin", "reception")
def create_final_invoice_from_account():
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    lines = supabase.table("patient_account_lines").select("*").eq("patient_id", patient_id).eq("status", "pending").execute().data or []
    if not lines:
        return jsonify({"error": "Aucun frais en attente pour ce patient"}), 422
    items = [{
        "code": line.get("category", "FRAIS"),
        "description": line.get("description", ""),
        "quantity": to_int(line.get("quantity"), 1),
        "unit_price": to_float(line.get("unit_price"), line.get("amount")),
        "amount": to_float(line.get("amount")),
        "date": line.get("created_at")
    } for line in lines]
    total = round(sum(to_float(item.get("amount")) for item in items), 2)
    paid = round(to_float(data.get("paid_amount"), 0), 2)
    invoice = {
        "invoice_number": f"FINAL-{int(time.time())}-{secrets.token_hex(2).upper()}",
        "patient_id": patient_id,
        "amount": total,
        "paid_amount": paid,
        "balance_due": max(0, total - paid),
        "description": "Facture finale du compte patient",
        "status": "paid" if paid >= total else "unpaid",
        "line_items": items,
        "items": items,
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["billing"], invoice)
    created_invoice = result.data[0] if result.data else invoice
    if paid > 0:
        add_invoice_payment(created_invoice.get("id"), patient_id, paid, "Acompte facture finale")
    for line in lines:
        if line.get("id"):
            compatible_update("patient_account_lines", {"status": "invoiced", "invoice_id": created_invoice.get("id"), "updated_at": now_iso()}, "id", line["id"])
    add_audit("CREATE", "billing", f"Facture finale patient #{patient_id}: {total}", created_invoice.get("id"))
    invalidate_cache()
    return jsonify({"invoice": created_invoice}), 201

@app.route("/api/workflow/hospitalizations", methods=["GET", "POST"])
@roles_required("super_admin", "infirmier", "docteur")
def workflow_hospitalizations():
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("hospitalizations").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("admission_date", desc=True).execute().data or []
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    payload = {
        "patient_id": patient_id,
        "admission_date": data.get("admission_date") or now_iso(),
        "discharge_date": data.get("discharge_date"),
        "status": "hospitalized",
        "reason": data.get("reason", ""),
        "room": data.get("room", ""),
        "bed": data.get("bed", ""),
        "doctor_name": data.get("doctor_name", ""),
        "daily_rate": to_float(data.get("daily_rate"), get_tariff_amount("hospitalisation", "Hospitalisation", 0)),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("hospitalizations", payload)
    supabase.table(TABLES["patients"]).update({"status": "admitted", "updated_at": now_iso()}).eq("id", patient_id).execute()
    add_audit("CREATE", "hospitalization", f"Admission patient #{patient_id}", patient_id)
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/hospitalizations/<int:hosp_id>/discharge", methods=["POST"])
@roles_required("super_admin", "infirmier", "docteur")
def discharge_workflow_hospitalization(hosp_id: int):
    data = fast_json()
    hosp = supabase.table("hospitalizations").select("*").eq("id", hosp_id).execute()
    if not hosp.data:
        return jsonify({"error": "Hospitalisation introuvable"}), 404
    row = hosp.data[0]
    discharge_date = data.get("discharge_date") or now_iso()
    start = parse_date(row.get("admission_date")) or datetime.now(timezone.utc).date()
    end = parse_date(discharge_date) or datetime.now(timezone.utc).date()
    days = max(1, (end - start).days + 1)
    daily_rate = to_float(data.get("daily_rate"), row.get("daily_rate") or 0)
    updates = {"discharge_date": discharge_date, "status": "discharged", "days_count": days, "daily_rate": daily_rate, "updated_at": now_iso()}
    result = supabase.table("hospitalizations").update(updates).eq("id", hosp_id).execute()
    
    # Facturation automatique de l'hospitalisation
    if daily_rate > 0:
        amount = days * daily_rate
        add_patient_account_line(to_int(row.get("patient_id")), "hospitalisation", f"Hospitalisation {days} jour(s)", amount, "hospitalization", hosp_id, days, daily_rate)
        facture_auto(to_int(row.get("patient_id")), "HOSPI_JOUR", days, "hospitalization", hosp_id)
    
    supabase.table(TABLES["patients"]).update({"status": "discharged", "updated_at": now_iso()}).eq("id", row.get("patient_id")).execute()
    add_audit("UPDATE", "hospitalization", f"Sortie hospitalisation #{hosp_id}", hosp_id)
    invalidate_cache()
    return jsonify(result.data[0] if result.data else updates)

@app.route("/api/workflow/hospitalizations/patient/<int:patient_id>/discharge", methods=["POST"])
@roles_required("super_admin", "infirmier", "docteur")
def discharge_patient_hospitalization_by_id(patient_id: int):
    """Sortie d'hospitalisation par ID patient (trouve automatiquement l'hospitalisation en cours)"""
    # Trouver l'hospitalisation en cours pour ce patient
    hosp = supabase.table("hospitalizations").select("*").eq("patient_id", patient_id).eq("status", "hospitalized").execute()
    if not hosp.data:
        return jsonify({"error": "Aucune hospitalisation en cours pour ce patient"}), 404
    
    hosp_id = hosp.data[0]["id"]
    return discharge_workflow_hospitalization(hosp_id)

@app.route("/api/workflow/followups", methods=["GET", "POST"])
@roles_required("super_admin", "docteur", "infirmier")
def workflow_followups():
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("medical_followups").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("followup_date", desc=True).execute().data or []
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    payload = {
        "patient_id": patient_id,
        "instructions": data.get("instructions", ""),
        "medication": data.get("medication", ""),
        "dose": data.get("dose", ""),
        "frequency": data.get("frequency", ""),
        "observations": data.get("observations", ""),
        "status": data.get("status", "pending"),
        "followup_date": data.get("followup_date") or now_iso(),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("medical_followups", payload)
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/followups/<int:followup_id>", methods=["PATCH"])
@roles_required("super_admin", "docteur", "infirmier")
def patch_followup(followup_id: int):
    data = fast_json()
    allowed = ["status"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table("medical_followups").update(updates).eq("id", followup_id).execute()
    if not result.data:
        return jsonify({"error": "Suivi introuvable"}), 404
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/workflow/administrations", methods=["GET", "POST"])
@roles_required("super_admin", "infirmier")
def workflow_administrations():
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("medication_administrations").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("created_at", desc=True).execute().data or []
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    amount = to_float(data.get("amount"), 0)
    followup = None
    if data.get("followup_id"):
        followup_rows = supabase.table("medical_followups").select("*").eq("id", to_int(data.get("followup_id"))).execute().data or []
        followup = followup_rows[0] if followup_rows else None
    payload = {
        "patient_id": patient_id,
        "followup_id": data.get("followup_id"),
        "medication": data.get("medication") or (followup.get("medication") if followup else ""),
        "dose": data.get("dose") or (followup.get("dose") if followup else ""),
        "status": normalize_status(data.get("status"), ["administered", "pending", "not_administered", "given"], "pending"),
        "observations": data.get("observations", ""),
        "nurse_name": data.get("nurse_name", g.current_user.get("name") or g.current_user.get("email")),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("medication_administrations", payload)
    if payload["status"] in ("administered", "given") and amount > 0:
        add_patient_account_line(patient_id, "medicament", f"Administration: {payload['medication']}", amount, "medication_administration", result.data[0].get("id"))
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/workflow/administrations/<int:admin_id>", methods=["PATCH"])
@roles_required("super_admin", "infirmier")
def patch_administration(admin_id: int):
    data = fast_json()
    allowed = ["status"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table("medication_administrations").update(updates).eq("id", admin_id).execute()
    if not result.data:
        return jsonify({"error": "Administration introuvable"}), 404
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/workflow/medical-record/<int:patient_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_workflow_medical_record(patient_id: int):
    patient_result = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
    if not patient_result.data:
        return jsonify({"error": "Patient introuvable"}), 404
    patient = add_pregnancy_flags(patient_result.data)[0]
    if not can_access_patient_record(patient):
        return jsonify({"error": "Acces patient non autorise"}), 403
    sources = [
        ("consultation", "Consultation", "medical_consultations", "created_at", "diagnosis"),
        ("prescription", "Prescription", TABLES["prescriptions"], "created_at", "medication"),
        ("examen", "Examen", TABLES["lab_tests"], "request_date", "test_type"),
        ("resultat", "Resultat", TABLES["lab_tests"], "completed_date", "result"),
        ("hospitalisation", "Hospitalisation", "hospitalizations", "admission_date", "reason"),
        ("facture", "Facture", TABLES["billing"], "created_at", "description"),
        ("constantes", "Signes vitaux", "vital_signs", "created_at", "notes"),
        ("suivi", "Suivi medical", "medical_followups", "followup_date", "observations"),
        ("administration", "Administration", "medication_administrations", "created_at", "medication"),
        ("frais", "Compte patient", "patient_account_lines", "created_at", "description"),
    ]
    timeline = []
    for source_type, label, table, date_field, title_field in sources:
        try:
            rows = supabase.table(table).select("*").eq("patient_id", patient_id).execute().data or []
        except Exception:
            rows = []
        for row in rows:
            if source_type == "resultat" and row.get("status") != "completed":
                continue
            event_date = row.get(date_field) or row.get("created_at") or row.get("updated_at")
            title = row.get(title_field) or row.get("description") or label
            timeline.append({
                "type": source_type,
                "label": label,
                "date": event_date,
                "title": title,
                "data": row
            })
    timeline.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return jsonify({"patient": patient, "timeline": timeline})

@app.route("/api/workflow/tariffs", methods=["GET", "POST"])
@roles_required("super_admin")
def workflow_tariffs():
    if request.method == "GET":
        category = request.args.get("category")
        query = supabase.table(TABLES["tariffs"]).select("*")
        if category:
            query = query.eq("category", category)
        rows = query.order("category").execute().data or []
        return jsonify(rows)
    data = fast_json()
    category = data.get("category", "").strip()
    label = data.get("label", "").strip()
    amount = round(to_float(data.get("amount"), 0), 2)
    if not category or not label or amount < 0:
        return jsonify({"error": "Categorie, libelle et montant requis"}), 422
    payload = {
        "category": category,
        "label": label,
        "amount": amount,
        "is_active": data.get("is_active", True),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["tariffs"], payload)
    tariff = result.data[0] if result.data else payload
    compatible_insert(TABLES["tariff_history"], {
        "tariff_id": tariff.get("id"),
        "category": category,
        "label": label,
        "old_amount": 0,
        "new_amount": amount,
        "action": "CREATE",
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso()
    })
    add_audit("CREATE", "tariff", f"Tarif {category}: {label} = {amount}", tariff.get("id"))
    invalidate_cache()
    return jsonify(tariff), 201

@app.route("/api/workflow/tariffs/<int:tariff_id>", methods=["PUT"])
@roles_required("super_admin")
def update_workflow_tariff(tariff_id: int):
    data = fast_json()
    existing = supabase.table(TABLES["tariffs"]).select("*").eq("id", tariff_id).execute().data or []
    if not existing:
        return jsonify({"error": "Tarif introuvable"}), 404
    current = existing[0]
    updates = {
        "category": data.get("category", current.get("category")),
        "label": data.get("label", current.get("label")),
        "amount": round(to_float(data.get("amount"), current.get("amount")), 2),
        "is_active": data.get("is_active", current.get("is_active", True)),
        "updated_at": now_iso()
    }
    result = compatible_update(TABLES["tariffs"], updates, "id", tariff_id)
    compatible_insert(TABLES["tariff_history"], {
        "tariff_id": tariff_id,
        "category": updates["category"],
        "label": updates["label"],
        "old_amount": to_float(current.get("amount"), 0),
        "new_amount": updates["amount"],
        "action": "UPDATE",
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso()
    })
    add_audit("UPDATE", "tariff", f"Tarif #{tariff_id} modifie", tariff_id)
    invalidate_cache()
    return jsonify(result.data[0] if result.data else updates)

@app.route("/api/workflow/tariffs/history", methods=["GET"])
@roles_required("super_admin")
def workflow_tariff_history():
    rows = supabase.table(TABLES["tariff_history"]).select("*").order("created_at", desc=True).execute().data or []
    return jsonify(rows)

# ==================== HOSPITALIZATION FOLLOWUPS ====================
@app.route("/api/workflow/hospitalization-followups", methods=["GET", "POST"])
@roles_required("super_admin", "infirmier", "docteur")
def workflow_hospitalization_followups():
    """Gestion des suivis d'hospitalisation"""
    if request.method == "GET":
        patient_id = request.args.get("patient_id")
        query = supabase.table("hospitalization_followups").select("*")
        if patient_id:
            query = query.eq("patient_id", to_int(patient_id))
        rows = query.order("created_at", desc=True).execute().data or []
        patients = get_patient_map()
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_id"), "Inconnu")
        return jsonify(rows)
    
    # POST
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    
    payload = {
        "patient_id": patient_id,
        "temperature": data.get("temperature"),
        "blood_pressure_sys": data.get("blood_pressure_sys"),
        "blood_pressure_dia": data.get("blood_pressure_dia"),
        "heart_rate": data.get("heart_rate"),
        "respiratory_rate": data.get("respiratory_rate"),
        "pain_level": data.get("pain_level"),
        "general_state": data.get("general_state", "good"),
        "notes": data.get("notes", ""),
        "nurse_id": g.current_user.get("id"),
        "nurse_name": g.current_user.get("name") or g.current_user.get("email"),
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    
    result = compatible_insert("hospitalization_followups", payload)
    add_audit("CREATE", "hospitalization_followup", f"Suivi patient #{patient_id}", result.data[0]["id"] if result.data else None)
    invalidate_cache()
    return jsonify(result.data[0] if result.data else payload), 201

# ==================== EXCHANGE RATE ====================
@app.route("/api/exchange-rate", methods=["GET"])
@roles_required("super_admin", "reception")
@cached(timeout=300)
def get_exchange_rate():
    """Récupère le taux de change actuel"""
    try:
        result = supabase.table("exchange_rates").select("*").order("created_at", desc=True).limit(1).execute()
        if result.data:
            return jsonify(result.data[0])
    except Exception:
        pass
    return jsonify({"rate": 2800, "currency_from": "USD", "currency_to": "CDF", "created_at": now_iso()})

@app.route("/api/exchange-rate", methods=["POST"])
@roles_required("super_admin", "reception")
def set_exchange_rate():
    """Définit un nouveau taux de change"""
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

@app.route("/api/exchange-rate/history", methods=["GET"])
@roles_required("super_admin", "reception")
@cached(timeout=300)
def get_exchange_rate_history():
    """Récupère l'historique des taux de change"""
    limit = to_int(request.args.get("limit"), 50)
    result = supabase.table("exchange_rates").select("*").order("created_at", desc=True).limit(min(limit, 100)).execute()
    return jsonify(result.data or [])

# ==================== MEDICAL BOXES ====================
@app.route("/api/medical/boxes", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
@cached(timeout=30)
def get_medical_boxes():
    """Liste tous les box de consultation"""
    try:
        result = supabase.table("medical_boxes").select("*").order("box_number").execute()
        return jsonify(result.data or [])
    except Exception as e:
        print(f"Erreur récupération box: {e}")
        # Si la table n'existe pas, retourner une liste vide
        return jsonify([])

@app.route("/api/medical/boxes", methods=["POST"])
@roles_required("super_admin", "infirmier")
def create_medical_box():
    """Crée un nouveau box de consultation"""
    data = fast_json()
    if not data.get("box_number"):
        return jsonify({"error": "Numéro de box requis"}), 422
    
    box = {
        "box_number": data.get("box_number"),
        "status": "free",
        "doctor_id": None,
        "doctor_name": None,
        "patient_id": None,
        "patient_name": None,
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("medical_boxes", box)
    add_audit("CREATE", "medical_box", f"Box {data['box_number']} créé", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/medical/boxes/<int:box_id>/assign", methods=["PUT"])
@roles_required("super_admin", "infirmier")
def assign_medical_box(box_id: int):
    """Assigne un médecin à un box"""
    data = fast_json()
    doctor_id = data.get("doctor_id")
    if not doctor_id:
        return jsonify({"error": "Médecin requis"}), 422
    
    # Récupérer le médecin
    doctor = supabase.table(TABLES["users"]).select("id,name").eq("id", doctor_id).execute()
    if not doctor.data:
        return jsonify({"error": "Médecin introuvable"}), 404
    
    # Vérifier que le box existe
    box = supabase.table("medical_boxes").select("*").eq("id", box_id).execute()
    if not box.data:
        return jsonify({"error": "Box introuvable"}), 404
    
    result = supabase.table("medical_boxes").update({
        "doctor_id": doctor_id,
        "doctor_name": doctor.data[0].get("name"),
        "status": "free",
        "updated_at": now_iso()
    }).eq("id", box_id).execute()
    
    add_audit("UPDATE", "medical_box", f"Box #{box_id} assigné au médecin #{doctor_id}", box_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/medical/boxes/<int:box_id>/free", methods=["PUT"])
@roles_required("super_admin", "infirmier")
def free_medical_box(box_id: int):
    """Libère un box (supprime le patient et remet en libre)"""
    result = supabase.table("medical_boxes").update({
        "status": "free",
        "patient_id": None,
        "patient_name": None,
        "occupied_at": None,
        "updated_at": now_iso()
    }).eq("id", box_id).execute()
    if not result.data:
        return jsonify({"error": "Box introuvable"}), 404
    
    add_audit("UPDATE", "medical_box", f"Box #{box_id} libéré", box_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/medical/boxes/<int:box_id>/occupy", methods=["PUT"])
@roles_required("super_admin", "infirmier", "docteur")
def occupy_medical_box(box_id: int):
    """Occupe un box avec un patient"""
    data = fast_json()
    patient_id = data.get("patient_id")
    if not patient_id:
        return jsonify({"error": "Patient requis"}), 422
    
    # Récupérer le patient
    patient = supabase.table(TABLES["patients"]).select("id,full_name").eq("id", patient_id).execute()
    if not patient.data:
        return jsonify({"error": "Patient introuvable"}), 404
    
    # Vérifier que le box existe
    box = supabase.table("medical_boxes").select("*").eq("id", box_id).execute()
    if not box.data:
        return jsonify({"error": "Box introuvable"}), 404
    
    # Vérifier qu'un médecin est assigné
    if not box.data[0].get("doctor_id"):
        return jsonify({"error": "Aucun médecin assigné à ce box"}), 422
    
    result = supabase.table("medical_boxes").update({
        "status": "occupied",
        "patient_id": patient_id,
        "patient_name": patient.data[0].get("full_name"),
        "occupied_at": now_iso(),
        "updated_at": now_iso()
    }).eq("id", box_id).execute()
    
    add_audit("UPDATE", "medical_box", f"Box #{box_id} occupé par patient #{patient_id}", box_id)
    invalidate_cache()
    return jsonify(result.data[0])

# ==================== PHARMACY ====================
@app.route("/api/pharmacy", methods=["GET"])
@roles_required(*ROLES["staff"])
@cached(60)
def get_pharmacy():
    low_stock = request.args.get("low_stock", "false").lower() == "true"
    result = supabase.table(TABLES["pharmacy"]).select("*").order("medication_name").execute()
    items = result.data
    if low_stock:
        items = [i for i in items if i.get("quantity", 0) <= i.get("threshold", 10)]
    return jsonify(items)

@app.route("/api/pharmacy", methods=["POST"])
@roles_required("super_admin", "pharmacie")
def create_pharmacy_item():
    data = fast_json()
    if not data.get("medication_name"):
        return jsonify({"error": "Nom du médicament requis"}), 422
    item = {
        "medication_name": data["medication_name"],
        "quantity": max(0, to_int(data.get("quantity"), 0)),
        "unit": data.get("unit", "comprimé(s)"),
        "purchase_price": max(0, to_float(data.get("purchase_price"), 0)),
        "selling_price": max(0, to_float(data.get("selling_price"), 0)),
        "threshold": max(0, to_int(data.get("threshold"), 10)),
        "expiry_date": optional_date(data.get("expiry_date")),
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert(TABLES["pharmacy"], item)
    add_audit("CREATE", "pharmacy", f"Médicament: {data['medication_name']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/pharmacy/<int:item_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_pharmacy_item(item_id: int):
    result = supabase.table(TABLES["pharmacy"]).select("*").eq("id", item_id).execute()
    if not result.data:
        return jsonify({"error": "Médicament introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/pharmacy/<int:item_id>", methods=["PUT"])
@roles_required("super_admin", "pharmacie")
def update_pharmacy_item(item_id: int):
    data = fast_json()
    allowed = ["medication_name", "unit", "purchase_price", "selling_price", "threshold", "expiry_date"]
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

@app.route("/api/pharmacy/<int:item_id>/stock", methods=["PUT"])
@roles_required("super_admin", "pharmacie")
def update_stock(item_id: int):
    data = fast_json()
    quantity = max(0, to_int(data.get("quantity"), 0))
    operation = data.get("operation", "set")
    reason = data.get("reason", "Ajustement manuel")
    item_result = supabase.table(TABLES["pharmacy"]).select("quantity").eq("id", item_id).execute()
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
    
    compatible_insert("pharmacy_movements", {
        "medication_id": item_id,
        "medication_name": item_result.data[0].get("medication_name", "Médicament"),
        "type": "entree" if operation == "add" else "sortie",
        "quantity": quantity,
        "reason": reason,
        "patient_id": data.get("patient_id"),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso()
    })
    
    add_audit("UPDATE", "pharmacy", f"Stock #{item_id}: {current} -> {new_qty}", item_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/pharmacy/<int:item_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_pharmacy_item(item_id: int):
    supabase.table(TABLES["pharmacy"]).delete().eq("id", item_id).execute()
    add_audit("DELETE", "pharmacy", f"Médicament #{item_id} supprimé", item_id)
    invalidate_cache()
    return jsonify({"message": "Médicament supprimé"})

@app.route("/api/pharmacy/<int:item_id>/dispense", methods=["POST"])
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
    if item.get("quantity", 0) < quantity:
        return jsonify({"error": f"Stock insuffisant. Disponible: {item.get('quantity')}"}), 422
    unit_price = to_float(data.get("unit_price"), item.get("selling_price", 0))
    total_amount = round(quantity * unit_price, 2)
    invoice_data = {
        "patient_id": to_int(data.get("patient_id")),
        "description": data.get("description", f"Délivrance: {item.get('medication_name')} x{quantity}"),
        "items": [{"medication_id": item_id, "description": item.get("medication_name"), "quantity": quantity, "unit_price": unit_price, "amount": total_amount}],
        "source": "pharmacy_dispense"
    }
    try:
        with app.test_request_context(json=invoice_data):
            g.current_user = g.current_user or {"id": 1, "name": "Pharmacie"}
            response = create_grouped_invoice()
        new_qty = item.get("quantity", 0) - quantity
        supabase.table(TABLES["pharmacy"]).update({"quantity": new_qty, "updated_at": now_iso()}).eq("id", item_id).execute()
        
        compatible_insert("pharmacy_movements", {
            "medication_id": item_id,
            "medication_name": item.get("medication_name"),
            "type": "sortie",
            "quantity": quantity,
            "reason": f"Dispensation - Patient #{data.get('patient_id')}",
            "patient_id": data.get("patient_id"),
            "created_by": g.current_user["id"],
            "created_by_name": g.current_user["name"],
            "created_at": now_iso()
        })
        
        add_audit("UPDATE", "pharmacy", f"Dispensation: {item.get('medication_name')} x{quantity}", item_id)
        invalidate_cache()
        return jsonify({"message": "Médicament délivré avec succès", "invoice": response.get_json() if hasattr(response, 'get_json') else None}), 201
    except Exception as exc:
        return jsonify({"error": f"Erreur lors de la dispensation: {str(exc)}"}), 500

@app.route("/api/pharmacy/medications/search", methods=["GET"])
@roles_required(*ROLES["staff"])
def search_medications():
    search = request.args.get("q", "").strip().lower()
    if not search:
        return jsonify({"error": "Terme de recherche requis"}), 422
    result = supabase.table(TABLES["pharmacy"]).select("*").ilike("medication_name", f"%{search}%").execute()
    return jsonify(result.data)

@app.route("/api/pharmacy/expiring", methods=["GET"])
@roles_required("super_admin", "pharmacie")
def get_expiring_medications():
    days = to_int(request.args.get("days"), 30)
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    result = supabase.table(TABLES["pharmacy"]).select("*").lte("expiry_date", cutoff).execute()
    return jsonify(result.data)

@app.route("/api/pharmacy/low-stock", methods=["GET"])
@roles_required("super_admin", "pharmacie")
def get_low_stock():
    result = supabase.table(TABLES["pharmacy"]).select("*").execute().data or []
    low_stock = [i for i in result if i.get("quantity", 0) <= i.get("threshold", 10)]
    return jsonify(low_stock)

# ==================== PHARMACY MOVEMENTS ====================
@app.route("/api/pharmacy/movements", methods=["GET"])
@roles_required("super_admin", "pharmacie")
def get_pharmacy_movements():
    result = supabase.table("pharmacy_movements").select("*").order("created_at", desc=True).execute()
    return jsonify(result.data or [])

@app.route("/api/pharmacy/movements", methods=["POST"])
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
@app.route("/api/pharmacy/patient-account/add", methods=["POST"])
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
@app.route("/api/pharmacy/cashier", methods=["POST"])
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
@app.route("/api/billing", methods=["GET"])
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

@app.route("/api/billing", methods=["POST"])
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

@app.route("/api/billing/grouped", methods=["POST"])
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
                invoice_id,
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

@app.route("/api/billing/<int:invoice_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_invoice(invoice_id: int):
    result = supabase.table(TABLES["billing"]).select("*").eq("id", invoice_id).execute()
    if not result.data:
        return jsonify({"error": "Facture introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/billing/<int:invoice_id>", methods=["PUT", "PATCH"])
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

@app.route("/api/billing/<int:invoice_id>/pay", methods=["PUT"])
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
    
    add_patient_account_line(
        invoice_data.get("patient_id"),
        "paiement",
        f"Paiement facture #{invoice_id}",
        -amount if amount > 0 else -invoice_data.get("amount", 0),
        "payment",
        invoice_id
    )
    
    add_audit("UPDATE", "billing", f"Facture #{invoice_id} payée", invoice_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/billing/<int:invoice_id>/partial", methods=["PATCH"])
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
    add_patient_account_line(
        invoice_data.get("patient_id"),
        "paiement",
        f"Acompte facture #{invoice_id}",
        -amount,
        "payment",
        invoice_id
    )
    
    add_audit("UPDATE", "billing", f"Paiement partiel #{invoice_id}: {amount}", invoice_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/billing/merge", methods=["POST"])
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

@app.route("/api/billing/<int:invoice_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_invoice(invoice_id: int):
    supabase.table(TABLES["billing"]).delete().eq("id", invoice_id).execute()
    add_audit("DELETE", "billing", f"Facture #{invoice_id} supprimée", invoice_id)
    invalidate_cache()
    return jsonify({"message": "Facture supprimée"})

# ==================== BILLING ACCOUNTS ====================
@app.route("/api/billing/accounts", methods=["GET"])
@roles_required("super_admin", "reception")
@cached(120)
def get_billing_accounts():
    result = supabase.table("patient_accounts").select("*").order("patient_id").execute()
    accounts = result.data or []
    patients = get_patient_map()
    for acc in accounts:
        acc["patient_name"] = patients.get(acc.get("patient_id"), "Inconnu")
    return jsonify(accounts)

@app.route("/api/billing/accounts", methods=["POST"])
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

@app.route("/api/billing/accounts/<int:patient_id>", methods=["GET"])
@roles_required("super_admin", "reception")
def get_billing_account(patient_id: int):
    result = supabase.table("patient_accounts").select("*").eq("patient_id", patient_id).execute()
    if not result.data:
        return jsonify({"patient_id": patient_id, "balance": 0, "status": "inactive"})
    return jsonify(result.data[0])

@app.route("/api/billing/accounts/update", methods=["POST"])
@roles_required("super_admin", "reception")
def update_billing_account():
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    amount = round(to_float(data.get("amount"), 0), 2)
    type_operation = data.get("type", "debit")
    description = data.get("description", "")
    
    if not patient_id or amount == 0:
        return jsonify({"error": "Patient et montant requis"}), 422
    
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

@app.route("/api/billing/accounts/<int:patient_id>/transactions", methods=["GET"])
@roles_required("super_admin", "reception")
def get_account_transactions(patient_id: int):
    result = supabase.table("patient_account_transactions").select("*").eq("patient_id", patient_id).order("created_at", desc=True).execute()
    return jsonify(result.data or [])

# ==================== SUBSCRIBERS ====================
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
@roles_required("super_admin")
@cached(120)
def get_users():
    result = supabase.table(TABLES["users"]).select("*").order("created_at", desc=True).execute()
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

@app.route("/api/users/<int:user_id>", methods=["PUT"])
@app.route("/api/auth/users/<int:user_id>", methods=["PUT"])
@roles_required("super_admin")
def update_user(user_id: int):
    data = fast_json()
    updates = {}
    if "name" in data:
        updates["name"] = data["name"].strip()
    if "email" in data:
        updates["email"] = data["email"].lower().strip()
    if "role" in data and data["role"] in ROLES["staff"]:
        updates["role"] = data["role"]
    if "is_active" in data:
        updates["is_active"] = bool(data["is_active"])
    if "password" in data and data["password"]:
        if len(data["password"]) >= 8:
            updates["password_hash"] = generate_password_hash(data["password"])
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table(TABLES["users"]).update(updates).eq("id", user_id).execute()
    if not result.data:
        return jsonify({"error": "Utilisateur introuvable"}), 404
    add_audit("UPDATE", "user", f"Compte #{user_id} modifié", user_id)
    invalidate_cache()
    return jsonify({k: v for k, v in result.data[0].items() if k != "password_hash"})

@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@app.route("/api/auth/users/<int:user_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_user(user_id: int):
    if user_id == g.current_user.get("id"):
        return jsonify({"error": "Vous ne pouvez pas supprimer votre propre compte"}), 422
    supabase.table(TABLES["users"]).delete().eq("id", user_id).execute()
    add_audit("DELETE", "user", f"Compte #{user_id} supprimé", user_id)
    invalidate_cache()
    return jsonify({"message": "Compte supprimé"})

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

# ==================== HEALTH ====================
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": now_iso(), "version": "2.0.0"})

# ==================== MATERNITÉ ROUTES (AVEC FACTURATION AUTO) ====================
@app.route("/api/maternity/pregnancies", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
@cached(60)
def get_pregnancies():
    patient_id = request.args.get("patient_id")
    status = request.args.get("status", "active")
    query = supabase.table("pregnancies").select("*")
    if patient_id:
        query = query.eq("patient_id", to_int(patient_id))
    if status:
        query = query.eq("status", status)
    result = query.order("created_at", desc=True).execute()
    pregnancies = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for p in pregnancies:
        p["patient_name"] = patient_map.get(p.get("patient_id"), "Inconnu")
    return jsonify(pregnancies)

@app.route("/api/maternity/pregnancies", methods=["POST"])
@roles_required("super_admin")
def create_pregnancy():
    data = fast_json()
    if not data.get("patient_id") or not data.get("last_menstrual_period"):
        return jsonify({"error": "Patient et DDR requis"}), 422
    pregnancy = {
        "patient_id": to_int(data.get("patient_id")),
        "last_menstrual_period": data.get("last_menstrual_period"),
        "expected_delivery_date": data.get("expected_delivery_date"),
        "blood_type": data.get("blood_type", ""),
        "risk_level": data.get("risk_level", "normal"),
        "medical_history": data.get("medical_history", ""),
        "status": data.get("status", "active"),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("pregnancies", pregnancy)
    add_audit("CREATE", "pregnancy", f"Grossesse #{result.data[0]['id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/maternity/pregnancies/<int:pregnancy_id>", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
def get_pregnancy(pregnancy_id: int):
    result = supabase.table("pregnancies").select("*").eq("id", pregnancy_id).execute()
    if not result.data:
        return jsonify({"error": "Grossesse introuvable"}), 404
    pregnancy = result.data[0]
    patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", pregnancy["patient_id"]).execute()
    if patient.data:
        pregnancy["patient_name"] = patient.data[0]["full_name"]
    return jsonify(pregnancy)

@app.route("/api/maternity/pregnancies/<int:pregnancy_id>", methods=["PUT"])
@roles_required("super_admin")
def update_pregnancy(pregnancy_id: int):
    data = fast_json()
    allowed = ["last_menstrual_period", "expected_delivery_date", "blood_type", "risk_level", "medical_history", "status", "notes"]
    updates = {key: value for key, value in data.items() if key in allowed and value is not None}
    if not updates:
        return jsonify({"error": "Aucune donnée à mettre à jour"}), 422
    updates["updated_at"] = now_iso()
    result = supabase.table("pregnancies").update(updates).eq("id", pregnancy_id).execute()
    if not result.data:
        return jsonify({"error": "Grossesse introuvable"}), 404
    add_audit("UPDATE", "pregnancy", f"Grossesse #{pregnancy_id} modifiée", pregnancy_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/maternity/pregnancies/<int:pregnancy_id>/followups", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
def get_pregnancy_followups(pregnancy_id: int):
    result = supabase.table("prenatal_consultations").select("*").eq("pregnancy_id", pregnancy_id).order("visit_date", desc=True).execute()
    return jsonify(result.data)

@app.route("/api/maternity/prenatal", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
@cached(60)
def get_prenatal_consultations():
    patient_id = request.args.get("patient_id")
    pregnancy_id = request.args.get("pregnancy_id")
    query = supabase.table("prenatal_consultations").select("*")
    if patient_id:
        query = query.eq("patient_id", to_int(patient_id))
    if pregnancy_id:
        query = query.eq("pregnancy_id", to_int(pregnancy_id))
    result = query.order("visit_date", desc=True).execute()
    consultations = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for c in consultations:
        c["patient_name"] = patient_map.get(c.get("patient_id"), "Inconnu")
    return jsonify(consultations)

@app.route("/api/maternity/prenatal", methods=["POST"])
@roles_required("super_admin")
def create_prenatal_consultation():
    data = fast_json()
    if not data.get("patient_id") or not data.get("visit_date"):
        return jsonify({"error": "Patient et date requis"}), 422
    
    patient_id = to_int(data.get("patient_id"))
    
    consultation = {
        "patient_id": patient_id,
        "pregnancy_id": data.get("pregnancy_id"),
        "visit_date": data.get("visit_date"),
        "weight": data.get("weight"),
        "blood_pressure": data.get("blood_pressure", ""),
        "fetal_heartbeat": data.get("fetal_heartbeat", ""),
        "gestational_weeks": data.get("gestational_weeks"),
        "observations": data.get("observations", ""),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("prenatal_consultations", consultation)
    
    # Facturation automatique de la CPN
    facture_auto(patient_id, "CPN", 1, "prenatal", result.data[0].get("id"))
    
    add_audit("CREATE", "prenatal", f"Consultation prénatale #{result.data[0]['id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/maternity/prenatal/<int:consultation_id>", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
def get_prenatal_consultation(consultation_id: int):
    result = supabase.table("prenatal_consultations").select("*").eq("id", consultation_id).execute()
    if not result.data:
        return jsonify({"error": "Consultation introuvable"}), 404
    return jsonify(result.data[0])

# ==================== DELIVERIES (AVEC FACTURATION AUTO) ====================
@app.route("/api/maternity/deliveries", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
@cached(60)
def get_deliveries():
    patient_id = request.args.get("patient_id")
    query = supabase.table("deliveries").select("*")
    if patient_id:
        query = query.eq("patient_id", to_int(patient_id))
    result = query.order("delivery_date", desc=True).execute()
    deliveries = result.data
    patients_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    patient_map = {p["id"]: p["full_name"] for p in patients_result.data}
    for d in deliveries:
        d["patient_name"] = patient_map.get(d.get("patient_id"), "Inconnu")
        if d.get("babies"):
            d["babies"] = json.loads(d["babies"]) if isinstance(d["babies"], str) else d["babies"]
    return jsonify(deliveries)

@app.route("/api/maternity/deliveries", methods=["POST"])
@roles_required("super_admin")
def create_delivery():
    data = fast_json()
    if not data.get("patient_id") or not data.get("delivery_date"):
        return jsonify({"error": "Patient et date requis"}), 422
    
    patient_id = to_int(data.get("patient_id"))
    pregnancy_id = data.get("pregnancy_id")
    delivery_type = data.get("delivery_type", "vaginal")
    
    if pregnancy_id:
        supabase.table("pregnancies").update({"status": "completed", "updated_at": now_iso()}).eq("id", pregnancy_id).execute()
    
    delivery = {
        "patient_id": patient_id,
        "pregnancy_id": pregnancy_id,
        "delivery_date": data.get("delivery_date"),
        "delivery_type": delivery_type,
        "baby_count": data.get("baby_count", 1),
        "babies": json.dumps(data.get("babies", [])),
        "baby_weight": data.get("baby_weight"),
        "observations": data.get("observations", ""),
        "status": "completed",
        "delivered_by": g.current_user["id"],
        "delivered_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("deliveries", delivery)
    created_delivery = result.data[0] if result.data else delivery
    
    # Facturation automatique de l'accouchement
    tarif_code = "ACC_VAG" if delivery_type == "vaginal" else "ACC_CES"
    facture_auto(patient_id, tarif_code, 1, "delivery", created_delivery.get("id"))
    
    # Créer les fiches enfants
    babies = data.get("babies", [])
    for idx, baby in enumerate(babies):
        child_data = {
            "full_name": f"Bébé de la patiente #{patient_id}",
            "date_of_birth": data.get("delivery_date"),
            "gender": baby.get("gender", "M"),
            "parent_id": patient_id,
            "blood_type": baby.get("blood_type", ""),
            "birth_weight": baby.get("weight"),
            "birth_height": baby.get("height"),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        try:
            compatible_insert("children", child_data)
        except Exception as exc:
            print(f" Fiche enfant non créée après accouchement: {exc}")
    
    add_audit("CREATE", "delivery", f"Accouchement #{created_delivery.get('id')}", created_delivery.get("id"))
    invalidate_cache()
    return jsonify(created_delivery), 201

@app.route("/api/maternity/deliveries/<int:delivery_id>", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
def get_delivery(delivery_id: int):
    result = supabase.table("deliveries").select("*").eq("id", delivery_id).execute()
    if not result.data:
        return jsonify({"error": "Accouchement introuvable"}), 404
    delivery = result.data[0]
    patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", delivery["patient_id"]).execute()
    if patient.data:
        delivery["patient_name"] = patient.data[0]["full_name"]
    if delivery.get("babies"):
        delivery["babies"] = json.loads(delivery["babies"]) if isinstance(delivery["babies"], str) else delivery["babies"]
    return jsonify(delivery)

# ==================== MATERNITY ROOMS ====================
@app.route("/api/maternity/rooms", methods=["GET"])
@roles_required("super_admin", "infirmier", "docteur", "reception")
@cached(60)
def get_maternity_rooms():
    result = supabase.table("maternity_rooms").select("*").order("room_number").execute()
    rooms = result.data
    for room in rooms:
        if room.get("patient_id") and room.get("status") == "occupied":
            patient = supabase.table(TABLES["patients"]).select("full_name").eq("id", room["patient_id"]).execute()
            if patient.data:
                room["patient_name"] = patient.data[0]["full_name"]
    return jsonify(rooms)

@app.route("/api/maternity/rooms", methods=["POST"])
@roles_required("super_admin", "infirmier")
def create_maternity_room():
    data = fast_json()
    if not data.get("room_number"):
        return jsonify({"error": "Numéro de lit requis"}), 422
    room = {
        "room_number": data.get("room_number"),
        "type": data.get("type", "Standard"),
        "status": "available",
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("maternity_rooms", room)
    add_audit("CREATE", "maternity_room", f"Lit #{result.data[0]['room_number']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/maternity/rooms/admit", methods=["POST"])
@roles_required("super_admin", "infirmier")
def admit_to_maternity():
    data = fast_json()
    if not data.get("room_id") or not data.get("patient_id"):
        return jsonify({"error": "Lit et patient requis"}), 422
    room = supabase.table("maternity_rooms").select("*").eq("id", data["room_id"]).execute()
    if not room.data:
        return jsonify({"error": "Lit introuvable"}), 404
    if room.data[0]["status"] != "available":
        return jsonify({"error": "Lit déjà occupé"}), 422
    updates = {
        "status": "occupied",
        "patient_id": to_int(data.get("patient_id")),
        "admission_date": now_iso(),
        "admission_reason": data.get("reason", ""),
        "updated_at": now_iso()
    }
    result = supabase.table("maternity_rooms").update(updates).eq("id", data["room_id"]).execute()
    add_audit("UPDATE", "maternity_room", f"Admission patient #{data['patient_id']} au lit #{room.data[0]['room_number']}", data["room_id"])
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/maternity/rooms/<int:room_id>/discharge", methods=["POST"])
@roles_required("super_admin", "infirmier")
def discharge_from_maternity(room_id: int):
    updates = {
        "status": "available",
        "patient_id": None,
        "discharge_date": now_iso(),
        "admission_reason": None,
        "updated_at": now_iso()
    }
    result = supabase.table("maternity_rooms").update(updates).eq("id", room_id).execute()
    if not result.data:
        return jsonify({"error": "Lit introuvable"}), 404
    add_audit("UPDATE", "maternity_room", f"Libération du lit #{result.data[0]['room_number']}", room_id)
    invalidate_cache()
    return jsonify(result.data[0])

# ==================== PÉDIATRIE ROUTES ====================
@app.route("/api/pediatrics/children", methods=["GET"])
@roles_required("super_admin")
@cached(60)
def get_children():
    parent_id = request.args.get("parent_id")
    query = supabase.table("children").select("*")
    if parent_id:
        query = query.eq("parent_id", to_int(parent_id))
    result = query.order("created_at", desc=True).execute()
    children = result.data
    parents_result = supabase.table(TABLES["patients"]).select("id", "full_name").execute()
    parent_map = {p["id"]: p["full_name"] for p in parents_result.data}
    for c in children:
        c["parent_name"] = parent_map.get(c.get("parent_id"), "Inconnu")
    return jsonify(children)

@app.route("/api/pediatrics/children", methods=["POST"])
@roles_required("super_admin")
def create_child():
    data = fast_json()
    if not data.get("full_name") or not data.get("date_of_birth"):
        return jsonify({"error": "Nom et date de naissance requis"}), 422
    child = {
        "full_name": data.get("full_name"),
        "date_of_birth": data.get("date_of_birth"),
        "gender": data.get("gender", "M"),
        "parent_id": data.get("parent_id"),
        "blood_type": data.get("blood_type", ""),
        "allergies": data.get("allergies", ""),
        "medical_history": data.get("medical_history", ""),
        "vaccination_status": "pending",
        "birth_weight": data.get("birth_weight"),
        "birth_height": data.get("birth_height"),
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("children", child)
    add_audit("CREATE", "child", f"Enfant #{result.data[0]['full_name']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/pediatrics/children/<int:child_id>", methods=["GET"])
@roles_required("super_admin")
def get_child(child_id: int):
    result = supabase.table("children").select("*").eq("id", child_id).execute()
    if not result.data:
        return jsonify({"error": "Enfant introuvable"}), 404
    child = result.data[0]
    if child.get("parent_id"):
        parent = supabase.table(TABLES["patients"]).select("full_name").eq("id", child["parent_id"]).execute()
        if parent.data:
            child["parent_name"] = parent.data[0]["full_name"]
    return jsonify(child)

@app.route("/api/pediatrics/children/<int:child_id>", methods=["PUT"])
@roles_required("super_admin")
def update_child(child_id: int):
    data = fast_json()
    allowed = ["full_name", "blood_type", "allergies", "medical_history", "vaccination_status"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    updates["updated_at"] = now_iso()
    result = supabase.table("children").update(updates).eq("id", child_id).execute()
    if not result.data:
        return jsonify({"error": "Enfant introuvable"}), 404
    add_audit("UPDATE", "child", f"Enfant #{child_id} modifié", child_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/pediatrics/children/<int:child_id>/vaccinations", methods=["GET"])
@roles_required("super_admin")
def get_child_vaccinations(child_id: int):
    result = supabase.table("vaccinations").select("*").eq("child_id", child_id).order("administered_date", desc=True).execute()
    return jsonify(result.data)

@app.route("/api/pediatrics/children/<int:child_id>/growth", methods=["GET"])
@roles_required("super_admin")
def get_child_growth(child_id: int):
    result = supabase.table("growth_measurements").select("*").eq("child_id", child_id).order("measurement_date", asc=True).execute()
    return jsonify(result.data)

@app.route("/api/pediatrics/vaccinations", methods=["GET"])
@roles_required("super_admin")
@cached(60)
def get_vaccinations():
    child_id = request.args.get("child_id")
    query = supabase.table("vaccinations").select("*")
    if child_id:
        query = query.eq("child_id", to_int(child_id))
    result = query.order("administered_date", desc=True).execute()
    vaccinations = result.data
    children_result = supabase.table("children").select("id", "full_name").execute()
    child_map = {c["id"]: c["full_name"] for c in children_result.data}
    for v in vaccinations:
        v["child_name"] = child_map.get(v.get("child_id"), "Inconnu")
    return jsonify(vaccinations)

@app.route("/api/pediatrics/vaccinations", methods=["POST"])
@roles_required("super_admin")
def create_vaccination():
    data = fast_json()
    if not data.get("child_id") or not data.get("vaccine_name") or not data.get("administered_date"):
        return jsonify({"error": "Enfant, vaccin et date requis"}), 422
    vaccination = {
        "child_id": to_int(data.get("child_id")),
        "vaccine_name": data.get("vaccine_name"),
        "administered_date": data.get("administered_date"),
        "dose_number": data.get("dose_number", 1),
        "next_due_date": data.get("next_due_date"),
        "batch_number": data.get("batch_number", ""),
        "notes": data.get("notes", ""),
        "administered_by": g.current_user["id"],
        "administered_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("vaccinations", vaccination)
    supabase.table("children").update({"vaccination_status": "up_to_date", "updated_at": now_iso()}).eq("id", data["child_id"]).execute()
    add_audit("CREATE", "vaccination", f"Vaccin #{data['vaccine_name']} pour enfant #{data['child_id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

@app.route("/api/pediatrics/vaccinations/<int:vaccination_id>", methods=["GET"])
@roles_required("super_admin")
def get_vaccination(vaccination_id: int):
    result = supabase.table("vaccinations").select("*").eq("id", vaccination_id).execute()
    if not result.data:
        return jsonify({"error": "Vaccination introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/pediatrics/vaccinations/<int:vaccination_id>", methods=["PUT"])
@roles_required("super_admin")
def update_vaccination(vaccination_id: int):
    data = fast_json()
    allowed = ["next_due_date", "notes"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    updates["updated_at"] = now_iso()
    result = supabase.table("vaccinations").update(updates).eq("id", vaccination_id).execute()
    if not result.data:
        return jsonify({"error": "Vaccination introuvable"}), 404
    add_audit("UPDATE", "vaccination", f"Vaccination #{vaccination_id} modifiée", vaccination_id)
    invalidate_cache()
    return jsonify(result.data[0])

@app.route("/api/pediatrics/growth", methods=["GET"])
@roles_required("super_admin")
@cached(60)
def get_growth_measurements():
    child_id = request.args.get("child_id")
    query = supabase.table("growth_measurements").select("*")
    if child_id:
        query = query.eq("child_id", to_int(child_id))
    result = query.order("measurement_date", desc=True).execute()
    measurements = result.data
    children_result = supabase.table("children").select("id", "full_name").execute()
    child_map = {c["id"]: c["full_name"] for c in children_result.data}
    for m in measurements:
        m["child_name"] = child_map.get(m.get("child_id"), "Inconnu")
        if m.get("measurement_date"):
            child = supabase.table("children").select("date_of_birth").eq("id", m["child_id"]).execute()
            if child.data and child.data[0].get("date_of_birth"):
                birth = datetime.fromisoformat(child.data[0]["date_of_birth"])
                measure_date = datetime.fromisoformat(m["measurement_date"])
                months = (measure_date.year - birth.year) * 12 + (measure_date.month - birth.month)
                m["age_months"] = max(0, months)
    return jsonify(measurements)

@app.route("/api/pediatrics/growth", methods=["POST"])
@roles_required("super_admin")
def create_growth_measurement():
    data = fast_json()
    if not data.get("child_id") or not data.get("measurement_date"):
        return jsonify({"error": "Enfant et date requis"}), 422
    measurement = {
        "child_id": to_int(data.get("child_id")),
        "measurement_date": data.get("measurement_date"),
        "weight": data.get("weight"),
        "height": data.get("height"),
        "head_circumference": data.get("head_circumference"),
        "percentile": data.get("percentile"),
        "notes": data.get("notes", ""),
        "created_by": g.current_user["id"],
        "created_by_name": g.current_user["name"],
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("growth_measurements", measurement)
    add_audit("CREATE", "growth", f"Mesure croissance pour enfant #{data['child_id']}", result.data[0]["id"])
    invalidate_cache()
    return jsonify(result.data[0]), 201

# ==================== AI ROUTES ====================
AI_DISCLAIMER = "Assistant médical uniquement: validation clinique obligatoire par un professionnel habilité."

def groq_chat(system_prompt: str, user_prompt: str) -> str:
    api_key = str(GROQ_API_KEY or "").strip()
    if not api_key:
        return "IA non configuree: ajoute une cle Groq valide dans app.py, puis redeploie le service."
    if isinstance(GROQ_MODEL, tuple):
        actual_model = GROQ_MODEL[0] if len(GROQ_MODEL) > 0 else "llama-3.1-8b-instant"
    else:
        actual_model = str(GROQ_MODEL or "llama-3.1-8b-instant").strip()
    payload = json.dumps({
        "model": actual_model,
        "messages": [
            {"role": "system", "content": f"{system_prompt}\n{AI_DISCLAIMER}"},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 900
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        try:
            details = exc.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            details = str(exc)
        if exc.code in (401, 403):
            return f"IA non autorisee: la cle Groq dans app.py est invalide, expiree, supprimee ou sans acces API. HTTP {exc.code}. {details}"
        if exc.code == 404:
            return f"Modele IA introuvable: verifie GROQ_MODEL='{actual_model}'. HTTP 404. {details}"
        return f"IA indisponible: Groq a retourne HTTP {exc.code}. {details}"
    except urllib.error.URLError as exc:
        return f"IA indisponible: impossible de joindre Groq ({exc.reason})."
    except Exception as exc:
        return f"IA indisponible temporairement: {exc}"

def ai_payload(key: str, value: str):
    return jsonify({key: value, "disclaimer": AI_DISCLAIMER})

@app.route("/api/ai/health", methods=["GET"])
@roles_required(*ROLES["staff"])
def ai_health():
    configured = bool(GROQ_API_KEY and str(GROQ_API_KEY).startswith("gsk_"))
    if not configured:
        return jsonify({"ok": False, "model": GROQ_MODEL, "message": "Cle Groq absente ou invalide dans app.py"}), 500
    answer = groq_chat("Reponds uniquement par OK.", "Test de connexion IA.")
    ok = answer.strip().lower().startswith("ok")
    return jsonify({"ok": ok, "model": str(GROQ_MODEL), "message": answer}), 200 if ok else 502

@app.route("/api/ai/patient-summary", methods=["POST"])
@roles_required("super_admin", "docteur")
def ai_patient_summary():
    data = fast_json()
    patient_id = to_int(data.get("patient_id"))
    patient_result = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
    if not patient_result.data:
        return jsonify({"error": "Patient introuvable"}), 404
    patient = filter_patients_for_role(patient_result.data)
    if not patient:
        return jsonify({"error": "Accès patient interdit pour ce rôle"}), 403
    summary = groq_chat("Résume un dossier patient de façon structurée et prudente, sans diagnostic final.", json.dumps(patient[0], ensure_ascii=False))
    return ai_payload("summary", summary)

@app.route("/api/ai/clinical-decision", methods=["POST"])
@roles_required("super_admin", "docteur")
def ai_clinical_decision():
    context = fast_json().get("context", "")
    analysis = groq_chat("Aide à l'orientation clinique. Donne priorités, signes d'alerte, examens utiles et limites. Ne pose pas de diagnostic final.", context)
    return ai_payload("analysis", analysis)

@app.route("/api/ai/hospital-flow", methods=["POST"])
@roles_required(*ROLES["staff"])
def ai_hospital_flow():
    data = fast_json()
    prediction = groq_chat("Prédis l'affluence hospitalière à court terme et propose des recommandations opérationnelles.", json.dumps(data, ensure_ascii=False)[:12000])
    return ai_payload("prediction", prediction)

# ==================== ROUTES MANQUANTES - RAPPORTS ====================
@app.route("/api/reports/patients", methods=["GET"])
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

@app.route("/api/reports/financial", methods=["GET"])
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

@app.route("/api/reports/pharmacy", methods=["GET"])
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

@app.route("/api/reports/activity", methods=["GET"])
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

# ==================== ROUTES MANQUANTES - DASHBOARD ====================
@app.route("/api/dashboard/stats", methods=["GET"])
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

# ==================== ROUTES MANQUANTES - NOTIFICATIONS ====================
@app.route("/api/notifications", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_notifications():
    user_id = g.current_user.get("id")
    result = supabase.table("notifications").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return jsonify(result.data)

@app.route("/api/notifications", methods=["POST"])
@roles_required(*ROLES["staff"])
def create_notification():
    data = fast_json()
    notification = {
        "user_id": g.current_user.get("id"),
        "title": data.get("title", ""),
        "message": data.get("message", ""),
        "type": data.get("type", "info"),
        "read": False,
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    result = compatible_insert("notifications", notification)
    return jsonify(result.data[0]), 201

@app.route("/api/notifications/<int:notification_id>/read", methods=["PUT"])
@roles_required(*ROLES["staff"])
def mark_notification_read(notification_id: int):
    result = supabase.table("notifications").update({"read": True, "updated_at": now_iso()}).eq("id", notification_id).execute()
    if not result.data:
        return jsonify({"error": "Notification introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/notifications/mark-all-read", methods=["PUT"])
@roles_required(*ROLES["staff"])
def mark_all_notifications_read():
    user_id = g.current_user.get("id")
    supabase.table("notifications").update({"read": True, "updated_at": now_iso()}).eq("user_id", user_id).execute()
    return jsonify({"message": "Toutes les notifications marquées comme lues"})

@app.route("/api/notifications/unread-count", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_unread_notifications_count():
    user_id = g.current_user.get("id")
    result = supabase.table("notifications").select("id").eq("user_id", user_id).eq("read", False).execute()
    return jsonify({"count": len(result.data)})

# ==================== ROUTES MANQUANTES - ATTACHMENTS ====================
@app.route("/api/attachments", methods=["POST"])
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

@app.route("/api/attachments/<string:attachment_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_attachment(attachment_id: str):
    result = supabase.table("attachments").select("*").eq("id", attachment_id).execute()
    if not result.data:
        return jsonify({"error": "Pièce jointe introuvable"}), 404
    return jsonify(result.data[0])

@app.route("/api/attachments/<string:attachment_id>", methods=["DELETE"])
@roles_required("super_admin")
def delete_attachment(attachment_id: str):
    supabase.table("attachments").delete().eq("id", attachment_id).execute()
    add_audit("DELETE", "attachment", f"Pièce jointe #{attachment_id} supprimée", None)
    return jsonify({"message": "Pièce jointe supprimée"})

@app.route("/api/attachments/entity/<string:entity_type>/<int:entity_id>", methods=["GET"])
@roles_required(*ROLES["staff"])
def get_attachments_by_entity(entity_type: str, entity_id: int):
    result = supabase.table("attachments").select("*").eq("entity_type", entity_type).eq("entity_id", entity_id).order("created_at", desc=True).execute()
    return jsonify(result.data)

# ==================== ROUTES MANQUANTES - LOGS ====================
@app.route("/api/logs/error", methods=["POST"])
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

@app.route("/api/metrics", methods=["GET"])
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

@app.route("/api/health/detailed", methods=["GET"])
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

# ==================== AUTO-PING (KEEP ALIVE) ====================
import threading

def auto_ping():
    """Ping auto toutes les 5 minutes pour garder l'API active"""
    import requests
    url = f"http://{HOST}:{PORT}/api/health"
    while True:
        try:
            requests.get(url, timeout=5)
            print(f"[AUTO-PING] Ping réussi à {url}")
        except Exception as e:
            print(f"[AUTO-PING] Échec du ping: {e}")
        time.sleep(300)

# Démarrer le thread de ping
if __name__ != "__main__":
    ping_thread = threading.Thread(target=auto_ping, daemon=True)
    ping_thread.start()

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
                "rate": 2800,
                "currency_from": "USD",
                "currency_to": "CDF",
                "set_by": None,
                "set_by_name": "Systeme",
                "created_at": now_iso()
            }).execute()
            print(" Table exchange_rates créée avec taux par défaut 2800")
        except Exception as e:
            print(f" Impossible de créer exchange_rates: {e}")

def init_medical_boxes_table():
    try:
        supabase.table("medical_boxes").select("*").limit(1).execute()
        print(" Table medical_boxes existe déjà")
    except Exception:
        print(" Table medical_boxes à créer")
        try:
            supabase.table("medical_boxes").insert({
                "box_number": "1",
                "status": "free",
                "created_at": now_iso(),
                "updated_at": now_iso()
            }).execute()
            supabase.table("medical_boxes").delete().eq("box_number", "1").execute()
            print(" Table medical_boxes créée")
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

# ==================== LANCEMENT ====================
if __name__ == "__main__":
    print("=" * 50)
    print(" I HUB HOSPITAL API - VERSION COMPLÈTE AVEC REDIS")
    print("=" * 50)
    print(f" Cache: {CACHE_TYPE}")
    seed_admin()
    init_workflow_tables()
    init_maternity_tables()
    init_exchange_rate_table()
    init_medical_boxes_table()
    init_hospitalization_followups_table()
    print(f" Serveur démarré sur http://{HOST}:{PORT}")
    print("=" * 50)
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True)
