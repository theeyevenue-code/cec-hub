# Hub assistant — design (3 Nov 2026, DESIGN ONLY)

Mark, 3 Nov 2026: "keep lens chat but make it a bigger AI ... full Jarvis style for price
list, lens parameters, etc" and "make it so it can expand its capability or scope as required".
Starting point: the Lens Finder chat (`hub/lenschat.py`, `hub/compare.py`, Codex review
`2026-11-03-lens-finder-chat.md` + response). Nothing here is built; Codex reviews this first.

## 1. Goal and "done"

**Done for the practice:** one chat box on the Hub home where Mark (later staff) types a
question in plain words and gets an answer card whose every number is the practice's own,
with its source and date — lens options, lens prices, lens ranges, frame price rules, how-to
steps, a few read-only counts. Adding a new kind of question is one small module, not a rebuild.

| Who | When | Gate |
|---|---|---|
| Mark | Phase 1 | Existing `lenschat.mark_device()` (this computer or `allow_ips`) |
| Staff (Karen, Angie) | After Mark says so, per tool | `@staff_only` + a per-tool `audience: staff` flag |

Not the goal: a general chatbot, web browsing, writing to Optomate, anything clinical.

## 2. Architecture — a tool registry the model picks from

One assistant loop (the existing `lenschat.ask()` generalised), one fixed system prompt, and
a **registry** of small read-only tools. The model chooses tools; the **core** owns the
outbound schema, number rendering, guards and spend. Adding a capability = one module + its
tests + one registry line. No core changes.

Each tool module declares:

| Field | Meaning | Example (price lookup) |
|---|---|---|
| `name`, `description` | What the model sees | `lens_price`: "Supplier cost and CEC sell price for a lens" |
| `input_schema` | Strict JSON schema, enums only where possible (as `compare_options` does today) | brand, product, index, coating from fixed lists |
| `source` | Which local function/file supplies the facts | `lenses.load_catalog` + `lenses.price_now` + `lenses.attach_cec_price` |
| `privacy` | `public` / `business` / `patient` (see §3) | `business` |
| `audience` | `mark` / `staff` | `mark` |
| `approved_by` | Who said yes to this tool and when (required; see §6) | "Mark, <date>" |
| `run(args, ctx)` | Returns **records**, each with `id`, fields, `source`, `as_of` | row id, per-lens cost, sell per pair, basis L25/deal, book date |
| `model_view(records)` | What the model may see: ids + words + flags, **no numbers** | cost rank, "priced"/"unavailable" |
| `render(records, picks)` | Server builds the card from records; the model only picked ids | table with $ figures from the CSV |
| `tests` | Unit tests incl. "no digit leaves in model_view" | `tests/test_tool_lens_price.py` |

Core responsibilities, enforced for every tool, never per tool:

| Step | Rule | Exists today in |
|---|---|---|
| Typed text guard | `guard_text()` before anything leaves | `lenschat.guard_text` |
| Tool-input check | Only enum/validated args; unknown keys refused | `lenschat.clean_filters` pattern |
| Outbound schema | Model sees only `model_view()` output + reason codes | `lenschat.candidates/model_view` |
| Answer check | Picked ids must come from this question's records; reason codes from the tool's list; free text digit-free and non-clinical | `lenschat.check_answer` |
| Rendering | Every number from records, Decimal arithmetic | `compare.compare`, `compare.fmt_money` |
| Spend | Reserve worst case before each call, settle after | `lenschat.Ledger` |
| Session | Memory only, 30 min, reset on patient change | `lenschat.session_for` |
| Logs | Tool names, reason codes, counts only | `lenschat.ask` logging |

Routing: the model gets the registry's tool list (filtered to the tools switched on for this
user) and calls `give_answer` once with `{tool_results_used, row_ids, reason_codes, text}`.
Tool loop stays capped at 4 calls per question (`MAX_CALLS`).

## 3. Privacy model (generalised from the lens chat)

| Class | What the tool reads | What may leave the server | Who approves adding a tool |
|---|---|---|---|
| **public** | Hub SOPs, lens CSVs' names/ranges, frame pricing rules | Product names, ranges as words/flags, SOP step text | Server Desk, Codex review of the module |
| **business** | Prices, costs, deal basis, counts of orders/jobs | **Never the figures** — only ranks ("cheapest", "priced"/"unavailable"), counts as bands ("none / some / many") or nothing; the server renders the numbers locally | Mark's explicit yes per tool (money) |
| **patient** | One patient's saved Rx, frame, order status | Only what the lens chat sends today: requirement words, fit flags, candidate attributes. **Never** a name, id, date, note, DOB, or Rx number | Mark's explicit yes per tool + Codex review before build |

Rules that apply to **every** tool and **every** typed message, not just lens questions:

1. **Pre-send check on typed text** — `guard_text()` (patient's own name, any first name or
   surname in Optomate's patient table unless a lens word, dates, phone/Medicare/ID-shaped
   numbers, text over 300 chars, @/http). Blocked = "Please rephrase; nothing was sent."
   The lens-word allowlist (`LENS_WORDS`) grows per tool via a `safe_words` field in the registry.
2. **Patient binding is UI-only.** A patient-class tool runs only when the page has picked a
   patient (`patient_id` in the POST body, as `/api/lenschat/ask` does); the id is bound
   server-side (`binding = "p:<id>:..."`). No tool takes a name, id or search term from the
   model. Patient search is never a tool.
3. **No free-text sources.** `local-reports\`, exam notes, Rx comments, invoice PDFs, emails
   and any Optomate note field are not tool sources — ever. Only structured fields.
4. **History** is memory-only, three turns, reset on patient change, never persisted.
5. **Anthropic retains API inputs/outputs up to 30 days** (Codex §1). Verify the workspace
   data setting once before staff use; say so on the chat panel ("Lens words only ...").
6. Model text in the answer is shown only after `check_answer()`; nothing streamed.

## 4. Numbers

- Every price, power, range, count or date on screen is **rendered by the server from tool
  records** (`render()`), copied verbatim or added with Decimal — the same way `compare.py`
  does pair totals.
- Model free text must be **digit-free** (`check_answer` already rejects `\d`, `$ %`, number
  words). No "verify digits against records" mode in phase 1 — simpler and already tested.
- A record with no figure renders **"Cost unavailable — supplier confirmation needed"**
  (`compare.COST_MISSING`) and is excluded from any "cheapest"; the model sees `cost:
  "unavailable"` and must not call it cheapest (SYSTEM rule 5).
- Each card shows **source + as-of**: CSV name and book/deal date (`compare.source_of`,
  `source_date`), SOP slug, config file name, or "Optomate, read <time>".

## 5. First capability set (smallest useful first)

| # | Tool | Privacy | Backed by (exists) | Missing |
|---|---|---|---|---|
| 1 | **Lens compare** | patient / typed Rx | `compare.compare`, `lenschat` (live, awaiting key) | Move into registry shape; no behaviour change |
| 2 | **Lens price lookup** (per lens cost + CEC sell per pair, basis, date) | business | `lenses.load_catalog`, `price_now`, `attach_cec_price`, `lensoptions.sell_index`; `config/cec_prices.json` | Tool module; a fixed product/coating enum built from the catalogue at start; per-lens vs per-pair labelled explicitly (Mark's per-lens rule, 27 Sep) |
| 3 | **Lens parameters / ranges** (sph, cyl, add, blank mm, stock vs grind) | public | `lenses.find_options`, CSV `sph_min..add_max`, `range_basis` | Tool module; "range not in the file" and "confirm with ZEISS" surfaced as flags |
| 4 | **Frame pricing rules** (what should this frame sell for) | business | Agent `inventory/frame_pricing.py::price_for`, `rule_group` | Hub-side subprocess wrapper (same shape as `ordercheck.check`); inputs are supplier/brand/list price typed by staff — no Optomate read |
| 5 | **How-to / SOP answer with citation** | public | `sop_parser.search_sops`, `load_sop`; `/api/sop-search` | Tool returns matching steps as records (slug, heading, step text); card links `#/sops/<slug>`; model picks the steps, never rewrites them |
| 6 | **Practice counts** (jobs arrived today; orders waiting by supplier) | business (aggregates) | Jobs: agent `orders.jobscan` (`status`, `lookup`); lens jobs log `integrations.lens_jobs` | **No existing "orders waiting by supplier" query** — needs a new read-only SQL in the agent (NOLOCK SELECT, like `lens_bridge.sqlq`) returning counts only; Mark's yes (Optomate read + business) |

Later candidates (not phase 1): Optomate quirks lookup (`KNOWN-QUIRKS.md`, public-ish but
technical), recall month summary (`recall.success`, aggregates), credits watch-list
(`lists.credits_list`), Medicare worklist counts (`medicare.worklist` — carries patient
labels; aggregate only). Each goes through §6.

## 6. Expansion process

| Step | Who | What |
|---|---|---|
| Propose | Mark or any desk | One line in `NEXT-SESSION.md`: question type, example wording, data source |
| Classify | Server Desk | Privacy class, audience, source function, what is missing (table like §5) |
| Approve | **Mark's explicit yes** for any tool that touches patient data, money or Optomate reads; Server Desk alone for public tools | Recorded in the registry's `approved_by` field |
| Review | **Codex gate** for: new data source, patient-class, anything near a write | Design note in `codex-reviews/` before build |
| Build | Server Desk | Module + tests; `python -m hub.assistant check` verifies no digits in `model_view` on fixtures |
| Switch on | `config/assistant.json` → `"tools": {"lens_price": {"enabled": true, "audience": "mark"}}` | Per-tool flag; off by default; `enabled: false` removes it from the model's list |
| Watch | Ledger + hub.log counts per tool (`answered / blocked / unverified`) | Weekly line in CHANGELOG |

**Hard rule: the assistant is read-only.** No tool writes to Optomate, Gmail, Twilio, Xero,
files or config. "Write" includes marking, ticking, sending, drafting. A write capability needs
its own future design with a confirm-and-human-press pattern (the model proposes; a person
presses a button that calls the existing gated endpoint, e.g. `/api/jobscan/confirm`), its own
Codex review and Mark's "turn on live X" line — the same two-key gates every send has today.

## 7. Model and cost

| Item | Choice | Why |
|---|---|---|
| Answers | **Sonnet 5.5** (`claude-sonnet-5-5`, as today) | Picks ids/reason codes reliably; US$2 / US$10 per MTok verified by Codex 3 Nov |
| Routing | **None in phase 1** — one call, tools in the list | A separate Haiku router adds a call and a failure mode; revisit if the tool list passes ~12 |
| Caching | Prompt-cache the system prompt + registry tool list | Fixed text; cuts input cost on every call |
| Cap | **Existing ledger, A$15 per calendar month** (`config/lenschat.json`: `monthly: true`, `cap_aud: "15.00"`), enforced **server-side** in `Ledger.reserve()` before every call; missing ledger = refuse | Already built and reviewed; one ledger for all tools |
| Backstop | Workspace console limit (USD) | Codex §4 |

Cost per question (estimate, not a quote): ~6–10k input tokens (system + 6 tools + records
view + history) and ~500–1,500 output over 1–3 calls ≈ US$0.03–0.05 ≈ **A$0.05–0.08**. At
20 questions a practice day that is ~A$25–30 a month, so the A$15 cap would stop the chat
mid-month at that rate. **No paid calls during build or test** — fixtures and a fake client
(as `tests/test_compare_chat.py` does); a live smoke test is quoted first (Mark, 29 Oct rule).

## 8. UI

- One **chat panel on the Hub home**, above the tiles; the Lens Finder page keeps its own
  chat box (same endpoint, patient pre-bound). Tile `assistant` in `config/tiles.json`.
- **Answer first, biggest**: the card leads with the decision/figure (22 px+), then a table
  (>2 items), then "Why" bullets (the reason-code words), then **Sources** (file + date) in
  one line. Body 17 px, nothing under 14 px, no emoji, one colour per meaning.
- Patient context shown as a chip ("Patient picked: yes — Rx saved today") — never the name
  in the chat; the name lives on the Lens Options page only.
- Blocked / unverified replies reuse the fixed strings (`REPHRASE`, `UNVERIFIED`) and always
  show the no-AI fallback (Compare table, SOP search results, price table).
- Staff-facing: product names (Digital, Ultra HDV, Superb, Individual), never tier words.
- Status line: "A$x left of A$15 this month" from `lenschat.status()`.
- Mockups go to Mark as a captured HTML file, not an artifact.

## 9. Risks, what not to build, phases

| Risk | Mitigation |
|---|---|
| A new tool leaks a figure or name in `model_view` | Registry test harness: fixture run, assert no `\d`, no name words; Codex review for patient-class |
| Staff type a patient's name into the home chat | Same `guard_text` on every message; fail closed when the name list can't load |
| Stale CSV prices answered confidently | Every card shows as-of date; "L25 verbal, not in writing" basis label kept (`compare.basis_label`) |
| Cap exhausted mid-month | Chat says so; every tool's no-AI fallback still works |
| Scope creep into writes | §6 hard rule; registry refuses a module without `read_only: true` |
| Tool list grows, answers degrade | Review at 12 tools; then consider a router |

**Do not build:** a vector database or RAG over notes; patient search as a tool; free-text
notes as sources; any write; general web/email access; Haiku router in phase 1; a staff
rollout before Mark has used it for a fortnight.

| Phase | Scope | Exit |
|---|---|---|
| **1** | Refactor lens chat into the registry (no behaviour change, tests green) + 3 read-only tools: lens price, lens ranges, SOP how-to. Mark-only. | Mark uses it ~2 weeks; counts per tool; Codex receipt |
| 2 | Frame pricing rules; practice counts (needs new agent SQL); staff audience per tool | Mark's yes per tool |
| 3 | Later candidates (§5); router if needed; confirm-and-press write pattern as its own design | Separate Codex review |

## 10. Open questions for Mark (one at a time)

1. **Home-page chat for staff too, or Mark-only until phase 2?** Recommend Mark-only in
   phase 1 (same gate as today), staff per tool from phase 2.
2. **Is A$15/month enough once there are six tools?** Recommend keeping A$15 for phase 1 and
   reading the ledger after a fortnight before any change.
3. **"Orders waiting by supplier" needs a new read-only Optomate query in the agent — yes to
   designing it?** Recommend yes, counts only, in phase 2.
