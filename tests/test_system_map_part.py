"""This repo's part of the practice map (Mark, 16 Nov 2026: "a part of the node and it's not an entity in itself").
Server Desk's map (C:/CEC/CEC-Optomate-Agent, python -m system.map) reads system-map.part.json from the default
branch; this only checks the file keeps the shape that map expects and holds names of things only."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PART = ROOT / "system-map.part.json"
APP = "hub"
PREFIXES = {"optomate", "mail", "sms", "patient", "doctor", "alert", "gate", "port", "task", "lock", "file", "ext"}
SENS = {"none", "business", "patient-admin", "health"}


def _part():
    return json.loads(PART.read_text(encoding="utf-8"))


def test_part_file_parses_and_names_its_app():
    part = _part()
    assert part["meta"]["app"] == APP and part["meta"]["owner_desk"] == "Server"
    assert set(part) <= {"_about", "meta", "resources", "adds", "launchers"}


def test_every_id_is_a_map_id_and_every_effect_is_well_formed():
    part = _part()
    own = [r["id"] for r in part.get("resources") or []]
    assert len(own) == len(set(own))
    adds = part.get("adds") or {}
    ids = own + [i for k in ("reads", "writes", "gates", "depends_on") for i in adds.get(k) or []]
    ids += [x["to"] for k in ("sends", "egress") for x in adds.get(k) or []]
    assert all(i.split(".")[0] in PREFIXES for i in ids), ids
    for e in adds.get("egress") or []:
        assert e["to"].startswith("ext.") and e["sensitivity"] in SENS and e["what"]
    for r in part.get("resources") or []:
        assert r.get("label") and (r.get("sensitivity", "none") in SENS)
    for ln in part.get("launchers") or []:
        assert (ROOT / ln["file"]).exists() and ln["how"], ln


def test_part_file_holds_names_only():
    text = PART.read_text(encoding="utf-8")
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    assert emails <= {"info@concordeyecare.com.au"}, emails
    assert not re.search(r"(?<![\w.])(?:\+?61\s?4|04)\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)", text)   # no mobiles
    assert not re.search(r"\b[0-9a-fA-F]{32,}\b", text)                                     # no keys
    assert not re.search(r"(?i)\b(password|api[_-]?key|secret|token)\s*[:=]", text)
