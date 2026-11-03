"""Every check on what LEAVES the server, held in one place (Codex
2026-11-03-hub-assistant #2, accepted response ACCEPT 1-2).

  normalise()    Unicode NFKC, accents folded, zero-width / control characters
                 removed, dashes and quotes made plain, spacing collapsed.
  guard()        The typed question: normalised first, then the Lens Finder's
                 guard_text (names, dates, ID-shaped numbers, contact details,
                 length). Any letter outside plain A-Z after normalising is
                 blocked (a name in another script can't be checked against the
                 patient list). Any error = blocked: fail closed.
  check_view()   One tool result against the outbound schema APPROVED for that
                 tool (allowed fields only; each value of its approved kind;
                 lens words must be the lens files' own strings).
  check_body()   The WHOLE request body just before it is sent: fixed system
                 prompt and tool list, every user turn (question re-guarded,
                 tool results re-checked), every history answer.
  check_answer() The model's give_answer: ids from this question only, reason
                 codes from the approved list, text digit-free and non-clinical.

Vocabulary exceptions (words that are also surnames but mean lenses here) live
ONLY in hub.lenschat.LENS_WORDS; a tool cannot add to them. This narrows what
can leak; it does not guarantee arbitrary prose is free of identifiers.
"""

from __future__ import annotations

import json
import re
import unicodedata

from hub import lenschat

_ZW = re.compile(r"[​-‏ - ⁠-⁯﻿]")
_DASH = re.compile(r"[‐-―−﹘﹣－]")
_QUOTE = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
          "“": '"', "”": '"', "„": '"', "″": '"'}


class OutboundRefused(Exception):
    """Raised when anything about to leave fails a check. The reason is a code."""


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = _ZW.sub("", t)
    t = _DASH.sub("-", t)
    t = "".join(_QUOTE.get(c, c) for c in t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = "".join(" " if unicodedata.category(c).startswith("C") else c for c in t)
    return re.sub(r"\s+", " ", t).strip()


def guard(text, names, given: str = "", surname: str = "") -> tuple[str, str]:
    """(text that may be sent, "") or ("", reason code). Fails closed."""
    try:
        if not isinstance(text, str):
            return "", "empty"
        if names is None:
            return "", "names_unavailable"
        t = normalise(text)
        if re.search(r"[^\x20-\x7e]", t):
            return "", "script"
        return lenschat.guard_text(t, names, normalise(given or ""), normalise(surname or ""))
    except Exception:                       # noqa: BLE001 - any surprise blocks the send
        return "", "guard_error"


# ---------------------------------------------------------------------------
# Outbound schema kinds (used by registry.APPROVED_TOOLS)
# ---------------------------------------------------------------------------

ID_RE = re.compile(r"^[A-Z][0-9a-f]{10}$")
INDEX_RE = re.compile(r"^1\.\d\d$")


def _bad(why: str):
    raise OutboundRefused(why)


def _check_value(kind, v, vocab: frozenset, path: str) -> None:
    if isinstance(kind, dict):
        if not isinstance(v, dict):
            _bad(f"shape:{path}")
        extra = set(v) - set(kind)
        if extra:
            _bad(f"field:{path}")
        for k, sub in kind.items():
            if k in v:
                _check_value(sub, v[k], vocab, f"{path}.{k}")
        return
    if isinstance(kind, list):
        if not isinstance(v, list) or len(v) > 60:
            _bad(f"list:{path}")
        for i, x in enumerate(v):
            _check_value(kind[0], x, vocab, f"{path}[{i}]")
        return
    if isinstance(kind, tuple):                     # ("enum", values)
        if v not in kind[1]:
            _bad(f"enum:{path}")
        return
    if kind == "id":
        if not (isinstance(v, str) and ID_RE.match(v)):
            _bad(f"id:{path}")
    elif kind == "index":
        if not (isinstance(v, str) and (v == "" or INDEX_RE.match(v))):
            _bad(f"index:{path}")
    elif kind == "count":
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100000:
            _bad(f"count:{path}")
    elif kind == "rank":
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 100000):
            _bad(f"rank:{path}")
    elif kind == "bool":
        if not isinstance(v, bool):
            _bad(f"bool:{path}")
    elif kind == "lens_word":
        # Provenance: the exact string must come from the lens files.
        if not (isinstance(v, str) and (v == "" or v in vocab)):
            _bad(f"lens_word:{path}")
    else:
        _bad(f"kind:{path}")


def check_view(schema, view, vocab: frozenset) -> None:
    _check_value(schema, view, vocab, "$")


# ---------------------------------------------------------------------------
# The model's answer
# ---------------------------------------------------------------------------

def plain_text_ok(text: str) -> str:
    """'' when the model's free text may be shown, else a reason code."""
    if re.search(r"\d", text) or re.search(r"[$%½¼¾]", text) \
            or lenschat.NUMBER_WORDS.search(text):
        return "digits"
    if lenschat.CLINICAL_WORDS.search(text):
        return "clinical"
    return ""


def check_answer(inp, allowed_ids: set, allowed_codes: set, empty_code: str) -> tuple[dict | None, str]:
    """(clean answer, "") or (None, reason code). row_ids may be empty only
    with the single reason code `empty_code` (the question isn't one the
    tools answer)."""
    if not isinstance(inp, dict):
        return None, "bad_shape"
    ids, codes, text = inp.get("row_ids"), inp.get("reason_codes"), inp.get("note")
    if not isinstance(ids, list) or not isinstance(codes, list) or not isinstance(text, str):
        return None, "bad_shape"
    if len(ids) > 5 or len(codes) > 6 or set(inp) - {"row_ids", "reason_codes", "note"}:
        return None, "bad_shape"
    if any(c not in allowed_codes for c in codes):
        return None, "unknown_reason"
    if not ids and codes != [empty_code]:
        return None, "bad_shape"
    if ids and empty_code in codes:
        return None, "bad_shape"
    if any(not isinstance(i, str) or i not in allowed_ids for i in ids):
        return None, "unknown_id"
    text = re.sub(r"\s+", " ", normalise(text)).strip()
    if len(text) > 300:
        return None, "too_long"
    why = plain_text_ok(text)
    if why:
        return None, why
    return {"row_ids": list(dict.fromkeys(ids)), "reason_codes": list(dict.fromkeys(codes)),
            "note": text}, ""


# ---------------------------------------------------------------------------
# The whole request body
# ---------------------------------------------------------------------------

BODY_KEYS = {"model", "max_tokens", "system", "tools", "tool_choice", "thinking",
             "output_config", "messages"}
MODEL_BLOCKS = {"tool_use", "text", "thinking", "redacted_thinking"}


def _g(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def check_body(body: dict, *, system: str, tools: list, names, schemas: dict,
               vocab: frozenset, codes: set, errors: set) -> None:
    """Raise OutboundRefused unless EVERY part of the body is one of: the
    fixed system prompt, the registry's tool list, a guarded question
    envelope, a tool result that passes its approved schema (or a fixed error
    string), a checked history answer, or the model's own blocks from this
    question. schemas: tool name -> approved outbound schema."""
    if set(body) - BODY_KEYS:
        _bad("body_keys")
    if body.get("system") != system:
        _bad("system")
    if body.get("tools") != tools:
        _bad("tools")
    pending: dict = {}                      # tool_use id -> tool name
    for m in body.get("messages") or []:
        role, content = m.get("role"), m.get("content")
        if role == "user" and isinstance(content, str):
            _check_envelope(content, names, schemas, vocab, codes)
        elif role == "user" and isinstance(content, list):
            for b in content:
                if _g(b, "type") != "tool_result":
                    _bad("user_block")
                name = pending.pop(_g(b, "tool_use_id"), None)
                text = _g(b, "content")
                if _g(b, "is_error"):
                    if text not in errors:
                        _bad("error_text")
                    continue
                if name not in schemas or not isinstance(text, str):
                    _bad("tool_result")
                check_view(schemas[name], json.loads(text), vocab)
        elif role == "assistant" and isinstance(content, str):
            ans = json.loads(content)
            if not isinstance(ans, dict) or set(ans) - {"row_ids", "reason_codes", "note"}:
                _bad("history")
            if any(not (isinstance(i, str) and ID_RE.match(i)) for i in ans.get("row_ids", [])):
                _bad("history_id")
            if any(c not in codes for c in ans.get("reason_codes", [])):
                _bad("history_code")
            if not isinstance(ans.get("note"), str) or plain_text_ok(ans["note"]):
                _bad("history_text")
        elif role == "assistant" and isinstance(content, list):
            for b in content:
                t = _g(b, "type")
                if t not in MODEL_BLOCKS:
                    _bad("model_block")
                if t == "tool_use":            # the model's own request; its result is checked
                    pending[_g(b, "id")] = _g(b, "name")
        else:
            _bad("message")


def _check_envelope(content: str, names, schemas: dict, vocab: frozenset, codes: set) -> None:
    try:
        env = json.loads(content)
    except ValueError:
        _bad("envelope")
    if not isinstance(env, dict) or set(env) - {"question", "reason_codes", "context"}:
        _bad("envelope")
    q = env.get("question")
    clean, why = guard(q, names)
    if why or clean != q:
        _bad("question")
    if any(c not in codes for c in env.get("reason_codes") or []):
        _bad("envelope_codes")
    ctx = env.get("context") or {}
    if not isinstance(ctx, dict):
        _bad("context")
    for name, view in ctx.items():
        if name not in schemas:
            _bad("context_tool")
        check_view(schemas[name], view, vocab)
