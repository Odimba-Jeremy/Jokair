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

# ==================== CACHE CONFIGURATION ====================
REDIS_URL = os.getenv("REDIS_URL", "")
MEMCACHED_URL = os.getenv("MEMCACHED_URL", "")
CACHE_TYPE = os.getenv("CACHE_TYPE", "SimpleCache")

# Priorité: Redis > Memcached > SimpleCache
if REDIS_URL:
    try:
        import redis
        redis_client = redis.from_url(REDIS_URL)
        redis_client.ping()
        print(f"✅ Redis connecté sur {REDIS_URL}")
        CACHE_TYPE = "RedisCache"
        CACHE_REDIS_URL = REDIS_URL
    except Exception as e:
        print(f"⚠️ Redis indisponible: {e}")
        CACHE_TYPE = "SimpleCache"
elif MEMCACHED_URL:
    try:
        import memcache
        mc = memcache.Client([MEMCACHED_URL])
        mc.set("test", "ok")
        mc.get("test")
        print(f"✅ Memcached connecté sur {MEMCACHED_URL}")
        CACHE_TYPE = "MemcachedCache"
        CACHE_MEMCACHED_SERVERS = [MEMCACHED_URL]
    except Exception as e:
        print(f"⚠️ Memcached indisponible: {e}, utilisation SimpleCache")
        CACHE_TYPE = "SimpleCache"
else:
    print("INFO: Aucun cache externe configure, utilisation SimpleCache")
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
MAX_BOXES = 3
BASE_URL = os.getenv("RENDER_URL", os.getenv("PUBLIC_URL", f"http://localhost:{PORT}"))

# ==================== INITIALISATION ====================
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["CACHE_TYPE"] = CACHE_TYPE
app.config["CACHE_REDIS_URL"] = REDIS_URL if REDIS_URL else None
app.config["CACHE_MEMCACHED_SERVERS"] = [MEMCACHED_URL] if MEMCACHED_URL else []
app.config["CACHE_DEFAULT_TIMEOUT"] = CACHE_TIMEOUT
app.config["JSON_AS_ASCII"] = False

cache = Cache(app)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)
cached = cache.cached

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
        # La file est une donnée de coordination temps réel : une réponse mise
        # en cache peut laisser le médecin sur une liste vide après dispatch.
        if request.path == "/api/workflow/queue":
            response.headers["Cache-Control"] = "no-store"
        else:
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

# ==================== GRILLE TARIFAIRE ====================
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
# Statuts autorisés pour la file d'attente
ALLOWED_STATUSES = {"en attente", "SV pris", "dispatché"}
# Rooms definition (added)
ROOMS = {
    "A1": {"service": "maternite", "beds": 6, "type": "standard", "price_usd": 15, "label": "Chambre A1"},
    "A2": {"service": "maternite", "beds": 1, "type": "standard", "price_usd": 25, "label": "Chambre A2"},
    "A3": {"service": "maternite", "beds": 1, "type": "standard", "price_usd": 25, "label": "Chambre A3"},
    "A4": {"service": "maternite", "beds": 1, "type": "standard", "price_usd": 25, "label": "Chambre A4"},
    "B1": {"service": "general", "beds": 6, "type": "standard", "price_usd": 15, "label": "Chambre B1"},
    "B2": {"service": "general", "beds": 2, "type": "standard", "price_usd": 20, "label": "Chambre B2"},
    "B3": {"service": "general", "beds": 3, "type": "standard", "price_usd": 20, "label": "Chambre B3"},
    "B4": {"service": "general", "beds": 1, "type": "standard", "price_usd": 25, "label": "Chambre B4"},
    "B5": {"service": "general", "beds": 1, "type": "standard", "price_usd": 25, "label": "Chambre B5"},
    "B6": {"service": "general", "beds": 1, "type": "standard", "price_usd": 25, "label": "Chambre B6"}
}

def hardcoded_hospitalization_rooms() -> list[dict]:
    """Catalogue fixe, avec occupation calculée depuis les admissions actives."""
    try:
        admissions = supabase.table("hospitalizations").select("room_id,bed_id,bed,status").in_("status", ["admitted", "hospitalized"]).execute().data or []
    except Exception:
        admissions = []
    rooms = []
    for static_id, (room_number, definition) in enumerate(ROOMS.items(), start=1):
        total_beds = max(1, to_int(definition.get("beds"), 1))
        occupied_bed_ids = {str(row.get("bed_id") or row.get("bed")) for row in admissions if str(row.get("room_id")) == str(static_id)}
        occupied = len(occupied_bed_ids)
        rooms.append({"id": static_id, "room_number": room_number, "label": definition.get("label", f"Chambre {room_number}"), "service": definition.get("service", "general"), "type": definition.get("type", "standard"), "daily_rate": definition.get("price_usd", 0), "total_beds": total_beds, "occupied_beds": occupied, "available_beds": max(0, total_beds - occupied), "occupied_bed_ids": sorted(occupied_bed_ids), "status": "available" if occupied < total_beds else "occupied"})
    return rooms
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated

def roles_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Simple stub: no actual role checking
            return f(*args, **kwargs)
        return wrapper
    return decorator


@app.route("/api/rooms", methods=["GET"])
@token_required
@roles_required(*ROLES["staff"])
@cached(timeout=120)
def get_rooms():
    """Retourne le dictionnaire des chambres hospitalières."""
    return jsonify(list(ROOMS.values()))

@app.route("/api/rooms/<room_id>/beds", methods=["GET"])
@token_required
@roles_required(*ROLES["staff"])
def get_room_beds(room_id: str):
    """Retourne la liste des lits pour la chambre donnée.
    Chaque lit est représenté par un dict avec un id et un statut d'occupation (toujours False ici)."""
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    bed_count = room.get("beds", 0)
    beds = [{"id": i, "occupied": False} for i in range(1, bed_count + 1)]
    return jsonify(beds)

# ==================== UTILITAIRES ====================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def hospital_patient_id(patient_id: Any) -> str:
    return f"IH-USD-{to_int(patient_id):05d}"

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
    if hasattr(g, "_cached_linked_patient_ids"):
        return g._cached_linked_patient_ids
    user_id = g.current_user.get("id") if hasattr(g, "current_user") else None
    if not user_id:
        return set()
    user_id_str = str(user_id)
    user_id_int = to_int(user_id)
    role = g.current_user.get("role") if hasattr(g, "current_user") else ""
    linked = set()

    if role == "docteur":
        sources = [
            ("patient_queue", "patient_id", "assigned_doctor_id"),
            ("patient_dispatches", "patient_id", "doctor_id"),
            ("medical_consultations", "patient_id", "doctor_id"),
            (TABLES["appointments"], "patient_id", "doctor_id"),
            (TABLES["prescriptions"], "patient_id", "doctor_id"),
            (TABLES["patients"], "id", "assigned_doctor_id"),
            (TABLES["patients"], "id", "created_by"),
        ]
    else:
        sources = [
            (TABLES["patients"], "id", "created_by"),
            ("patient_queue", "patient_id", "assigned_doctor_id"),
            ("patient_dispatches", "patient_id", "doctor_id"),
            ("medical_consultations", "patient_id", "doctor_id"),
            (TABLES["appointments"], "patient_id", "doctor_id"),
            (TABLES["prescriptions"], "patient_id", "doctor_id"),
            (TABLES["lab_tests"], "patient_id", "requested_by"),
            (TABLES["care"], "patient_id", "performed_by"),
            (TABLES["billing"], "patient_id", "created_by"),
        ]

    for table_name, patient_field, user_field in sources:
        try:
            # Essayer avec l'ID numérique d'abord, puis chaîne si besoin
            rows = supabase.table(table_name).select(patient_field).eq(user_field, user_id_int).execute().data or []
            if not rows and str(user_id_int) != user_id_str:
                rows = supabase.table(table_name).select(patient_field).eq(user_field, user_id_str).execute().data or []
            linked.update(str(row.get(patient_field)) for row in rows if row.get(patient_field))
        except Exception:
            continue

    g._cached_linked_patient_ids = linked
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
def compatible_insert(table_name: str, data: dict):
    """Insère en ignorant uniquement les colonnes absentes du schéma Supabase.

    Certaines bases existantes ne possèdent pas encore les colonnes d'audit
    récentes (`created_by`, `created_by_name`). La requête est réessayée sans
    ces seules colonnes ; les autres erreurs restent visibles et sont levées.
    """
    payload = dict(data)
    removed_columns = []
    while True:
        try:
            result = supabase.table(table_name).insert(payload).execute()
            if removed_columns:
                print(f"Colonnes ignorées pour {table_name}: {', '.join(removed_columns)}")
            return result
        except Exception as exc:
            column = missing_schema_column(exc)
            if not column or column not in payload:
                print(f"Erreur d'insertion dans {table_name} : {exc}")
                raise
            removed_columns.append(column)
            payload.pop(column, None)

def invalidate_cache():
    """Invalidate Flask‑Caching."""
    try:
        cache.clear()
    except Exception as exc:
        print(f"Erreur lors du vidage du cache : {exc}")
def get_user_map(role: str = None) -> dict:
    try:
        query = supabase.table(TABLES["users"]).select("id,name,role")
        if role:
            query = query.eq("role", role)
        rows = query.execute().data or []
        # Les identifiants viennent de Supabase sous forme numérique dans la plupart
        # des routes. Garder les deux formes évite qu'un médecin valide soit rejeté
        # lors du dispatch à cause d'une simple différence int/str.
        users = {}
        for row in rows:
            users[row["id"]] = row
            users[str(row["id"])] = row
        return users
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

# ==================== FACTURATION AUTOMATIQUE ====================
def get_current_rate():
    try:
        result = supabase.table("exchange_rates").select("*").order("created_at", desc=True).limit(1).execute()
        if result.data:
            return float(result.data[0].get("rate", 2800))
    except Exception:
        pass
    return 2800

def get_tarif_from_db(code_tarif):
    try:
        result = supabase.table(TABLES["tariffs"]).select("*").eq("code", code_tarif).eq("is_active", True).execute()
        if result.data:
            return result.data[0]
        result = supabase.table(TABLES["tariffs"]).select("*").eq("category", code_tarif).eq("is_active", True).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception:
        return None

def facture_auto(patient_id, code_tarif, quantite=1, source="", source_id=None):
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
    
    patient_id_str = f"IH-USD-{patient_id:05d}"
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

# ==================== GENERATE QR CODE ====================
def generate_qr_code_data(patient_id: int, patient_name: str, phone: str = "") -> str:
    """Génère les données pour un QR code"""
    return f"IH-USD-{patient_id:05d}|{patient_name}|{phone}"

# ==================== DISPATCH INFERMIER ====================

@app.route("/api/dispatch", methods=["POST"])
@token_required
@roles_required("super_admin", "infirmier", "reception")
def dispatch_patient_workflow_legacy():
    """Dispatch d'un patient depuis la réception/infirmier.
    Payload attendu:
        {"patient_id": int, "nurse_id": int, "room_id": str, "bed_id": int}
    """
    data = fast_json()
    patient_id = data.get("patient_id")
    nurse_id = data.get("nurse_id")
    room_id = data.get("room_id")
    bed_id = data.get("bed_id")

    # Vérifications de base
    if not (patient_id and nurse_id and room_id is not None and bed_id is not None):
        return jsonify({"error": "patient_id, nurse_id, room_id et bed_id requis"}), 422

    # Vérifier que le patient existe
    patient_res = supabase.table(TABLES["patients"]).select("*").eq("id", patient_id).execute()
    if not patient_res.data:
        return jsonify({"error": "Patient introuvable"}), 404
    patient = patient_res.data[0]

    # Vérifier les signes vitaux existent
    vitals_res = supabase.table("vitals").select("*").eq("patient_id", patient_id).execute()
    if not vitals_res.data:
        return jsonify({"error": "Signes vitaux manquants pour le patient"}), 400

    # Vérifier la chambre et le lit
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({"error": f"Chambre {room_id} inexistante"}), 404
    if bed_id < 1 or bed_id > room.get("beds", 0):
        return jsonify({"error": f"Lit {bed_id} invalide pour la chambre {room_id}"}), 422

    # Mettre à jour le patient
    updates = {
        "status": "dispatché",
        "room_number": room_id,
        "bed_id": bed_id,
        "assigned_nurse_id": nurse_id,
        "updated_at": now_iso()
    }
    supabase.table(TABLES["patients"]).update(updates).eq("id", patient_id).execute()

    # Ajout à la file d'attente si besoin
    try:
        last = supabase.table("patient_queue").select("arrival_order").order("arrival_order", desc=True).limit(1).execute().data or []
        arrival_order = (last[0].get("arrival_order", 0) + 1) if last else 1
        supabase.table("patient_queue").insert({
            "patient_id": patient_id,
            "status": "dispatché",
            "arrival_order": arrival_order,
            "arrival_time": now_iso(),
            "created_by": g.current_user.get("id"),
            "created_at": now_iso(),
            "updated_at": now_iso()
        }).execute()
    except Exception as e:
        print(f"Erreur lors de l'ajout à la file d'attente : {e}")

    add_audit("DISPATCH", "patient", f"Dispatch du patient {patient_id} vers {room_id} lit {bed_id}", patient_id)
    invalidate_cache()
    return jsonify({"message": "Patient dispatché", "patient_id": patient_id, "room": room_id, "bed": bed_id}), 200

# ==================== BOX RELEASE ====================

@app.route("/api/boxes/<int:box_id>/release", methods=["POST"])
@token_required
@roles_required("super_admin", "infirmier")
def release_box(box_id: int):
    """Libère un box (marque le statut à 'free')."""
    try:
        supabase.table("medical_boxes").update({"status": "free", "updated_at": now_iso()}).eq("id", box_id).execute()
    except Exception as e:
        return jsonify({"error": f"Impossible de libérer le box {box_id}: {e}"}), 500
    add_audit("RELEASE", "box", f"Box {box_id} libéré", box_id)
    invalidate_cache()
    return jsonify({"message": f"Box {box_id} libéré"}), 200

# ==================== PRESCRIPTIONS ====================

# ==================== LAB PDF ====================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": now_iso(), "version": "2.0.0"})

# ==================== MATERNITÉ ROUTES ====================

# ==================== MODULES ROUTES ====================
# Les Blueprints reçoivent le contexte du noyau pour éviter les imports
# circulaires tout en conservant les contrats API existants.
try:
    from .auth import register_auth_routes
    from .patients import register_patient_routes
    from .workflow import register_workflow_routes
    from .pharmacy import register_pharmacy_routes
    from .laboratory import register_laboratory_routes
    from .billing_api import register_billing_routes
    from .maternity import register_maternity_routes
    from .reports import register_reports_routes
    from .doctor import register_doctor_routes
    from .medical import register_medical_routes
    from .admin import register_admin_routes
    from .pediatrics import register_pediatrics_routes
    from .ai import register_ai_routes
except ImportError:
    from auth import register_auth_routes
    from patients import register_patient_routes
    from workflow import register_workflow_routes
    from pharmacy import register_pharmacy_routes
    from laboratory import register_laboratory_routes
    from billing_api import register_billing_routes
    from maternity import register_maternity_routes
    from reports import register_reports_routes
    from doctor import register_doctor_routes
    from medical import register_medical_routes
    from admin import register_admin_routes
    from pediatrics import register_pediatrics_routes
    from ai import register_ai_routes

register_auth_routes(app, fast_json=fast_json, supabase=supabase, tables=TABLES,
                     roles=ROLES, now_iso=now_iso, create_token=create_token,
                     token_required=token_required, add_audit=add_audit,
                     invalidate_cache=invalidate_cache)
register_patient_routes(app, supabase=supabase, tables=TABLES, roles=ROLES,
                        fast_json=fast_json, to_int=to_int, optional_date=optional_date,
                        now_iso=now_iso, cached=cached, roles_required=roles_required,
                        compatible_insert=compatible_insert, invalidate_cache=invalidate_cache,
                        add_audit=add_audit, hospital_patient_id=hospital_patient_id,
                        enrich_patient_identifier=enrich_patient_identifier,
                        enrich_patient_identifiers=enrich_patient_identifiers,
                        add_pregnancy_flags=add_pregnancy_flags, is_female=is_female,
                        filter_patients_for_role=filter_patients_for_role,
                        can_access_patient_record=can_access_patient_record,
                        allowed_statuses=ALLOWED_STATUSES,
                        generate_barcode_svg=generate_barcode_svg,
                        generate_qr_code_data=generate_qr_code_data)
register_workflow_routes(app, runtime=globals())
register_pharmacy_routes(app, runtime=globals())
register_laboratory_routes(app, runtime=globals())
register_billing_routes(app, runtime=globals())
register_maternity_routes(app, runtime=globals())
register_reports_routes(app, runtime=globals())
register_doctor_routes(app, runtime=globals())
register_medical_routes(app, runtime=globals())
register_admin_routes(app, runtime=globals())
register_pediatrics_routes(app, runtime=globals())
register_ai_routes(app, runtime=globals())


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True)
