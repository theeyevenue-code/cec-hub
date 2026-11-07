"""One-off: read MANUAL-TASKS.md and PROPOSE To do items, as a preview file for review.

    python tools/import_manual_tasks.py C:\\CEC\\CEC-Optomate-Agent\\MANUAL-TASKS.md preview.json

Reads the OPEN parts only: unticked "[ ]" items, "[~]" (waiting on someone) items, sections that
are a single task written as a table, and the staff-meeting "Who | Tell them" table. Ticked "[x]"
items and everything from the "Done" heading down are skipped.

It does NOT invent anything. Owner is Unassigned unless the text itself names who (a "Who" column,
or "Mark is raising it"); the basis is recorded beside each owner. First-seen is null ("date
unknown") unless the section heading states a date ("added 28 Oct 2026"). Nothing is written to
the Hub: review the preview, fix owners, then load it with
    python tools/todo_add.py --import-file preview.json
Re-running is safe: the keys are stable, so the Hub never doubles up.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
OWNER_NAMES = {"karen": "Karen", "angie": "Angie", "mark": "Mark", "claude": "Claude"}
# Explicit statements only. Each is (pattern, owner, plain-words basis).
OWNER_PHRASES = [
    (re.compile(r"\bMark is raising\b", re.I), "Mark", 'text says "Mark is raising it"'),
]
CHECKBOX = re.compile(r"^- \[([ x~])\]\s*(.*)$", re.I)


def clean(s: str) -> str:
    s = re.sub(r"~~(.*?)~~", r"\1", s)
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    return " ".join(s.split())


def heading_date(heading: str):
    m = re.search(r"added\s+(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", heading, re.I)
    if not m:
        return None
    mon = MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(1))).isoformat()
    except ValueError:
        return None


def slug(s: str, n=40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", clean(s).lower()).strip("-")[:n] or "item"


def guess_owner(text: str):
    for pat, owner, why in OWNER_PHRASES:
        if pat.search(text):
            return owner, why
    return "Unassigned", "source does not say who"


def short(s: str, n=300) -> str:
    s = clean(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "..."


def split_sections(lines):
    """[(heading, [lines])], stopping at the Done section."""
    sections, cur = [], None
    for ln in lines:
        if ln.startswith("## "):
            head = ln[3:].strip()
            if head.lower().startswith("done"):
                break
            cur = (head, [])
            sections.append(cur)
        elif cur is not None:
            cur[1].append(ln)
    return sections


def checkbox_items(body):
    """[(mark, first_line_text, [continuation lines])] for each top-level checkbox."""
    out, cur = [], None
    for ln in body:
        m = CHECKBOX.match(ln)
        if m:
            cur = (m.group(1).lower(), m.group(2), [])
            out.append(cur)
        elif ln.strip() == "---":
            cur = None
        elif cur is not None:
            cur[2].append(ln)
    return out


def table_rows(body):
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in body
            if ln.strip().startswith("|")]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
    return rows


def propose(text: str):
    lines = text.splitlines()
    items, skipped = [], {"ticked": 0}
    for head, body in split_sections(lines):
        hd = heading_date(head)
        base = re.sub(r"\(.*?\)", "", head)           # keys ignore "(added ...)" so they stay stable
        source = f"MANUAL-TASKS.md - {clean(head).split(' - ')[0].split(' (')[0][:70]}"
        boxes = checkbox_items(body)
        if boxes:
            for mark, first, cont in boxes:
                if mark == "x":
                    skipped["ticked"] += 1
                    continue
                bold = re.search(r"\*\*(.+?)\*\*", first)
                title = clean(bold.group(1)) if bold else clean(first)
                num = re.match(r"(\d+)\.\s*(.*)", title)
                number, title = (num.group(1), num.group(2)) if num else (None, title)
                title = title.rstrip(".:")
                rest = clean(first[bold.end():]) if bold else ""
                paras = [c for c in cont if c.strip() and not c.strip().startswith("|")]
                detail = short(" ".join([rest] + paras))
                owner, why = guess_owner(first + " " + " ".join(cont))
                items.append({
                    "key": f"manual-tasks:{number}" if number else f"manual-tasks:{slug(base)}:{slug(title)}",
                    "text": title[:300], "detail": detail, "owner": owner, "owner_basis": why,
                    "first_seen": hd, "source": source,
                    "source_status": "waiting on someone ([~])" if mark == "~" else "open ([ ])",
                })
            continue
        rows = table_rows(body)
        if rows and [c.lower() for c in rows[0][:2]] == ["who", "tell them"]:
            for who, tell, *_ in rows[1:]:
                first_word = re.split(r"[ /]", who.strip())[0].lower()
                owner = OWNER_NAMES.get(first_word, "Unassigned")
                items.append({
                    "key": f"manual-tasks:{slug(base)}:{slug(tell)}",
                    "text": short(f"Tell {who.strip()}: {tell}", 300), "detail": "",
                    "owner": owner,
                    "owner_basis": f'"Who" column says "{clean(who)}"' if owner != "Unassigned"
                    else "source does not say who",
                    "first_seen": hd, "source": source, "source_status": "open (table row)"})
            continue
        paras = [clean(c) for c in body if c.strip() and not c.strip().startswith("|")]
        if paras:                                    # a whole section that is one task
            owner, why = guess_owner(head + " " + " ".join(body))
            items.append({
                "key": f"manual-tasks:{slug(base)}", "text": clean(head)[:300],
                "detail": short(" ".join(paras)), "owner": owner, "owner_basis": why,
                "first_seen": hd, "source": source, "source_status": "open (section)"})
    return items, skipped


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dest = Path(argv[1]), Path(argv[2])
    items, skipped = propose(src.read_text(encoding="utf-8"))
    unassigned = sum(1 for i in items if i["owner"] == "Unassigned")
    preview = {"_readme": "PREVIEW. Review owners, then: python tools/todo_add.py --import-file <this file>",
               "generated_from": src.name, "proposed": len(items), "unassigned": unassigned,
               "skipped_ticked": skipped["ticked"], "items": items}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(preview, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Proposed {len(items)} items ({unassigned} Unassigned); skipped {skipped['ticked']} ticked. "
          f"Preview: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
