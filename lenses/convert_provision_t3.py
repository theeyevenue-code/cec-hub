"""ProVision T3 pricelist -> lenses/hoya.csv (for the next price update).

Usage:  pdftotext -layout "ProVision T3 Pricelist.pdf" t3.txt
        python lenses/convert_provision_t3.py t3.txt

Built against the April 2026 layout - if ProVision reshuffle the
columns, re-check the COATS_* lists against the section headers.

Only the SINGLE VISION sections (grind + stock) go in — that's what the
Lens Finder matches on. Progressives/vocational/bifocals are priced per
add/design and would mislead Rx matching until adds are supported.
"""

import csv
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "provision.txt"
OUT = Path(__file__).parent / "hoya.csv"

COATS_GRIND = ["Super Hard", "Hi-Vision ViewProtect", "ViewProtect BlueControl",
               "Hi-Vision Meiryo Diamond", "Diamond Finish UV BlueControl",
               "Full Control", "Hi-Vision Sun Pro", "Mirror"]
COATS_STOCK_SPH = ["Hard (tintable)", "Super Hard", "Hi-Vision ViewProtect",
                   "Diamond Finish", "Diamond Finish UV Control",
                   "Hi-Vision Meiryo Diamond"]
COATS_STOCK_ASP = ["Super Hard", "Hi-Vision ViewProtect",
                   "Diamond Finish UV Control", "Full Control"]
COATS_STELLIFY = ["Super Hard", "Hi-Vision ViewProtect", "ViewProtect BlueControl"]

INDEX_RE = re.compile(r"^\d\.\d{2}$")
DIAM_RE = re.compile(r"^\d{2}(?:/\d{2})*(?:mm)?$")
PRICE_RE = re.compile(r"^\$\d+(?:\.\d{2})?$")

rows_out = []
mode = None          # None | "grind" | "stock"
coats = None

for line in SRC.read_text(encoding="utf-8").splitlines():
    s = line.strip()
    if "SINGLE VISION - GRIND" in s:
        mode, coats = "grind", COATS_GRIND
        continue
    if "SINGLE VISION - STOCK" in s:
        mode, coats = "stock", COATS_STOCK_SPH
        continue
    if mode == "stock" and s.startswith("ASPHERIC DESIGN"):
        coats = COATS_STOCK_ASP
        continue
    if mode == "stock" and "STELLIFY SINGLE VISION" in s:
        coats = COATS_STELLIFY
        continue
    if "SUNDRY OPTIONS" in s:
        break
    if mode is None or not s or s.startswith("*"):
        continue

    parts = re.split(r"\s{2,}", s)
    if len(parts) < 5 or not INDEX_RE.match(parts[1]) or not DIAM_RE.match(parts[2]):
        continue
    name, index, diamtr, values = parts[0], parts[1], parts[2], parts[4:]
    if len(values) > len(coats):
        raise SystemExit(f"Too many price cells for '{name}': {values}")

    notes = [f"blanks {diamtr}"]
    if "Sensity" in name:
        notes.append("Sensity photochromic")
    if "Polarised" in name:
        notes.append("polarised")
    sph_min = sph_max = ""
    if mode == "stock" and name == "Nulux" and index == "1.50":
        sph_min, sph_max = "-4.00", "+4.00"
        notes.append("range per Mark - confirm against Hoya guide")
    if mode == "stock" and name.startswith("Hilux Stellify") and index == "1.50":
        notes.append("CHECK: Mark says 75mm blank; this pricelist lists 65/70")
    if mode == "stock" and "Stellify" in name and index == "1.55":
        notes.append("plus powers only per Mark - range to confirm")

    for coat, val in zip(coats, values):
        if not PRICE_RE.match(val):
            continue
        rows_out.append({
            "brand": "Hoya",
            "lens": name,
            "index": index,
            "type": mode,
            "design": "Single vision",
            "blank_mm": diamtr,
            "sph_min": sph_min,
            "sph_max": sph_max,
            "cyl_max": "",
            "combined_max": "",
            "price": val.lstrip("$"),
            "coating": coat,
            "notes": "; ".join(notes),
        })

OUT.parent.mkdir(exist_ok=True)
with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
    writer.writeheader()
    writer.writerows(rows_out)

stock = sum(1 for r in rows_out if r["type"] == "stock")
print(f"{len(rows_out)} rows ({stock} stock, {len(rows_out) - stock} grind) -> {OUT}")
