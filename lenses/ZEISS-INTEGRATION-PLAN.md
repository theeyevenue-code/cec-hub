# Lens Finder — ZEISS integration plan

*Written 19 Sep 2026 (Fable, practice server) for the execution session (Opus). Mark's brief:
"the lens finder needs to be updated to integrate Zeiss. I need a revamp esp on lens power
availability … goal is full integration to Zeiss as I'm switching supplier."*

Everything below was verified against the code and the second-brain documents on 19 Sep.
**Built 19 Sep 2026 (Opus) on branch `session/zeiss-lens-finder` — phases A–E done, F not started.**
The plan is kept as the design record; `lenses/README.md` is the operating doc. Findings from
the build that Mark should know are at the end (§8).

---

## 0. Where things stand (verified facts)

**The Lens Finder is in THIS repo (cec-hub), not the Optomate agent.**

| Piece | File | Notes |
|---|---|---|
| Engine | `hub/lenses.py` (790 lines) | CSV parse → `find_options` / `check_job` / `group_products` / `attach_cec_price` |
| Routes | `app.py` lines 347–470 | `/api/lenses`, `/find`, `/check`, `/jobs`, `/upload` |
| UI | `static/app.js` lines 1153–1650 | finder form, results table, library with coating picker |
| Data | `lenses/hoya.csv` (1,881 rows, 244 products) | built by `lenses/convert_provision_t3.py` from the ProVision T3 PDF + Hoya Product Guide charts |
| Retail | `config/cec_prices.json` | Concord sell prices, keyed on **Hoya design names + Hoya coating tiers** |
| Declutter | `config/lens_filter.json` (git-ignored) | `keep_only` / `preferred` keyed on **Hoya names** |
| Tests | `tests/test_lenses.py` (48 tests) | parse, find, check_job, upload |

**What the engine can and cannot do today**

- Matches **single vision only** (`sv_only()`); progressives/occupationals/bifocals are browse-only.
  987 of the 1,881 Hoya rows have no sphere range at all (all the multifocals).
- Fit = sphere in range, |cyl| ≤ cyl_max, |sph+cyl| ≤ combined_max, blank ≥ frame need.
- Ranks cheapest **index-appropriate** lens first (`INDEX_BY_POWER`: ≤2.00→1.50, ≤4.00→1.60,
  ≤6.00→1.67, else 1.74). Says what a stock lens saves against the cheapest grind.
- One `price` per row. No notion of supplier, deal vs book price, promo period, or "no longer ordered".
- The "Recent lens jobs from Optomate" panel is **dark**: `config/integrations.json` has no
  `lens_jobs` path and the Optomate module that writes `lens-jobs.jsonl` sits unmerged on
  `origin/claude/lensjobs-hub-checks` in the CEC-Optomate-Agent repo.

**The ZEISS deal, as it stands 19 Sep 2026** (second-brain wiki `zeiss-switch-net-position-2026-09`,
`lens-rrp-reset-2026-09`, staff sheet `price-lists/staff/CEC-Lens-Decision-Sheet-Staff.html`)

- Pricing went live at ZEISS on **11 Sep 2026**. Bundle agreement amendments agreed in writing 18 Sep
  (70% ZEISS-brand mix floor, 42 months). Adobe Sign copy pending.
- **Quoted lines** (97 products with ZEISS order codes, flat per-piece prices for the term, coating
  = DuraVision Plus Platinum / HMC+ included): ClearView FSV 1.50/1.60/1.67/1.74 · SmartLife Digital ·
  SmartLife Progressive Individual 3 and Superb · Synchrony FSV 1.50 flat / 1.56 aspheric / 1.60 ·
  Synchrony Prog Ultra HDV and Performance HD (+ Short). BluePro +$2.50. PFX / Polarised ≈ base +$25.
  Source: `cec-second-brain/raw-sources/Finance/zeiss-proposal/ZEISS-Full-treatments-Concord-V2-2026-08-27.xlsx`
  (one sheet "Products with Codes": Our Code · Our Code 2 · Product · Concord price).
- **Unquoted lines** bill off the book at a discount level: **L50 for six months from 11 Sep 2026
  (to ~11 Mar 2027), then L25.** The book on disk is L20: `L50 = L20 × 0.625`, `L25 = L20 × 0.9375`.
  🟧 L25 step is verbal (Trisha), not in writing — treat as an assumption, keep it in config.
  Unquoted = Synchrony grind SV, Synchrony polarised, Synchrony bifocal D28, Synchrony Work & Office HD
  (Desk Pair), ClearView surfaced SV, every non-Platinum coating on quoted products.
- **Practice routing (staff sheet, 15 Sep, "plan D")**: everything to ZEISS except **MiyoSmart (Hoya)**
  and **1.76 (Tokai)**. Grind, polarised, tints, bifocals → **Synchrony**; 1.67 grind → ClearView is
  *optional* (plan D+). **Rule 2: a minus script with cyl −2.25 to −4.00 goes on ClearView 1.67 STOCK,
  not grind.** Plus scripts and anything else over −2.00 cyl still grind.
- **Retail (staff sheet 15 Sep, per pair, GST incl.)**: Signature (Individual 3) 750/790/850/950/1050 ·
  Everyday (Superb) 560/610/660/760/860 · Essential (Ultra HDV) 420/490/540/640/760 · Screen Relief
  (SmartLife Digital) 350/400/450/560/740 · Desk Pair (Synchrony Work & Office HD) 420/480/520/630/— ·
  SV Premium (ClearView Platinum) 200/—/250/300/460 · SV Standard (Synchrony) 140/—/220/250/460 ·
  GRIND +$100 · TINT (grind only) +$50 · Blue +$50 · Transitions +$130. Index order 1.50/1.53/1.60/1.67/1.74.
  (The earlier §3 list with blue +$80 is superseded by §3b — use the staff sheet.)
- Price books on disk (text, `pdftotext -layout`, one `--- PAGE n ---` marker per page):
  `cec-second-brain/price-lists/supplier-price-books/zeiss-L20-2026-07.txt` (977 lines, 31 pages) and
  `synchrony-L20-2026-07.txt`. Page map:

  | ZEISS pages | Section |
  |---|---|
  | 3 | Finished SV (stock) — per-diameter bands, cyl per band, `*` = max minus combined |
  | 4–8 | SV made-to-order: ClearMind Individual 3, ClearMind, SmartLife Individual 3, SmartLife, ClearView — columns Gold / Platinum / Chrome / Sun / DSHC |
  | 9–12 | Digital (ClearMind, SmartLife) |
  | 13 | Office |
  | 14–21 | Progressives (ClearMind ×4 tiers, SmartLife ×4 tiers) — adds `Add` column |
  | 22–23 | Kids (MyoCare — Mark declined, MiyoSmart stays) |
  | 24–25 | DriveSafe |
  | 26 | Specialty SV / bifocal / trifocal |
  | 27 | Sport |
  | 28–29 | Coatings, tints, fitting, services |

  | Synchrony pages | Section |
  |---|---|
  | 3 | FSV stock (incl. **FSV Clear HC tintable $4.25**, 1.56 aspheric $5.05, 1.60, 1.67) |
  | 4 | SV grind (HMC+ / HMC Blue / back-surface / HC columns) |
  | 5–7 | Progressives (Ultra HDC, Ultra HDV, Performance HD) |
  | 8 | Curves (wrap) · 9 Workplace (Work & Go / Office / Read / Access) · 10–11 coatings, tints, bifocals |

**Semantics of the book's Rx range** (matters for "power availability"): `-6.00* to +6.00 | -4.00`
means sphere −6.00…+6.00, cyl to −4.00, and the starred number is the **maximum minus combined power**
(sph+cyl in minus-cyl form). The star only ever sits on the minus end. The FSV page is per diameter:
`65mm +6.00 to +2.00 | -2.00`, `70mm +4.00 to -6.00* | -2.00`, `75mm 0.00 to -6.00* | -3.00` etc.

---

## 1. What "full integration" means — the target behaviour

1. Type an Rx (sph / cyl / **add**) and a frame size, choose what kind of lens the patient is buying
   (single vision, multifocal, screen, desk, bifocal, sunglasses), and see every ZEISS/Synchrony lens
   that can make it — **stock first**, then made-to-order — with the practice's own cost and sell price,
   and a plain verdict that follows the staff sheet's routing rules.
2. Every row says **who supplies it** (ZEISS / Synchrony / Hoya / Tokai), whether it is **still ordered**,
   and **which price basis** applies (deal price · L50 promo until 11 Mar 2027 · L25 book · Hoya T3).
3. Power availability is complete for the whole ZEISS range — SV, digital, office, progressive,
   bifocal — at the book's resolution (per material / index / diameter / coating, cyl and combined
   limits, add range), taken from ZEISS's own product guide; Synchrony ranges come from its price
   book, the only source that exists (see §6).
4. The library shows the practice's tier names (Signature / Everyday / Essential / Screen Relief /
   Desk Pair / SV Premium / SV Standard) beside the manufacturer's name, priced from one config.
5. Hoya stays loaded for reference and for MiyoSmart, greyed as "no longer ordered" everywhere else.

---

## 2. Phases (do in order; each is independently shippable and testable)

### Phase A — extend the CSV contract (`hub/lenses.py`, `lenses/README.md`)

Add optional columns (loose aliases as now; absent = today's behaviour):

| Column | Meaning |
|---|---|
| `supplier` | `ZEISS`, `Synchrony`, `Hoya`, `Tokai`. Defaults to `brand`. Drives chips, filters, and the "counts toward ZEISS mix" flag (ZEISS + Synchrony both count — Schedule 1(D) "ZEISS branded" is unconfirmed for Synchrony; keep the flag in config). |
| `price_basis` | `deal` / `L50` / `L25` / `book` / `T3`. Shown as a chip; `deal` rows never change with the promo date. |
| `price_promo` | Optional promo price. Engine uses it while `today < promo_until` (config `lens_pricing.json`: `{"promo_until": "2027-03-11"}`), else `price`. Both shown. |
| `add_min` / `add_max` | Numeric add range for multifocals/digital/office (from the book's `Add` column, e.g. `0.75 to 3.50`). `add_range` free text stays for display. |
| `orderable` | `yes` (default) / `no`. `no` rows are greyed, never "best", listed under "not ordered any more". Set by `lens_filter.json` rules rather than hand-editing 1,881 Hoya rows — see Phase E. |
| `code` | For ZEISS rows: the ZEISS order code from the V2 schedule (`375175`, `46919`…), so `check_job` can match what staff key into Optomate. Synchrony codes (`SMC70.`, `SA56HM`, `N6492`) likewise. |

Fix the `combined_max` semantics while here: apply only when `sph + cyl < 0` (minus meridian). Today's
`abs(sph+cyl)` happens to give the same answer for every real case but is not what the books mean —
document it and test it.

### Phase B — ZEISS + Synchrony converters (`lenses/convert_zeiss_l20.py`, `lenses/convert_synchrony_l20.py`)

Same shape as `convert_provision_t3.py`: text in, CSV out, deterministic, no network.

- Parse every product page listed above. One CSV row per **product × index × material variant
  (Clear / BluePro / PhotoFusionX / Polarised / AdaptiveSun) × coating column × diameter band**.
  Layout quirks seen in the text: the index (`1.50`, `1.60`…) appears as a leading cell on *one* of the
  rows of its block, not all — carry it forward; the FSV page puts the product name on the middle row
  of its diameter block; `-` means "not available in this coating"; `PFX` = PhotoFusionX.
- Category mapping: FSV/SV → `Single vision`; Digital → `Anti-fatigue` (new); Office / Workplace →
  `Occupational`; Progressives → `Progressive`; bifocal/trifocal → `Bifocal`; DriveSafe → its own
  `design` note under SV/Progressive; Kids and Sport → **skip** (not stocked; Mark declined MyoCare).
- `type`: FSV → `stock` with `blank_mm`; everything else → `grind` (made to order).
- Prices: book (L20) × level multiplier from `lens_pricing.json` (`{"L50": 0.625, "L25": 0.9375}`),
  written as `price` = L25 and `price_promo` = L50, `price_basis` = `L25`.
- **Deal overlay**: read the V2 xlsx (97 rows) and match each to catalogue rows by product family +
  index + BluePro/PFX/Polarised flag + coating (Platinum / HMC+) + diameter (FSV 65/70). Matched rows get
  `price` = Concord price, `price_promo` blank, `price_basis` = `deal`, `code` = Our Code. **Fail loudly
  if any of the 97 rows matches zero or more than one catalogue row** — print the reconciliation and
  exit non-zero. Do not hand-edit the CSV to make it pass; fix the matcher.
- Also emit `lenses/synchrony.csv` (Synchrony is a separate `supplier`; brand text on the sheet is
  "ZEISS Synchrony …").
- Keep `_template.csv` and README in step with the new columns.

### Phase C — engine (`hub/lenses.py`)

- `find_options(lenses, sph, cyl, add=None, min_blank=None, kind=None)`: drop the `sv_only` gate.
  `kind` filters category; `add` is required for Progressive / Anti-fatigue / Occupational rows and is
  checked against `add_min..add_max` (missing → amber warning, as with sphere today).
- Ranking (see decision D1): `orderable` first → **stock before grind when any stock lens fits** →
  index-appropriate → effective price. The verdict still states what the stock choice saves or costs
  against the cheapest grind, so the trade-off stays visible.
- Effective price = `price_promo` if today < `promo_until` else `price`. Surface both on the row.
- `check_job`: unchanged logic, but `code_known` looks at ZEISS/Synchrony codes; a chosen Hoya code on a
  non-MiyoSmart job gets a note "Hoya — no longer ordered, use ZEISS".
- Warnings to add: cyl beyond −4.00 on a grind → "cyls 4–6D surcharge $13.75/piece at ZEISS";
  tint requested on a non-hard-coat row → "tint needs a hard-coat (DSHC/HC) row"; ClearView 1.67/1.74
  have no hard-coat option at all.
- `attach_cec_price`: replace the Hoya-specific `sv_tiers`/`design_prices` with **tiers by name
  snippet + index** (`tiers` list: `{name, match, by_index}`), plus `addons`: `{"BluePro": 50,
  "PhotoFusionX|PFX|Transitions": 130, "grind": 100, "tint": 50}`. Keep backward compatibility so an old
  `cec_prices.json` still loads.
- `group_products`: key on supplier + name + index + type; fold coatings as now.

### Phase D — UI (`static/app.js`, `static/*.css`)

- Finder form: add **Add** and **Lens kind** (select: Single vision · Multifocal · Screen · Desk ·
  Bifocal). Page subtitle drops "one eye at a time" once `check_job` covers both eyes from the form
  (optional stretch; keep single-eye if time is short).
- Results row: supplier chip, price-basis chip (`deal` / `promo to 11 Mar 2027` / `L25 book` / `T3`),
  tier badge from retail config, greyed `not ordered` rows collapsed under a details block.
- Library: filters gain **Supplier**; `CAT_ORDER` adds `Anti-fatigue`; "Hoya cost" wording → "cost".
- Nothing patient-facing changes; this is a staff tool on the LAN.

### Phase E — configs

- `config/lens_pricing.json` (new, tracked): `{"promo_until": "2027-03-11", "levels": {"L50": 0.625,
  "L25": 0.9375}, "zeiss_mix_brands": ["ZEISS", "Synchrony"]}`.
- `config/cec_prices.json`: rewrite to the 15 Sep staff sheet (tiers above). Keep the `_readme`.
- `config/lens_filter.json` (this machine) + `lens_filter.example.json`: `orderable` rules —
  `{"not_ordered": {"Hoya": ["*"], "Hoya_except": ["MiyoSmart"]}}` or simpler: `"orderable_suppliers":
  ["ZEISS", "Synchrony", "Tokai"], "orderable_extra": ["MiyoSmart"]`. `preferred` reordered to ZEISS names
  (ClearView, Synchrony FSV, Superb, Individual 3, Ultra HDV, SmartLife Digital, Work & Office).
- Delete nothing from `hoya.csv`.

### Phase F — Optomate lens-jobs feed (separate approval; touches the other repo)

Merge `origin/claude/lensjobs-hub-checks` in CEC-Optomate-Agent (one commit, 457 lines, tests
included), set `optomate_agent.lens_jobs` in `config/integrations.json`, and the "Recent lens jobs"
panel starts checking real jobs against the ZEISS ranges. Job numbers, Rx and frame sizes only — no
patient details cross into the Hub. Needs Mark's separate OK because it adds a scheduled task.

---

## 3. Tests (add to `tests/test_lenses.py`, new `tests/test_convert_zeiss.py`)

- Converter fixtures: a 15-line excerpt of the FSV page, one SV made-to-order block, one progressive
  block, one Synchrony stock block → exact expected rows (index carry-forward, `-` cells, star →
  `combined_max`, add range, diameter bands).
- Deal overlay: a 5-row fake V2 sheet must match 5/5; a 6th unmatched row must raise.
- Engine: add-range pass/fail/missing; combined-power minus-only; stock-before-grind ranking with the
  verdict still quoting the saving; promo price before/after `promo_until`; `orderable=no` never best;
  cyl>4 grind warning; tier price + add-ons (BluePro, grind, PFX).
- Route: `/api/lenses/find?kind=Progressive&add=2.00` and the supplier filter on `/api/lenses`.
- Run `python -m pytest tests -q` — all 48 existing must still pass.

---

## 4. Ship checklist

1. Branch `session/zeiss-lens-finder` off `main` in **this** repo; commit by phase.
2. Generate `lenses/zeiss.csv` + `lenses/synchrony.csv`; commit them (they are price data, not PHI).
3. Refresh `lenses/README.md` (columns + converter usage) and `WHATS-BUILT.md` / changelog in the
   Optomate repo.
4. Push the branch. **Do not merge to `main` without Mark's OK** (same rule as the Optomate repo).
5. After merge: `RESTART-HUB.bat` (code changes need a restart; CSV/config changes do not).
6. Add a dated line to CEC-Optomate-Agent `NEXT-SESSION.md`: *"11 Mar 2027 — ZEISS L50 promo ends;
   `lens_pricing.json` flips to L25 automatically, confirm the first March invoice matches."*

---

## 5. Decisions for Mark (defaults chosen so work can start; each is one config line to flip)

| # | Decision | Default in this plan |
|---|---|---|
| D1 | Should the finder rank **stock before grind** even when the grind is cheaper (staff rule 2, ZEISS mix floor)? | **Yes** — stock first, grind shown with the $ difference |
| D2 | Leftover 1.67 grind: Synchrony (plan D) or ClearView (plan D+)? | Synchrony ranked first, ClearView shown as alternative |
| D3 | Hoya rows: hide, or grey "no longer ordered"? | Grey; MiyoSmart stays active |
| D4 | Is the **L25** post-promo level confirmed in writing? | Assumed; lives in `lens_pricing.json` |
| D5 | Does **Synchrony** count toward the 70% ZEISS-brand mix? | Assumed yes; config flag |
| D6 | Blue filter retail: +$50 (staff sheet 15 Sep) vs +$80 (superseded §3) | +$50 |

---

## 6. Power availability — the ZEISS product guide IS on Drive (found 19 Sep, after the first draft)

`G:\My Drive\CEC-Reference and Images\ZEISS Product Portfolio .pdf` (84 pp, ZEISS AU). Its Rx
Range pages are extracted to **`lenses/zeiss-portfolio-rx-ranges.txt`** in this repo — build from
that, not from the PDF. What it gives, per product family × index × material variant:

- Sphere range, cyl limit, max minus combined power (`*`) — **the same numbers as the price book**,
  so the book ranges are confirmed, not guessed. The converter should still take ranges from the
  *guide* text and use the book only for prices, and assert the two agree (fail loudly on a mismatch).
- **FSV per-diameter bands** (p29, p31) incl. variants the book folds together (1.60 BluePro and
  PhotoFusion X have their own bands). ⚠ p31 carries the ClearView FSV table **twice** (a second
  "With UVProtect" copy with two cells differing: 1.60 Clear 65 mm `+6.00` vs `+6.25`). Parse the
  second (UVProtect) table as current; note the discrepancy in the row's notes.
- **Add range per material** for Digital (p36/38), Progressive Individual 3 / Superb / Pure
  (p44–46), Office (p55–56), Bifocal D28 (p60: add 1.00–3.00, 1.50 only).
- **Minimum fitting height** per design (p47): Pure 18/16/14 mm (three fixed corridors);
  Plus / Superb / Individual 3 13 mm (FrameFit −1 to 6). Office and Digital on their pages.
  → new optional CSV column `min_fh_mm`; the finder warns when the frame's B/fitting height is
  below it (only when a fitting height is typed — never a hard miss).
- **Coating rules** (p66) to encode as warnings: 1.67 and 1.74 **must** have an AR coat (no
  DSHC/hard-coat rows at those indices — matches the book); BluePro not on tinted/polarised;
  Sun UV and Mirror only on tinted/polarised.
- Design/material codes (p35, p39, p59) for `code` on made-to-order rows where the V2 schedule
  has no ZEISS order number.
- Not in the guide: Synchrony — its price book is the only range source and there is no separate
  guide on Drive. The pages for the lines Concord uses are in **`lenses/synchrony-rx-ranges.txt`**
  (FSV stock, SV grind, Ultra HDV, Performance HD, Curves + D28 bifocal, Work & Office HD, services,
  coatings/tints). Same rectangle semantics; add ranges and cyl limits are in the same row. Build
  Synchrony rows from that file. MyoCare/Sport/Safety: skip.

**So the amber "confirm in VISUSTORE" note is NOT needed for ZEISS rows** whose range comes from the
guide. Synchrony rows carry a softer note ("range from the price book") — same numbers, but no
independent guide to cross-check against.

## 7. Out of scope (say so if asked)

- Ordering through VISUSTORE / Rx Connect from the Hub — no API is on offer to a practice this size.
- The ZEISS invoice parser for the inventory lane (separate item in the Optomate repo; first real
  ZEISS invoice needed).
- Patient price lists and the website — marketing lane (second-brain).
- MyoCare / kids portfolio — Mark declined on clinical grounds (4 Aug).

---

## 8. Findings from the build (19 Sep 2026)

1. **Schedule gap — ClearView FSV 1.60, 75 mm blank.** The negotiated schedule quotes the 1.60
   ClearView stock lens in 65 and 70 mm only (codes 343865/343867). The **75 mm** band is the
   one that carries the minus powers 0.00 to −6.00 with cyl to −3.00 — i.e. most 1.60 stock
   jobs. Unquoted, it bills at book: $17.72 now (L50), **$26.58 after 11 Mar 2027 vs the $22.50
   deal price.** 1.67 and 1.74 are quoted "70/75". Worth one line to Eoin.
2. **The ZEISS book and the ZEISS product guide disagree on 133 made-to-order rows** (of ~1,200
   compared), e.g. SmartLife SV 1.67 Clear: book −17.00 to +10.00, guide −12.00 to +8.00. The
   catalogue keeps the book range and shows the guide's on the row. Nothing to decide unless a
   job lands in the gap — then it's a VISUSTORE check.
3. **Deal-price rows never change with the promo date;** all other rows flip from L50 to L25 on
   11 Mar 2027 automatically. Diary entry is in NEXT-SESSION.md.
4. Live-machine step still to do at merge: copy the three `config/*.example.json` over the
   machine's own `lens_filter.json` / `cec_prices.json` (and create `lens_pricing.json`), then
   `RESTART-HUB.bat`. The examples are the ZEISS-era truth; the machine copies are still Hoya.

## 9. Range-reading audit (19 Sep, after Mark caught −6.50/−2.00 being called a grind)

Every place the code turns a printed range into a yes/no, and the reading applied:

| Where | Reading | Status |
|---|---|---|
| ZEISS + synchrony **stock** bands (`range_basis=combined`) | band = strongest-meridian power; diameter changes with it | **Changed 19 Sep.** Was sphere-in-band + combined cap, which put −6.50/−2.00 in a hole. Evidence: bands tile the axis in 0.25 steps. Interpretation — confirm in VISUSTORE (README) |
| ZEISS + synchrony **made-to-order** tables (`-8.00* 0 +8.00 (-4.00)`) | sphere range + cyl cap + combined cap | Unchanged; the two readings coincide here (minus meridian ≥ −8.00, plus meridian ≤ +8.00) |
| Hoya rows (`convert_provision_t3.py`) | sphere rectangle + cyl + optional combined, transcribed from Hoya's stair-step charts | Unchanged; `range_basis` defaults to `sphere` |
| Plus cyl typed | transposed to minus-cyl form first | Unchanged |
| Combined cap on plus scripts | never applied (it is a minus-side limit) | Fixed 19 Sep (was abs()) |
| Cyl sign | stored as magnitude; ZEISS prints `(-4.00)`, synchrony `5.00` | Unchanged |
| Guide vs book disagreements (133 ZEISS rows) | book range kept, guide's quoted on the row | Unchanged; visible on the card |
| Thickness table (`INDEX_BY_POWER`) | Mark's July table; a stock lens one step under it may lead (19 Sep) | Mark's rule to revisit if wanted |
| FSV 1.60 75 mm blank not on the schedule | bills at book | Question for Eoin |
