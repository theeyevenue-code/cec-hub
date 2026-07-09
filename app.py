"""CEC Hub — Concord Eyecare's staff home screen. Flask app on port 5680.

Big tiles, big text, plain words. No accounts, no patient data — Optomate
stays the system of record; this is the procedures-and-buttons layer.

Everything the Hub knows about OTHER systems comes from
config\\integrations.json, and every one of those connections degrades
gracefully when the files aren't on this machine.
"""

import json
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from hub import integrations, sop_parser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "hub.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("cec-hub")

app = Flask(__name__, static_folder="static", static_url_path="/")

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"

# Tests (and unusual machines) can repoint these with environment variables.
SOPS_DIR = Path(os.getenv("CEC_HUB_SOPS_DIR", str(BASE_DIR / "sops")))
INTEGRATIONS_PATH = Path(
    os.getenv("CEC_HUB_INTEGRATIONS", str(CONFIG_DIR / "integrations.json"))
)


def _load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _integrations() -> dict:
    # Read fresh on every request so path edits apply without a restart.
    return integrations.load_integrations(INTEGRATIONS_PATH)


def _staff_name() -> str:
    """Name from the optional who-is-this cookie, for audit lines only."""
    name = (request.cookies.get("hub_staff") or "").strip()
    return name[:60] if name else "someone (no name picked)"


# --- Pages -------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/sop-images/<path:filename>")
def sop_image(filename):
    # send_from_directory refuses anything outside the folder.
    return send_from_directory(SOPS_DIR / "images", filename)


# --- Home tiles + staff ------------------------------------------------------

@app.route("/api/tiles")
def tiles():
    data = _load_json(CONFIG_DIR / "tiles.json", {"tiles": []})
    return jsonify(data)


@app.route("/api/staff")
def staff():
    data = _load_json(CONFIG_DIR / "staff.json", {"staff": []})
    return jsonify(data)


# --- SOPs ----------------------------------------------------------------

@app.route("/api/sops")
def sops_list():
    return jsonify({"sops": sop_parser.list_sops(SOPS_DIR)})


@app.route("/api/sops/<slug>")
def sop_detail(slug):
    parsed = sop_parser.load_sop(SOPS_DIR, slug)
    if parsed is None:
        return jsonify({"error": "That guide isn't here. Go back to the "
                                 "guide list and pick it again."}), 404
    return jsonify(parsed)


@app.route("/api/sop-search")
def sop_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Type something to search for."}), 400
    return jsonify({"results": sop_parser.search_sops(SOPS_DIR, query)})


# --- Reviews / Orders / Stock ---------------------------------------------

@app.route("/api/reviews/status")
def reviews_status():
    return jsonify(integrations.reviews_status(_integrations()))


@app.route("/api/orders/digest")
def orders_digest():
    return jsonify(integrations.orders_digest(_integrations()))


@app.route("/api/stock/proposals")
def stock_proposals():
    return jsonify(integrations.stock_proposals(_integrations()))


@app.route("/api/stock/approve", methods=["POST"])
def stock_approve():
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    ok, payload = integrations.approve_proposal(_integrations(), filename)
    if not ok:
        return jsonify({"error": payload["error"]}), payload["status"]

    who = _staff_name()
    logger.info(f"Stock proposal '{filename}' approved by {who} "
                f"-> {payload['new_filename']}")
    return jsonify({
        "success": True,
        "new_filename": payload["new_filename"],
        "message": "Approved. Mark/Claude will enter this into Optomate — "
                   "nothing is ordered automatically.",
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5680, debug=False)
