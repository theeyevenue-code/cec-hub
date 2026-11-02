"""python <hub>\\hub\\lens_bridge.py <command> - the Lens options page's READ-ONLY
link to Optomate's database (Mark, 3 Nov 2026).

Run by hub/lensoptions.py as a subprocess, the same shape as the Send guide's
hub/guide_bridge.py: one JSON object on the last stdout line, exit 0 even on
failure ({"error": "plain words"}). It reads Optomate's SQL Server with Windows
auth through sqlcmd (WITH (NOLOCK), SELECT only), the way the agent's
pricing/audit_qr_pages.py::sqlq and pulls/recent_rx.py do - no credential is
copied into the Hub and nothing is ever written.

Commands
  recent [--date YYYY-MM-DD] [--n 3]
      -> {"date", "patients": [{"id", "given", "surname", "saved"}], "more"}
         the last N patients whose SPECTACLE_RX was saved (added or edited)
         that day, newest first, one line per patient.
  search --q TEXT
      -> {"patients": [{"id", "given", "surname", "dob", "last_rx"}], "more"}
         Mark's search rule (hub/guide_search.py): full name either order,
         surname + given pairs first, then the wide net; capped with "more".
         Only patients who have a saved spectacle Rx are listed.
  rx --pid N
      -> {"rxs": [...]} every SPECTACLE_RX row saved on the day of this
         patient's LATEST one (newest first; two on a day is usually a distance
         and a reading pair), each with both eyes verbatim and the lens type
         read from the Rx comment.

THE SAVED SPECTACLE Rx, not the exam: table SPECTACLE_RX (the script the optom
saved), never the subjective/final slots of EXAMINATION.

NUMBERS are copied verbatim (Optomate keeps them as strings such as '+2.25').
parse_power() only reads them so the Lens Finder can look them up; the page
always shows the verbatim string.

FIXTURE: LENS_OPTIONS_FIXTURE=1 -> fictional ZZTEST patients, the database is
never touched (for screenshots and demos).

PRIVACY: nothing here logs anything. Names go to the Hub page on the practice
network and nowhere else.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
if str(HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(HUB_ROOT))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEP = "\t"
SQL_TIMEOUT_S = 20
MAX_N = 10
DEFAULT_N = 3
MAX_SAME_DAY = 4

EYE_COLS = ("SPHERE", "CYLINDER", "AXIS", "ADD", "INTADD", "PD", "NEARPD",
            "HPRISM", "HPRISM_TYPE", "VPRISM", "VPRISM_TYPE")


class BridgeError(Exception):
    """A plain-words problem for staff."""


# ---------------------------------------------------------------------------
# SQL (read only)
# ---------------------------------------------------------------------------

def sqlq(query: str) -> list[list[str]]:
    """Rows from Optomate's SQL Server, tab separated. Raises BridgeError.
    sqlcmd can exit 0 on a SQL error, so a "Msg " line is an error too."""
    server = os.environ.get("LENS_OPTIONS_SQL_SERVER", r".\SQLEXPRESS")
    try:
        r = subprocess.run(
            ["sqlcmd", "-S", server, "-E", "-d", "Optomate", "-W", "-s", SEP,
             "-h", "-1", "-f", "o:65001", "-Q", "SET NOCOUNT ON; " + query],
            capture_output=True, timeout=SQL_TIMEOUT_S)
    except FileNotFoundError:
        raise BridgeError("Optomate's database tool (sqlcmd) isn't on this computer.")
    except subprocess.TimeoutExpired:
        raise BridgeError("Optomate's database took too long - try again.")
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    if r.returncode != 0:
        raise BridgeError("Couldn't read Optomate's database - is it running on the server?")
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        if line.startswith("Msg ") or line.startswith("Sqlcmd: Error"):
            raise BridgeError("Couldn't read Optomate's database.")
        rows.append([c.strip() for c in line.split(SEP)])
    return rows


def _txt(col: str) -> str:
    """A text column made safe for a tab-separated, one-line-per-row read."""
    return (f"REPLACE(REPLACE(REPLACE(ISNULL(CAST({col} AS nvarchar(4000)),''),CHAR(9),' '),"
            f"CHAR(13),' '),CHAR(10),' ')")


def _comment(col: str) -> str:
    """A long text column with its line breaks kept as a marker (the RTF keeps
    \\par anyway; old plain-text comments use real line breaks)."""
    return (f"REPLACE(REPLACE(REPLACE(ISNULL(CAST({col} AS nvarchar(4000)),''),CHAR(9),' '),"
            f"CHAR(13),''),CHAR(10),' \\n ')")


def _day_bounds(day: str) -> tuple[str, str]:
    d = date.fromisoformat(day)
    return d.strftime("%Y%m%d"), (d + timedelta(days=1)).strftime("%Y%m%d")


SAVED_AT = "(CASE WHEN s.DATE_EDITED > s.DATE_ADDED THEN s.DATE_EDITED ELSE s.DATE_ADDED END)"


def recent_query(day: str, top: int) -> str:
    lo, hi = _day_bounds(day)
    return (
        f"SELECT TOP {int(top)} s.PATIENTID, CONVERT(varchar(19), {SAVED_AT}, 120), "
        f"{_txt('p.GIVEN')}, {_txt('p.SURNAME')} "
        "FROM SPECTACLE_RX s WITH (NOLOCK) JOIN PATIENT p WITH (NOLOCK) ON p.ID = s.PATIENTID "
        f"WHERE {SAVED_AT} >= '{lo}' AND {SAVED_AT} < '{hi}' "
        f"ORDER BY {SAVED_AT} DESC, s.ID DESC")


def rx_query(pid: int) -> str:
    eye = ", ".join(_txt(f"s.{side}_{c}") for side in ("RIGHT", "LEFT") for c in EYE_COLS)
    return (
        f"SELECT TOP {MAX_SAME_DAY * 3} s.ID, CONVERT(varchar(19), s.DATE_ADDED, 120), "
        "CONVERT(varchar(10), s.RXDATE, 120), "
        f"{_txt('s.USER_IDENTIFIER')}, "
        "ISNULL(CAST(s.RIGHT_EYE_ONLY AS int),0), ISNULL(CAST(s.LEFT_EYE_ONLY AS int),0), "
        f"{_comment('s.COMMENT')}, {_comment('s.PRACTICE_COMMENT')}, {eye} "
        f"FROM SPECTACLE_RX s WITH (NOLOCK) WHERE s.PATIENTID = {int(pid)} "
        "ORDER BY s.DATE_ADDED DESC, s.ID DESC")


def patient_query(pid: int) -> str:
    return (f"SELECT p.ID, {_txt('p.GIVEN')}, {_txt('p.SURNAME')}, "
            "CONVERT(varchar(10), p.BIRTHDATE, 120) "
            f"FROM PATIENT p WITH (NOLOCK) WHERE p.ID = {int(pid)}")


def frame_query(pid: int) -> str:
    """The frame on the patient's most recent spectacle order that has a frame
    size (not cancelled; lens-only orders carry none).
    Optomate keeps the box size as one string: SIZE '5219' = eye 52, bridge 19
    (the DBL column is 0 on every 2026 order); DIAG when someone measured it.
    The frame catalogue row (SPECFRAME, by barcode) is the fallback."""
    return (
        "SELECT TOP 1 CONVERT(varchar(10), o.ORDER_DATE, 120), "
        f"{_txt('o.SIZE')}, ISNULL(o.DBL,0), ISNULL(o.DIAG,0), "
        "ISNULL(f.SIZE,0), ISNULL(f.BRIDGE,0), ISNULL(f.DIAG,0) "
        "FROM SPECTACLE_ORDERS o WITH (NOLOCK) "
        "LEFT JOIN SPECFRAME f WITH (NOLOCK) ON f.BARCODE = o.FRAME_BARCODE AND ISNULL(o.FRAME_BARCODE,'') <> '' "
        f"WHERE o.PATIENTID = {int(pid)} AND ISNULL(o.CANCELLED,0) = 0 "
        "AND (ISNULL(o.SIZE,'') NOT IN ('','0') OR ISNULL(f.SIZE,0) > 0) "
        "ORDER BY o.ORDER_DATE DESC, o.ID DESC")


def _mm(raw, lo, hi):
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def split_size(raw) -> tuple[float | None, float | None]:
    """Optomate's frame size -> (eye size A, bridge DBL). '5219' / 5219.0 ->
    (52, 19); '52-19' / '52 19' -> (52, 19); '52' -> (52, None)."""
    s = str(raw or "").strip()
    s = re.sub(r"\.0+$", "", s)
    m = re.fullmatch(r"(\d{2})\D+(\d{2})", s)
    if m:
        return _mm(m.group(1), 30, 75), _mm(m.group(2), 10, 30)
    if re.fullmatch(r"\d{4}", s):
        return _mm(s[:2], 30, 75), _mm(s[2:], 10, 30)
    if re.fullmatch(r"\d{2}", s):
        return _mm(s, 30, 75), None
    return None, None


def build_frame(row: list[str] | None) -> dict | None:
    """{"a", "dbl", "ed", "order_date"} from frame_query's row, or None when
    the last order carries no usable eye size."""
    if not row or len(row) < 7:
        return None
    a, dbl = split_size(row[1])
    fa, fdbl = split_size(row[4])
    a = a or fa
    dbl = _mm(row[2], 10, 30) or dbl or _mm(row[5], 10, 30) or fdbl
    ed = _mm(row[3], 30, 90) or _mm(row[6], 30, 90)
    if not a:
        return None
    return {"a": a, "dbl": dbl, "ed": ed, "order_date": row[0]}


def _sql_str(t: str) -> str:
    # guide_search.terms() leaves only letters, digits, space, ' and -,
    # so ' is the one character to escape and LIKE has no wildcard to fear.
    return t.replace("'", "''")


def search_sql(spec, top: int) -> str:
    kind, ts = spec
    like = lambda col, t: f"p.{col} LIKE N'%{_sql_str(t)}%'"  # noqa: E731
    if kind == "exact":
        a, b = ts
        where = (f"(({like('SURNAME', a)} AND {like('GIVEN', b)}) OR "
                 f"({like('SURNAME', b)} AND {like('GIVEN', a)}))")
    else:
        where = " AND ".join(f"({like('SURNAME', t)} OR {like('GIVEN', t)})" for t in ts)
    return (
        f"SELECT TOP {int(top)} p.ID, {_txt('p.GIVEN')}, {_txt('p.SURNAME')}, "
        "CONVERT(varchar(10), p.BIRTHDATE, 120), "
        "(SELECT CONVERT(varchar(10), MAX(s.DATE_ADDED), 120) FROM SPECTACLE_RX s WITH (NOLOCK) "
        "WHERE s.PATIENTID = p.ID) "
        "FROM PATIENT p WITH (NOLOCK) "
        f"WHERE {where} AND EXISTS (SELECT 1 FROM SPECTACLE_RX s WITH (NOLOCK) WHERE s.PATIENTID = p.ID) "
        "ORDER BY p.SURNAME, p.GIVEN")


# ---------------------------------------------------------------------------
# Rx values
# ---------------------------------------------------------------------------

PLANO_WORDS = {"PLANO", "PL", "PLAN", "0", "DS", "SPH", "SPHERE", "SPHERICAL"}


def parse_power(raw) -> Decimal | None:
    """Optomate's power string -> Decimal, for the lens lookup only.
    '+2.25' -> 2.25, '-0.50' -> -0.5, 'PLANO' / 'PL' / 'DS' -> 0, a unicode
    minus or a comma decimal is read, a trailing 'D' is ignored. Blank,
    BALANCE or anything else unreadable -> None (the page flags it)."""
    s = str(raw if raw is not None else "").strip()
    if not s:
        return None
    s = s.replace("\u2212", "-").replace("\u2013", "-").replace(",", ".").replace(" ", "")
    if s.upper() in PLANO_WORDS:
        return Decimal("0")
    s = re.sub(r"(?i)d(s)?$", "", s)
    if not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _nz(raw) -> str | None:
    """The verbatim string when it carries a non-zero number, else None."""
    v = parse_power(raw)
    return str(raw).strip() if v else None


def _prism(h, ht, v, vt) -> str | None:
    parts = []
    if parse_power(h):
        parts.append(f"{str(h).strip()} {str(ht or '').strip()}".strip())
    if parse_power(v):
        parts.append(f"{str(v).strip()} {str(vt or '').strip()}".strip())
    return " + ".join(parts) or None


def parse_eye(cells: list[str]) -> dict:
    """One eye of a saved Rx, verbatim strings. cells follow EYE_COLS."""
    c = dict(zip(EYE_COLS, cells))
    cyl = _nz(c["CYLINDER"])
    return {
        "sph": (c["SPHERE"] or "").strip() or None,
        "cyl": cyl,
        "axis": ((c["AXIS"] or "").strip() or None) if cyl else None,
        "add": _nz(c["ADD"]),
        "intadd": _nz(c["INTADD"]),
        "pd": _nz(c["PD"]),
        "near_pd": _nz(c["NEARPD"]),
        "prism": _prism(c["HPRISM"], c["HPRISM_TYPE"], c["VPRISM"], c["VPRISM_TYPE"]),
    }


# ---------------------------------------------------------------------------
# The Rx comment: RTF -> text -> without the printed footer -> lens type
# ---------------------------------------------------------------------------

_RTF_SKIP_GROUPS = ("fonttbl", "colortbl", "stylesheet", "info", "*")


def strip_rtf(text) -> str:
    """Plain text out of the RichEdit RTF Optomate stores. Plain text (older
    comments) comes back as it is. \\par and line breaks become newlines;
    font tables and other destination groups are dropped; \\'hh and \\uN
    escapes are decoded."""
    s = str(text or "").replace(" \\n ", "\n").replace("\r", "")
    if not s.lstrip().startswith("{\\rtf"):
        return s.strip()
    out, i, n = [], 0, len(s)
    stack, skip = [], False
    uc_skip = 0
    while i < n:
        ch = s[i]
        if ch == "{":
            stack.append(skip)
            # a destination group we don't want: {\fonttbl ...}, {\*\generator ...}
            m = re.match(r"\{\\(\*|[a-z]+)", s[i:i + 20])
            if m and m.group(1) in _RTF_SKIP_GROUPS:
                skip = True
            i += 1
        elif ch == "}":
            skip = stack.pop() if stack else False
            i += 1
        elif ch == "\\":
            if i + 1 < n and s[i + 1] in "\\{}":
                if not skip:
                    out.append(s[i + 1])
                i += 2
                continue
            if i + 1 < n and s[i + 1] == "'":
                if not skip:
                    try:
                        out.append(bytes([int(s[i + 2:i + 4], 16)]).decode("cp1252"))
                    except (ValueError, UnicodeDecodeError):
                        pass
                i += 4
                continue
            if i + 1 < n and s[i + 1] == "\n":
                if not skip:
                    out.append("\n")
                i += 2
                continue
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if not m:
                i += 2
                continue
            word, arg = m.group(1), m.group(2)
            i += m.end()
            if skip:
                continue
            if word in ("par", "line", "row"):
                out.append("\n")
            elif word == "tab":
                out.append(" ")
            elif word == "u" and arg is not None:
                v = int(arg)
                out.append(chr(v + 65536 if v < 0 else v))
                uc_skip = 1
            # every other control word (fonts, sizes, \pard, ...) is formatting
        elif ch == "\n":
            i += 1                     # a raw line break inside RTF is not text
        else:
            if uc_skip:
                uc_skip -= 1           # the ? fallback after a \\uN
            elif not skip:
                out.append(ch)
            i += 1
    return "\n".join(line.strip() for line in "".join(out).split("\n")).strip()


# The practice's printed footer, every wording seen in SPECTACLE_RX (3 Nov 2026):
#   "*** Warranty for this prescription is only applicable to glasses made at
#    Concord Eye Care *** Valid for 1 year" and "... Please note this
#   prescription is valid for 1 year".
FOOTER_RES = [
    re.compile(r"\**\s*warranty\b[^\n]*?concord\s*eye\s*care\.?\s*\**", re.I),
    re.compile(r"(please\s+note,?\s+)?(this\s+(prescription|rx)\s+is\s+)?valid\s+for\s+"
               r"(\d+|one|two)\s*(years?|yrs?|months?)\.?", re.I),
    re.compile(r"\bconcord\s*eye\s*care\b", re.I),
]


def clean_comment(text) -> str:
    """The Rx comment as staff would read it: RTF stripped, the printed
    warranty/validity footer removed, blank lines and stray asterisks gone."""
    s = strip_rtf(text)
    for rx in FOOTER_RES:
        s = rx.sub(" ", s)
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"\s+", " ", line.replace("*", " ")).strip(" .;,-")
        if line and not re.search(r"\bwarranty\b", line, re.I):
            lines.append(line)
    return "\n".join(lines)


# Lens types, staff words. "office" = an office / occupational lens,
# "intermediate" = single vision at arm's length (computer) on the Rx's INT add.
TYPE_WORDS = {
    "distance": "Distance single vision",
    "near": "Reading single vision",
    "intermediate": "Computer single vision",
    "office": "Office lens",
    "multifocal": "Multifocal",
    "bifocal": "Bifocal",
}

_W = r"(?<![A-Za-z0-9])"           # word start that treats / and + as breaks
_E = r"(?![A-Za-z0-9])"
KEYWORDS = [
    # (type, pattern) - checked on the cleaned comment. Order matters only for
    # the overlapping ones, which are rewritten first (see _normalise).
    ("office", _W + r"(office|occupational|occ|work\s*&\s*office|desk|room\s*lens)" + _E),
    ("multifocal", _W + r"(multi\s*-?\s*focals?|mfs?|progressives?|progs?|pals?|varifocals?)" + _E),
    ("bifocal", _W + r"(bi\s*-?\s*focals?|bfs?|bif|tri\s*-?\s*focals?|d28|d35|flat\s*top)" + _E),
    ("near", _W + r"(svn|sv\s*-?\s*near|near|reading|readers?|nv\s*only|n/v)" + _E),
    ("distance", _W + r"(svd|sv\s*-?\s*dist(ance)?|dist(ance)?|driving|drive|dv\s*only|d/v)" + _E),
    ("intermediate", _W + r"(computers?|pc|screen|intermediate|inter|int\s*sv|sv\s*int)" + _E),
]
SV_RE = re.compile(_W + r"(sv|s/v|single\s*vision)" + _E, re.I)
# A named special lens the standard Lens Finder can't stand in for (myopia
# control, anti-fatigue, extended focus): the page says so instead of quietly
# offering a standard lens.
SPECIAL_RE = re.compile(
    _W + r"(miyo\s*smart|myo\s*care|myo\s*vision|stellest|dims|myopia\s*control|"
    r"anti\s*-?\s*fatigue|sync\s*iii|extended\s*focus|edof)" + _E, re.I)


def _normalise(text: str) -> str:
    t = " " + text + " "
    # "Occ MF" / "occupational progressive" is an office lens, not a multifocal
    t = re.sub(_W + r"(occ(upational)?|office)\s*-?\s*(mf|multifocal|pal|progressive)s?" + _E,
               " office ", t, flags=re.I)
    return t


def detect_type(comment, practice_comment="", has_add: bool = False,
                has_intadd: bool = False) -> dict:
    """What lens the optom wrote on the Rx, from the comment keywords.

    Returns {"type", "types", "words", "source", "note", "text"}:
      types  - every type named, in the order written (first = default tab);
      words  - the words that matched, so staff can see why;
      source - "comment", "practice_comment" or "none";
      note   - one plain line when the page had to choose (never silent);
      text   - the cleaned comment, footer removed, for the page.
    No keyword: no add -> distance single vision; add -> multifocal, with a
    note asking for the type to be written on the Rx.
    """
    text = clean_comment(comment)
    pc = clean_comment(practice_comment)
    found, source = [], "none"
    for src, body in (("comment", text), ("practice_comment", pc)):
        if not body:
            continue
        hits = _scan(body, has_add)
        if hits:
            found, source = hits, src
            break
    types = list(dict.fromkeys(t for t, _, _ in sorted(found, key=lambda h: h[1])))
    words = list(dict.fromkeys(w for _, _, w in sorted(found, key=lambda h: h[1])))
    notes = []
    as_written = False
    if not types:
        if has_add:
            types = ["multifocal"]
            notes.append("Lens type not written on the Rx - showing multifocal; "
                         "add the type to the Rx comment.")
        else:
            types = ["distance"]
    elif not has_add:
        # No add: the powers ARE the pair. A reading or computer pair saved
        # as its own Rx (common: "SVN" with the near power as the sphere).
        sv = [t for t in types if t in ("distance", "near", "intermediate")]
        lost = [t for t in types if t not in sv]
        if lost:
            notes.append("The Rx comment says " + ", ".join(TYPE_WORDS[t].lower() for t in lost)
                         + ", but there is no add on the Rx - showing single vision. "
                         "Check with the optom.")
        types = sv or ["distance"]
        if types[0] in ("near", "intermediate"):
            as_written = True
            notes.append("No add on this Rx, so its powers are the "
                         + ("reading" if types[0] == "near" else "computer")
                         + " pair as written.")
        types = types[:1]
    if len(types) > 1:
        notes.append("The Rx comment names more than one lens: "
                     + " and ".join(TYPE_WORDS[t].lower() for t in types)
                     + ". Each has its own tab.")
    special = SPECIAL_RE.search(" " + text + " " + pc + " ")
    if special:
        notes.append(f"The Rx note names a particular lens (\"{special.group(0).strip()}\") - "
                     "the options below are our standard lenses. Check with the optom.")
    return {"type": types[0], "types": types, "words": words, "source": source,
            "as_written": as_written, "note": " ".join(notes), "notes": notes,
            "special": special.group(0).strip() if special else "",
            "text": text[:300], "practice_text": pc[:300]}


def _scan(body: str, has_add: bool) -> list[tuple[str, int, str]]:
    """(type, position, word) for every keyword in one comment."""
    t = _normalise(body)
    hits = []
    for typ, pat in KEYWORDS:
        for m in re.finditer(pat, t, re.I):
            hits.append((typ, m.start(), m.group(0).strip()))
    # A bare "SV": single vision. With a near / computer word it is already
    # read as that; on its own it means the distance pair.
    named_sv = {"near", "distance", "intermediate"}
    for m in SV_RE.finditer(t):
        if not any(typ in named_sv and abs(pos - m.start()) <= 12 for typ, pos, _ in hits):
            hits.append(("distance", m.start(), m.group(0).strip()))
    # "computer" on its own, with an add on the Rx: an office lens, unless
    # written as single vision (SV computer / int SV). With no add the Rx
    # powers are the computer pair itself (single vision as written).
    out = []
    for typ, pos, word in hits:
        if typ == "intermediate":
            sv_near = any(abs(m.start() - pos) <= 16 for m in SV_RE.finditer(t))
            if has_add and not sv_near and not re.match(r"(?i)int\s*sv|sv\s*int", word):
                typ = "office"
        out.append((typ, pos, word))
    # When "SV computer" was read, drop the distance hit the bare SV made
    if any(t2 == "intermediate" for t2, _, _ in out):
        out = [h for h in out if not (h[0] == "distance" and SV_RE.fullmatch(h[2] or ""))]
    return out


# ---------------------------------------------------------------------------
# Rows -> answers
# ---------------------------------------------------------------------------

def clock(stamp: str) -> str:
    """'2026-10-03 14:15:00' -> '2:15pm'. '' when unreadable."""
    try:
        t = datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def build_recent(rows: list[list[str]], n: int) -> dict:
    """One line per patient, newest first (rows arrive newest first)."""
    seen, out = set(), []
    for row in rows:
        if len(row) < 4:
            raise BridgeError("Optomate's answer had an unexpected shape.")
        pid = row[0]
        if pid in seen:
            continue
        seen.add(pid)
        out.append({"id": int(pid), "given": row[2], "surname": row[3],
                    "saved": clock(row[1]), "saved_at": row[1]})
    return {"patients": out[:n], "more": len(out) > n}


def build_rx(rows: list[list[str]]) -> list[dict]:
    """The rows saved on the day of the latest one, newest first."""
    width = 8 + 2 * len(EYE_COLS)
    k = len(EYE_COLS)
    out, day = [], None
    for row in rows:
        if len(row) < width:
            raise BridgeError("Optomate's answer had an unexpected shape.")
        added = row[1]
        if day is None:
            day = added[:10]
        if added[:10] != day or len(out) >= MAX_SAME_DAY:
            break
        r_only, l_only = row[4] == "1", row[5] == "1"
        right = None if l_only else parse_eye(row[8:8 + k])
        left = None if r_only else parse_eye(row[8 + k:8 + 2 * k])
        has_add = any(e and e.get("add") for e in (right, left))
        has_int = any(e and e.get("intadd") for e in (right, left))
        out.append({
            "rx_id": int(row[0]), "saved_at": added, "saved": clock(added),
            "saved_date": added[:10], "rx_date": row[2] or "", "optom": row[3],
            "right_only": r_only, "left_only": l_only,
            "right": right, "left": left,
            "lens_type": detect_type(row[6], row[7], has_add, has_int),
        })
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def fixture_mode(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("LENS_OPTIONS_FIXTURE", "") or "").strip().lower() in ("1", "true", "yes")


FOOTER = ("{\\rtf1\\ansi\\ansicpg1252\\deff0\\nouicompat{\\fonttbl{\\f0\\fnil\\fcharset0 Segoe UI;}}"
          " \\n {\\*\\generator Riched20 10.0.26100}\\viewkind4\\uc1  \\n \\pard\\f0\\fs20\\lang1033 "
          "%sWarranty for this prescription is only applicable to glasses made at Concord Eye Care \\par \\n "
          "\\par \\n Please note this prescription is valid for 1 year\\par \\n }")


def _fx_eye(sph, cyl="", axis="", add="", intadd="", pd="", prism=("", "", "", "")):
    return [sph, cyl, axis, add, intadd, pd, "", *prism]


def fixture_db(day: str) -> dict:
    """Fictional patients (ZZTEST), shaped exactly like the SQL rows."""
    at = lambda hm: f"{day} {hm}:00"  # noqa: E731
    people = {
        9001: ("Alex", "ZZTEST", "1961-04-02"),
        9002: ("Sam", "ZZTEST-NEAR", "1958-09-12"),
        9003: ("Mia", "ZZTEST", "2001-01-20"),
        9004: ("Priya", "ZZTEST-PRISM", "1970-06-30"),
    }
    rx = {
        9001: [["501", at("11:42"), day, "MJ", "0", "0", FOOTER % "", "",
                *_fx_eye("+1.00", "-0.50", "90", "+2.00", "", "31.5"),
                *_fx_eye("+1.25", "0.00", "", "+2.00", "", "32")]],
        9002: [["502", at("10:40"), day, "MJ", "0", "0", FOOTER % "SVN\\par ", "",
                *_fx_eye("-0.75", "-0.75", "180", "+2.25", "", "31"),
                *_fx_eye("-1.00", "", "", "+2.25", "", "31")]],
        9003: [["503", at("09:15"), day, "MJ", "0", "0", FOOTER % "", "",
                *_fx_eye("-2.75", "-0.75", "180", pd="30"),
                *_fx_eye("-3.00", "-0.50", "10", pd="30")]],
        9004: [["504", at("08:50"), day, "MJ", "0", "0", FOOTER % "Occ MF\\par ", "",
                *_fx_eye("+2.50", "", "", "+2.00", "+1.00", "32", ("1.00", "IN", "", "")),
                *_fx_eye("+2.75", "-0.25", "75", "+2.00", "+1.00", "32")]],
    }
    return {"people": people, "rx": rx}


FIXTURE_FRAMES = {
    9001: ["2025-05-01", "5219", "0.0", "57.0", "0", "0", "0"],
    9004: ["2024-11-20", "5418", "0.0", "0.0", "0", "0", "0"],
}


def cmd_recent(day: str | None = None, n: int = DEFAULT_N, sql=None) -> dict:
    day = day or date.today().isoformat()
    if not DATE_RE.match(day):
        raise BridgeError("Bad date.")
    date.fromisoformat(day)
    n = max(1, min(MAX_N, int(n)))
    if fixture_mode() and sql is None:
        db = fixture_db(day)
        rows = sorted(([str(pid), r[0][1], *db["people"][pid][:2]] for pid, r in db["rx"].items()),
                      key=lambda x: x[1], reverse=True)
    else:
        rows = (sql or sqlq)(recent_query(day, 40))
    return {"date": day, **build_recent(rows, n)}


def cmd_search(q: str, sql=None) -> dict:
    from hub import guide_search
    if fixture_mode() and sql is None:
        db = fixture_db(date.today().isoformat())
        rows = [{"ID": pid, "GIVEN": g, "SURNAME": s, "BIRTHDATE": dob}
                for pid, (g, s, dob) in db["people"].items()]

        def fetch(filt, top, spec):
            return guide_search.fixture_match(rows, spec)[:top]
    else:
        run = sql or sqlq

        def fetch(filt, top, spec):
            return [{"ID": int(r[0]), "GIVEN": r[1], "SURNAME": r[2], "BIRTHDATE": r[3],
                     "LAST_RX": r[4] if len(r) > 4 else ""}
                    for r in run(search_sql(spec, top)) if r and r[0].isdigit()]
    rows, more = guide_search.search(fetch, q)
    return {"patients": [{"id": r.get("ID"), "given": str(r.get("GIVEN") or "").strip(),
                          "surname": str(r.get("SURNAME") or "").strip(),
                          "dob": str(r.get("BIRTHDATE") or "")[:10],
                          "last_rx": str(r.get("LAST_RX") or "")[:10]} for r in rows],
            "more": more}


def cmd_rx(pid: int, sql=None) -> dict:
    pid = int(pid)
    if fixture_mode() and sql is None:
        db = fixture_db(date.today().isoformat())
        if pid not in db["people"]:
            raise BridgeError("That patient isn't in the fake test list.")
        g, s, dob = db["people"][pid]
        prow, rows = [str(pid), g, s, dob], db["rx"][pid]
    else:
        run = sql or sqlq
        prows = run(patient_query(pid))
        if not prows:
            raise BridgeError("That patient isn't in Optomate.")
        prow, rows = prows[0], run(rx_query(pid))
    patient = {"id": pid, "given": prow[1], "surname": prow[2],
               "dob": (prow[3] if len(prow) > 3 else "")[:10]}
    if fixture_mode() and sql is None:
        frame = build_frame(FIXTURE_FRAMES.get(pid))
    else:
        frows = run(frame_query(pid))
        frame = build_frame(frows[0] if frows else None)
    return {"patient": patient, "rxs": build_rx(rows), "frame": frame}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    opts = dict(zip(argv[1::2], argv[2::2]))
    try:
        if cmd == "recent" and set(opts) <= {"--date", "--n"}:
            result = cmd_recent(opts.get("--date") or None, int(opts.get("--n") or DEFAULT_N))
        elif cmd == "search" and set(opts) == {"--q"}:
            result = cmd_search(opts["--q"])
        elif cmd == "rx" and set(opts) == {"--pid"} and opts["--pid"].isdigit():
            result = cmd_rx(int(opts["--pid"]))
        else:
            result = {"error": "Bad command."}
    except BridgeError as exc:
        result = {"error": str(exc)}
    except Exception as exc:
        result = {"error": "Something went wrong reading Optomate.",
                  "error_code": type(exc).__name__[:60]}
    result.setdefault("fixture", fixture_mode())
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
