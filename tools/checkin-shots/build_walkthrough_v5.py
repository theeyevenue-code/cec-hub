"""Build Mark's phone walkthrough for check-in v5 from walkthrough_v5.py's shots.

Usage: build_walkthrough_v5.py <shots dir> <out.html>
One self-contained page: images embedded (JPEG), a caption per screen, the
flow chart, the paper-form table and the path timings at the top. ZZTEST only:
refuses to build if a shot recorded a non-fixture name or no ZZTEST at all on
a staff screen."""
import base64
import html
import io
import json
import os
import sys

from PIL import Image

SHOTS, OUT = sys.argv[1], sys.argv[2]
shots = json.load(open(os.path.join(SHOTS, "shots.json"), encoding="utf-8"))
FIXTURE = {"Adultone", "Childone", "Childtwo", "Examdone", "Newbooking", "Seniorone", "Anne"}
for s in shots:
    assert set(s["names"]) <= FIXTURE, s
    if "staff" in s["file"]:
        assert s["zztest"], s


def e(t):
    return html.escape(str(t))


def img(name, width):
    im = Image.open(os.path.join(SHOTS, name)).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


FLOWS = [("adult-new", "Adult, new patient", "Online booking, never examined here. No module opened."),
         ("adult-returning", "Adult, returning", "Examined here before; age 46 opens close-up questions."),
         ("child-new", "Child, new", "Routine visit, parent fills it in. No module opened."),
         ("dry-eye", "Adult, dry eye", "The dry-eye tile opens the dry-eye screen."),
         ("myopia-child", "Child, myopia", "Booking said 'Myopia Control Consultation'.")]

PAPER = [
    ("Details", "Pre-filled from Optomate; 'Updated online already? Skip'. Home phone, title, GP gone.",
     "Check what's on file, not re-type it."),
    ("Private health", "Yes/No, then fund tiles or type. Goes to the notes.", "Fund back for claiming."),
    ("Reason / symptoms", "Tap-all reasons; patient's words verbatim in Reason for visit.",
     "Know why they came before the exam."),
    ("Last eye exam", "New: asked. Returning: 'Last exam here' read from Optomate, shown to staff.",
     "How long it has been."),
    ("Ocular history", "Ticks plus an always-there typed box.", "Must be able to type."),
    ("Glasses", "Each pair's type and age; sunglasses folded in.", "Prompts them that specs are old."),
    ("Looking at new?", "Glasses / Sunglasses / Both / Not right now - always asked.",
     "Who is keen, before the exam."),
    ("Contact lenses", "One row: No, Daily, 2-weekly, Monthly, Ortho-K.", "Soft CLs not a priority."),
    ("Hobbies", "Tiles plus 'Tell us more'.", "Tailor multifocals and solutions."),
    ("Driving", "Do you drive? then Lots of night driving?", "Driving-oriented specs."),
    ("Screens", "Which screens, hours on each.", "Working distances."),
    ("Health, family", "New: full ticks. Returning: 'Anything new?' first.", "Same answers, less re-asking."),
    ("Water, sleep, outdoors, BP, bloods", "Gone from the core; water, sleep, outdoors only in modules.",
     "Low priority (Mark)."),
]

PATHS = [("Adult, returning, routine", "12", "~1 min 20 s", "Details checked or skipped; +10 s if 40+"),
         ("Adult, new (booked online)", "16", "~2.5 min", "~3.3 min if every detail is typed"),
         ("Child, new", "14", "~2 min", "Parent types name and relationship"),
         ("Child, returning", "8", "~1.5 min", "")]
MODS = [("Dry eye", "dry tile, or booking says dry eye / IPL", "11", "+50 s"),
        ("Myopia (child)", "myopia tile or 'can't see the board', booking myopia / Ortho-K / atropine, or Mum/Dad short-sighted", "3-4", "+15 s"),
        ("Headaches", "headache tile", "3 (child 1)", "+13 s"),
        ("Close-up vision", "'Blurry up close' tile, age 40+, or booking says multifocal", "2", "+10 s"),
        ("Flashes / floaters", "flashes tile", "2", "+10 s")]

CSS = """
:root { --ink:#111; --muted:#555; --line:#ddd; --green:#2e6b4f; --amber:#b45309; --violet:#6d28d9; --bg:#fff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:17px/1.45 -apple-system, "Segoe UI", Arial, sans-serif; }
main { max-width:860px; margin:0 auto; padding:16px 16px 60px; }
h1 { font-size:26px; margin:6px 0 4px; }
h2 { font-size:22px; margin:34px 0 6px; }
h3 { font-size:19px; margin:22px 0 4px; }
.lead { font-size:18px; margin:0 0 10px; }
.muted { color:var(--muted); font-size:15px; }
table { width:100%; border-collapse:collapse; margin:6px 0; }
th { text-align:left; font-size:14px; color:var(--muted); font-weight:600; padding:6px; border-bottom:2px solid var(--ink); }
td { padding:8px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
td.b { font-weight:700; } td.n { text-align:right; font-variant-numeric:tabular-nums; font-weight:700; white-space:nowrap; }
.flow { display:flex; flex-direction:column; align-items:center; gap:0; margin:10px 0 4px; }
.box { border:2px solid var(--ink); border-radius:10px; padding:8px 14px; text-align:center; max-width:520px; width:100%; }
.box.mod { border-color:var(--amber); }
.box small { display:block; color:var(--muted); font-size:14px; }
.arrow { width:2px; height:18px; background:var(--ink); position:relative; }
.arrow:after { content:""; position:absolute; bottom:-6px; left:-5px; border:6px solid transparent; border-top-color:var(--ink); }
.mods { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:8px; width:100%; max-width:620px; }
.mods .box { border-color:var(--amber); padding:6px 8px; font-size:15px; }
.key { font-size:15px; color:var(--muted); }
.key b.a { color:var(--amber); }
figure { margin:14px 0 26px; }
figure img { width:100%; height:auto; border:1px solid var(--line); border-radius:8px; display:block; }
figcaption { font-size:17px; margin-top:6px; }
figcaption .n { font-weight:700; margin-right:6px; }
.ipad img { max-width:420px; }
@media (max-width:600px) { td, th { font-size:15px; } .ipad img { max-width:100%; } }
"""


def flow_chart():
    return """<div class="flow">
<div class="box"><b>Your details</b><small>pre-filled; skip if updated online</small></div><div class="arrow"></div>
<div class="box"><b>Today</b><small>what's brought you in (taps) · anything else · private health · how heard (new)</small></div><div class="arrow"></div>
<div class="box mod"><b>Modules open only if called for</b><small>by a Today tap, the online booking reason, or age</small></div><div class="arrow"></div>
<div class="mods">
<div class="box">Dry eye</div><div class="box">Myopia (child)</div><div class="box">Headaches</div>
<div class="box">Close-up vision</div><div class="box">Flashes / floaters</div></div><div class="arrow"></div>
<div class="box"><b>Glasses &amp; daily life</b><small>each pair's age · looking at new? · contacts · hobbies · driving · screens</small></div><div class="arrow"></div>
<div class="box"><b>Your eyes</b><small>new: last exam, history, family · returning: "Anything new?"</small></div><div class="arrow"></div>
<div class="box"><b>Your health</b><small>new: ticks, smoking, medicines · returning: "Anything new?"</small></div><div class="arrow"></div>
<div class="box"><b>Check and send</b></div></div>
<p class="key">Black = everyone. <b class="a">Amber</b> = only when called for. Myopia's family trigger opens it right after the eyes screen.</p>"""


def table(head, rows, num_cols=()):
    h = "".join(f"<th>{e(x)}</th>" for x in head)
    body = "".join("<tr>" + "".join(
        f'<td class="{"n" if i in num_cols else ("b" if i == 0 else "")}">{e(c)}</td>' for i, c in enumerate(r))
        + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


parts = [f"""<h1>iPad check-in v5</h1>
<p class="lead">A short core for everyone; extra screens only when the visit needs them.</p>
<p class="muted">Test copy, fake ZZTEST patients only. Nothing written to Optomate.</p>
<h2>The flow</h2>{flow_chart()}
<h2>Time per path</h2>{table(["Path", "Questions", "Time", "Note"], PATHS, (1, 2))}
{table(["Module", "Opens when", "Taps", "Adds"], MODS, (2, 3))}
<p class="muted">Times are estimates: about 4 s a tap row, 3 s a follow-up, typing extra.</p>
<h2>Your paper form → iPad</h2>{table(["Paper section", "On the iPad", "Why (your purpose)"], PAPER)}"""]

n = 0
for flow, title, sub in FLOWS:
    items = [s for s in shots if s["flow"] == flow]
    if not items:
        continue
    parts.append(f"<h2>{e(title)}</h2><p class='muted'>{e(sub)}</p>")
    for s in items:
        n += 1
        staff = "staff" in s["file"]
        src = img(s["file"], 1000 if staff else 560)
        parts.append(f'<figure class="{"" if staff else "ipad"}"><img src="{src}" alt="{e(s["caption"])}" loading="lazy">'
                     f'<figcaption><span class="n">{n}</span>{e(s["caption"])}</figcaption></figure>')

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>iPad check-in v5</title><style>{CSS}</style></head><body><main>
{''.join(parts)}
</main></body></html>"""
open(OUT, "w", encoding="utf-8").write(page)
print(OUT, round(len(page) / 1e6, 2), "MB,", n, "screens")
