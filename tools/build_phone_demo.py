"""Build the OFFLINE phone demo of the iPad check-in (patient side).

    python tools/build_phone_demo.py [--engine DIR] [--out FILE]

One self-contained HTML file: the real ipad/ipad.css and ipad/ipad.js inlined
UNCHANGED, the engine's questions.json resolved exactly as `cli questions
--audience X [--returning]` serves it, and a mock of the Hub's /api/checkin/ipad
routes inside the page (window.fetch is replaced; a CSP blocks all network).
Made-up ZZTEST patients only. Nothing is saved anywhere.

Rebuild whenever questions.json or the iPad page changes. Template:
tools/phone-demo/template.html. Mark, 10 Oct 2026: "I want to test it out"
on a phone - not a move to a phone-based system.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
TEMPLATE = HUB / "tools" / "phone-demo" / "template.html"
DEFAULT_ENGINE = Path(r"C:\CEC\wt-checkin-codex")
DEFAULT_OUT = Path(r"C:\CEC\CEC-Optomate-Agent\local-reports\checkin-phone-demo.html")

# Made-up people only (ZZTEST). Mobiles are from ACMA's fictitious-use range
# (0491 570 xxx); emails are @example.com. No Medicare numbers.
PATHS = [
    {"id": "adult-new", "title": "Adult, new", "set": "adult-new", "audience": "adult",
     "blurb": "Sam ZZTEST, booked online, first visit. Age 41.",
     "prefill": {"given_name": "Sam", "family_name": "ZZTEST", "dob": "14/03/1985",
                 "phone_mobile": "0491 570 156", "email": "sam.zztest@example.com"}},
    {"id": "adult-returning", "title": "Adult, returning", "set": "adult-returning", "audience": "adult",
     "returning": True, "last_exam": "14 Mar 2025",
     "blurb": "Alex ZZTEST, last exam here 14 Mar 2025. Age 68.",
     "prefill": {"given_name": "Alexandra", "family_name": "ZZTEST", "preferred_name": "Alex",
                 "dob": "02/07/1958", "phone_mobile": "0491 570 157",
                 "email": "alex.zztest@example.com", "address": "1 Demo Street",
                 "suburb": "Concord", "postcode": "2137", "occupation": "RETIRED", "source": "Google"}},
    {"id": "child-new", "title": "Child, new (parent fills in)", "set": "child-new", "audience": "child",
     "blurb": "Riley ZZTEST, age 9, first visit.",
     "prefill": {"given_name": "Riley", "family_name": "ZZTEST", "dob": "05/05/2017",
                 "phone_mobile": "0491 570 158", "email": "parent.zztest@example.com"}},
    {"id": "dry-eye", "title": "Dry eye adult", "set": "adult-new", "audience": "adult",
     "booking_reason": "Dry Eye Assessment",
     "blurb": "Jordan ZZTEST, booked \u201cDry Eye Assessment\u201d online. Age 46.",
     "prefill": {"given_name": "Jordan", "family_name": "ZZTEST", "dob": "20/11/1979",
                 "phone_mobile": "0491 570 159", "email": "jordan.zztest@example.com"}},
    {"id": "myopia-child", "title": "Myopia child", "set": "child-new", "audience": "child",
     "booking_reason": "Myopia Control Consultation",
     "blurb": "Charlie ZZTEST, booked \u201cMyopia Control Consultation\u201d. Age 11.",
     "prefill": {"given_name": "Charlie", "family_name": "ZZTEST", "dob": "12/08/2015",
                 "phone_mobile": "0491 570 110", "email": "parent2.zztest@example.com"}},
]

SETS = {"adult-new": ("adult", False), "adult-returning": ("adult", True), "child-new": ("child", False)}


def build(engine: Path, out: Path) -> Path:
    sys.path.insert(0, str(engine))
    from checkin import schema  # the engine's own loader + validator + resolver

    qs = schema.load()
    sets = {k: schema.resolve(qs, aud, returning=ret) for k, (aud, ret) in SETS.items()}
    fixture_lists = json.loads((engine / "checkin" / "fixtures" / "lists.json").read_text(encoding="utf-8"))
    needed = {q.get("list") for s in sets.values() for sc in s["screens"]
              for q in sc["questions"] if q.get("type") == "picklist"}
    lists = {k: fixture_lists.get(k) or [] for k in sorted(needed) if k in ("occupations", "sources")}
    data = {"version": qs["version"], "sets": sets, "lists": lists, "paths": PATHS}

    css = (HUB / "ipad" / "ipad.css").read_text(encoding="utf-8")
    js = (HUB / "ipad" / "ipad.js").read_text(encoding="utf-8")
    for name, text in (("ipad.css", css), ("ipad.js", js)):
        if "</script" in text.lower() or "</style" in text.lower():
            raise SystemExit(f"{name} contains a closing tag - cannot inline it safely")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = (html.replace("__BUILT__", f"{datetime.now():%d %b %Y %H:%M}, form {qs['version']}")
                .replace("__VERSION__", qs["version"])
                .replace("__IPAD_CSS__", css)
                .replace("__DATA__", blob)
                .replace("__IPAD_JS__", js))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", type=Path, default=DEFAULT_ENGINE,
                    help="Optomate-agent checkout holding checkin/questions.json")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    p = build(a.engine, a.out)
    print(f"{p}  {p.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
