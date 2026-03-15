import json
import os
import re
import uuid
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import bcrypt
import jwt

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------

SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key-change")
DB_FILE = "users.json"

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100 per hour"]
)

# ---------------- DATABASE ----------------

def load_users():
    if not os.path.exists(DB_FILE):
        return []

    with open(DB_FILE, "r") as f:
        return json.load(f)


def save_users(users):
    with open(DB_FILE, "w") as f:
        json.dump(users, f, indent=2)


# ---------------- SECURITY ----------------

def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id):

    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(days=7)
    }

    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)


# ---------------- ROUTE TEST ----------------

@app.route("/")
def home():
    return {"status": "UniConnect API running"}


# ---------------- SIGNUP ----------------

@app.route("/signup", methods=["POST"])
@limiter.limit("5 per minute")
def signup():

    data = request.json

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if not name or not email or not password:
        return jsonify({"error": "Champs manquants"}), 400

    if not valid_email(email):
        return jsonify({"error": "Email invalide"}), 400

    if len(password) < 6:
        return jsonify({"error": "Mot de passe trop court"}), 400

    users = load_users()

    for u in users:
        if u["email"] == email:
            return jsonify({"error": "Email déjà utilisé"}), 400

    user = {
        "id": str(uuid.uuid4()),
        "name": name,
        "email": email,
        "password": hash_password(password)
    }

    users.append(user)
    save_users(users)

    token = create_token(user["id"])

    return jsonify({
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "token": token
    })


# ---------------- LOGIN ----------------

@app.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():

    data = request.json

    email = data.get("email")
    password = data.get("password")

    users = load_users()

    for u in users:

        if u["email"] == email and verify_password(password, u["password"]):

            token = create_token(u["id"])

            return jsonify({
                "id": u["id"],
                "name": u["name"],
                "email": u["email"],
                "token": token
            })

    return jsonify({"error": "Email ou mot de passe incorrect"}), 401


# ---------------- FORGOT PASSWORD ----------------

@app.route("/forgot-password", methods=["POST"])
@limiter.limit("5 per minute")
def forgot():

    data = request.json
    email = data.get("email")

    users = load_users()

    for u in users:
        if u["email"] == email:

            return jsonify({
                "message": "Lien de récupération envoyé (simulation)"
            })

    return jsonify({"error": "Email introuvable"}), 404


# ---------------- START SERVER ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
