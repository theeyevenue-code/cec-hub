"""PD shift to fit a stock blank (Mark, 9 Nov 2026).

Mark's rule, in his words: "if pd not large enough for blank size I'd like a variation option where
we allow pd changes to make blank size fit if it's less than 0.7D prism horizontally total" - agreed
the same day: SINGLE VISION ONLY, STOCK LENSES ONLY, CAP 3 MM TOTAL.

Prentice: prism (D) = horizontal power (D) x decentration (cm), per eye, added across both eyes
(absolute values - conservative; with both eyes moved outward the bases are the same way anyway).
Widening the job PD by d mm on one eye cuts that eye's blank need by 2d mm (the blank rule is
ED + 2 x per-eye decentration + 2), so only an OUTWARD shift ever helps, and only while the frame
PD is wider than the patient's.

This is the one place the Hub works out a clinical number instead of copying it, under Mark's
rule above; the measured PD stays on the Rx and the job's practice comment records both.
The Optomate engine (orders/pickjob.py, CEC-Optomate-Agent) re-checks the same rule from Optomate's
own Rx before it writes anything - this module only offers the variation."""
from __future__ import annotations

import math

MAX_PRISM = 0.7        # dioptres, horizontal, both eyes added
MAX_TOTAL_MM = 3.0     # both eyes added
STEP_MM = 0.5
SV_PARTS = ("distance", "near")


def horizontal_power(sph, cyl=0.0, axis=None) -> float:
    """Power in the 180 meridian: S + C sin^2(axis). No axis with a cyl -> the worse of S and S+C."""
    s, c = float(sph or 0), float(cyl or 0)
    if not c:
        return s
    if axis in (None, ""):
        return s if abs(s) >= abs(s + c) else s + c
    return s + c * math.sin(math.radians(float(axis))) ** 2


def prism(powers: dict, shift: dict) -> float:
    """Total horizontal prism (D) for a per-eye shift in mm."""
    return sum(abs(powers.get(e, 0.0)) * shift.get(e, 0.0) / 10 for e in shift)


def base_word(powers: dict) -> str:
    """Moving the optical centres outward: base-in for minus, base-out for plus."""
    signs = {p < 0 for p in powers.values() if p}
    if signs == {True}:
        return "base-in"
    if signs == {False}:
        return "base-out"
    return "mixed base"


def _need(a, dbl, ed, mono_pd) -> int:
    """lenses.min_blank_from_frame for one eye (monocular PD)."""
    return math.ceil((ed if ed else a + 2) + max(a + dbl - 2 * mono_pd, 0) + 2)


def find(frame: dict, pds: dict, powers: dict, largest: dict) -> dict | None:
    """The smallest outward PD shift that lets every eye fit its largest stock blank, inside the
    rule; None when none does (then the next-best lens applies).
    frame {"a","dbl","ed"}; pds {"R": mono mm, ...}; powers {"R": horizontal D}; largest {"R": mm}."""
    if not frame or frame.get("dbl") is None or not pds or set(pds) != set(largest):
        return None
    a, dbl, ed = float(frame["a"]), float(frame["dbl"]), frame.get("ed")
    ed = float(ed) if ed else None
    shift = {}
    for e, pd in pds.items():
        big = largest.get(e)
        if big is None:
            return None
        d = 0.0
        while _need(a, dbl, ed, pd + d) > big + 1e-9:
            d += STEP_MM
            if d > MAX_TOTAL_MM + 1e-9 or a + dbl - 2 * (pd + d) < -1e-9:
                return None
        shift[e] = d
    total = sum(shift.values())
    if total <= 0 or total > MAX_TOTAL_MM + 1e-9:
        return None
    p = prism(powers, shift)
    if round(p, 4) >= MAX_PRISM:          # Mark: "less than 0.7D"
        return None
    new = {e: pds[e] + shift[e] for e in pds}
    words = " / ".join(f"{e} {pds[e]:g} → {new[e]:g}" for e in sorted(pds, key=lambda e: e != "R")
                       if shift[e])
    return {"shift": shift, "pd_from": dict(pds), "pd_to": new, "total_mm": total,
            "prism": round(p, 2), "base": base_word({e: powers.get(e, 0.0) for e in shift if shift[e]}),
            "blank": {e: largest[e] for e in pds},
            "line": f"Fits with PD {words} ({total:g} mm, {p:.2f}Δ {base_word(powers)} total)"}
