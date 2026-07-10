"""ProVision T3 pricelist -> lenses/hoya.csv (for the next price update).

Usage:  pdftotext -layout "ProVision T3 Pricelist.pdf" t3.txt
        python lenses/convert_provision_t3.py t3.txt

Built against the April 2026 layout - if ProVision reshuffle the
columns, re-check the COATS_* lists against the section headers.

Only the SINGLE VISION sections (grind + stock) go in - that's what the
Lens Finder matches on. Progressives/vocational/bifocals are priced per
add/design and would mislead Rx matching until adds are supported.

Power ranges come from RANGES below, transcribed from the availability
charts in the HOYA Product Guide 2025 (page numbers in each band's note).
The charts are per-diameter, stair-stepped grids; each band here is a
conservative rectangle (+ optional sphere+cyl combined limit), so a job
right on a chart's staircase edge may be flagged unavailable when the
guide allows it - check the quoted guide page for borderline jobs.
Pricelist rows with no chart (polarised SVs) stay rangeless and the
Finder warns instead.
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

# --- Power ranges from the Product Guide 2025 availability charts ------------
# Band = (blank_mm, sph_min, sph_max, cyl_max_abs, combined_max_abs or None,
#         "guide page / caveat"). One CSV row is written per band.

RANGES = {
    # Stock - spherical Hilux (guide pp.54-66)
    "HL75":         [(75, -4.00, 4.00, 2, None, "p54")],
    "HLT_ET":       [(70, -6.00, 4.00, 2, None, "p54"),
                     (65, 4.25, 6.00, 2, None, "p54")],
    "HTU_SH":       [(70, -6.00, 3.00, 2, None, "p55; plus cyl over -1.00 comes on 65mm"),
                     (65, 3.25, 6.00, 2, None, "p55")],
    "HTU_VP":       [(70, -6.00, 3.00, 2, None, "p56; plus cyl over -1.00 comes on 65mm"),
                     (65, 3.25, 4.00, 2, None, "p56")],
    "HTU_DF":       [(70, -6.00, 4.00, 2, None, "p57"),
                     (65, 4.25, 6.00, 2, None, "p57")],
    "HTU_DFMO":     [(70, -6.00, 0.00, 2, 6.00, "p58"),
                     (65, 0.25, 6.00, 2, None, "p58")],
    "HTU_S2_SH":    [(70, -6.00, 0.00, 2, None, "p59"),
                     (65, 0.25, 4.00, 2, 4.00, "p59")],
    "HTU_S2_DFUV":  [(70, -6.00, 0.00, 2, 6.00, "p59"),
                     (65, 0.25, 4.00, 2, 4.00, "p59")],
    "HLPNX_STOCK":  [(70, -7.00, 4.00, 2, None, "p61"),
                     (70, -8.00, -7.25, 1.50, 8.75, "p61 cyl steps down past -7.00")],
    "STELLIFY150":  [(70, -6.00, 4.00, 2, None, "p60"),
                     (65, 4.25, 6.00, 2, None, "p60")],
    "STELLIFY155":  [(70, -8.00, 0.00, 2, None, "p62"),
                     (65, 0.25, 6.00, 2, None, "p62")],
    "STELLIFY160":  [(70, -8.00, 0.00, 2, 8.00, "p63"),
                     (65, 0.25, 6.00, 2, None, "p63")],
    "HLEYAS_STOCK": [(75, -4.00, 1.00, 2, None, "p64"),
                     (70, -8.00, -4.25, 2, 8.00, "p64"),
                     (70, 1.25, 2.00, 2, None, "p64"),
                     (65, 2.25, 6.00, 2, None, "p64")],
    "HLEYAS_S2":    [(70, -6.00, 0.00, 2, 6.00, "p65"),
                     (65, 0.25, 4.00, 2, 4.00, "p65")],
    "HLEYNOA_STOCK": [(75, -6.00, 0.00, 2, None, "p66"),
                      (70, -8.00, -6.25, 2, 8.00, "p66"),
                      (70, 0.25, 2.00, 2, None, "p66"),
                      (65, 2.25, 6.00, 2, None, "p66")],
    # Stock - aspheric Nulux (guide pp.67-71)
    "NULUX_VP":     [(75, -3.50, 0.00, 2, None, "p67"),
                     (70, -6.00, -3.75, 2, 6.00, "p67"),
                     (70, 0.25, 2.00, 2, None, "p67"),
                     (65, 2.25, 6.00, 2, None, "p67")],
    "NULUX_DF":     [(75, -3.50, 0.00, 2, None, "p67"),
                     (70, -6.00, -3.75, 2, 6.00, "p67"),
                     (70, 0.25, 4.00, 2, None, "p67"),
                     (65, 4.25, 6.00, 2, None, "p67")],
    "NULUXEYAS_STOCK": [(75, -6.00, 0.00, 2, None, "p68"),
                        (75, -7.50, -6.25, 2, 7.75, "p68"),
                        (70, 0.25, 2.00, 2, None, "p68"),
                        (65, 2.25, 6.00, 2, None, "p68")],
    "NLSTELLIFY":   [(75, -4.00, 0.00, 2, None, "p69"),
                     (70, 0.25, 2.00, 2, None, "p69"),
                     (65, 2.25, 3.00, 2, None, "p69")],
    "NULUXEYNOA_STOCK": [(75, -7.00, 2.00, 2, None, "p70"),
                         (70, -10.00, -7.25, 2, 10.00, "p70"),
                         (70, 2.25, 4.00, 2, None, "p70"),
                         (65, 4.25, 6.00, 2, None, "p70")],
    "NULUXEYVIA_STOCK": [(75, -7.00, -4.00, 2, None, "p71 minus only, starts at -4.00"),
                         (70, -8.00, -7.25, 2, 9.50, "p71"),
                         (70, -10.00, -8.25, 0, None, "p71 sphere only")],
    # Grind (guide pp.73-90)
    "RX_HTU":       [(70, -6.00, 0.00, 2, 6.00, "p73"),
                     (65, -8.00, 0.00, 6, 8.00, "p73"),
                     (60, -13.00, 0.00, 6, 13.00, "p73"),
                     (60, -20.00, -13.25, 0, None, "p73 sphere only"),
                     (65, 0.25, 14.00, 6, None, "p73")],
    "RX_PNX":       [(70, -10.00, 2.75, 6, 10.00, "p74"),
                     (65, 3.00, 10.00, 6, None, "p74")],
    "RX_STELLIFY155": [(70, -11.00, 0.00, 4, 11.00, "p75"),
                       (65, 0.25, 8.00, 4, None, "p75")],
    "RX_EYAS":      [(75, -13.00, 6.75, 6, 13.00, "p76"),
                     (70, 7.00, 10.00, 6, None, "p76")],
    "RX_EYNOA":     [(75, -5.00, 0.00, 6, None, "p77"),
                     (70, -13.00, 0.00, 6, 13.00, "p77"),
                     (70, 0.25, 5.50, 6, None, "p77"),
                     (65, 5.75, 8.00, 6, None, "p77")],
    "RX_NULUX":     [(75, -1.50, 0.50, 6, None, "p79"),
                     (70, -9.50, 0.00, 6, 9.75, "p79"),
                     (70, 0.75, 6.50, 6, None, "p79 lower plus partly on 75mm"),
                     (65, 6.75, 7.50, 6, None, "p79")],
    "RX_NULUXPNX":  [(75, -4.00, 2.50, 4, None, "p80 70/75mm"),
                     (70, -8.25, 0.00, 4, None, "p80"),
                     (70, -9.25, -8.50, 3, 12.25, "p80 chart ends -9.25"),
                     (70, 2.25, 6.00, 4, None, "p80"),
                     (65, 6.25, 8.00, 4, None, "p80")],
    "RX_NULUXEYAS": [(80, -2.00, 2.50, 6, None, "p81"),
                     (75, -4.50, 0.00, 6, None, "p81"),
                     (70, -12.00, 0.00, 6, 12.25, "p81"),
                     (75, 2.75, 4.75, 6, None, "p81"),
                     (70, 5.00, 8.00, 6, None, "p81")],
    "RX_NULUXEYNOA": [(75, -6.00, 0.00, 6, None, "p82"),
                      (70, -10.00, 0.00, 6, None, "p82"),
                      (65, -15.00, 0.00, 6, 15.25, "p82"),
                      (75, 0.25, 2.50, 6, None, "p82"),
                      (70, 2.75, 6.00, 6, None, "p82"),
                      (65, 6.25, 10.00, 6, None, "p82")],
    "RX_NULUXEYVIA": [(75, -4.00, 0.00, 5, None, "p83"),
                      (70, -10.00, 0.00, 5, None, "p83"),
                      (65, -15.00, 0.00, 5, 15.25, "p83"),
                      (75, 0.00, 2.50, 5, None, "p83"),
                      (70, 2.75, 6.00, 5, None, "p83"),
                      (65, 6.25, 12.00, 5, None, "p83")],
    "NIV_150":      [(70, -8.00, 2.50, 6, 8.25, "p84"),
                     (65, -10.00, 0.00, 6, 10.00, "p84"),
                     (65, 2.75, 10.00, 6, None, "p84")],
    "NIV_PNX":      [(70, -10.00, 8.00, 6, 10.00,
                      "p85 minus grid label reads 75mm but spec says 65/70 - using 70"),
                     (65, -13.00, 0.00, 6, 13.00, "p85"),
                     (65, 8.25, 9.00, 6, None, "p85")],
    "NIV_EYAS":     [(75, -2.00, 4.00, 6, None, "p86"),
                     (70, -13.00, 0.00, 6, 13.25, "p86"),
                     (70, 4.25, 8.00, 6, None, "p86")],
    "NIV_EYNOA":    [(75, -4.00, 4.00, 6, None, "p87"),
                     (70, -15.00, 0.00, 6, 15.25, "p87"),
                     (70, 4.25, 10.00, 6, None, "p87")],
    "NIV_EYVIA":    [(70, -12.00, 9.50, 6, None, "p88"),
                     (70, -17.75, -12.25, 6, 18.00, "p88"),
                     (70, -20.00, -18.00, 0, None, "p88 sphere only"),
                     (65, 9.75, 12.00, 6, None, "p88")],
    "EP_EYNOA":     [(80, -3.25, 0.00, 4, None, "p89 no plus below +2.00"),
                     (75, -6.25, 0.00, 4, None, "p89"),
                     (70, -8.50, 0.00, 4, None, "p89"),
                     (65, -11.00, 0.00, 4, 15.00, "p89"),
                     (75, 2.00, 2.50, 4, None, "p89"),
                     (70, 2.75, 6.00, 4, None, "p89"),
                     (65, 6.25, 10.00, 4, None, "p89")],
    "EP_EYVIA":     [(75, -4.00, 2.50, 6, None, "p90"),
                     (70, -12.00, 0.00, 6, None, "p90"),
                     (65, -16.00, 0.00, 6, None, "p90"),
                     (60, -18.00, 0.00, 6, 18.00, "p90 sphere only to -20.00"),
                     (70, 2.75, 6.00, 6, None, "p90"),
                     (65, 6.25, 12.00, 6, None, "p90")],
}

# Pricelist name (+ coating where the charts differ per coating) -> RANGES key.
STOCK_KEYS = {
    "Hilux Hard Easy Tint 75mm": "HL75",
    "Hilux Thin Easy Tint Hard": "HLT_ET",
    "Hilux Thin": {"Super Hard": "HTU_SH", "Hi-Vision ViewProtect": "HTU_VP",
                   "Diamond Finish": "HTU_DF"},
    "Hilux Thin Hi-Vision Meiryo Diamond": "HTU_DFMO",
    "Hilux Thin Sensity 2 Grey": {"Super Hard": "HTU_S2_SH",
                                  "Diamond Finish UV Control": "HTU_S2_DFUV"},
    "Hilux Phoenix": "HLPNX_STOCK",
    "Hilux Phoenix Hi-Vision Meiryo Diamond": "HLPNX_STOCK",
    "Hilux Stellify": "STELLIFY150",
    "Hilux Stellify 1.55 VP": "STELLIFY155",
    "Hilux Stellify 1.60 VP": "STELLIFY160",
    "Hilux Eyas": "HLEYAS_STOCK",
    "Hilux Eyas Sensity 2 Grey": "HLEYAS_S2",
    "Hilux Eynoa": "HLEYNOA_STOCK",
    "Hilux Eynoa Hi-Vision Meiryo Diamond": "HLEYNOA_STOCK",
    "Nulux": {"Hi-Vision ViewProtect": "NULUX_VP",
              "Diamond Finish UV Control": "NULUX_DF",
              "Full Control": "NULUX_DF"},
    "Nulux Eyas": "NULUXEYAS_STOCK",
    "Nulux Stellify 1.60 VP BlueControl": "NLSTELLIFY",
    "Nulux Eynoa": "NULUXEYNOA_STOCK",
    "Nulux Eyvia": "NULUXEYVIA_STOCK",
}
GRIND_KEYS = {
    "Hilux Thin (UV)": "RX_HTU",
    "Hilux Phoenix": "RX_PNX",
    "Hilux Stellify": "RX_STELLIFY155",
    "Hilux Eyas": "RX_EYAS",
    "Hilux Eynoa": "RX_EYNOA",
    "Nulux": "RX_NULUX",
    "Nulux Phoenix": "RX_NULUXPNX",
    "Nulux Eyas": "RX_NULUXEYAS",
    "Nulux Eynoa": "RX_NULUXEYNOA",
    "Nulux Eyvia": "RX_NULUXEYVIA",
    "Nulux iDentity V+": "NIV_150",
    "Nulux iDentity V+ Phoenix": "NIV_PNX",
    "Nulux iDentity V+ Eyas": "NIV_EYAS",
    "Nulux iDentity V+ Eynoa": "NIV_EYNOA",
    "Nulux iDentity V+ Eyvia": "NIV_EYVIA",
    "Nulux EP Eynoa": "EP_EYNOA",
    "Nulux EP Eyvia": "EP_EYVIA",
}

SENSITY_RE = re.compile(r"\s+Sensity.*$")


def bands_for(name, coating, lens_type):
    """-> (bands or None, extra_note or None) for a pricelist row."""
    if "Polarised" in name:
        return None, "polarised - no availability chart in the guide"
    base = SENSITY_RE.sub("", name)
    sens_note = None
    if base != name and lens_type == "grind":
        sens_note = "clear-lens range shown - Sensity availability can be narrower"
    keys = STOCK_KEYS if lens_type == "stock" else GRIND_KEYS
    entry = keys.get(name) if lens_type == "stock" and name in keys else keys.get(base)
    if isinstance(entry, dict):
        entry = entry.get(coating)
    if entry is None:
        return None, None
    return RANGES[entry], sens_note


def fmt(v):
    return f"{v:+.2f}"


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

    base_notes = []
    if "Sensity" in name:
        base_notes.append("Sensity photochromic")
    if "Polarised" in name:
        base_notes.append("polarised")

    for coat, val in zip(coats, values):
        if not PRICE_RE.match(val):
            continue
        bands, extra = bands_for(name, coat, mode)
        common = {
            "brand": "Hoya", "lens": name, "index": index, "type": mode,
            "design": "Single vision", "price": val.lstrip("$"),
            "coating": coat,
        }
        if bands is None:
            rows_out.append({**common, "blank_mm": diamtr,
                             "sph_min": "", "sph_max": "",
                             "cyl_max": "", "combined_max": "",
                             "notes": "; ".join(
                                 [f"blanks {diamtr}"] + base_notes
                                 + ([extra] if extra else []))})
            continue
        for blank, smin, smax, cyl, comb, page in bands:
            rows_out.append({**common, "blank_mm": f"{blank:g}",
                             "sph_min": fmt(smin), "sph_max": fmt(smax),
                             "cyl_max": f"-{cyl:.2f}" if cyl else "0",
                             "combined_max": f"{comb:.2f}" if comb else "",
                             "notes": "; ".join(
                                 base_notes + [f"guide {page}"]
                                 + ([extra] if extra else []))})

OUT.parent.mkdir(exist_ok=True)
with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "brand", "lens", "index", "type", "design", "blank_mm",
        "sph_min", "sph_max", "cyl_max", "combined_max",
        "price", "coating", "notes"])
    writer.writeheader()
    writer.writerows(rows_out)

stock = sum(1 for r in rows_out if r["type"] == "stock")
ranged = sum(1 for r in rows_out if r["sph_min"])
print(f"{len(rows_out)} rows ({stock} stock, {len(rows_out) - stock} grind, "
      f"{ranged} with ranges) -> {OUT}")
