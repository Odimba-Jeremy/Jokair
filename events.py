"""Module d'événements temps réel pour I-HUB.

Fournit :
1. Server-Sent Events (SSE) : GET /api/events/stream
2. Polling intelligent : GET /api/events/poll?since=<timestamp>
3. Webhook entrant : POST /api/events/webhook
4. Helper broadcast_event(...) utilisable dans toute l'application.
"""

from __future__ import annotations
import json
import time
import queue
import threading
from datetime import datetime, timezone
from typing import Any
from flask import Blueprint, Response, jsonify, request, g

# Buffer circulaire en mémoire des derniers événements (jusqu'à 1000 événements)
MAX_EVENTS_HISTORY = 1000
_events_lock = threading.Lock()
_events_history: list[dict[str, Any]] = []
_listeners: list[queue.Queue] = []


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def broadcast_event(event_type: str, data: dict[str, Any] | None = None, role: str | None = None) -> dict[str, Any]:
    """Diffuse un événement vers tous les clients connectés (SSE et polling)."""
    event = {
        "id": int(time.time() * 1000),
        "type": event_type,
        "data": data or {},
        "target_role": role,
        "timestamp": time.time(),
        "created_at": now_iso()
    }

    with _events_lock:
        _events_history.append(event)
        if len(_events_history) > MAX_EVENTS_HISTORY:
            _events_history.pop(0)

        dead_queues = []
        for q in _listeners:
            try:
                q.put_nowait(event)
            except Exception:
                dead_queues.append(q)

        for dq in dead_queues:
            if dq in _listeners:
                _listeners.remove(dq)

    return event


def register_events_routes(app, **kwargs):
    events_bp = Blueprint("events", __name__)

    @events_bp.route("/api/events/poll", methods=["GET"])
    def poll_events():
        """Polling intelligent : retourne les événements survenus depuis le timestamp donné."""
        since = request.args.get("since", 0)
        try:
            since = float(since)
        except (ValueError, TypeError):
            since = 0.0

        role_filter = request.args.get("role", "").strip().lower()

        with _events_lock:
            new_events = [
                e for e in _events_history
                if e["timestamp"] > since and (not e.get("target_role") or not role_filter or e["target_role"] == role_filter)
            ]

        current_time = time.time()
        return jsonify({
            "events": new_events,
            "timestamp": current_time,
            "count": len(new_events)
        })

    @events_bp.route("/api/events/stream", methods=["GET"])
    def event_stream():
        """Server-Sent Events (SSE) : flux temps réel continu."""
        q = queue.Queue(maxsize=100)
        with _events_lock:
            _listeners.append(q)

        def generate():
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"
            try:
                while True:
                    try:
                        event = q.get(timeout=15.0)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield f": ping {time.time()}\n\n"
            except GeneratorExit:
                pass
            finally:
                with _events_lock:
                    if q in _listeners:
                        _listeners.remove(q)

        response = Response(generate(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Connection"] = "keep-alive"
        return response

    @events_bp.route("/api/events/webhook", methods=["POST"])
    def incoming_webhook():
        """Point d'entrée Webhook générique."""
        payload = request.get_json(silent=True) or {}
        event_type = payload.get("type", "generic_update")
        event_data = payload.get("data", {})
        target_role = payload.get("role")

        event = broadcast_event(event_type, event_data, target_role)
        return jsonify({"status": "received", "event": event}), 200

    app.register_blueprint(events_bp)
