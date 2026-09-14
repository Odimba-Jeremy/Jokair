"""Routes du module pédiatrie."""

from flask import Blueprint


def register_pediatrics_routes(app, *, runtime):
    globals().update(runtime)
    pediatrics = Blueprint("pediatrics", __name__)

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


    app.register_blueprint(pediatrics)
