"""Module de synchronisation temps réel pour I-HUB : SSE, Polling de secours et Webhook."""

from __future__ import annotations
import json
import time
from typing import Any
from flask import Blueprint, Response, request, jsonify

events_bp = Blueprint("events", __name__)

# File circulaire en mémoire pour conserver les 1000 derniers événements
_EVENTS_BUFFER: list[dict[str, Any]] = []
_MAX_EVENTS = 1000


def broadcast_event(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Enregistre et diffuse un événement en temps réel vers tous les clients connectés."""
    event = {
        "id": int(time.time() * 1000),
        "type": event_type,
        "payload": payload or {},
        "timestamp": time.time()
    }
    _EVENTS_BUFFER.append(event)
    if len(_EVENTS_BUFFER) > _MAX_EVENTS:
        del _EVENTS_BUFFER[0 : len(_EVENTS_BUFFER) - _MAX_EVENTS]
    return event


@events_bp.route("/api/events/stream", methods=["GET"])
def event_stream():
    """Flux Server-Sent Events (SSE) avec ping régulier de maintien de connexion."""
    # ⚠️ Capturer AVANT le générateur : avec gunicorn sync worker,
    # le contexte de requête est détruit dès que le générateur commence à itérer.
    try:
        last_id_init = int(request.args.get("last_event_id", 0))
    except (ValueError, TypeError):
        last_id_init = 0

    def generate(last_id):
        # Envoyer les événements manqués
        for ev in _EVENTS_BUFFER:
            if ev["id"] > last_id:
                yield f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev['payload'])}\n\n"
                last_id = ev["id"]

        while True:
            # Vérifier les nouveaux événements
            new_events = [ev for ev in _EVENTS_BUFFER if ev["id"] > last_id]
            for ev in new_events:
                yield f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev['payload'])}\n\n"
                last_id = ev["id"]

            # Heartbeat pour garder la connexion ouverte
            yield ": ping\n\n"
            time.sleep(2)

    return Response(
        generate(last_id_init),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*"
        }
    )


@events_bp.route("/api/events/poll", methods=["GET"])
def event_poll():
    """Mécanisme de secours par polling régulier (toutes les 8 secondes sur Render gratuit)."""
    try:
        since = float(request.args.get("since", 0))
    except (ValueError, TypeError):
        since = 0.0

    recent = [ev for ev in _EVENTS_BUFFER if ev["timestamp"] > since]
    return jsonify({
        "events": recent,
        "server_time": time.time()
    })


@events_bp.route("/api/events/webhook", methods=["POST"])
def event_webhook():
    """Webhook pour ingérer des événements depuis des systèmes externes ou d'autres services."""
    data = request.get_json(silent=True) or {}
    event_type = data.get("type", "generic_event")
    payload = data.get("payload", {})
    ev = broadcast_event(event_type, payload)
    return jsonify({"status": "received", "event": ev}), 200


def register_events_routes(app):
    """Enregistre le blueprint events sur l'application Flask."""
    app.register_blueprint(events_bp)
