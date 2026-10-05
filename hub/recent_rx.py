"""Today's patients — the spectacle scripts just issued, for the Lens Finder.

Mark, 5 Oct 2026: show the last few patients who left the consult room today;
staff tap a name and the issued script fills in for both eyes, so the Lens
Finder answers stock / grind without retyping the Rx.

The Hub has no Optomate client by design. Like the recall tile (hub/recall.py)
it runs a READ-ONLY command in the agent repo and reads the one JSON line it
prints:  python -m pulls.recent_rx --json --n 3 [--date YYYY-MM-DD] [--fixture]

Privacy: the dispensing bench may face patients, so the agent sends given name
+ surname initial only — no DOB, no patient ID. This module logs nothing about
any patient and the endpoint is served no-store.

Numbers: every Rx value is the agent's verbatim copy of the issued script. The
ONE figure worked out here is a readers sphere (sphere + add, per eye), and it
is never shown without its working ("R +1.00 + add +2.00 = +3.00"). The
binocular PD for the blank helper is R PD + L PD, also shown with its working.
"""

import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMEOUT_S = 15
DEFAULT_N = 3

MULTIFOCAL_KIND = "Progressive"   # the catalogue's multifocal category


def not_connected(msg: str) -> dict:
    return {"connected": False, "message": msg}


def _agent_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("optomate_agent", {}) or {}


def agent_dir(cfg: dict):
    d = _agent_cfg(cfg).get("agent_dir")
    return Path(d) if d else None


# ---- numbers -------------------------------------------------------------------
def dec(raw):
    """Optomate's signed string -> Decimal. 'PLANO' -> 0. None when not a number."""
    s = str(raw if raw is not None else "").strip()
    if s.upper() in ("PLANO", "PL"):
        return Decimal("0")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def fmt_power(d) -> str:
    """+1.25 / -0.50 / 0.00 — how a script writes it."""
    if d is None:
        return ""
    return "0.00" if d == 0 else f"{d:+.2f}"


def fmt_mm(d) -> str:
    return f"{d.normalize():f}" if d is not None else ""


# ---- the three ways to read a script with an add --------------------------------
def _eye_for_check(eye):
    """{sph, cyl} for /api/lenses/check, or None when the eye has no power to
    check (balance lens, blank or unreadable sphere, eye not on the script)."""
    if not eye:
        return None
    sph = dec(eye.get("sph"))
    if sph is None:
        return None
    cyl = dec(eye.get("cyl")) or Decimal("0")
    return {"sph": sph, "cyl": cyl}


def _out(e):
    return None if e is None else {"sph": float(e["sph"]), "cyl": float(e["cyl"])}


def modes(patient: dict) -> dict:
    """distance / readers / multifocal payloads for the existing two-eye check,
    each with a plain line saying what is being checked."""
    right, left = patient.get("right"), patient.get("left")
    eyes = {"R": (right, _eye_for_check(right)), "L": (left, _eye_for_check(left))}
    adds = {s: dec((e or {}).get("add")) for s, (e, _) in eyes.items()}
    has_add = any(a for a in adds.values())

    dist_line = " · ".join(
        f"{s} {fmt_power(c['sph'])}" + (f" / {fmt_power(c['cyl'])}" if c["cyl"] else "")
        for s, (_, c) in eyes.items() if c)
    out = {"distance": {"right": _out(eyes["R"][1]), "left": _out(eyes["L"][1]),
                        "kind": "Single vision", "line": dist_line}}
    if not has_add:
        return out

    # Readers: each eye's own sphere + its own add. Cyl and axis unchanged.
    parts, r_eyes, notes = [], {}, []
    for s, (_, c) in eyes.items():
        if not c:
            continue
        a = adds[s]
        if a:
            total = c["sph"] + a
            parts.append(f"{s} {fmt_power(c['sph'])} + add {fmt_power(a)} = {fmt_power(total)}")
            r_eyes[s] = {"sph": total, "cyl": c["cyl"]}
        else:
            parts.append(f"{s} {fmt_power(c['sph'])} (no add on the script for this eye)")
            notes.append(f"{s} has no add on the script — its sphere is checked as written.")
            r_eyes[s] = c
    out["readers"] = {"right": _out(r_eyes.get("R")), "left": _out(r_eyes.get("L")),
                      "kind": "Single vision", "line": " · ".join(parts),
                      "cyl_note": "Cyl and axis as on the script.", "notes": notes}

    # Multifocal: the existing check takes one add for the pair. Use the larger
    # when they differ (it is the one more likely to fall outside a design's
    # range) and say so.
    add_vals = [a for a in adds.values() if a]
    m_add = max(add_vals)
    m_notes = []
    if len(set(add_vals)) > 1:
        m_notes.append(f"Adds differ — checked at the higher, {fmt_power(m_add)}.")
    out["multifocal"] = {"right": _out(eyes["R"][1]), "left": _out(eyes["L"][1]),
                         "kind": MULTIFOCAL_KIND, "add": float(m_add),
                         "line": f"{dist_line} · add {fmt_power(m_add)}",
                         "notes": m_notes}
    return out


def binocular_pd(patient: dict) -> dict | None:
    """R PD + L PD for the blank helper, with its working. None unless both
    monocular PDs are on the script."""
    r = dec((patient.get("right") or {}).get("pd"))
    l = dec((patient.get("left") or {}).get("pd"))
    if not r or not l:
        return None
    total = r + l
    return {"value": float(total),
            "line": f"PD {fmt_mm(r)} + {fmt_mm(l)} = {fmt_mm(total)}"}


def enrich(patient: dict) -> dict:
    """Agent record -> what the page needs. Copies only whitelisted fields, so
    nothing new the agent might send can reach the screen by accident."""
    p = {k: patient.get(k) for k in ("label", "time", "optom", "status")}
    p["label"] = str(p["label"] or "Patient")[:20]
    p["flags"] = [str(f.get("text") or "") for f in (patient.get("flags") or [])
                  if isinstance(f, dict) and f.get("text")]
    if p["status"] != "script":
        p["status"] = "no_script"
        return p
    eye_keys = ("sph", "cyl", "axis", "add", "intadd", "pd", "prism")
    p["right"] = ({k: patient["right"].get(k) for k in eye_keys}
                  if isinstance(patient.get("right"), dict) else None)
    p["left"] = ({k: patient["left"].get(k) for k in eye_keys}
                 if isinstance(patient.get("left"), dict) else None)
    p["modes"] = modes(p)
    p["has_add"] = "readers" in p["modes"]
    p["pd"] = binocular_pd(p)
    p["checkable"] = bool(p["modes"]["distance"]["right"] or p["modes"]["distance"]["left"])
    return p


# ---- the subprocess ------------------------------------------------------------
def recent(cfg: dict, n: int = DEFAULT_N, day: str = "", fixture: bool = False) -> dict:
    """Ask the agent for today's patients. Never raises."""
    try:
        n = max(1, min(10, int(n)))
    except (TypeError, ValueError):
        n = DEFAULT_N
    day = str(day or "").strip()
    if day and not DATE_RE.match(day):
        day = ""
    dpath = agent_dir(cfg)
    if dpath is None:
        return not_connected("Optomate isn't connected on this computer.")
    if not (dpath / "pulls" / "recent_rx.py").is_file():
        return not_connected("Today's patients isn't set up on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    argv = [python, "-m", "pulls.recent_rx", "--json", "--n", str(n)]
    if day:
        argv += ["--date", day]
    if fixture:
        argv += ["--fixture"]
    try:
        proc = subprocess.run(argv, cwd=str(dpath), capture_output=True, text=True,
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"connected": True, "error": "Optomate took too long — try again."}
    except OSError:
        return not_connected("Couldn't reach Optomate from this computer.")
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data:
        return {"connected": True, "error": "Couldn't read today's patients."}
    if not data.get("ok"):
        return {"connected": True,
                "error": str(data.get("error") or "Couldn't read today's patients.")}
    return {"connected": True, "date": str(data.get("date") or ""),
            "fixture": bool(data.get("fixture")), "more": bool(data.get("more")),
            "patients": [enrich(p) for p in (data.get("patients") or [])
                         if isinstance(p, dict)]}
