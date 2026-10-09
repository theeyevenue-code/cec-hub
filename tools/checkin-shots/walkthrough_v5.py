"""v5 walkthrough (flow chart: core + modules), iPad portrait 820 wide, with
example ZZTEST answers, plus staff check screens. Test copy only
(127.0.0.1:5699, CHECKIN_FIXTURE=1, test config, dry run).

Usage: walkthrough_v5.py <outdir>
Writes NN-<flow>-<screen>.png and shots.json (file, flow, caption, title,
names seen). Every patient name shown must be a ZZTEST fixture name; the run
stops if one is not. Each flow's screen order is checked against the engine's
schema.screen_order (printed as order-check)."""
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from cdp import Browser  # noqa: E402

BASE = "http://127.0.0.1:5699"
KEY = "zztest-ipad-key-not-for-real-use"
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
FAKE_GIVEN = {"Adultone", "Childone", "Childtwo", "Examdone", "Newbooking", "Seniorone", "Anne"}
W, H = 820, 1180

b = Browser(os.path.join(OUT, "_profile"))
n = [0]
shots = []
titles = {}


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path).read())


def names_check(text):
    """Patient names on screen: the welcome line, 'This form is for X', the
    staff page title. Each must be a fixture name (ZZTEST people only)."""
    seen = set(re.findall(r"Welcome(?: back)?, ([A-Z][\w']+)", text))
    seen |= set(re.findall(r"This form is for ([A-Z][\w']+)", text))
    bad = [x for x in seen if x not in FAKE_GIVEN]
    if bad:
        raise SystemExit(f"NON-FIXTURE NAME ON SCREEN: {bad}")
    return sorted(seen | {g for g in FAKE_GIVEN if g in text})


def snap(flow, name, caption, staff=False):
    n[0] += 1
    p = os.path.join(OUT, f"{n[0]:02d}-{flow}-{name}.png")
    w, h = b.w, b.h
    tall = b.js("document.documentElement.scrollHeight")
    b.size(w, max(h, int(tall)), mobile=not staff)
    time.sleep(0.6)
    b.shot(p, full=False)
    b.size(w, h, mobile=not staff)
    text = b.js("document.body.innerText") or ""
    title = b.js("(document.querySelector('#app h1') || {}).textContent || ''") or ""
    shots.append({"file": os.path.basename(p), "flow": flow, "caption": caption, "title": title,
                  "names": names_check(text), "zztest": "ZZTEST" in text})
    titles.setdefault(flow, [])
    if title and not staff and (not titles[flow] or titles[flow][-1] != title):
        titles[flow].append(title)
    print("shot", os.path.basename(p), "|", title)


def click(text):
    r = b.js(f"""(() => {{ const el = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === {json.dumps(text)});
        if (!el) return 'MISSING'; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print("MISSING button", text)
    time.sleep(0.3)


def tap(qid, value):
    r = b.js(f"""(() => {{ const el = [...document.querySelectorAll('button[data-v]')].find(x =>
        (x.dataset.tick === {json.dumps(qid)} || x.dataset.pick === {json.dumps(qid)}) && x.dataset.v === {json.dumps(value)});
        if (!el) return 'MISSING'; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print("MISSING tile", qid, value)


def typ(qid, text):
    b.js(f"""(() => {{ const el = document.querySelector('[data-q={json.dumps(qid)}]');
        if (!el) return; el.focus(); el.value = {json.dumps(text)};
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}})); el.blur(); }})()""")


def screen_title():
    return b.js("(document.querySelector('#app h1') || {}).textContent || ''") or ""


def note_title(flow):
    t = screen_title()
    titles.setdefault(flow, [])
    if t and (not titles[flow] or titles[flow][-1] != t):
        titles[flow].append(t)


def nxt(flow):
    note_title(flow)
    click("Next")
    time.sleep(0.4)
    note_title(flow)


def skip(flow):
    note_title(flow)
    click("Skip")
    time.sleep(0.4)
    note_title(flow)


def open_ipad(appt):
    r = post("/api/checkin/send", {"kind": "appointment", "appointment_id": appt, "replace": True})
    assert r.get("ok"), r
    b.size(W, H)
    b.go("about:blank", wait=0.5)          # a fresh page load every flow
    b.go(f"{BASE}/checkin/ipad#{KEY}", wait=4.5)
    return r["token"]


def send_and_check(flow, appt, caption, top_only=True):
    click("Send")
    time.sleep(2.5)
    st = get("/api/checkin/today")
    tok = next(a["token"] for a in st["appointments"] if a["appointment_id"] == appt)
    b.size(1280, 900, mobile=False)
    b.go(f"{BASE}/#/checkin/{tok}", wait=5)
    snap(flow, "staff-check", caption, staff=True)
    plan = get(f"/api/checkin/session/{tok}").get("plan") or {}
    post(f"/api/checkin/session/{tok}/discard", {})
    return plan


try:
    # ---- A. adult, NEW (on file from an online booking, never examined) ----------
    F = "adult-new"
    open_ipad(1004)
    snap(F, "welcome", "Start screen: greets by first name, collection notice in plain text.")
    click("Start")
    typ("email", "zztest.newbooking@example.invalid")
    snap(F, "details", "Details pre-filled from Optomate. Occupation: new patients only.")
    nxt(F)
    tap("reason", "Routine check")
    tap("reason", "Blurry far away")
    tap("reason_far_onset", "Gradually")
    tap("private_health", "Yes")
    tap("private_health_fund", "Bupa")
    tap("source", "Google")
    snap(F, "today", "Today: tap all reasons. Blurry far asks when it came on. Fund back.")
    nxt(F)
    tap("glasses", "Distance"); tap("glasses_age_distance", "2–4 yrs")
    tap("glasses", "Reading"); tap("glasses_age_reading", "Under 1 yr")
    tap("update_intent", "Both")
    tap("contacts", "No")
    tap("hobbies", "Reading"); tap("hobbies", "Gardening")
    tap("drive", "Yes"); tap("drive_night", "No")
    tap("devices", "Laptop"); tap("devices_hours_laptop", "4 h+")
    tap("devices", "Phone"); tap("devices_hours_phone", "2–4 h")
    snap(F, "glasses-daily-life", "Each pair's age, keen on new specs, hobbies, night driving, each screen's hours.")
    nxt(F)
    tap("last_exam", "2–5 yrs")
    tap("ocular", "Dry eye")
    tap("family", "Glaucoma")
    snap(F, "eyes", "New patient: last exam asked; eye history ticks plus a typed box.")
    nxt(F)
    tap("medical", "None"); tap("smoking", "Never"); tap("medications", "None")
    snap(F, "health", "Health ticks, smoking, medicines (typed or None).")
    nxt(F)
    snap(F, "send", "Check and send: consent sentence, notice, one big Send.")
    plan_a = send_and_check(F, 1004, "Staff check, new patient: no 'Last exam here' - they told us.")

    # ---- B. adult, RETURNING (exam here before; 46 -> close-up questions) --------
    F = "adult-returning"
    open_ipad(1001)
    snap(F, "welcome", "Returning patient: same start screen.")
    click("Start")
    snap(F, "details", "Details pre-filled; 'Updated online already? Skip' jumps past.")
    nxt(F)
    tap("reason", "Routine check")
    tap("private_health", "Yes"); tap("private_health_fund", "Medibank")
    snap(F, "today", "Booking reason pre-fills the typed box. No 'how heard' for returning.")
    nxt(F)
    tap("near_tasks", "Reading"); tap("near_tasks", "Computer"); tap("multifocal_before", "Never")
    snap(F, "module-close-up", "Module: age 40+ opens close-up questions.")
    nxt(F)
    tap("glasses", "Multifocal"); tap("glasses_age_multifocal", "5 yrs+")
    tap("update_intent", "Glasses"); tap("contacts", "No")
    tap("hobbies", "Golf"); tap("drive", "Yes"); tap("drive_night", "Yes")
    tap("devices", "Computer (desktop)"); tap("devices_hours_desktop", "4 h+")
    snap(F, "glasses-daily-life", "Always asked, new or returning: glasses ages and update intent.")
    nxt(F)
    tap("eyes_new", "No"); tap("family_new", "No")
    snap(F, "eyes-anything-new", "Returning: 'Anything new?' instead of the full history.")
    nxt(F)
    tap("health_new", "Yes")
    tap("medical", "High blood pressure"); tap("smoking", "Never")
    typ("medications", "ZZTEST tablet 10 mg")
    snap(F, "health-yes-opens-grid", "Yes opens the health grid; No moves straight on.")
    nxt(F)
    snap(F, "send", "Check and send.")
    plan_b = send_and_check(F, 1001, "Staff check: 'Last exam here' from Optomate, plus extra questions opened.")

    # ---- C. child, NEW, routine ----------------------------------------------------
    F = "child-new"
    open_ipad(1002)
    snap(F, "welcome", "Child form: greets the parent, names the child.")
    click("Start")
    snap(F, "details", "Your child's details, pre-filled.")
    nxt(F)
    tap("reason_child", "Routine or school check")
    tap("noticed_child", "Nothing")
    tap("private_health", "No")
    tap("source", "School")
    snap(F, "today", "New child: 'Have you noticed?' always shown.")
    nxt(F)
    tap("glasses_child", "Never worn")
    tap("update_intent_child", "Not right now")
    tap("close_work_child", "2–4 h")
    snap(F, "glasses-daily-life", "Glasses or lenses, keen on new, screens and close work.")
    nxt(F)
    tap("last_exam", "Never or not sure")
    tap("eye_conditions_child", "None")
    tap("family_child_myopia", "Nobody")
    tap("family_child_turn", "No")
    snap(F, "eyes", "Eye history (new only) and family: who is short-sighted?")
    nxt(F)
    tap("medical_child", "None"); tap("medications_child", "None")
    snap(F, "health", "Health and early years (new only; returning see 'Anything new?').")
    nxt(F)
    typ("guardian_name", "ZZTEST Parentone"); typ("guardian_relationship", "Mother")
    snap(F, "send", "Parent's name and relationship, then Send.")
    click("Send"); time.sleep(2)
    st = get("/api/checkin/today")
    tok = next(a["token"] for a in st["appointments"] if a["appointment_id"] == 1002)
    post(f"/api/checkin/session/{tok}/discard", {})

    # ---- D. DRY EYE adult ------------------------------------------------------------
    F = "dry-eye"
    open_ipad(1009)
    click("Start")
    nxt(F)
    tap("reason", "Dry, gritty or tired by evening")
    tap("private_health", "No"); tap("source", "Friend or family")
    snap(F, "today", "Tapping the dry-eye tile opens the dry-eye screen next.")
    nxt(F)
    tap("dry_often", "Often"); tap("dry_bedtime", "Moderate"); tap("dry_watery", "Yes")
    tap("dry_tried", "Eye drops"); tap("dry_tried", "Warm compress")
    tap("dry_body", "Rosacea"); tap("dry_meds", "Antihistamines")
    tap("dry_environment", "Air-con or heating most of the day")
    tap("dry_water", "Under 1 L"); tap("dry_sleep", "6–8 h"); tap("dry_sleep_quality", "Fair")
    tap("dry_makeup", "Yes")
    snap(F, "module-dry-eye", "Dry-eye module: DEQ-style taps, body, medicines, room, water, sleep.")
    nxt(F)
    for _ in range(3):
        skip(F)
    plan_d = send_and_check(F, 1009, "Staff check: dry-eye answers under a 'Dry eye:' subhead.")

    # ---- E. MYOPIA child (booking: Myopia Control Consultation) ------------------------
    F = "myopia-child"
    open_ipad(1005)
    click("Start")
    nxt(F)
    tap("reason_child", "Follow-up for myopia control")
    tap("noticed_child", "Squints or screws up eyes")
    tap("private_health", "Yes"); tap("private_health_fund", "HCF")
    tap("source", "Doctor or health professional")
    snap(F, "today", "Booking reason (myopia consult) already opened the myopia screen.")
    nxt(F)
    tap("myopia_outdoors", "Under 1 h"); tap("myopia_sleep", "8–10 h"); tap("myopia_rx_up", "Yes")
    snap(F, "module-myopia", "Myopia module: outdoors, sleep, prescription up. 'Holds close' already asked.")
    nxt(F)
    tap("glasses_child", "Myopia-control glasses or lenses")
    tap("glasses_child_first", "6–9"); tap("glasses_child_current", "Under 1 yr")
    tap("update_intent_child", "Yes"); tap("close_work_child", "4 h+")
    snap(F, "glasses-daily-life", "First glasses age and screen time: asked once, here.")
    nxt(F)
    tap("last_exam", "Under 1 yr"); tap("eye_conditions_child", "None")
    tap("family_child_myopia", "Mum"); tap("family_child_myopia", "Dad"); tap("family_child_turn", "No")
    nxt(F)
    skip(F)
    typ("guardian_name", "ZZTEST Parenttwo"); typ("guardian_relationship", "Father")
    note_title(F)
    plan_e = send_and_check(F, 1005, "Staff check: myopia answers under a 'Myopia:' subhead.")

    # ---- order check against the engine ------------------------------------------------
    with open(os.path.join(OUT, "titles.json"), "w", encoding="utf-8") as f:
        json.dump(titles, f, indent=1, ensure_ascii=False)
    with open(os.path.join(OUT, "plans.json"), "w", encoding="utf-8") as f:
        json.dump({"adult-new": plan_a, "adult-returning": plan_b, "dry-eye": plan_d,
                   "myopia-child": plan_e}, f, indent=1, ensure_ascii=False)
finally:
    with open(os.path.join(OUT, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(shots, f, indent=1, ensure_ascii=False)
    b.close()
