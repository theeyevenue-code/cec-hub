"""CEC Hub — Concord Eyecare's staff home screen. Flask app on port 5680.

Big tiles, big text, plain words. No accounts. Optomate stays the system of
record; this is the procedures-and-buttons layer. The Check-in tile shows
patient details on screen (and the reception iPad) but stores the form only in
the Optomate agent's git-ignored local-reports\\checkin\\ folder and never
logs it — see hub/checkin.py.

Everything the Hub knows about OTHER systems comes from
config\\integrations.json, and every one of those connections degrades
gracefully when the files aren't on this machine.
"""

import json
import html
import functools
import ipaddress
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_from_directory

from hub import checkin, integrations, jobscan, lenses, lensoptions, sendguide, sop_parser
from hub import lenschat, workqueue
from hub.assistant import core as assistant, registry as assistant_registry
from hub.assistant.adapters import ReadContext

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
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024   # an iPad form (no signature) is well under this

BASE_DIR = Path(__file__).parent
IPAD_DIR = BASE_DIR / "ipad"                       # the iPad page's own files (no Hub links)
EXAMPLE_CONFIG_DIR = BASE_DIR / "config"          # the committed *.example.json templates
CONFIG_DIR = Path(os.getenv("CEC_HUB_CONFIG_DIR", str(EXAMPLE_CONFIG_DIR)))

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
    return _load_json(EXAMPLE_CONFIG_DIR / f"{stem}.example.{ext}", fallback)


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


def _pricing() -> dict:
    """Promo date, stock-first rule, level multipliers (config/lens_pricing.json),
    plus the everyday-lens names from lens_filter.json as the finder's tie-breaker."""
    pricing = dict(_config_json("lens_pricing.json", {}) or {})
    pref = (_config_json("lens_filter.json", {}) or {}).get("preferred") or {}
    pricing.setdefault("preferred_names", pref.get("match") or [])
    return pricing


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
    from hub import lists
    real = _load_json(CONFIG_DIR / "tiles.json", None)
    example = _load_json(CONFIG_DIR / "tiles.example.json", {"tiles": []})
    merged = merge_tiles(real, example)
    merged["tiles"] = lists.apply_layout(merged.get("tiles") or [])
    merged["hidden"] = lists.layout_get()["hidden"]
    return jsonify(merged)


@app.route("/api/tilelayout", methods=["POST"])
def tilelayout():
    from hub import lists
    body = request.get_json(silent=True) or {}
    lists.layout_save(body.get("order") or [], body.get("hidden") or [])
    return jsonify({"ok": True})


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


@app.route("/api/invoices/status")
def invoices_status():
    return jsonify(integrations.invoice_status(_integrations()))


@app.route("/api/revenue/status")
def revenue_status():
    return jsonify(integrations.revenue_status(_integrations()))


@app.route("/api/letters/status")
def letters_status():
    return jsonify(integrations.letters_status(_integrations()))


@app.route("/api/tasks", methods=["GET", "POST"])
def tasks():
    from hub import lists
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        ok, err = lists.tasks_mutate(str(body.get("action") or ""), body, _staff_name())
        if not ok:
            return jsonify({"error": err}), 400
    return jsonify({"tasks": lists.tasks_list()})


@app.route("/api/credits", methods=["GET", "POST"])
def credits():
    from hub import lists
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        ok, err = lists.credits_mutate(_integrations(), str(body.get("action") or ""),
                                       body, _staff_name())
        if not ok:
            return jsonify({"error": err}), 400
    return jsonify({"credits": lists.credits_list(_integrations()),
                    "suppliers": lists.CREDIT_SUPPLIERS})


@app.route("/api/order-check")
def order_check():
    from hub import ordercheck
    order = request.args.get("order", "")
    return jsonify(ordercheck.check(_integrations(), order))


@app.route("/api/recall/preview", methods=["POST"])
def recall_preview():
    """Build a recall batch preview for one month + touch: full patient list,
    exact messages, tick-box state. Practice-network only, like every other Hub
    page. Nothing can be sent from here."""
    from hub import recall
    data = request.get_json(silent=True) or {}
    result = recall.preview(_integrations(), data.get("month", ""),
                            data.get("touch", "T0"), data.get("filters"))
    # Deliberately not logging patients — the log is not the place for PHI.
    return jsonify(result)


@app.route("/api/recall/deselect", methods=["POST"])
def recall_deselect():
    """Remember which patients Mark un-ticked for one batch (stored with the
    agent so the eventual sender honours it)."""
    from hub import recall
    data = request.get_json(silent=True) or {}
    result = recall.set_deselected(_integrations(), data.get("month", ""),
                                   data.get("touch", "T0"),
                                   data.get("pids", []))
    if result.get("ok"):
        # Who reviewed the batch, and how many they held back — no patient data.
        logger.info(f"Recall batch {data.get('month')}/{data.get('touch')}: "
                    f"{result['deselected']} patient(s) un-ticked by {_staff_name()}")
    status = 400 if result.get("error") else 200
    return jsonify(result), status


@app.route("/api/recall/send", methods=["POST"])
def recall_send():
    """Send the reviewed recall batch. Layers of protection: the agent's config
    gates (off = dry run, nothing sends), the batch-hash check (list changed =
    refuse), the safety cap, and the crash journal. Who pressed it is logged."""
    from hub import recall
    data = request.get_json(silent=True) or {}
    result = recall.send(_integrations(), data.get("month", ""),
                         data.get("touch", "T0"), data.get("hash", ""),
                         data.get("limit"), data.get("filters"))
    if result.get("sent"):
        logger.info(f"RECALL SEND by {_staff_name()}: {data.get('month')}/"
                    f"{data.get('touch')} -> {result.get('sent')} sent, "
                    f"{result.get('failed', 0)} failed")
    status = 400 if result.get("error") else 200
    return jsonify(result), status


@app.route("/api/fundsms/preview", methods=["POST"])
def fundsms_preview():
    """Health-fund texts: the full list for HF1 (Nov) or HF2 (Dec). Sends nothing."""
    from hub import fundsms
    data = request.get_json(silent=True) or {}
    return jsonify(fundsms.preview(_integrations(), data.get("touch", "HF1"), data.get("filters")))


@app.route("/api/fundsms/deselect", methods=["POST"])
def fundsms_deselect():
    from hub import fundsms
    data = request.get_json(silent=True) or {}
    result = fundsms.set_deselected(_integrations(), data.get("touch", ""), data.get("year"),
                                    data.get("pids", []))
    if result.get("ok"):
        logger.info(f"Health-fund texts {data.get('touch')}: {result['deselected']} "
                    f"patient(s) un-ticked by {_staff_name()}")
    return jsonify(result), (400 if result.get("error") else 200)


@app.route("/api/fundsms/send", methods=["POST"])
def fundsms_send():
    """Send the reviewed health-fund list. The agent's own gate (off = dry run),
    the list fingerprint, the cap, the pilot and the crash journal all apply."""
    from hub import fundsms
    data = request.get_json(silent=True) or {}
    result = fundsms.send(_integrations(), data.get("touch", ""), data.get("hash", ""),
                          data.get("limit"), data.get("filters"))
    if result.get("sent"):
        logger.info(f"HEALTH-FUND SEND by {_staff_name()}: {data.get('touch')} -> "
                    f"{result.get('sent')} sent, {result.get('failed', 0)} failed")
    return jsonify(result), (400 if result.get("error") else 200)


@app.route("/api/contactlog/search", methods=["POST"])
def contactlog_search():
    """Who got what: find a patient by name. POST so the name never sits in a URL
    or a log line; nothing about the patient is logged; read-only."""
    from hub import contactlog
    data = request.get_json(silent=True) or {}
    return _no_store(jsonify(contactlog.search(_integrations(), data.get("q", ""))))


@app.route("/api/contactlog/timeline", methods=["POST"])
def contactlog_timeline():
    """Who got what: every text and email one patient got, newest first. Read-only."""
    from hub import contactlog
    data = request.get_json(silent=True) or {}
    return _no_store(jsonify(contactlog.timeline(_integrations(), data.get("pid"))))


@app.route("/api/recall/success")
def recall_success():
    """How many texted patients went on to book (optionally ?months=N)."""
    from hub import recall
    return jsonify(recall.success(_integrations(), request.args.get("months")))


@app.route("/api/recall/history")
def recall_history():
    """What's been sent — by month and by patient (the dedup log, visible)."""
    from hub import recall
    return jsonify(recall.history(_integrations()))


@app.route("/recall-chase-sheet")
def recall_chase_sheet():
    """Serve the phone recall list (optionally ?month=YYYY-MM). PHI —
    practice network only, like the rest of the Hub."""
    from hub import recall
    path = recall.chase_sheet_file(_integrations(), request.args.get("month", ""))
    if path is None:
        return ("That phone list hasn't been made yet — press "
                "\"Make the list\" first.", 404)
    return send_from_directory(path.parent, path.name)


@app.route("/api/recall/chasesheet", methods=["POST"])
def recall_chase_refresh():
    """Regenerate the phone recall list, by month when given (read-only
    against Optomate; writes a local file). Slow (~1 min) — the UI says so."""
    from hub import recall
    data = request.get_json(silent=True) or {}
    result = recall.refresh_chase_sheet(_integrations(), data.get("month", ""))
    if result.get("ok"):
        logger.info(f"Phone recall list ({data.get('month') or 'rolling'}) "
                    f"regenerated by {_staff_name()}: "
                    f"{result.get('patients')} patients")
    status = 400 if result.get("error") else 200
    return jsonify(result), status


@app.route("/recall-preview/<month>/<touch>")
def recall_preview_file(month, touch):
    """Serve the generated recall patient list (PHI — practice network only, same
    as the rest of the Hub). Path is rebuilt from a validated month+touch, never
    from anything the caller supplies."""
    from hub import recall
    path = recall.preview_file(_integrations(), month, touch)
    if path is None:
        return ("That preview hasn't been made yet — press "
                "\"Show me who would get a text\" first.", 404)
    return send_from_directory(path.parent, path.name)


# --- Check-in (iPad registration form) ---------------------------------------
# Routes only; the logic is hub/checkin.py. Patient details pass through these
# responses to staff screens and the iPad, never into hub.log: nothing below
# logs a request body, and names travel in POST bodies, never in a URL.

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _json(status_and_body):
    status, body = status_and_body
    return _no_store(jsonify(body)), status


# Staff guard (Codex #1, 9 Oct 2026). The device key opens the iPad routes and
# nothing else; every staff route below needs ONE of:
#   - the request comes from the server itself (loopback), or
#   - the browser was unlocked once with the staff code (checkin.staff_code in
#     config\integrations.json) and carries the HttpOnly cookie that sets.
# A request carrying the device key is refused first, whatever else it has, so
# the iPad can never be a staff screen. A patient who gets out of Guided Access
# can reach the Hub's address, but not the staff code.

def _from_this_computer() -> bool:
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _staff_refusal():
    """None when this request may use the staff Check-in routes, else the
    refusal response (403, plain words, no-store)."""
    if request.headers.get("X-Checkin-Key"):
        logger.warning("Check-in: staff route refused - request carried the iPad device key")
        return _no_store(jsonify({"error": "Not from the iPad.", "ipad": True})), 403
    if _from_this_computer():
        return None
    cfg = _integrations()
    if checkin.staff_cookie_ok(cfg, request.cookies.get(checkin.STAFF_COOKIE)):
        return None
    return _no_store(jsonify({
        "staff_needed": True, "code_set": bool(checkin.staff_code(cfg)),
        "error": "Check-in is locked on this computer. Type the staff code to unlock it."})), 403


def staff_only(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        refused = _staff_refusal()
        if refused is not None:
            return refused
        return view(*args, **kwargs)
    return wrapped


# --- Work queue ("To do" + "Waiting on Mark", 5 Nov 2026) --------------------------
# Same staff guard as Check-in: items can name patients, so a computer other than the server
# unlocks once with the staff code. Item text is never logged (counts and who-did-what only).
# Recording a Yes / Not now only WRITES THE ANSWER into the list. No route here sends, merges,
# runs a command or touches Optomate; the session that asked reads the answer and acts itself.

def _wq_state():
    state = workqueue.view()
    state["me"] = (request.cookies.get("hub_staff") or "").strip()[:60]
    return state


@app.route("/api/workqueue", methods=["GET", "POST"])
@staff_only
def work_queue():
    try:
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            action, who = str(body.get("action") or ""), _staff_name()
            if action == "add":
                workqueue.add_item(text=body.get("text"), owner=body.get("owner") or "Unassigned",
                                   source=body.get("source") or "", added_by=who)
            elif action == "done":
                workqueue.mark_done(body.get("id"), who)
            elif action == "reopen":
                workqueue.reopen(body.get("id"), who)
            elif action in ("yes", "not_now"):
                # Only Mark's answer counts. The who-is-this picker is a name, not a login,
                # so this stops a slip by staff; it is not security.
                if (request.cookies.get("hub_staff") or "").strip() != "Mark":
                    return _no_store(jsonify({"error": "Only Mark can answer these. Pick Mark in "
                                                       "'Who's using this?' at the top."})), 403
                workqueue.record_answer(body.get("id"), action, "Mark")
                logger.info("Work queue: Mark answered %s on a question (recorded only)", action)
            elif action == "undo_answer":
                if (request.cookies.get("hub_staff") or "").strip() != "Mark":
                    return _no_store(jsonify({"error": "Only Mark can change his answers."})), 403
                workqueue.undo_answer(body.get("id"), "Mark")
            else:
                return _no_store(jsonify({"error": "Unknown action."})), 400
        return _no_store(jsonify(_wq_state()))
    except workqueue.WorkQueueError as e:
        return _no_store(jsonify({"error": str(e)})), 400


@app.route("/api/checkin/staff/unlock", methods=["POST"])
def checkin_staff_unlock():
    """Unlock Check-in on this front-desk computer with the staff code. Sets an
    HttpOnly cookie (no patient data in it); changing the code locks every
    computer out again."""
    if request.headers.get("X-Checkin-Key"):
        return _no_store(jsonify({"error": "Not from the iPad.", "ipad": True})), 403
    cfg = _integrations()
    data = request.get_json(silent=True) or {}
    ok, why = checkin.staff_code_ok(cfg, data.get("code", ""))
    if not ok:
        return _no_store(jsonify({"error": why})), 403
    resp = _no_store(jsonify({"ok": True}))
    resp.set_cookie(checkin.STAFF_COOKIE, checkin.staff_cookie_value(cfg),
                    max_age=checkin.STAFF_COOKIE_DAYS * 86400, httponly=True, samesite="Strict",
                    path="/")
    logger.info("Check-in: a computer was unlocked with the staff code")
    return resp


@app.route("/api/checkin/today")
@staff_only
def checkin_today():
    return _no_store(jsonify(checkin.today(_integrations())))


@app.route("/api/checkin/search", methods=["POST"])
@staff_only
def checkin_search():
    data = request.get_json(silent=True) or {}
    out = checkin.search(_integrations(), data.get("q", ""))
    return _no_store(jsonify(out)), (400 if out.get("error") else 200)


@app.route("/api/checkin/send", methods=["POST"])
@staff_only
def checkin_send():
    return _json(checkin.send(_integrations(), request.get_json(silent=True) or {}))


@app.route("/api/checkin/session/<token>")
@staff_only
def checkin_session(token):
    return _json(checkin.detail(_integrations(), token))


@app.route("/api/checkin/session/<token>/override", methods=["POST"])
@staff_only
def checkin_override(token):
    return _json(checkin.override(_integrations(), token, request.get_json(silent=True) or {}))


@app.route("/api/checkin/session/<token>/save", methods=["POST"])
@staff_only
def checkin_save(token):
    data = request.get_json(silent=True) or {}
    return _json(checkin.save(_integrations(), token, data.get("hash", ""), _staff_name()))


@app.route("/api/checkin/session/<token>/discard", methods=["POST"])
@staff_only
def checkin_discard(token):
    return _json(checkin.discard(_integrations(), token))


@app.route("/api/checkin/session/<token>/resume", methods=["POST"])
@staff_only
def checkin_resume(token):
    """A form the iPad locked (left untouched) goes back on the iPad."""
    return _json(checkin.resume(_integrations(), token))


# --- Send guide (hub/sendguide.py) -------------------------------------------
# Same staff guard as Check-in. Names and searches go in POST bodies, every
# answer is no-store, and only ids reach hub.log.

@app.route("/api/sendguide/setup")
@staff_only
def sendguide_setup():
    return _no_store(jsonify(sendguide.setup(_integrations())))


@app.route("/api/sendguide/today")
@staff_only
def sendguide_today():
    return _no_store(jsonify(sendguide.today(_integrations())))


@app.route("/api/sendguide/search", methods=["POST"])
@staff_only
def sendguide_search():
    data = request.get_json(silent=True) or {}
    out = sendguide.search(_integrations(), data.get("q", ""))
    return _no_store(jsonify(out)), (400 if out.get("error") else 200)


@app.route("/api/sendguide/review", methods=["POST"])
@staff_only
def sendguide_review():
    return _json(sendguide.review(_integrations(), request.get_json(silent=True) or {}))


@app.route("/api/sendguide/print/<guide_id>")
def sendguide_print(guide_id):
    """The approved guide PDF, inline, for the desk printer. Same staff lock as
    the other Send guide routes, but a locked or refused request gets a plain
    page (it opens in a new tab). Print-only items are served here and nowhere
    else; they can never be emailed."""
    def page(msg, status):
        body = ("<!doctype html><meta charset=utf-8><title>Print guide</title>"
                "<body style='font:20px Arial,sans-serif;max-width:560px;margin:40px auto;padding:0 16px'>"
                f"<p>{html.escape(msg)}</p><p>Close this tab and go back to the Hub.</p>")
        return _no_store(make_response(body, status))
    if _staff_refusal() is not None:
        return page("This computer is locked. Unlock Send guide in the Hub first, then press Print again.", 403)
    data, problem = sendguide.print_guide(_integrations(), guide_id)
    if data is None:
        return page(problem, 404)
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return _no_store(resp)


@app.route("/api/sendguide/send", methods=["POST"])
@staff_only
def sendguide_send():
    return _json(sendguide.send(_integrations(), request.get_json(silent=True) or {},
                                _staff_name()))


# --- Lens options (hub/lensoptions.py) ----------------------------------------
# Click a patient -> the lenses for their saved spectacle Rx (Mark, 3 Nov 2026).
# Same staff guard as Send guide; names in POST bodies only; no-store; nothing
# about a patient reaches hub.log.

def _sells() -> dict:
    products = lenses.group_products(_catalog()["lenses"], pricing=_pricing())
    products = lenses.mark_preferred(products, _config_json("lens_filter.json", {}))
    products = lenses.attach_cec_price(products, _config_json("cec_prices.json", {}))
    return lensoptions.sell_index(products)


@app.route("/api/lensoptions/recent")
@staff_only
def lensoptions_recent():
    return _no_store(jsonify(lensoptions.recent(_integrations())))


@app.route("/api/lensoptions/search", methods=["POST"])
@staff_only
def lensoptions_search():
    data = request.get_json(silent=True) or {}
    out = lensoptions.search(_integrations(), data.get("q", ""))
    return _no_store(jsonify(out)), (400 if out.get("error") else 200)


@app.route("/api/lensoptions/rx", methods=["POST"])
@staff_only
def lensoptions_rx():
    data = request.get_json(silent=True) or {}
    catalog = _catalog()
    if catalog.get("message"):
        return _no_store(jsonify({"error": catalog["message"]})), 503
    rows = [l for l in catalog["lenses"] if l.get("orderable", True)]
    frame = data.get("frame") if isinstance(data.get("frame"), dict) else None
    out = lensoptions.options(_integrations(), data.get("patient_id"), rows,
                              _pricing(), _sells(), frame)
    return _no_store(jsonify(out)), (400 if out.get("error") else 200)


# --- Compare (hub/compare.py) - no AI; the Lens Finder box's chat is below ------
# Compare: no AI, the same engine the chat uses. Chat: Mark-only capped trial,
# off unless config/lenschat.json says enabled AND a key is present. Patient
# ids in POST bodies only; hub.log gets counts and reason codes only.

FILTER_KEYS = ("feature", "colour", "index", "coating", "type")


def _compare_inputs(data: dict) -> dict:
    """{"req", "binding", "given", "surname"} or {"error"}. A picked patient
    (patient_id + rx + part) is read locally; otherwise typed numbers."""
    if data.get("patient_id"):
        got = lensoptions.compare_request(_integrations(), data.get("patient_id"),
                                          data.get("rx", 0), str(data.get("part") or ""),
                                          data.get("frame") if isinstance(data.get("frame"), dict) else None)
        if got.get("error"):
            return got
        binding = f"p:{int(data['patient_id'])}:{int(data.get('rx') or 0)}:{data.get('part')}"
        return {**got, "binding": binding}
    typed = data.get("typed") if isinstance(data.get("typed"), dict) else {}
    req = {k: typed.get(k) for k in ("right", "left", "kind", "add", "min_blank")}
    return {"req": req, "given": "", "surname": "",
            "binding": "t:" + json.dumps(req, sort_keys=True, default=str)}


def _filters(data: dict) -> dict:
    f = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    return {k: str(f.get(k) or "any") for k in FILTER_KEYS}


def _run_compare(req: dict, filters: dict):
    from hub import compare
    q, why = compare.clean_request({**req, **filters})
    if q is None:
        return None, why
    catalog = _catalog()
    return compare.compare(catalog["lenses"], q, _pricing(), _sells()), ""


@app.route("/api/lenscompare", methods=["POST"])
@staff_only
def lens_compare():
    from hub import compare
    data = request.get_json(silent=True) or {}
    got = _compare_inputs(data)
    if got.get("error"):
        return _no_store(jsonify({"error": got["error"]})), 400
    res, why = _run_compare(got["req"], _filters(data))
    if res is None:
        return _no_store(jsonify({"error": why})), 400
    logger.info("Compare: %d matching, %d verified", res["counts"]["matching"],
                res["counts"]["verified"])
    return _no_store(jsonify(compare.public(res)))


# --- Hub assistant (hub/assistant/) -------------------------------------------
# One endpoint for the home-page chat box AND the Lens Finder box (Mark, 3 Nov
# 2026; Codex 2026-11-03-hub-assistant, phase 1a). Mark-only, off unless
# config/lenschat.json says enabled AND a key is present. The tool registry is
# checked once here at start-up and again on every tool call; config/assistant.json
# can only switch a tool off or narrow it. Read adapters only: the assistant is
# handed lens data and (for the Lens Finder box) the comparison bound to the Rx
# the page picked - no Optomate config, no write route, no credentials.
# The old /api/lenschat/* URLs are the same handlers (a tab opened before the
# update keeps working).

ASSISTANT_REG = assistant_registry.build(_config_json("assistant.json", {}))


def _assistant_cfgs():
    return _config_json("lenschat.json", {}), _config_json("assistant.json", {})


def _chat_status():
    chat_cfg, asst_cfg = _assistant_cfgs()
    return assistant.status(chat_cfg, ASSISTANT_REG, request.remote_addr or "",
                            _from_this_computer(), asst_cfg=asst_cfg)


@app.route("/api/assistant/status")
@app.route("/api/lenschat/status")
@staff_only
def assistant_status():
    return _no_store(jsonify(_chat_status()))


@app.route("/api/assistant/ask", methods=["POST"])
@app.route("/api/lenschat/ask", methods=["POST"])
@staff_only
def assistant_ask():
    st = _chat_status()
    if not st.get("available"):
        return _no_store(jsonify({"error": st.get("why") or "The assistant is off.",
                                  "cap_reached": bool(st.get("cap_reached"))})), 403
    data = request.get_json(silent=True) or {}
    engine, base, binding, given, surname = None, None, "home", "", ""
    if data.get("patient_id") or isinstance(data.get("typed"), dict):
        got = _compare_inputs(data)
        if got.get("error"):
            return _no_store(jsonify({"error": got["error"]})), 400
        base = _filters(data)
        probe, why = _run_compare(got["req"], base)
        if probe is None:
            return _no_store(jsonify({"error": why})), 400

        def engine(filters):
            return _run_compare(got["req"], filters)[0]
        binding, given, surname = got["binding"], got.get("given", ""), got.get("surname", "")
    catalog = _catalog()
    ctx = ReadContext(rows=catalog.get("lenses") or [], pricing=_pricing(),
                      cec_prices=_config_json("cec_prices.json", {}), engine=engine,
                      base_filters=base)
    chat_cfg, asst_cfg = _assistant_cfgs()
    names = lenschat.name_words(_integrations())
    out = assistant.ask(chat_cfg, asst_cfg, ASSISTANT_REG, binding=binding,
                        token=data.get("session"), question=data.get("question"), ctx=ctx,
                        names=names, given=given, surname=surname,
                        audience=assistant.user_audience(chat_cfg, request.remote_addr or "",
                                                         _from_this_computer()))
    return _no_store(jsonify(out))


# --- Jobs arrived (hub/jobscan.py) --------------------------------------------
# Scan job cards, confirm once -> received in Optomate + "glasses ready" text, as far
# as the agent's orders\jobscan_config.json switches allow (Mark, 3 Nov 2026). Same
# staff guard; job numbers in POST bodies; no-store; hub.log gets counts only.

@app.route("/api/jobscan/status")
@staff_only
def jobscan_status():
    return _no_store(jsonify(jobscan.status(_integrations())))


@app.route("/api/jobscan/lookup", methods=["POST"])
@staff_only
def jobscan_lookup():
    data = request.get_json(silent=True) or {}
    out = jobscan.lookup(_integrations(), data.get("ids"))
    return _no_store(jsonify(out)), (200 if out.get("ok") else 400)


@app.route("/api/jobscan/preview", methods=["POST"])
@staff_only
def jobscan_preview():
    data = request.get_json(silent=True) or {}
    out = jobscan.preview(_integrations(), data.get("ids"), data.get("own"))
    return _no_store(jsonify(out)), (200 if out.get("ok") else 400)


@app.route("/api/jobscan/confirm", methods=["POST"])
@staff_only
def jobscan_confirm():
    data = request.get_json(silent=True) or {}
    out = jobscan.confirm(_integrations(), data.get("ids"), data.get("expected"), data.get("own"))
    if out.get("ok"):
        c = out.get("counts") or {}
        logger.info("Jobs arrived: confirm %s - done %d, would %d, skipped %d, failed %d, texted %d",
                    "TEST MODE" if out.get("nothing_changed") else "live",
                    c.get("done", 0), c.get("would", 0), c.get("skipped", 0), c.get("failed", 0),
                    int(out.get("texted") or 0))
    else:
        logger.info("Jobs arrived: confirm not done (%s)",
                    "timeout" if out.get("timeout") else "refused")
    return _no_store(jsonify(out)), (200 if out.get("ok") else 400)


# iPad: its own page and files, every API call needs the device key header.

@app.route("/checkin/ipad")
def checkin_ipad_page():
    return _no_store(send_from_directory(IPAD_DIR, "index.html", max_age=0))


@app.route("/checkin/ipad/<name>")
def checkin_ipad_file(name):
    if name not in ("ipad.js", "ipad.css"):
        return ("Not found", 404)
    return _no_store(send_from_directory(IPAD_DIR, name, max_age=0))


def _ipad_cfg():
    """(cfg, None) when the request carries the right device key, else
    (cfg, error response)."""
    cfg = _integrations()
    if not checkin.ipad_key(cfg):
        return cfg, (_no_store(jsonify({"setup": False, "error": "Not set up yet"})), 503)
    if not checkin.key_ok(cfg, request.headers.get("X-Checkin-Key", "")):
        return cfg, (_no_store(jsonify({"setup": False, "error": "Wrong device key"})), 401)
    return cfg, None


@app.route("/api/checkin/ipad/current")
def checkin_ipad_current():
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _no_store(jsonify(checkin.ipad_current(cfg)))


@app.route("/api/checkin/ipad/self", methods=["POST"])
def checkin_ipad_self():
    """The patient checks themselves in: surname + date of birth (or mobile)
    matched to one of today's bookings (Mark, 13 Oct 2026, option A)."""
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _json(checkin.ipad_self(cfg, request.get_json(silent=True) or {}))


@app.route("/api/checkin/ipad/self/pick", methods=["POST"])
def checkin_ipad_self_pick():
    """A family on one mobile: which of them is checking in."""
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _json(checkin.ipad_pick(cfg, request.get_json(silent=True) or {}))


@app.route("/api/checkin/ipad/session/<token>")
def checkin_ipad_session(token):
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _json(checkin.ipad_session(cfg, token))


@app.route("/api/checkin/ipad/session/<token>/filling", methods=["POST"])
def checkin_ipad_filling(token):
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _json(checkin.ipad_filling(cfg, token))


@app.route("/api/checkin/ipad/session/<token>/draft", methods=["POST"])
def checkin_ipad_draft(token):
    """Answers so far (kept on the server, not the iPad); pause=true = the
    inactivity lock."""
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _json(checkin.ipad_draft(cfg, token, request.get_json(silent=True) or {}))


@app.route("/api/checkin/ipad/session/<token>/submit", methods=["POST"])
def checkin_ipad_submit(token):
    cfg, bad = _ipad_cfg()
    if bad:
        return bad
    return _json(checkin.ipad_submit(cfg, token, request.get_json(silent=True) or {}))


# --- Medicare rejections -------------------------------------------------------

def _medicare_fixture() -> bool:
    """A test copy shows fictional patients with CEC_HUB_MEDICARE_FIXTURE=1
    (environment, never the URL)."""
    return os.getenv("CEC_HUB_MEDICARE_FIXTURE", "").strip().lower() in ("1", "true", "yes")


@app.route("/api/medicare")
def medicare_list():
    """Rejected / short-paid Medicare items, read live from Optomate by the agent.
    Given name + surname initial only; served no-store; nothing about a patient
    is logged. Item review cards (note snippets) only when the name picked is Mark."""
    from hub import medicare
    who = request.cookies.get("hub_staff") or ""
    resp = jsonify(medicare.worklist(_integrations(), _medicare_fixture(), medicare.is_mark(who), viewer=who))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/medicare/mark", methods=["POST"])
def medicare_mark():
    """Tick one item: resubmitted, written_off, or open (undo).
    Mark only, with his 4-digit PIN: accepted_10913 / accepted_10914 / accepted_10915 /
    not_eligible, and undoing those. The PIN is passed to the agent on stdin and is
    never logged."""
    from hub import medicare
    data = request.get_json(silent=True) or {}
    who = (request.cookies.get("hub_staff") or "").strip()
    result = medicare.mark(_integrations(), data.get("key", ""), data.get("status", ""),
                           who, _medicare_fixture(), pin=str(data.get("pin") or ""),
                           claim_note=str(data.get("claim_note") or ""))
    if result.get("ok"):
        logger.info(f"Medicare item marked {result['status']} by {who[:60]}")
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store"
    return resp, (200 if result.get("ok") else 400)


@app.route("/api/medicare/tick", methods=["POST"])
def medicare_tick():
    """Karen's tick (Option C, Mark 13 Oct 2026): one line or a whole batch, each with the
    item she used. Body {lines: [{key, to}]}. No PIN; the agent logs suggested vs used."""
    from hub import medicare
    data = request.get_json(silent=True) or {}
    who = (request.cookies.get("hub_staff") or "").strip()
    result = medicare.tick(_integrations(), data.get("lines"), who, _medicare_fixture())
    if result.get("ok"):
        logger.info(f"Medicare: {result['n']} lines ticked by {who[:60]}")
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store"
    return resp, (200 if result.get("ok") else 400)


@app.route("/api/attention")
def attention():
    return jsonify(integrations.attention_summary(_integrations()))


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


# --- Frame stocktake (TEST MODE: counts only, nothing goes into Optomate) ----------
# Sessions live in data\stocktake\ (git-ignored): barcodes, areas, counts, staff first
# names. Frame details come from the agent's read-only catalogue (hub/stocktake.py).

def _st_cat(force=False):
    from hub import stocktake
    return stocktake.get_catalogue(_integrations(), force=force)


def _st_call(fn, *args):
    try:
        out = fn(*args)
    except (FileNotFoundError, ValueError):
        return _json((404, {"error": "That stocktake session isn't there any more. Go back and pick one."}))
    return _json((200 if out.get("ok") or "sessions" in out or "rows" in out else 400, out))


@app.route("/api/stocktake/sessions", methods=["GET", "POST"])
def stocktake_sessions():
    from hub import stocktake
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        return _st_call(stocktake.create_session, data.get("name"), request.cookies.get("hub_staff"))
    return _st_call(stocktake.list_sessions)


@app.route("/api/stocktake/session/<sid>")
def stocktake_session(sid):
    from hub import stocktake
    return _st_call(lambda: stocktake.session_view(sid, _st_cat()))


@app.route("/api/stocktake/session/<sid>/<action>", methods=["POST"])
def stocktake_action(sid, action):
    from hub import stocktake
    data = request.get_json(silent=True) or {}
    by = request.cookies.get("hub_staff")
    if action == "scan":
        return _st_call(stocktake.scan, sid, data.get("barcode"), data.get("area"), by, _st_cat)
    if action == "adjust":
        try:
            delta = int(data.get("delta"))
        except (TypeError, ValueError):
            delta = 0
        return _st_call(stocktake.adjust, sid, data.get("barcode"), data.get("area"), delta, by)
    if action == "undo":
        return _st_call(stocktake.undo, sid, by)
    if action == "unlabelled":
        return _st_call(stocktake.add_unlabelled, sid, data, by)
    if action == "unlabelled-remove":
        return _st_call(stocktake.remove_unlabelled, sid, str(data.get("id") or ""), by)
    return _json((404, {"error": "Unknown stocktake action."}))


@app.route("/api/stocktake/suppliers")
def stocktake_suppliers():
    """Optomate supplier codes + names for the unlabelled form's pick list."""
    cat = _st_cat()
    sups = sorted(({"id": k, "name": v} for k, v in (cat.get("suppliers") or {}).items()),
                  key=lambda s: s["id"])
    return _json((200, {"suppliers": sups}))


@app.route("/api/stocktake/refresh", methods=["POST"])
def stocktake_refresh():
    cat = _st_cat(force=True)
    return _json((200, {"ok": not cat.get("error"), "frames": len(cat["frames"]),
                        "loaded": cat.get("loaded"), "error": cat.get("error")}))


# --- Scanner card (read live from the Second Brain clone) -------------------

@app.route("/api/scanner-card")
@app.route("/api/scanner-card/<which>")
def scanner_card(which="card"):
    """The scanner pages ("card" = staff fixes, "settings" = the setting
    barcodes, open to everyone). Read from the Second Brain clone on EVERY request and sent
    no-store, so a `git pull` in that clone shows on the next refresh — no Hub
    restart, no copied file to drift (Mark: "make it so I can pull it from
    master")."""
    if which not in integrations.SCANNER_PAGES:
        return jsonify({"error": "There's no scanner page by that name. Go back "
                                 "to the Scanner page and try again."}), 404
    resp = jsonify(integrations.scanner_card(_integrations(), which))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# --- Lenses ----------------------------------------------------------------

@app.route("/api/lenses")
def lenses_catalog():
    # The library browses PRODUCTS (one row per lens+index+type, coatings and
    # blanks folded in), with the practice's day-to-day lenses floated to the
    # top. The finder keeps using the flat rows via /find.
    cat = _catalog()
    pricing = _pricing()
    products = lenses.group_products(cat["lenses"], pricing=pricing)
    products = lenses.mark_preferred(products, _config_json("lens_filter.json", {}))
    products = lenses.attach_cec_price(products, _config_json("cec_prices.json", {}))
    return jsonify({"products": products,
                    "files": cat["files"], "message": cat["message"],
                    "pricing": {"promo_until": pricing.get("promo_until", ""),
                                "prefer_stock": bool(pricing.get("prefer_stock", True))}})


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
    add = _arg("add")
    if (request.args.get("add") or "").strip() and (add is None or not 0 < add <= 6):
        return jsonify({"error": "That add doesn't look right — e.g. 2.00 "
                                 "(or leave it empty for single vision)."}), 400
    fh = _arg("fh")
    if (request.args.get("fh") or "").strip() and (fh is None or not 8 <= fh <= 40):
        return jsonify({"error": "Fitting height should be in millimetres, "
                                 "e.g. 18 (or leave it empty)."}), 400
    kind = (request.args.get("kind") or "Single vision").strip()
    tint = (request.args.get("tint") or "").strip().lower() in ("1", "true", "yes", "on")

    # Which supplier's range to search. "current" = what we order now (the
    # orderable suppliers in lens_filter.json); "hoya" = the old range, ranked
    # as if it were still ordered, for a side-by-side; "all" = everything,
    # retired lenses greyed.
    which = (request.args.get("supplier") or "current").strip().lower()
    catalog = _catalog()
    rows = catalog["lenses"]
    if which == "hoya":
        rows = [{**l, "orderable": True} for l in rows
                if str(l.get("supplier") or l.get("brand") or "").lower() == "hoya"]
    elif which != "all":
        rows = [l for l in rows if l.get("orderable", True)]
    # Optional other eye (lsph / lcyl): a lens is only offered when BOTH
    # eyes fit it. Absent, the finder answers for the one Rx as before.
    left = None
    lsph = _arg("lsph")
    if (request.args.get("lsph") or "").strip():
        if lsph is None or not -30 <= lsph <= 30:
            return jsonify({"error": "The left sphere doesn't look right — "
                                     "e.g. -2.75 (or leave it empty)."}), 400
        lcyl = _arg("lcyl")
        if (request.args.get("lcyl") or "").strip() and (lcyl is None or abs(lcyl) > 10):
            return jsonify({"error": "The left cyl doesn't look right — "
                                     "e.g. -1.25 (or leave it empty)."}), 400
        left = {"sph": lsph, "cyl": lcyl or 0.0}
    result = lenses.find_options(rows, sph, cyl or 0.0, min_blank,
                                 add=add, kind=kind, fh=fh, tint=tint,
                                 pricing=_pricing(), left=left)
    result["supplier"] = which
    if which == "hoya" and result.get("verdict"):
        result["verdict"] = "HOYA (old supplier, for comparison) — " + result["verdict"]
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
    result = lenses.check_job(catalog["lenses"], right, left, min_blank,
                              data.get("chosen") or {},
                              add=lenses.parse_number(data.get("add")),
                              kind=(data.get("kind") or "").strip() or None,
                              pricing=_pricing())
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
            catalog["lenses"],
            job.get("right") or {}, job.get("left") or {},
            min_blank,
            {"code": job.get("code"), "type": job.get("stk_grd")},
            pricing=_pricing(),
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
    # CEC_HUB_PORT lets a second copy run for testing without touching the live Hub.
    app.run(host=os.environ.get("CEC_HUB_HOST", "0.0.0.0"),
            port=int(os.environ.get("CEC_HUB_PORT", "5680")), debug=False)
