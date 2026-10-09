"""Every screen of the iPad check-in, adult then child, landscape 1180x820, with
example ZZTEST answers - plus the staff check screen for each. Test copy only
(127.0.0.1:5699, CHECKIN_FIXTURE=1, test config). Usage: walkthrough.py <outdir>
Writes NN-<flow>-<screen>.png and checks each page's text holds no name other
than the ZZTEST fixtures'."""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from cdp import Browser  # noqa: E402

BASE = "http://127.0.0.1:5699"
KEY = "zztest-ipad-key-not-for-real-use"
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
# The only names the fixtures hold (given names are invented, surnames ZZTEST).
FAKE_GIVEN = {"Adultone", "Childone", "Childtwo", "Examdone", "Newbooking", "Seniorone", "Anne"}

b = Browser(os.path.join(OUT, "_profile"))
n = [0]
shots = []


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def snap(flow, name, caption, staff=False):
    n[0] += 1
    p = os.path.join(OUT, f"{n[0]:02d}-{flow}-{name}.png")
    w, h = b.w, b.h
    # Grow the window to the whole page so fixed / sticky bars sit at the bottom,
    # where they are on the real screen, instead of floating mid-page.
    tall = b.js("document.documentElement.scrollHeight")
    b.size(w, max(h, int(tall)), mobile=not staff)
    time.sleep(0.6)
    b.shot(p, full=False)
    b.size(w, h, mobile=not staff)
    text = b.js("document.body.innerText") or ""
    shots.append({"file": os.path.basename(p), "flow": flow, "caption": caption, "zztest": "ZZTEST" in text,
                  "names": sorted({g for g in FAKE_GIVEN if g in text})})
    print("shot", os.path.basename(p))


def click(text):
    r = b.js(f"""(() => {{ const el = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === {json.dumps(text)});
        if (!el) return 'MISSING'; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print("MISSING button", text)


def tap(qid, value):
    r = b.js(f"""(() => {{ const el = [...document.querySelectorAll('button[data-v]')].find(x =>
        (x.dataset.tick === {json.dumps(qid)} || x.dataset.pick === {json.dumps(qid)}) && x.dataset.v === {json.dumps(value)});
        if (!el) return 'MISSING'; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print("MISSING tile", qid, value)


def typ(sel, text):
    r = b.js(f"""(() => {{ const el = document.querySelector({json.dumps(sel)});
        if (!el) return 'MISSING'; el.focus(); el.value = {json.dumps(text)};
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}})); el.blur(); return 'ok'; }})()""")
    if r != "ok":
        print("MISSING field", sel)


def top():
    b.js("window.scrollTo(0, 0)")


def start(appt):
    print(post("/api/checkin/send", {"kind": "appointment", "appointment_id": appt, "replace": True}).get("ok"))
    b.size(1180, 820, mobile=True)
    b.go(f"{BASE}/checkin/ipad#{KEY}", wait=4.5)


def staff_check(flow, appt, caption):
    st = json.loads(urllib.request.urlopen(BASE + "/api/checkin/today").read())
    tok = next(a["token"] for a in st["appointments"] if a["appointment_id"] == appt)
    b.size(1280, 900, mobile=False)
    b.go(f"{BASE}/#/checkin/{tok}", wait=5)
    b.js("const d = document.querySelector('.ci-notes-fold'); if (d) d.open = true;")
    snap(flow, "staff-check", caption, staff=True)
    return tok


try:
    # ------------------------------------------------------------------ adult
    F = "adult"
    start(1001)
    snap(F, "welcome", "Welcome by first name, privacy notice under Start.")
    click("Start")
    typ("input[data-q=phone_mobile]", "0400 000 000")
    typ("input[data-q=email]", "zztest.adultone.new@example.invalid")
    top()
    snap(F, "details", "Details from Optomate to check; mobile and email updated here.")
    click("Next")
    tap("symptoms", "Blurry vision")
    tap("symptoms_blurry_where", "Near")
    tap("symptoms_blurry_onset", "Gradually")
    tap("symptoms", "Dry, gritty or burning")
    tap("source", "Friend or family")
    typ("input[data-q=source_name]", "ZZTEST Friend")
    top()
    snap(F, "today", "Visit reason first, then symptoms; how heard last.")
    click("Next")
    tap("glasses", "Distance")
    tap("glasses_age_distance", "2 yrs+")
    tap("glasses", "Reading")
    tap("glasses_age_reading", "Under 1 yr")
    tap("contacts", "I wear them")
    tap("contacts_type", "Replace monthly")
    top()
    snap(F, "glasses", "Glasses owned with ages; contact lens type.")
    click("Next")
    tap("last_exam", "1–2 yrs")
    tap("eye_conditions", "Dry eye")
    top()
    snap(F, "eyes", "Last eye test and eye history.")
    click("Next")
    tap("medical", "High blood pressure")
    tap("smoking", "Used to smoke")
    typ("textarea[data-q=medications]", "ZZTEST blood pressure tablet")
    top()
    snap(F, "health", "Medical conditions, smoking, medicines including eye drops.")
    click("Next")
    tap("family", "Glaucoma")
    tap("screen_time", "4 h+")
    tap("drive", "Yes")
    top()
    snap(F, "family-habits", "Family eye history, screen time, driving.")
    click("Next")
    snap(F, "check-and-send", "Last screen: consent sentence, notice, big Send. No signature.")
    click("Send")
    time.sleep(1.5)
    snap(F, "thank-you", "Thank you; iPad clears itself after 8 seconds.")
    time.sleep(1)
    staff_check(F, 1001, "Staff check: old vs new, Save bar; note says who sent it.")
    # ------------------------------------------------------------------ child
    F = "child"
    start(1002)
    snap(F, "welcome", "Child form: greets the parent, names the child.")
    click("Start")
    top()
    snap(F, "details", "The child's details from Optomate to check.")
    click("Next")
    tap("noticed_child", "Squints or screws up eyes to see")
    tap("noticed_child", "Sits close to the TV or holds things close")
    tap("source", "School")
    typ("textarea[data-q=reason]", "ZZTEST school vision check follow-up")
    top()
    snap(F, "today", "Reason, signs a parent may notice, how heard last.")
    click("Next")
    tap("glasses_child", "Glasses")
    tap("glasses_child_first", "6–9")
    tap("glasses_child_current", "1–2 yrs")
    top()
    snap(F, "glasses", "Glasses or lenses now, first glasses age, current pair age.")
    click("Next")
    tap("last_exam", "Under 1 yr")
    tap("eye_conditions_child", "None")
    top()
    snap(F, "eyes", "Last eye test and the child's eye history.")
    click("Next")
    tap("medical_child", "Asthma")
    tap("medications_child", "None")
    top()
    snap(F, "health", "Child's health and medicines.")
    click("Next")
    tap("family_child", "Short-sighted (myopia)")
    tap("family_child_myopia_who", "Mum")
    tap("family_child_myopia_who", "Dad")
    tap("outdoors", "1–2 h")
    tap("screen_time", "2–4 h")
    top()
    snap(F, "family-habits", "Family myopia (who), time outdoors, screen time.")
    click("Next")
    typ("input[data-q=guardian_name]", "ZZTEST Parentone")
    typ("input[data-q=guardian_relationship]", "Mother")
    top()
    snap(F, "check-and-send", "Parent's name and relationship, consent, Send. No signature.")
    click("Send")
    time.sleep(1.5)
    snap(F, "thank-you", "Thank you; iPad clears itself after 8 seconds.")
    time.sleep(1)
    staff_check(F, 1002, "Staff check for the child; note names the parent who sent it.")
finally:
    b.close()
    with open(os.path.join(OUT, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(shots, f, indent=1)
