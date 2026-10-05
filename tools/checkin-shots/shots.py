"""Drive the iPad page and the staff check-in screens in headless Chrome and save
screenshots (fixture mode, ZZTEST only). Usage: shots.py <outdir> [adult|child|portrait|staff|check|setup]
adult / child = landscape iPad 1180x820 (v2); portrait = the adult flow at 820x1180."""
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
WHAT = sys.argv[2] if len(sys.argv) > 2 else "adult"
os.makedirs(OUT, exist_ok=True)


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def click(b, text):
    return b.js(f"""(() => {{ const t = {json.dumps(text)};
        const el = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === t);
        if (!el) return 'MISSING ' + t; el.click(); return 'ok'; }})()""")


def typ(b, sel, text):
    return b.js(f"""(() => {{ const el = document.querySelector({json.dumps(sel)});
        if (!el) return 'MISSING'; el.focus(); el.value = {json.dumps(text)};
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}})); el.blur(); return 'ok'; }})()""")


profile = os.path.join(OUT, "_profile")
b = Browser(profile)
n = [0]


def snap(name, full=False):
    n[0] += 1
    p = os.path.join(OUT, f"{WHAT}-{n[0]:02d}-{name}.png")
    if full and WHAT not in ("staff768", "staff1280", "check"):
        # A whole-page shot of the iPad: grow the window to the page so the fixed
        # Back/Next bar sits under the content instead of over it.
        w, h = b.w, b.h
        y = b.js("window.scrollY")
        tall = b.js("document.documentElement.scrollHeight")
        b.size(w, max(h, int(tall)))
        time.sleep(0.4)
        b.shot(p, full=False)
        b.size(w, h)
        b.js(f"window.scrollTo(0, {int(y or 0)})")
    else:
        b.shot(p, full=full)
    print("shot", p)


def tap(qid, value):
    """Tap one answer tile of one question (tiles repeat words across questions)."""
    r = b.js(f"""(() => {{ const el = [...document.querySelectorAll('button[data-v]')].find(x =>
        (x.dataset.tick === {json.dumps(qid)} || x.dataset.pick === {json.dumps(qid)})
        && x.dataset.v === {json.dumps(value)});
        if (!el) return 'MISSING ' + {json.dumps(qid + ': ' + value)}; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print(r)
    return r


def ticked(qid):
    return b.js(f"""[...document.querySelectorAll('button.on')].filter(x =>
        x.dataset.tick === {json.dumps(qid)} || x.dataset.pick === {json.dumps(qid)}).map(x => x.dataset.v)""")


def overflow():
    """Any tile or label whose text spills out of its box (should be none)."""
    return b.js("""[...document.querySelectorAll('.t, .band .lab, .reveal .cap, .skipbig')]
        .filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent.trim().slice(0, 40))""")


def check(name):
    bad = overflow()
    print(f"overflow on {name}: {bad or 'none'}")


try:
    if WHAT in ("adult", "child", "portrait"):
        # v2 (Mark, 5 Oct 2026): 7 screens, landscape iPad 1180x820. Details -
        # Today - Glasses & contacts - Your eyes - Your health - Family & habits -
        # Consent. Adult keeps details and taps every follow-up; child takes the
        # big "Skip to your history" button.
        appt = {"adult": 1006, "child": 1005, "portrait": 1006}[WHAT]
        print(post("/api/checkin/send", {"kind": "appointment", "appointment_id": appt, "replace": True}))
        if WHAT == "portrait":
            b.size(820, 1180)
        else:
            b.size(1180, 820)
        b.go(f"{BASE}/checkin/ipad#{KEY}", wait=4.5)
        snap("welcome")
        click(b, "Start")
        snap("details")
        check("details")
        if WHAT == "adult":
            snap("details-full", full=True)
            click(b, "Next")
        else:
            b.js("document.querySelector('.skipbig').click()")
        # --- Today
        if WHAT == "child":
            tap("source", "Doctor or health professional")
            typ(b, "input[data-q=source_name]", "ZZTEST Dr Example")
            tap("noticed_child", "Squints or screws up eyes to see")
            tap("noticed_child", "Words blur, move or go double up close")
            tap("noticed_child", "Nothing")
            print("noticed after Nothing:", ticked("noticed_child"))
            tap("noticed_child", "Squints or screws up eyes to see")
            print("noticed after a tick:", ticked("noticed_child"))
            b.js("window.scrollTo(0, 0)")
            snap("today")
        else:
            tap("source", "Friend or family")
            typ(b, "input[data-q=source_name]", "ZZTEST Friend")
            b.js("window.scrollTo(0, 0)")
            snap("today-top")
            tap("symptoms", "Blurry vision")
            tap("symptoms_blurry_where", "Near")
            tap("symptoms_blurry_onset", "Gradually")
            tap("symptoms", "Red or sore eye")
            tap("symptoms_red_pain", "Yes")
            tap("symptoms", "Flashes or floaters")
            tap("symptoms_flashes_when", "Started this week")
            snap("today-follow-ups")
        if not b.js("document.querySelector('textarea').value"):
            typ(b, "textarea", "ZZTEST reason: blurry when reading.")
        snap("today-full", full=True)
        check("today")
        click(b, "Next")
        # --- Glasses & contacts
        if WHAT == "child":
            tap("glasses_child", "Glasses")
            tap("glasses_child", "Ortho-K")
            tap("glasses_child_first", "6–9")
            tap("glasses_child_current", "Under 1 yr")
        else:
            tap("glasses", "Distance")
            tap("glasses", "Reading")
            tap("glasses_age_distance", "2 yrs+")
            tap("glasses_age_reading", "Under 1 yr")
            tap("contacts", "I wear them")
            tap("contacts_type", "Monthlies")
        snap("glasses", full=True)
        check("glasses")
        click(b, "Next")
        # --- Your eyes
        tap("last_exam", "1–2 yrs")
        if WHAT == "child":
            tap("eye_conditions_child", "Lazy eye")
            tap("eye_conditions_child", "Other")
            typ(b, "input[data-q=eye_conditions_child_details]", "ZZTEST detail")
        else:
            tap("eye_conditions", "Dry eye")
            tap("eye_conditions", "Eye surgery, laser or injury")
            typ(b, "input[data-q=eye_conditions_details]", "ZZTEST LASIK 2015")
        snap("eyes", full=True)
        check("eyes")
        click(b, "Next")
        # --- Your health
        if WHAT == "child":
            tap("medical_child", "Born premature or birth complications")
            tap("medical_child", "ADHD")
            tap("medications_child", "None")
        else:
            tap("medical", "Diabetes")
            tap("medical", "None")
            print("medical after None:", ticked("medical"))
            tap("medical", "High blood pressure")
            tap("medical", "Other")
            typ(b, "input[data-q=medical_other]", "ZZTEST gout")
            tap("smoking", "Previous")
            tap("medications", "None")
        snap("health", full=True)
        check("health")
        click(b, "Next")
        # --- Family & habits
        if WHAT == "child":
            tap("family_child", "Short-sighted (myopia)")
            tap("family_child_myopia_who", "Mum")
            tap("family_child_myopia_who", "Brother or sister")
            tap("outdoors", "1–2 h")
        else:
            tap("family", "Glaucoma")
            tap("drive", "Yes")
        tap("screen_time", "2–4 h")
        snap("family-habits", full=True)
        check("family")
        click(b, "Next")
        snap("consent")
        click(b, "Finish")
        snap("consent-unsigned-error", full=True)
        if WHAT == "child":
            typ(b, "input[data-q=guardian_name]", "ZZTEST Parent")
            typ(b, "input[data-q=guardian_relationship]", "Mother")
        b.js("document.getElementById('sig').scrollIntoView({block: 'center'})")
        r = b.js("(() => { const c = document.getElementById('sig'); const r = c.getBoundingClientRect();"
                 " return [r.left, r.top, r.width, r.height]; })()")
        x0, y0, w, h = r
        pts = [(x0 + 40 + i * 12, y0 + h / 2 + (30 if i % 2 else -30)) for i in range(30)]
        b.drag(pts)
        time.sleep(0.3)
        snap("consent-signed")
        click(b, "Finish")
        time.sleep(1.5)
        snap("thanks")
        time.sleep(9)
        snap("idle-after-thanks")
    elif WHAT == "staff":
        tok = sys.argv[3]
        for w, h in ((768, 1024), (1280, 900)):
            b.size(w, h, mobile=False)
            WHAT = f"staff{w}"
            b.go(f"{BASE}/#/checkin", wait=4)
            snap("list", full=True)
            b.go(f"{BASE}/#/checkin/{tok}", wait=4)
            snap("check", full=True)
            if w == 1280:
                click(b, "Test save (nothing is written)")
                time.sleep(4)
                snap("test-saved", full=True)
    elif WHAT == "check":
        b.size(1280, 900, mobile=False)
        for tok in sys.argv[3:]:
            b.go(f"{BASE}/#/checkin/{tok}", wait=4)
            snap("check-" + tok[:6], full=True)
    elif WHAT == "setup":
        b.size(768, 1024)
        b.go(f"{BASE}/checkin/ipad", wait=2)
        snap("no-key")
        b.go(f"{BASE}/checkin/ipad#wrong-key-wrong-key-wrong", wait=4)
        snap("wrong-key")
finally:
    b.close()
