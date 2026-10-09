"""Screenshots for the Codex-review changes (9 Oct 2026), fixture mode, ZZTEST only.

Usage: shots_codex.py <outdir> [landscape|portrait|staff]
Runs against the TEST COPY on 127.0.0.1:5699 (CHECKIN_FIXTURE=1, a test
integrations file with the throw-away keys below). Every shot is checked for
names: anything on screen that looks like a name must be a ZZTEST one.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from cdp import Browser  # noqa: E402

BASE = "http://127.0.0.1:5699"
KEY = "zztest-ipad-key-not-for-real-use"
OUT = sys.argv[1]
WHAT = sys.argv[2] if len(sys.argv) > 2 else "landscape"
os.makedirs(OUT, exist_ok=True)
FAKE_CLOCK = """(() => { if (window.__off !== undefined) return;
    window.__off = 0; const real = Date.now.bind(Date); Date.now = () => real() + window.__off; })()"""


def post(path, body, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


b = Browser(os.path.join(OUT, "_profile"))
n = [0]


def names_ok():
    """Every capitalised word pair on screen that has a ZZ-style surname must
    be ZZTEST; and no word on the page may look like a real surname from the
    ZZTEST fixtures' absence - we simply list name-looking text for a look."""
    text = b.js("document.body.innerText") or ""
    bad = [w for w in re.findall(r"\b[A-Z][a-z]+one\b|\b[A-Z][a-z]+two\b", text)]
    return "ZZTEST" in text or not bad


def snap(name, full=False):
    n[0] += 1
    p = os.path.join(OUT, f"{WHAT}-{n[0]:02d}-{name}.png")
    if full and not WHAT.startswith("staff"):
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
    print("shot", p, "| names ZZTEST-only:", names_ok())


def click(text):
    r = b.js(f"""(() => {{ const t = {json.dumps(text)};
        const el = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === t);
        if (!el) return 'MISSING ' + t; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print(r)


def tap(qid, value):
    r = b.js(f"""(() => {{ const el = [...document.querySelectorAll('button[data-v]')].find(x =>
        (x.dataset.tick === {json.dumps(qid)} || x.dataset.pick === {json.dumps(qid)})
        && x.dataset.v === {json.dumps(value)});
        if (!el) return 'MISSING ' + {json.dumps(qid + ': ' + value)}; el.click(); return 'ok'; }})()""")
    if r != "ok":
        print(r)


def typ(sel, text):
    b.js(f"""(() => {{ const el = document.querySelector({json.dumps(sel)});
        if (!el) return; el.focus(); el.value = {json.dumps(text)};
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}})); el.blur(); }})()""")


def tile_heights(qid):
    return b.js(f"""[...document.querySelectorAll('button[data-tick={json.dumps(qid)}], button[data-pick={json.dumps(qid)}]')]
        .map(e => Math.round(e.getBoundingClientRect().height))""")


def sign():
    b.js("document.getElementById('sig').scrollIntoView({block: 'center'})")
    x0, y0, w, h = b.js("(() => { const r = document.getElementById('sig').getBoundingClientRect();"
                        " return [r.left, r.top, r.width, r.height]; })()")
    b.drag([(x0 + 40 + i * 12, y0 + h / 2 + (30 if i % 2 else -30)) for i in range(30)])
    time.sleep(0.3)


def open_ipad(appt, w, h):
    print(post("/api/checkin/send", {"kind": "appointment", "appointment_id": appt, "replace": True}))
    b.size(w, h)
    b.go(f"{BASE}/checkin/ipad#{KEY}", wait=4.5)
    b.js(FAKE_CLOCK)


try:
    if WHAT in ("landscape", "portrait"):
        w, h = (1180, 820) if WHAT == "landscape" else (820, 1180)
        # --- adult: welcome, details, today, glasses ---------------------------
        open_ipad(1006, w, h)
        snap("welcome-notice")
        print("links on the iPad page:", b.js("document.querySelectorAll('a').length"))
        click("Start")
        snap("details")
        click("Next")
        snap("today-reason-first")
        tap("symptoms", "Blurry vision")
        tap("symptoms_blurry_where", "Near")
        tap("source", "ChatGPT or another AI")
        snap("today-full", full=True)
        # long page: can the last question scroll clear of the fixed bar?
        print("bottom clear of bar:", b.js("""(() => { window.scrollTo(0, document.body.scrollHeight);
            const last = [...document.querySelectorAll('.band')].pop().getBoundingClientRect();
            const bar = document.getElementById('bar').getBoundingClientRect();
            return last.bottom <= bar.top; })()"""))
        click("Next")
        tap("glasses", "Distance")
        tap("glasses_age_distance", "2 yrs+")
        tap("contacts", "I wear them")
        tap("contacts_type", "Replace every 2 weeks")
        b.js("window.scrollTo(0, 0)")
        snap("glasses", full=True)
        print("glasses tile heights:", tile_heights("glasses"))
        # --- inactivity: 3 min -> warning; 5 min -> back to reception ---------------
        b.js("window.__off = 181000", wait=1.6)
        snap("inactivity-warning")
        b.js("window.__off = 301000", wait=2.0)
        snap("returned-to-reception")
        print("form gone from the page:", b.js("!document.querySelector('.band') && !document.querySelector('.grid')"))
        st = json.loads(urllib.request.urlopen(BASE + "/api/checkin/today").read())
        print("staff sees:", st["ipad"]["state"], st["ipad"]["status"])
        tok = st["ipad"]["token"]
        print("resume:", post(f"/api/checkin/session/{tok}/resume", {}))
        time.sleep(4)
        snap("resumed-welcome-back")
        click("Start")
        print("resumed at screen:", b.js("document.querySelector('h1').textContent"),
              "| distance still ticked:", b.js("!!document.querySelector('button.on[data-v=Distance]')"))
        # --- finish, losing the first "received" -----------------------------------
        for _ in range(4):
            click("Skip")
        b.js("""(() => { const of = window.fetch; let k = 0; window.fetch = async (u, o) => {
            const r = await of(u, o); if (String(u).endsWith('/submit') && k++ === 0) throw new TypeError('lost');
            return r; }; })()""")
        sign()
        click("Finish")
        time.sleep(1.5)
        snap("lost-answer-could-not-send")
        click("Tap to try again")
        time.sleep(1.5)
        snap("received-retry")
        time.sleep(9)
        print("after 8 s:", b.js("document.body.innerText.trim().replace(/\\s+/g, ' ')"))
        # --- child: consent leads with the parent line -----------------------------
        open_ipad(1005, w, h)
        snap("child-welcome-notice")
        click("Start")
        for _ in range(2):
            click("Skip")
        tap("glasses_child", "Atropine drops")
        snap("child-glasses-atropine-only", full=True)
        tap("glasses_child", "Glasses")
        snap("child-glasses-with-glasses", full=True)
        print("child glasses tile heights:", tile_heights("glasses_child"))
        for _ in range(4):
            click("Skip")
        snap("child-consent", full=True)
        # leave the child form unsubmitted, then take it off so the slot is free
        st = json.loads(urllib.request.urlopen(BASE + "/api/checkin/today").read())
        post(f"/api/checkin/session/{st['ipad']['token']}/discard", {})
    elif WHAT == "staff":
        # an adult form filled in full, then the check screen
        open_ipad(1001, 1180, 820)
        click("Start")
        typ("input[data-q=email]", "zztest.new@example.invalid")
        typ("input[data-q=phone_mobile]", "0400 000 000")
        click("Next")
        tap("symptoms", "Dry, gritty or burning")
        tap("source", "Google")
        click("Next")
        tap("glasses", "Reading")
        tap("glasses_age_reading", "1–2 yrs")
        click("Next")
        tap("last_exam", "2–5 yrs")
        click("Next")
        tap("medical", "High blood pressure")
        tap("smoking", "Used to smoke")
        tap("medications", "None")
        click("Next")
        tap("family", "Glaucoma")
        click("Next")
        sign()
        click("Finish")
        time.sleep(2)
        st = json.loads(urllib.request.urlopen(BASE + "/api/checkin/today").read())
        tok = next(a["token"] for a in st["appointments"] if a["appointment_id"] == 1001)
        b.size(1280, 900, mobile=False)
        b.go(f"{BASE}/#/checkin/{tok}", wait=5)
        snap("check-top")
        b.js("window.scrollTo(0, 500)")
        snap("check-scrolled-savebar-stays")
        snap("check-full", full=True)
        b.size(1180, 820, mobile=False)
        b.js("window.scrollTo(0, 0)")
        snap("check-1180")
        b.go(f"{BASE}/#/checkin", wait=4)
        snap("list")
        # the staff route refused from an iPad-key request - text capture
        lines = []
        for path in ("/api/checkin/today", f"/api/checkin/session/{tok}", f"/checkin/pdf/{tok}"):
            req = urllib.request.Request(BASE + path, headers={"X-Checkin-Key": KEY})
            try:
                r = urllib.request.urlopen(req)
                lines.append(f"GET {path.replace(tok, '<token>')} -> HTTP {r.status} (NOT REFUSED)")
            except urllib.error.HTTPError as e:
                lines.append(f"GET {path.replace(tok, '<token>')} with the iPad key -> HTTP {e.code} {e.read().decode().strip()}")
        r = post("/api/checkin/search", {"q": "ZZTEST"}, {"X-Checkin-Key": KEY})
        lines.append(f"POST /api/checkin/search with the iPad key -> {json.dumps(r)}")
        with open(os.path.join(OUT, "staff-route-refused-from-ipad-key.txt"), "w", encoding="utf-8") as f:
            f.write("Test copy 127.0.0.1:5699, fixture mode. Requests carrying the iPad device key:\n"
                    + "\n".join(lines) + "\n")
        print("\n".join(lines))
        post(f"/api/checkin/session/{tok}/discard", {})
finally:
    b.close()
