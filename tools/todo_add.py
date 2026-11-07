"""Add (or refresh) an item on the Hub's To do list from a script or a Claude session.

    python tools/todo_add.py --key nightly-zeiss:missing-code --text "ZEISS code missing on 3 lines" \
        --owner Karen --source "Nightly ZEISS email"
    python tools/todo_add.py --ask --key ask:merge-lens-options --text "Merge Lens options to main?" \
        --recommendation "Yes - tests pass, nothing live changes" --origin "branch session/2026-11-04-lens"
    python tools/todo_add.py --get ask:merge-lens-options        # read Mark's answer
    python tools/todo_add.py --resolve nightly-zeiss:missing-code # the problem has cleared
    python tools/todo_add.py --import-file preview.json          # load a reviewed importer preview

The same --key never makes a second item: a repeat only updates "last seen". Prints ONE JSON line
(result added / updated / reopened ...) so a scheduled job can log it. Exit 0 = recorded, 2 = refused.

Where it writes: the Hub's git-ignored data\\workqueue.json, or --file / CEC_HUB_WORKQUEUE.
This tool sends nothing and never touches Optomate. Don't put patient detail in --text unless the
job needs it: the file stays on this machine, but staff see it on screen.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import workqueue  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(description="Add or refresh a Hub To do item (deduped by --key).")
    p.add_argument("--key", help="stable source key; the same key never duplicates")
    p.add_argument("--text", help="what to do (or, with --ask, the question)")
    p.add_argument("--owner", default="Unassigned", help="Karen, Angie, Mark, Claude or Unassigned")
    p.add_argument("--source", default="", help="where this came from, e.g. 'Nightly ZEISS email'")
    p.add_argument("--detail", default="", help="one short extra line")
    p.add_argument("--first-seen", default=None, help="YYYY-MM-DD; default today")
    p.add_argument("--ask", action="store_true", help="a question for Mark (Waiting on Mark view)")
    p.add_argument("--recommendation", default="", help="with --ask: what you recommend")
    p.add_argument("--origin", default="", help="with --ask: which session or branch asked")
    p.add_argument("--get", metavar="KEY", help="print the item with this key (its answer, status)")
    p.add_argument("--resolve", metavar="KEY", help="mark the open job with this key done (cleared)")
    p.add_argument("--import-file", metavar="PREVIEW.json", help="add the items in a reviewed importer preview")
    p.add_argument("--file", help="override the data file (default: the Hub's data\\workqueue.json)")
    return p


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.file:
        os.environ["CEC_HUB_WORKQUEUE"] = args.file
    try:
        if args.get:
            item = workqueue.get_by_key(args.get)
            print(json.dumps({"result": "found" if item else "not_found", "item": item}, ensure_ascii=False))
            return 0 if item else 2
        if args.resolve:
            out = workqueue.resolve_by_key(args.resolve, "job")
            print(json.dumps(out, ensure_ascii=False))
            return 0 if out["result"] != "not_found" else 2
        if args.import_file:
            data = json.loads(Path(args.import_file).read_text(encoding="utf-8"))
            tally = {"added": 0, "updated": 0}
            for entry in data.get("items", []):
                tally[workqueue.import_item(entry)["result"]] += 1
            print(json.dumps({"result": "imported", **tally}))
            return 0
        if not args.key or not args.text:
            print(json.dumps({"result": "refused", "error": "--key and --text are required."}))
            return 2
        out = workqueue.add_item(
            key=args.key, text=args.text, owner=args.owner, source=args.source, detail=args.detail,
            first_seen=args.first_seen, kind="ask" if args.ask else "todo",
            recommendation=args.recommendation, origin=args.origin, added_by="todo_add")
        print(json.dumps({"result": out["result"], "id": out["item"]["id"], "key": out["item"]["key"],
                          "seen_count": out["item"]["seen_count"]}))
        return 0
    except (workqueue.WorkQueueError, OSError, ValueError) as e:
        print(json.dumps({"result": "refused", "error": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(run())
