"""
Minimal Flask UI for the follow-spot injector.

Runs in a background thread inside the same process as follow_spot.py, so it
reads the injector's live state (pan/tilt offset, log lines) directly out of
memory - no extra IPC. Settings edits are written straight to profile.json;
the running injector picks them up on its next restart.
"""

import json
import logging
import time

from flask import Flask, jsonify, render_template, request

EDITABLE_SECTIONS = ("listen", "target", "channels", "controller")
EDITABLE_TOP_LEVEL = ("receive_universe", "send_universe", "update_rate_hz")


def deep_update(dst, src):
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_update(dst[key], value)
        else:
            dst[key] = value


def create_app(ctx):
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        with ctx["lock"]:
            pan_raw = ctx["state"]["last_pan_raw"]
            tilt_raw = ctx["state"]["last_tilt_raw"]
            offset_pan = ctx["state"]["offset_pan"]
            offset_tilt = ctx["state"]["offset_tilt"]
            last_packet_time = ctx["state"]["last_packet_time"]

        connected = last_packet_time is not None and (time.time() - last_packet_time) < 2.0

        payload = {
            "connected": connected,
            "controller_name": ctx["controller_name"],
            "pan_max": ctx["pan_max"],
            "tilt_max": ctx["tilt_max"],
            "have_data": pan_raw is not None,
        }
        if pan_raw is not None:
            payload.update({
                "pan_raw": pan_raw,
                "tilt_raw": tilt_raw,
                "pan_out": pan_raw + offset_pan,
                "tilt_out": tilt_raw + offset_tilt,
                "offset_pan": offset_pan,
                "offset_tilt": offset_tilt,
            })
        return jsonify(payload)

    @app.route("/api/logs")
    def api_logs():
        return jsonify(list(ctx["log_buffer"]))

    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        with open(ctx["profile_path"], "r") as f:
            profile = json.load(f)
        profile.pop("ui", None)  # don't ship the edit code to the browser
        return jsonify(profile)

    @app.route("/api/settings", methods=["POST"])
    def api_save_settings():
        body = request.get_json(force=True, silent=True) or {}
        code = str(body.get("code", ""))
        data = body.get("data", {})

        with open(ctx["profile_path"], "r") as f:
            profile = json.load(f)

        expected_code = str(profile.get("ui", {}).get("edit_code", ""))
        if not expected_code or code != expected_code:
            return jsonify({"ok": False, "error": "Incorrect code."}), 403

        filtered = {}
        for section in EDITABLE_SECTIONS:
            if isinstance(data.get(section), dict):
                filtered[section] = data[section]
        for key in EDITABLE_TOP_LEVEL:
            if key in data:
                filtered[key] = data[key]

        deep_update(profile, filtered)

        with open(ctx["profile_path"], "w") as f:
            json.dump(profile, f, indent=4)

        ctx["log"]("Settings updated via web UI.")
        return jsonify({"ok": True, "note": "Saved. Restart follow_spot.py to apply the changes."})

    @app.route("/api/restart", methods=["POST"])
    def api_restart():
        body = request.get_json(force=True, silent=True) or {}
        code = str(body.get("code", ""))

        with open(ctx["profile_path"], "r") as f:
            profile = json.load(f)
        expected_code = str(profile.get("ui", {}).get("edit_code", ""))
        if not expected_code or code != expected_code:
            return jsonify({"ok": False, "error": "Incorrect code."}), 403

        ctx["log"]("Restart requested via web UI.")
        ctx["restart_event"].set()
        return jsonify({"ok": True, "note": "Restarting..."})

    return app


def run_ui_server(ctx):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app = create_app(ctx)
    app.run(host="0.0.0.0", port=ctx["port"], debug=False, use_reloader=False)
