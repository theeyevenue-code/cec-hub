"""SOP markdown parser for CEC Hub.

Turns the markdown files in the sops folder into structured blocks that the
frontend renders as big-text checklists. The conventions live in
sops/README.md — keep this parser and that README in sync.

Everything is HTML-escaped BEFORE any tags are added, so an SOP file can
never inject markup into the app.
"""

import re
from pathlib import Path

# --- Inline patterns (applied to already-escaped text) ---
LINK_RE = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
CODE_RE = re.compile(r"`([^`]+)`")
MARK_RE = re.compile(r"\[MARK:\s*([^\]]+)\]", re.IGNORECASE)

# --- Block patterns (applied to raw lines) ---
STEP_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
BRANCH_RE = re.compile(r"^IF\s+(.+?):\s*(.*)$", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def parse_frontmatter(text: str):
    """Split optional ``--- key: value ---`` frontmatter from the body.

    Returns (meta_dict, body). Files without frontmatter come back with an
    empty dict and the full text — never an error.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        if ":" in lines[i]:
            key, value = lines[i].split(":", 1)
            meta[key.strip().lower()] = value.strip()
    # Opening --- but no closing one: treat the whole file as body.
    return {}, text


def render_inline(text: str, mark_flags=None) -> str:
    """Escape text, then apply the small inline vocabulary:
    [MARK: ...] flags, [label](http...) big buttons, `code`, **bold**.
    """
    out = _escape(text)

    def _mark(m):
        note = m.group(1).strip()
        if mark_flags is not None:
            mark_flags.append(note)
        return (
            '<span class="mark-flag">Still to confirm with Mark: '
            f"{note}</span>"
        )

    out = MARK_RE.sub(_mark, out)

    def _link(m):
        label, url = m.group(1), m.group(2)
        if url.startswith(("http://", "https://")):
            return (
                f'<a class="sop-btn" href="{url}" target="_blank" '
                f'rel="noopener">{label}</a>'
            )
        return label  # anything else (javascript:, file:, relative) stays plain text

    out = LINK_RE.sub(_link, out)
    out = CODE_RE.sub(r"<code>\1</code>", out)
    out = BOLD_RE.sub(r"<strong>\1</strong>", out)
    return out


def _safe_image_src(src: str):
    """Only serve local images out of sops\\images\\. Returns the URL path
    the app serves them at, or None if the reference looks unsafe."""
    name = src.replace("\\", "/")
    if name.startswith("images/"):
        name = name[len("images/"):]
    if not name or "/" in name or ".." in name or ":" in name:
        return None
    return f"/sop-images/{name}"


def _parse_quote_run(qlines, mark_flags):
    """A run of consecutive ``> ...`` lines becomes DECISION branch boxes
    (lines shaped ``IF condition: action``) and/or plain note boxes."""
    blocks = []
    current = None  # ("branch", condition, [action bits]) or ("note", [bits])

    def flush():
        nonlocal current
        if current is None:
            return
        if current[0] == "branch":
            blocks.append({
                "type": "branch",
                "condition": render_inline(current[1], mark_flags),
                "action": render_inline(" ".join(current[2]).strip(), mark_flags),
            })
        else:
            blocks.append({
                "type": "note",
                "html": render_inline(" ".join(current[1]).strip(), mark_flags),
            })
        current = None

    for line in qlines:
        m = BRANCH_RE.match(line)
        if m:
            flush()
            current = ("branch", m.group(1).strip(), [m.group(2).strip()])
        elif current is not None:
            current[-1].append(line)
        else:
            current = ("note", [line])
    flush()
    return blocks


def parse_sop(text: str) -> dict:
    """Parse one SOP file into {title, meta, blocks, mark_flags}."""
    meta, body = parse_frontmatter(text)
    mark_flags = []
    blocks = []
    title = None

    lines = body.splitlines()
    para: list[str] = []
    bullets: list[str] = []
    quotes: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            blocks.append({
                "type": "para",
                "html": render_inline(" ".join(para).strip(), mark_flags),
            })
            para = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            blocks.append({
                "type": "bullets",
                "items": [render_inline(b, mark_flags) for b in bullets],
            })
            bullets = []

    def flush_quotes():
        nonlocal quotes
        if quotes:
            blocks.extend(_parse_quote_run(quotes, mark_flags))
            quotes = []

    def flush_all():
        flush_para()
        flush_bullets()
        flush_quotes()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_all()
            i += 1
            continue

        if stripped.startswith("# ") and not stripped.startswith("## "):
            flush_all()
            text_part = stripped[2:].strip()
            if title is None:
                title = text_part
            else:
                blocks.append({"type": "heading", "text": text_part})
            i += 1
            continue

        m = HEADING_RE.match(stripped)
        if m:
            flush_all()
            blocks.append({"type": "heading", "text": m.group(2).strip()})
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            flush_bullets()
            quotes.append(stripped.lstrip(">").strip())
            i += 1
            continue
        flush_quotes()

        m = IMAGE_RE.match(stripped)
        if m:
            flush_all()
            src = _safe_image_src(m.group(2))
            if src:
                blocks.append({"type": "image", "src": src,
                               "alt": m.group(1).strip()})
            i += 1
            continue

        m = STEP_RE.match(stripped)
        if m:
            flush_all()
            number, step_text = m.group(1), [m.group(2)]
            # Continuation lines: plain text until a blank line or a new block.
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if (not nxt or nxt.startswith(("#", ">")) or STEP_RE.match(nxt)
                        or BULLET_RE.match(nxt) or IMAGE_RE.match(nxt)):
                    break
                step_text.append(nxt)
                j += 1
            blocks.append({
                "type": "step",
                "number": number,
                "html": render_inline(" ".join(step_text).strip(), mark_flags),
            })
            i = j
            continue

        m = BULLET_RE.match(stripped)
        if m:
            flush_para()
            bullets.append(m.group(1).strip())
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_all()

    return {
        "title": title or "Untitled guide",
        "meta": meta,
        "blocks": blocks,
        "mark_flags": mark_flags,
    }


# --- Directory-level helpers -------------------------------------------------

def _plain_text(body: str) -> str:
    """Rough plain text of a markdown body, for summaries and snippets."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)         # images out
    text = re.sub(r"\[MARK:[^\]]*\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # links -> label
    text = re.sub(r"[#>*`_]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sop_files(sops_dir: Path):
    if not sops_dir.is_dir():
        return []
    return sorted(
        p for p in sops_dir.glob("*.md") if p.name.lower() != "readme.md"
    )


def load_sop(sops_dir: Path, slug: str):
    """Load and parse one SOP by slug (filename without .md). None if absent
    or the slug looks like a path escape."""
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", slug) or ".." in slug:
        return None
    path = sops_dir / f"{slug}.md"
    if not path.is_file() or path.name.lower() == "readme.md":
        return None
    parsed = parse_sop(path.read_text(encoding="utf-8"))
    parsed["slug"] = slug
    return parsed


def list_sops(sops_dir: Path):
    """Catalogue of every SOP: slug, title, category, updated, owner, summary."""
    items = []
    for path in _sop_files(sops_dir):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        summary = _plain_text(re.sub(r"^#\s+.+$", " ", body, count=1,
                                     flags=re.MULTILINE))[:140]
        items.append({
            "slug": path.stem,
            "title": title_match.group(1).strip() if title_match else path.stem,
            "category": meta.get("category", "General"),
            "updated": meta.get("updated", ""),
            "owner": meta.get("owner", ""),
            "summary": summary,
            "mark_count": len(MARK_RE.findall(raw)),
        })
    items.sort(key=lambda s: (s["category"].lower(), s["title"].lower()))
    return items


def search_sops(sops_dir: Path, query: str):
    """Case-insensitive substring search over title + full body text.
    Returns list entries plus a plain-text snippet around the first hit."""
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for path in _sop_files(sops_dir):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        plain = _plain_text(body)
        haystack = f"{title}\n{plain}".lower()
        idx = haystack.find(q)
        if idx == -1:
            continue
        if idx <= len(title):
            snippet = plain[:120]
        else:
            body_idx = plain.lower().find(q)
            start = max(0, body_idx - 55)
            end = min(len(plain), body_idx + len(q) + 65)
            snippet = ("…" if start > 0 else "") + plain[start:end] + \
                      ("…" if end < len(plain) else "")
        results.append({
            "slug": path.stem,
            "title": title,
            "category": meta.get("category", "General"),
            "snippet": snippet,
        })
    return results
