"""Drive the iPad page and the staff check-in screens in headless Chrome and save
screenshots (fixture mode, ZZTEST only). Usage: shots.py <outdir> [adult|child|landscape|staff]"""
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
    b.shot(p, full=full)
    print("shot", p)


try:
    if WHAT in ("adult", "child", "landscape"):
        appt = {"adult": 1006, "child": 1005, "landscape": 1001}[WHAT]
        print(post("/api/checkin/send", {"kind": "appointment", "appointment_id": appt, "replace": True}))
        if WHAT == "landscape":
            b.size(1024, 768)
        else:
            b.size(768, 1024)
        b.go(f"{BASE}/checkin/ipad#{KEY}", wait=4.5)
        snap("welcome")
        click(b, "Start")
        snap("details")
        snap("details-full", full=True)
        if WHAT == "landscape":
            click(b, "Skip")
            snap("today")
            for _ in range(3):
                click(b, "Skip")
            snap("family-lifestyle")
            click(b, "Skip")
            snap("consent")
            sys.exit(0)
        # 6 screens (Mark's cut-down set, 4 Oct 2026): Details (How heard at the
        # bottom) - Today - Eyes - Health - Family & lifestyle - Consent.
        click(b, "Friend")
        snap("details-friend", full=True)
        click(b, "Next")
        if not b.js("document.querySelector('textarea').value"):
            typ(b, "textarea", "ZZTEST reason: blurry when reading.")
        click(b, "I wear glasses" if WHAT == "adult" else "Glasses")
        snap("today", full=True)
        click(b, "Next")
        b.js("document.querySelector('[data-pick]').click()")
        b.js("document.querySelector('[data-tick]').click()")
        snap("eyes", full=True)
        click(b, "Next")
        click(b, "None")
        snap("health", full=True)
        click(b, "Next")
        snap("family-lifestyle", full=True)
        click(b, "Next")
        snap("consent")
        click(b, "Finish")
        snap("consent-unsigned-error", full=True)
        if WHAT == "child":
            typ(b, "input[data-q=guardian_name]", "ZZTEST Parent")
            typ(b, "input[data-q=guardian_relationship]", "Mother")
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
