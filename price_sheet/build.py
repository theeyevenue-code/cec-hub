"""Step 1: the approved 9 Nov iPad price sheet demo -> the Hub's patient price sheet (no patient data).
Source: local-reports/ipad-price-sheet/template.html (Mark: "that's the one"). Output: cec-hub static/price-sheet.html.
Patient mode: opens straight on the sheet, no name, no "suits your prescription", no staff steps; the summary ends
with "Back to the price sheets". Fonts and logos load from the Hub (/pricesheets/assets, /pricesheets/brand)."""
import hashlib, json, pathlib, sys
SRC = pathlib.Path(__file__).resolve().parent / "template.html"
PRICES = pathlib.Path(__file__).resolve().parent / "prices.json"   # the one price file (steps 2-3)
SB = pathlib.Path(r"C:\Users\ConcordeyecareServer\Claude\cec-second-brain")
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent / "static" / "price-sheet.html"
s = SRC.read_text(encoding="utf-8")


def rep(old, new, n=1):
    global s
    assert s.count(old) == n, (old[:70], s.count(old))
    s = s.replace(old, new)


for w, name in ((300, "Light"), (400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold")):
    rep(f"url(data:font/ttf;base64,{{{{F{w}}}}})", f"url(/pricesheets/assets/fonts/Hind-{name}.ttf)")
z = (SB / "price-lists/assets/zeiss-logo.svg").read_text(encoding="utf-8").replace("<svg ", '<svg class="zlogo" ', 1)
s = s.replace("{{ZEISS}}", z)
raw = PRICES.read_bytes()
data = json.loads(raw.decode("utf-8"))
data["version"] = hashlib.sha256(raw).hexdigest()[:12]   # the server refuses a page built from other prices
rep("{{PRICES}}", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
s = s.replace("data:image/jpeg;base64,{{CEC}}", "/pricesheets/brand/Concord%20Eyecare%20LOGO%20HORIZ.jpg")
assert "{{" not in s, "placeholder left"

# the fixed 1180x820 stage scales to the screen
rep("body { width:1180px; height:820px; overflow:hidden; position:relative; margin:0 auto; }",
    "body { width:1180px; height:820px; overflow:hidden; position:relative; margin:0 auto; transform-origin:0 0; }\n"
    "html { overflow:hidden; background:#fff; }")

# patient mode: a nameless viewer; the who line, the rec chip and the staff row only appear with a patient
rep('<div class="who">Lenses for <b>${esc(p.first)}</b></div>', '${p.first ? `<div class="who">Lenses for <b>${esc(p.first)}</b></div>` : ""}')
rep('<div class="staffrow"><span class="lab">Staff</span>',
    '<div class="staffrow" id="patrow" style="display:none"><button class="cta" id="backsheets">&larr; Back to the price sheets</button></div>\n'
    '    <div class="staffrow" id="staffrow"><span class="lab">Staff</span>')
rep("""  $("sum-who").textContent = st.p.first;""",
    """  $("sum-who").textContent = st.p.first;
  $("sum-who").parentElement.style.display = st.p.first ? "" : "none";
  $("staffrow").style.display = PATIENT ? "none" : "";
  $("patrow").style.display = PATIENT ? "" : "none";""")
rep("""$("again").onclick = () => { st = {}; drawPats(); show("s-staff"); };
drawPats();""",
    """$("again").onclick = () => { st = {}; drawPats(); show("s-staff"); };
/* Hub patient price sheet (Mark 10 Nov 2026, step 1): no patients, no staff steps; ?staff=demo keeps the old demo. */
const PATIENT = new URLSearchParams(location.search).get("staff") !== "demo";
const VIEWER = { first:"", sur:"", rec:null, thick:[], h:0, pd:{} };
$("backsheets").onclick = () => { st.chosen = null; drawPage(); show("s-sheet"); };
function fit() { const k = Math.min(innerWidth / 1180, innerHeight / 820); document.body.style.transform = k < 1 || k > 1 ? `scale(${k})` : ""; }
addEventListener("resize", fit); fit();
if (PATIENT) { st = { p: VIEWER, tab: (location.hash.slice(1) && PAGES.find(x => x.key === location.hash.slice(1))) ? location.hash.slice(1) : "sv" }; drawPage(); show("s-sheet"); }
else drawPats();""")
rep("""  st = { p: PATS[pi], tab: go }; drawPage(); show("s-sheet");""",
    """  st = { p: PATIENT ? VIEWER : PATS[pi], tab: go }; drawPage(); show("s-sheet");""")
rep("<title>Lens price sheet</title>", "<title>Lens price sheets</title>")
rep('<div class="mockflag">Mock-up · fake patients · nothing is written</div>',
    '<div class="mockflag" id="mockflag" style="display:none">Demo · fake patients · nothing is written</div>')
rep("if (PATIENT) {", 'if (!PATIENT) $("mockflag").style.display = "";\nif (PATIENT) {')
OUT.write_text(s, encoding="utf-8")
print("built", OUT, len(s))
