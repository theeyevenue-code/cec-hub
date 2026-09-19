# Lens catalogue files — how they work

The Lens Finder page reads every `*.csv` file in this folder, fresh on each
page load. One file per supplier: `zeiss.csv`, `synchrony.csv`, `hoya.csv`.
Files starting with an underscore (like `_template.csv`) are **ignored** —
they're just examples.

Since the ZEISS switch (Sep 2026) the practice orders from **ZEISS** and
**Synchrony** (ZEISS's value brand). `hoya.csv` stays loaded for reference and
for MiyoSmart, but every other Hoya lens is greyed "not ordered any more" and
is never the Finder's pick. That is a per-machine setting —
`config/lens_filter.json` → `orderable_suppliers` / `orderable_extra`.

## Rebuilding zeiss.csv and synchrony.csv

Don't hand-edit them. Run, from the Hub folder:

```
python lenses/convert_zeiss_books.py --check    # parse + reconcile, write nothing
python lenses/convert_zeiss_books.py            # write both files
```

It reads `lenses/source/`:

| File | What |
|---|---|
| `zeiss-L20-2026-07.txt`, `synchrony-L20-2026-07.txt` | the two July 2026 price books as text (`pdftotext -layout` of the PDFs on Drive, *CEC-Reference and Images*) |
| `ZEISS-Full-treatments-Concord-V2-2026-08-27.xlsx` | the negotiated schedule: ZEISS order code, product, flat per-piece price, fixed for the term |
| `zeiss-portfolio-rx-ranges.txt` | the Rx Range pages of the ZEISS Product Portfolio (the availability guide), used to cross-check the book's ranges |
| `synchrony-rx-ranges.txt` | the synchrony book pages for the lines on the staff sheet (no separate synchrony guide exists) |

The converter **fails and writes nothing** if any schedule line matches no
catalogue row or two lines land on one row — fix the matcher in the script,
never the CSV. Where the ZEISS book and the product guide disagree on a range,
the row keeps the book's range and gets a note quoting the guide (the Finder
shows it as an amber line). The book has a few slips of its own, e.g. Superb
1.50 BluePro printed as −10 to +10 where every neighbour says −7 to +6.

**When a new price book arrives:** `pdftotext -layout` it into `source/`,
point the two path constants at the new files, run `--check`, read the
report, then run for real. When the schedule changes, replace the xlsx.

## Prices: which one applies

Every row can carry two prices. `price` is the ongoing one; `price_promo` is
the launch level. `price_basis` says where `price` came from:

| basis | meaning |
|---|---|
| `deal` | the negotiated flat price from the schedule — fixed for the term, no promo, order code in `code` |
| `L25` | book price × 0.9375 (the level after the promo — Trisha's verbal confirmation, not yet in writing); `price_promo` = book × 0.625 (L50) |
| `T3` | Hoya ProVision T3 (reference only) |

The Finder uses `price_promo` while today is before `promo_until` in
`config/lens_pricing.json` (**11 Mar 2027**, six months from the 11 Sep 2026
go-live), then `price`. Nothing needs editing on the day.

## Columns

Header names are matched loosely (case and spacing don't matter, and common
aliases work — e.g. `diameter` for `blank_mm`, `cost` for `price`).

| Column | Required | What it means |
|---|---|---|
| `supplier` | no | who we order it from: `ZEISS`, `Synchrony`, `Hoya`, `Tokai`. Defaults to `brand` |
| `brand` | no | what it says on the lens, e.g. `ZEISS Synchrony` |
| `lens` (or `name`) | **yes** | e.g. `ClearView FSV 1.67`, `SmartLife Progressive Superb 1.50` |
| `code` | no | the supplier's order code — shown on results and searchable; the job check matches what staff key into Optomate against it |
| `category` | no | `Single vision`, `Progressive`, `Anti-fatigue`, `Occupational` or `Bifocal` — the "lens kind" the Finder searches within. Blank counts as Single vision |
| `index` | no | refractive index, e.g. `1.50`, `1.60`, `1.67` |
| `material` | no | `Clear`, `Clear BluePro`, `PhotoFusionX`, `Polarised`, … — clear lenses rank first; a tinted job only takes `Clear` |
| `form` | no | `Spherical`, `Aspheric` or `Freeform` |
| `type` | no | `stock` or `grind`. If blank: has a blank size → stock, no blank size → grind |
| `blank_mm` | no | blank diameter, e.g. `70` — a list like `65/70/75` is fine (the biggest is used for fit checks; leave blank for grind) |
| `sph_min` / `sph_max` | no | sphere range, signed. Leave blank and the Finder flags "range not in file" instead of guessing |
| `sph_range` | *alt* | *instead of the two above*: one cell like `+4.00 to -4.00` |
| `cyl_max` | no | biggest cyl it can do, e.g. `-2.00` (sign doesn't matter) |
| `combined_max` | no | the supplier's **maximum minus combined power** (the starred number in the books): sphere + cyl in minus-cyl form. Only ever limits a minus prescription, and only on `sphere`-basis rows |
| `range_basis` | no | `sphere` (default) or `combined` — see "How ranges are read" |
| `add_min` / `add_max` | no | add range for multifocal-type lenses, e.g. `0.75` / `3.50` — the Finder checks the typed add against it |
| `add_range` | no | free text shown on the card (`Add 0.75 to 3.50`); parsed into the two above when they are blank |
| `min_fh_mm` | no | minimum fitting height for the design (Superb / Individual 3 = 13, Pure = 14) — a warning when the typed fitting height is under it |
| `price` | no | **cost per lens** ex GST that applies after any promo |
| `price_promo` | no | the launch-level cost, used while before `promo_until` |
| `price_basis` | no | `deal` / `L25` / `L50` / `T3` — shown as a chip |
| `orderable` | no | `no` retires the row regardless of supplier (greyed, never the pick) |
| `coating` | no | e.g. `DuraVision Platinum UV`, `HMC+`, `HC hard coat`. The practice's standard coatings (`preferred_coatings` in lens_pricing.json: Platinum, HMC+) rank first; a tinted job wants a hard coat |
| `notes` | no | anything else worth seeing on the results card. `RANGE DIFFERS — …` notes are shown amber |

Numbers shrug off `$`, `mm` and `+` signs, so `$18.50`, `65mm`, `+4.00`
are all fine.

## How ranges are read — two readings, one flag

The supplier books print a power range, a cyl limit and a starred number
("*maximum combined power"). They never say whether the range is the
**sphere** or the **whole lens**. The catalogue carries the reading per row
in `range_basis`:

| `range_basis` | Used for | A row fits when |
|---|---|---|
| `sphere` (default; Hoya, every made-to-order table) | sphere range + separate cyl cap + starred combined cap | sphere inside the range, \|cyl\| ≤ cyl_max, and sphere+cyl not beyond the combined cap |
| `combined` (ZEISS and synchrony **stock** bands, set by the converter) | the band is a range of the lens's **strongest-meridian power** — sphere+cyl for a minus lens, the sphere for a plus lens; the diameter changes as that power grows | that power inside the band and \|cyl\| ≤ the band's cyl_max |

Why the stock bands are read as `combined`: ClearView 1.60/1.67/1.74 bands
tile one axis in 0.25 steps with no gaps (−8.00 to −6.25 on 70 mm, −6.00 to
0.00 on 75 mm, +0.25 to +4.00 on 70 mm …). Under a sphere-only reading every
cyl script near a band edge would fall into a hole ZEISS does not have.
Worked examples, ClearView 1.74 (75 mm −3.00 to −8.00, 70 mm −8.25 to −12.00,
cyl to −2.00): −6.50/−2.00 is a −8.50 lens → **stock, 70 mm blank**;
−10.00/−2.00 (−12.00) → stock, 70 mm; −10.25/−2.00 → grind; −12.00 plain →
stock. **So "1.74 stock to −12.00" means −12.00 in the strongest meridian:
−12.00 sphere with no cyl, or −10.00 with −2.00 cyl.**

⚠ This is an interpretation, not a printed rule. The one-line check that
settles it: key −6.50/−2.00 in ClearView FSV 174 into VISUSTORE. If it is
refused, change the converter to write `sphere` for stock rows and rebuild.

## How the Finder decides (so the data means what you think)

- Cyl is checked in **minus-cyl form**. A plus cyl is transposed first.
- A row fits when its power is inside the range (see "How ranges are
  read"), |cyl| ≤ `cyl_max`, `blank_mm` covers the frame's blank (when
  typed), and for multifocal kinds the add is inside `add_min`–`add_max`.
- Order of the results: still-ordered lenses → the **right thickness** for
  the power (`INDEX_BY_POWER` in `hub/lenses.py`: ≤2.00 → 1.50, ≤4.00 →
  1.60, ≤6.00 → 1.67, above → 1.74; a thinner-index lens that only
  technically fits is flagged "thick", not led with) → **a stock lens before
  a grind** (practice rule, Sep 2026; `prefer_stock` in lens_pricing.json)
  → the standard coating → clear before BluePro / photochromic / polarised
  → price → lenses on the staff sheet win ties.
- The verdict line says **STOCK covers this** or **GRIND**, names the lens
  and its price basis, and quotes what the other route would cost. Multifocal
  kinds say "made to order" instead.
- Missing limits surface as amber warnings, never as guesses.

## Selling prices and tiers

`config/cec_prices.json` (per machine; the shipped example is the 15 Sep 2026
staff sheet) maps name snippets + index to the practice's per-pair price and
tier name (Signature / Everyday / Essential / Screen Relief / Desk Pair /
SV Premium / SV Standard), with add-ons for BluePro (+$50), photochromic
(+$130) and the grind surcharge on single-vision grind rows (+$100). The
library and the finder show it beside the cost.
