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

from hub import integrations, lenses, sop_parser

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
LENSES_DIR = Path(os.getenv("CEC_HUB_LENSES_DIR", str(BASE_DIR / "lenses")))


def _load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _config_json(filename: str, fallback):
    """Per-machine config if it exists, else the committed *.example.json
    template. config/*.json is per-machine and git-ignored (paths and the
    referral link token differ per machine), so a fresh clone — and the
    tests — fall back to the shipped example instead of showing nothing."""
    real = CONFIG_DIR / filename
    if real.is_file():
        return _load_json(real, fallback)
    stem, _, ext = filename.rpartition(".")
    return _load_json(CONFIG_DIR / f"{stem}.example.{ext}", fallback)


def merge_tiles(real, example):
    """This machine's tiles, plus any tile in the shipped template that this
    machine doesn't have yet (matched by id). So a new tile (e.g. the Lens
    Finder) rolls out on a git pull without hand-editing every machine's
    config, while each machine keeps its own tile order and custom links."""
    if not isinstance(real, dict) or "tiles" not in real:
        return example
    tiles = list(real.get("tiles", []))
    have = {t.get("id") for t in tiles if isinstance(t, dict)}
    for t in (example or {}).get("tiles", []):
        if isinstance(t, dict) and t.get("id") not in have:
            tiles.append(t)
    return {**real, "tiles": tiles}


def _integrations() -> dict:
    # Read fresh on every request so path edits apply without a restart.
    return integrations.load_integrations(INTEGRATIONS_PATH)


def _staff_name() -> str:
    """Name from the optional who-is-this cookie, for audit lines only."""
    name = (request.cookies.get("hub_staff") or "").strip()
    return name[:60] if name else "someone (no name picked)"


def _catalog() -> dict:
    """The lens catalogue, narrowed to what this machine dispenses.

    Read fresh on every request (like the price files themselves), so an edit
    to config/lens_filter.json takes effect on the next page load. The filter
    config is per-machine and git-ignored — see config/lens_filter.example.json."""
    catalog = lenses.load_catalog(LENSES_DIR)
    return lenses.apply_lens_filter(catalog, _config_json("lens_filter.json", {}))


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
    real = _load_json(CONFIG_DIR / "tiles.json", None)
    example = _load_json(CONFIG_DIR / "tiles.example.json", {"tiles": []})
    return jsonify(merge_tiles(real, example))


@app.route("/api/staff")
def staff():
    return jsonify(_config_json("staff.json", {"staff": []}))


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


# --- Reviews / Stock ------------------------------------------------------

@app.route("/api/reviews/status")
def reviews_status():
    return jsonify(integrations.reviews_status(_integrations()))


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


# --- Lenses ----------------------------------------------------------------

@app.route("/api/lenses")
def lenses_catalog():
    return jsonify(_catalog())


@app.route("/api/lenses/find")
def lenses_find():
    def _arg(name):
        raw = (request.args.get(name) or "").strip()
        return lenses.parse_number(raw) if raw else None

    sph = _arg("sph")
    if sph is None or not -30 <= sph <= 30:
        return jsonify({"error": "Type the sphere power first — "
                                 "e.g. -2.75 or +1.50."}), 400
    cyl = _arg("cyl")
    if (request.args.get("cyl") or "").strip() and cyl is None:
        return jsonify({"error": "That cyl doesn't look like a number — "
                                 "e.g. -1.25 (or leave it empty)."}), 400
    if cyl is not None and abs(cyl) > 10:
        return jsonify({"error": "That cyl looks too big — check it against "
                                 "the Rx."}), 400
    min_blank = _arg("blank")
    if (request.args.get("blank") or "").strip() and (
            min_blank is None or not 40 <= min_blank <= 90):
        return jsonify({"error": "Blank size should be in millimetres, "
                                 "e.g. 68 (or leave it empty)."}), 400

    catalog = _catalog()
    result = lenses.find_options(lenses.sv_only(catalog["lenses"]), sph,
                                 cyl or 0.0, min_blank)
    result["catalog_message"] = catalog["message"]
    return jsonify(result)


@app.route("/api/lenses/check", methods=["POST"])
def lenses_check():
    """Order-screen check: both eyes' Rx (+ blank size or frame numbers,
    + optionally what was chosen) -> plain-words verdict. This is the
    contract the Optomate agent and any future helper call."""
    data = request.get_json(silent=True) or {}
    right, left = data.get("right") or {}, data.get("left") or {}
    if lenses.parse_number((right or {}).get("sph")) is None and \
            lenses.parse_number((left or {}).get("sph")) is None:
        return jsonify({"error": "Send at least one eye with a sphere, "
                                 "e.g. {\"right\": {\"sph\": -2.75}}."}), 400
    min_blank = lenses.parse_number(data.get("min_blank"))
    if min_blank is None:
        min_blank = lenses.min_blank_from_frame(data.get("frame") or {})
    if min_blank is not None and not 40 <= min_blank <= 90:
        min_blank = None

    catalog = _catalog()
    result = lenses.check_job(lenses.sv_only(catalog["lenses"]), right, left,
                              min_blank, data.get("chosen") or {})
    result["catalog_message"] = catalog["message"]
    return jsonify(result)


@app.route("/api/lenses/jobs")
def lenses_jobs():
    """Recent Optomate spectacle jobs (from the agent's lens-jobs.jsonl),
    each checked against the loaded price files."""
    data = integrations.lens_jobs(_integrations())
    if not data["connected"]:
        return jsonify(data)
    catalog = _catalog()
    jobs = []
    for job in data["jobs"]:
        min_blank = lenses.parse_number(job.get("min_blank"))
        if min_blank is None:
            min_blank = lenses.min_blank_from_frame(job.get("frame") or {})
        check = lenses.check_job(
            lenses.sv_only(catalog["lenses"]),
            job.get("right") or {}, job.get("left") or {},
            min_blank,
            {"code": job.get("code"), "type": job.get("stk_grd")},
        )
        jobs.append({
            "job": str(job.get("job") or ""),
            "entered": str(job.get("entered") or ""),
            "supplier": str(job.get("supplier") or ""),
            "code": str(job.get("code") or ""),
            "stk_grd": str(job.get("stk_grd") or ""),
            "check": check,
        })
    return jsonify({"connected": True, "updated": data["updated"],
                    "jobs": jobs, "catalog_message": catalog["message"]})


@app.route("/api/lenses/upload", methods=["POST"])
def lenses_upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "Pick a CSV file first."}), 400
    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "That isn't a CSV file. Save the price list "
                                 "as CSV first (lenses\\README.md shows the "
                                 "layout)."}), 400
    raw = file.read(lenses.MAX_UPLOAD_BYTES + 1)
    if len(raw) > lenses.MAX_UPLOAD_BYTES:
        return jsonify({"error": "That file is too big for a price list — "
                                 "it should be well under 2 MB."}), 400
    text = raw.decode("utf-8-sig", errors="replace")

    name = (request.form.get("name") or "").strip() or file.filename
    ok, payload = lenses.save_upload(LENSES_DIR, name, text)
    if not ok:
        return jsonify({"error": payload["error"]}), payload["status"]

    who = _staff_name()
    logger.info(f"Lens price file '{payload['filename']}' uploaded by {who} "
                f"({payload['count']} lenses"
                f"{', replaced the old file' if payload['replaced'] else ''})")
    return jsonify({
        "success": True,
        "filename": payload["filename"],
        "count": payload["count"],
        "replaced": payload["replaced"],
        "row_errors": payload["errors"],
        "message": (f"Loaded {payload['count']} lenses from "
                    f"{payload['filename']}."
                    + (" It replaced the old file with the same name."
                       if payload["replaced"] else "")),
    })


if __name__ == "__main__":
    # 0.0.0.0 = reachable from other practice PCs (http://<this-machine>:5680).
    # Set CEC_HUB_HOST=127.0.0.1 in the environment to go back to this-machine-only.
    app.run(host=os.environ.get("CEC_HUB_HOST", "0.0.0.0"), port=5680, debug=False)
