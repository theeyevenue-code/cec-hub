"""To do / Waiting on Mark (hub/workqueue.py, /api/workqueue, tools/todo_add.py,
tools/import_manual_tasks.py). INVENTED data only: no real patient, no real job."""

import importlib
import json
import logging
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from hub import workqueue as wq

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


@pytest.fixture
def store(tmp_path, monkeypatch):
    p = tmp_path / "wq" / "workqueue.json"
    monkeypatch.setenv("CEC_HUB_WORKQUEUE", str(p))
    return p


def _items(store):
    return json.loads(store.read_text(encoding="utf-8"))["items"]


# --- the entry point: add, dedupe, repeat ---------------------------------------------------

def test_add_then_same_key_does_not_duplicate(store):
    a = wq.add_item(key="nightly:zeiss-code", text="ZEISS code missing on 3 lines", owner="karen",
                    source="Nightly ZEISS email")
    b = wq.add_item(key="nightly:zeiss-code", text="ZEISS code missing on 4 lines", owner="Karen",
                    source="Nightly ZEISS email")
    assert a["result"] == "added" and b["result"] == "updated"
    items = _items(store)
    assert len(items) == 1
    assert items[0]["owner"] == "Karen"                       # owner normalised
    assert items[0]["seen_count"] == 2
    assert items[0]["text"].endswith("4 lines")               # the line stays current
    assert items[0]["first_seen"] == date.today().isoformat() # first-seen never moves


def test_repeat_never_changes_who_or_first_seen(store):
    wq.add_item(key="k1", text="Job", owner="Angie", first_seen="2026-10-01")
    wq.add_item(key="k1", text="Job", owner="Karen", first_seen="2026-11-04")
    it = _items(store)[0]
    assert it["owner"] == "Angie" and it["first_seen"] == "2026-10-01"


def test_done_item_stays_done_same_day_but_comes_back_next_day(store, monkeypatch):
    wq.add_item(key="k2", text="Scan backlog", owner="Angie")
    item_id = _items(store)[0]["id"]
    wq.mark_done(item_id, "Angie")
    again = wq.add_item(key="k2", text="Scan backlog", owner="Angie")      # the same night
    assert again["result"] == "updated" and _items(store)[0]["status"] == "done"
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(wq, "_today", lambda: tomorrow)
    back = wq.add_item(key="k2", text="Scan backlog", owner="Angie")
    it = _items(store)[0]
    assert back["result"] == "reopened" and it["status"] == "open" and it["came_back"] == 1
    assert len(_items(store)) == 1


def test_manual_double_click_does_not_make_two(store):
    wq.add_item(text="Ring Hoya about credit", owner="Karen", added_by="Karen")
    r = wq.add_item(text="  ring hoya   about credit ", owner="Karen", added_by="Karen")
    assert r["result"] == "updated" and len(_items(store)) == 1
    wq.add_item(text="Ring Hoya about credit", owner="Angie", added_by="Angie")     # other owner: a new job
    assert len(_items(store)) == 2


def test_validation_is_plain_words(store):
    for kw in ({"text": ""}, {"text": "x", "owner": "Bob"}, {"text": "x", "first_seen": "5/11"},
               {"text": "x", "key": "bad\nkey"}, {"text": "x", "kind": "nope"}):
        with pytest.raises(wq.WorkQueueError):
            wq.add_item(**kw)
    assert not store.exists()                                   # a refusal writes nothing


def test_damaged_file_is_never_overwritten(store):
    store.parent.mkdir(parents=True)
    store.write_text("{not json", encoding="utf-8")
    with pytest.raises(wq.WorkQueueError):
        wq.add_item(key="k", text="x")
    assert store.read_text(encoding="utf-8") == "{not json"


def test_unknown_date_stays_unknown_and_sorts_after_dated(store):
    wq.import_item({"key": "m:1", "text": "Undated job", "owner": "Unassigned", "first_seen": None})
    wq.add_item(key="m:2", text="Old job", first_seen="2026-10-01")
    wq.add_item(key="m:3", text="Newer job", first_seen="2026-11-03")
    v = wq.view(date(2026, 11, 5))
    assert [r["text"] for r in v["todos"]] == ["Old job", "Newer job", "Undated job"]
    assert v["todos"][0]["age_days"] == 35 and v["todos"][0]["stale"] and v["todos"][0]["oldest"]
    assert not v["todos"][1]["stale"] and not v["todos"][1].get("oldest")
    assert v["todos"][2]["age_days"] is None and not v["todos"][2]["stale"]
    assert v["counts"]["Unassigned"] == 3 and v["total_open"] == 3


def test_done_keeps_who_and_when_and_first_click_wins(store):
    wq.add_item(key="k", text="Job")
    i = _items(store)[0]["id"]
    wq.mark_done(i, "Karen")
    wq.mark_done(i, "Angie")                                    # a second click a moment later
    it = _items(store)[0]
    assert it["status"] == "done" and it["done_by"] == "Karen" and it["done_at"]
    wq.reopen(i, "Karen")
    assert _items(store)[0]["status"] == "open" and _items(store)[0]["done_by"] is None


def test_resolve_by_key_marks_done(store):
    wq.add_item(key="k", text="Job")
    assert wq.resolve_by_key("k")["result"] == "resolved"
    assert _items(store)[0]["done_by"] == "job"
    assert wq.resolve_by_key("nope")["result"] == "not_found"


# --- questions for Mark ---------------------------------------------------------------------

def _ask(key="ask:1"):
    return wq.add_item(key=key, kind="ask", text="Merge Lens options to main?",
                       recommendation="Yes - tests pass, nothing live changes",
                       origin="branch session/2026-11-04-lens", first_seen="2026-11-04")["item"]["id"]


def test_ask_records_answer_only(store):
    i = _ask()
    wq.record_answer(i, "yes", "Mark")
    it = _items(store)[0]
    assert it["status"] == "answered" and it["answer"] == "yes" and it["answered_by"] == "Mark" and it["answered_at"]
    assert wq.get_by_key("ask:1")["answer"] == "yes"
    v = wq.view()
    assert v["asks"] == [] and len(v["answered"]) == 1 and v["total_open"] == 0


def test_ask_is_owned_by_mark_and_cannot_be_ticked_done(store):
    i = _ask()
    assert _items(store)[0]["owner"] == "Mark"
    with pytest.raises(wq.WorkQueueError):
        wq.mark_done(i, "Karen")
    with pytest.raises(wq.WorkQueueError):
        wq.record_answer(i, "maybe", "Mark")


def test_ask_repeat_does_not_overwrite_an_answer(store):
    i = _ask()
    wq.record_answer(i, "not_now", "Mark")
    wq.add_item(key="ask:1", kind="ask", text="Merge Lens options to main?")
    it = _items(store)[0]
    assert it["answer"] == "not_now" and it["status"] == "answered" and len(_items(store)) == 1


def test_undo_answer_puts_it_back(store):
    i = _ask()
    wq.record_answer(i, "yes", "Mark")
    wq.undo_answer(i, "Mark")
    it = _items(store)[0]
    assert it["status"] == "open" and it["answer"] is None


# --- two people at once ---------------------------------------------------------------------

def test_threads_adding_distinct_items_lose_none(store):
    errs = []

    def work(n):
        try:
            wq.add_item(key=f"t:{n}", text=f"Job {n}")
        except Exception as e:                                   # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=work, args=(n,)) for n in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(_items(store)) == 40


def test_threads_with_same_key_make_one_item(store):
    ts = [threading.Thread(target=lambda: wq.add_item(key="same", text="Same job")) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    items = _items(store)
    assert len(items) == 1 and items[0]["seen_count"] == 30


def test_two_clicks_on_one_question_only_one_wins(store):
    i = _ask()
    results = []

    def click(ans):
        try:
            wq.record_answer(i, ans, "Mark")
            results.append("ok")
        except wq.WorkQueueError:
            results.append("refused")
    ts = [threading.Thread(target=click, args=(a,)) for a in ("yes", "not_now", "yes", "not_now")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["ok", "refused", "refused", "refused"]
    assert _items(store)[0]["status"] == "answered"


def test_separate_processes_writing_at_once_lose_none(store, tmp_path):
    """The real case: a scheduled job, a Claude session and a person all writing together."""
    procs = [subprocess.Popen([PY, str(ROOT / "tools" / "todo_add.py"), "--file", str(store),
                               "--key", f"p:{n}", "--text", f"Process job {n}"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in range(12)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert len(_items(store)) == 12
    assert not list(store.parent.glob("*.tmp"))


# --- the command-line helper ----------------------------------------------------------------

def _cli(store, *args):
    p = subprocess.run([PY, str(ROOT / "tools" / "todo_add.py"), "--file", str(store), *args],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_cli_add_dedupe_get_resolve(store):
    rc, out = _cli(store, "--key", "cli:1", "--text", "Check the Luxottica scan inbox", "--owner", "Angie",
                   "--source", "Scan backlog")
    assert rc == 0 and out["result"] == "added"
    rc, out = _cli(store, "--key", "cli:1", "--text", "Check the Luxottica scan inbox", "--owner", "Angie")
    assert rc == 0 and out["result"] == "updated" and out["seen_count"] == 2
    assert len(_items(store)) == 1
    rc, out = _cli(store, "--get", "cli:1")
    assert rc == 0 and out["item"]["owner"] == "Angie"
    rc, out = _cli(store, "--resolve", "cli:1")
    assert rc == 0 and out["result"] == "resolved"


def test_cli_ask_then_read_answer(store):
    rc, out = _cli(store, "--ask", "--key", "ask:cli", "--text", "Turn on X?", "--recommendation", "Yes",
                   "--origin", "session abc")
    assert rc == 0
    wq.record_answer(_items(store)[0]["id"], "yes", "Mark")
    rc, out = _cli(store, "--get", "ask:cli")
    assert out["item"]["answer"] == "yes" and out["item"]["answered_by"] == "Mark"


def test_cli_refuses_in_plain_json(store):
    rc, out = _cli(store, "--key", "x", "--text", "y", "--owner", "Nobody")
    assert rc == 2 and out["result"] == "refused" and not store.exists()
    rc, out = _cli(store, "--text", "no key")
    assert rc == 2


# --- the importer ---------------------------------------------------------------------------

SAMPLE = """# To do - invented

## Tints

- [x] ~~**1. Already done thing**~~ - finished.
- [ ] **2. Fix the crossed names.** `XYZ` is labelled wrong.
  Second line of detail that explains it.
- [~] **3. Is it free?** - **Mark is raising it with the rep.** Verbal only.

## Next meeting - tell staff (added 28 Oct 2026)

| Who | Tell them |
|---|---|
| Karen / front desk | Scan every invented invoice |
| Someone else | Do the other thing |

## Single task section (Mark said yes 30 Oct)

In Touch, copy an item and change the price.

| Field | Value |
|---|---|
| Sell | $10 |

## Done - so it doesn't come back

- [ ] **99. This is in the Done section and must be skipped**
"""


def _import(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import import_manual_tasks as imp
        importlib.reload(imp)
    finally:
        sys.path.pop(0)
    return imp.propose(SAMPLE)


def test_importer_open_items_only_and_never_invents(tmp_path):
    items, skipped = _import(tmp_path)
    keys = [i["key"] for i in items]
    assert skipped["ticked"] == 1
    assert "manual-tasks:1" not in keys and "manual-tasks:99" not in keys
    by = {i["key"]: i for i in items}
    assert by["manual-tasks:2"]["owner"] == "Unassigned" and by["manual-tasks:2"]["first_seen"] is None
    assert "Second line of detail" in by["manual-tasks:2"]["detail"]
    assert by["manual-tasks:3"]["owner"] == "Mark" and "raising" in by["manual-tasks:3"]["owner_basis"]
    assert by["manual-tasks:3"]["source_status"].startswith("waiting")
    staff = [i for i in items if "Scan every invented" in i["text"]][0]
    assert staff["owner"] == "Karen" and staff["first_seen"] == "2026-10-28"      # the heading states the date
    other = [i for i in items if "other thing" in i["text"]][0]
    assert other["owner"] == "Unassigned"
    section = [i for i in items if i["source_status"] == "open (section)"][0]
    assert section["owner"] == "Unassigned" and section["first_seen"] is None      # "30 Oct" is not an added date
    assert len(items) == 5
    assert len(set(keys)) == len(keys)


def test_importer_preview_loads_without_duplicates(store, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import import_manual_tasks as imp
    finally:
        sys.path.pop(0)
    src, preview = tmp_path / "MANUAL-TASKS.md", tmp_path / "preview.json"
    src.write_text(SAMPLE, encoding="utf-8")
    assert imp.main(["x", str(src), str(preview)]) == 0
    assert json.loads(preview.read_text(encoding="utf-8"))["proposed"] == 5
    assert not store.exists()                                    # the importer alone writes nothing to the Hub
    for _ in range(2):
        rc, out = _cli(store, "--import-file", str(preview))
        assert rc == 0
    items = _items(store)
    assert len(items) == 5
    assert sum(1 for i in items if i["first_seen"] is None) == 3        # unknown dates stay unknown


# --- the page's routes: access, answers, and nothing else happening --------------------------

@pytest.fixture
def client(tmp_path, monkeypatch, store):
    cfg = tmp_path / "integrations.json"
    cfg.write_text(json.dumps({"checkin": {"staff_code": "test-code-9"}}), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg))
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _as(client, name):
    client.set_cookie("hub_staff", name)


def test_page_route_is_staff_locked_like_checkin(client):
    r = client.get("/api/workqueue", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 403 and r.get_json()["staff_needed"] is True
    assert r.headers["Cache-Control"] == "no-store"
    r = client.post("/api/workqueue", json={"action": "add", "text": "x"},
                    environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 403
    r = client.get("/api/workqueue", headers={"X-Checkin-Key": "ipad"})        # the iPad can never
    assert r.status_code == 403 and r.get_json().get("ipad") is True
    # unlock once with the staff code, and the same computer is in
    r = client.post("/api/checkin/staff/unlock", json={"code": "test-code-9"},
                    environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 200
    r = client.get("/api/workqueue", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 200
    # the server itself is always allowed
    assert client.get("/api/workqueue").status_code == 200


def test_add_done_and_filter_flow_through_the_routes(client, store):
    _as(client, "Karen")
    r = client.post("/api/workqueue", json={"action": "add", "text": "Ring the lab", "owner": "Karen"})
    d = r.get_json()
    assert r.status_code == 200 and d["total_open"] == 1 and d["me"] == "Karen"
    iid = d["todos"][0]["id"]
    d = client.post("/api/workqueue", json={"action": "done", "id": iid}).get_json()
    assert d["total_open"] == 0 and d["done"][0]["done_by"] == "Karen"
    bad = client.post("/api/workqueue", json={"action": "add", "text": "x", "owner": "Zed"})
    assert bad.status_code == 400 and "Owner" in bad.get_json()["error"]
    assert client.post("/api/workqueue", json={"action": "explode"}).status_code == 400


def test_only_mark_can_answer(client, store):
    iid = _ask()
    _as(client, "Karen")
    r = client.post("/api/workqueue", json={"action": "yes", "id": iid})
    assert r.status_code == 403 and "Only Mark" in r.get_json()["error"]
    assert _items(store)[0]["status"] == "open"
    client.set_cookie("hub_staff", "")
    assert client.post("/api/workqueue", json={"action": "yes", "id": iid}).status_code == 403
    _as(client, "Mark")
    r = client.post("/api/workqueue", json={"action": "yes", "id": iid})
    assert r.status_code == 200 and r.get_json()["answered"][0]["answer"] == "yes"
    again = client.post("/api/workqueue", json={"action": "not_now", "id": iid})
    assert again.status_code == 400 and "Already answered" in again.get_json()["error"]


def test_a_yes_click_triggers_nothing_else(client, store, monkeypatch, tmp_path):
    """Clicking Yes must only write the answer: no process, no mail, no network, no other file."""
    iid = _ask()
    wq.add_item(key="other", text="An unrelated job", owner="Karen")
    before = {i["key"]: i for i in _items(store)}
    files_before = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())

    def boom(*a, **k):
        raise AssertionError("a Yes click must not do this")
    import os, smtplib, socket, subprocess as sp, urllib.request
    for obj, name in ((sp, "run"), (sp, "Popen"), (sp, "call"), (sp, "check_output"), (os, "system"),
                      (os, "startfile"), (smtplib.SMTP, "__init__"), (smtplib.SMTP_SSL, "__init__"),
                      (urllib.request, "urlopen"), (socket.socket, "connect"), (socket, "create_connection")):
        if hasattr(obj, name):
            monkeypatch.setattr(obj, name, boom)
    _as(client, "Mark")
    r = client.post("/api/workqueue", json={"action": "yes", "id": iid})
    assert r.status_code == 200
    after = {i["key"]: i for i in _items(store)}
    assert after["other"] == before["other"]                                   # nothing else touched
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"ask:1"}
    ask = after["ask:1"]
    assert {k for k in ask if ask[k] != before["ask:1"].get(k)} == {"status", "answer", "answered_by", "answered_at"}
    files_after = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert [f for f in files_after if f not in files_before] == []


def test_the_answer_code_path_imports_nothing_that_can_send():
    src = (ROOT / "hub" / "workqueue.py").read_text(encoding="utf-8")
    for banned in ("subprocess", "smtplib", "requests", "urllib", "socket", "os.system", "gmail", "twilio"):
        assert banned not in src.replace("\n", " ").lower().replace("# ", ""), banned


def test_item_text_is_never_logged(client, store, caplog):
    caplog.set_level(logging.DEBUG)
    _as(client, "Mark")
    client.post("/api/workqueue", json={"action": "add", "text": "Ring Zztest Patient about lenses", "owner": "Karen"})
    iid = _ask()
    client.post("/api/workqueue", json={"action": "yes", "id": iid})
    low = caplog.text.lower()
    assert "zztest" not in low and "merge lens options" not in low


def test_data_file_is_git_ignored_and_lives_in_data(monkeypatch):
    monkeypatch.delenv("CEC_HUB_WORKQUEUE", raising=False)
    assert wq.data_path().parent.name == "data" and wq.data_path().name == "workqueue.json"
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in [l.strip() for l in ignore]


def test_tile_is_in_the_shipped_template_and_page_is_wired():
    tiles = json.loads((ROOT / "config" / "tiles.example.json").read_text(encoding="utf-8"))["tiles"]
    t = [x for x in tiles if x["id"] == "workqueue"]
    assert len(t) == 1 and t[0]["link"] == "#/queue"
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "renderWorkQueue" in js and "/^#\\/queue$/" in js
    assert "only save Mark's answer here" in js and "do not merge, send, or change anything" in js


def test_work_queue_css_follows_the_readability_standard():
    import re
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    block = css[css.index("/* --- To do / Waiting on Mark"):css.index("/* --- Medicare rejections")]
    assert "border-left" not in block
    sizes = [int(x) for x in re.findall(r"font-size:\s*(\d+)px", block)]
    assert sizes and min(sizes) >= 14
