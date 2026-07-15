# Lens catalogue files — how they work

The Lens Finder page reads every `*.csv` file in this folder, fresh on each
page load. One file per supplier price guide is the tidy way (`hoya.csv`,
`shamir.csv`, …). Files starting with an underscore (like `_template.csv`)
are **ignored** — they're just examples.

Two ways to get a file in here:

1. **Upload it** from the Lens Finder page (the "Add or update a price list"
   card). Uploading a file with the same name replaces the old one — that's
   how you load new pricing.
2. Drop the CSV straight into this folder and refresh the browser.

## Turning a supplier's PDF guide into a CSV

For the ProVision T3 (Hoya) pricelist there's already a converter —
`convert_provision_t3.py` in this folder (usage in its docstring). Run it
on the new quarter's PDF and the single-vision pricing lands in `hoya.csv`.

For anything else, give the PDF to a Claude session and point it at this
README. The job is: one row per orderable lens variant
(each index, each blank size, stock vs grind), with the columns below.
Anything the guide doesn't state, leave blank — the Finder flags unknowns
rather than guessing.

## Columns

Header names are matched loosely (case and spacing don't matter, and common
aliases work — e.g. `diameter` for `blank_mm`, `cost` for `price`).

| Column | Required | What it means |
|---|---|---|
| `brand` | no | e.g. `Hoya` |
| `lens` (or `name`) | **yes** | e.g. `Nulux 1.50`, `Stellify 1.55` |
| `code` | no | the supplier's order code / lens type, e.g. `S-NULUX`, `HLSY-1.50-70` — shown on results and searchable |
| `category` | no | `Single vision`, `Progressive`, `Bifocal` or `Occupational` — the top browse filter. **Only `Single vision` rows go through the "best lens for a job" cost engine**; the rest are browse/price/search reference. Blank counts as Single vision |
| `index` | no | refractive index, e.g. `1.50`, `1.55`, `1.60`, `1.67` |
| `form` | no | `Spherical`, `Aspheric` or `Freeform` — the spheric/aspheric browse filter (Hilux = Spherical, Nulux = Aspheric, progressives = Freeform) |
| `type` | no | `stock` or `grind`. If blank: has a blank size → stock, no blank size → grind |
| `design` | no | free text — shown, not matched on (optional; `category`/`form` replaced it) |
| `blank_mm` | no | blank diameter, e.g. `65` — a supplier list like `65/70/75` is fine (the biggest is used for fit checks; leave blank for grind — made to size) |
| `sph_min` / `sph_max` | no | sphere range, signed: `-4.00` and `+4.00`. Price lists often don't state it — leave blank and the Finder flags "range not in file" instead of guessing |
| `sph_range` | *alt* | *instead of the two above*: one cell like `+4.00 to -4.00` |
| `cyl_max` | no | biggest cyl it can do, e.g. `-2.00` (sign doesn't matter) |
| `combined_max` | no | limit on sphere + cyl combined, if the guide states one |
| `add_range` | no | free text for multifocals, e.g. `Add +0.75 to +3.50` or the boost values — shown & searchable, not matched on |
| `price` | no | **cost per lens** — keep every file on the same basis (per lens, ex GST) or the "best value" comparison lies |
| `coating` | no | e.g. `Hi-Vision LongLife` |
| `notes` | no | anything else worth seeing on the results card |

Numbers shrug off `$`, `mm` and `+` signs, so `$18.50`, `65mm`, `+4.00`
are all fine.

## How matching works (so the data means what you think)

- Cyl is checked in **minus-cyl form**. If someone types a plus cyl on the
  Finder page it's transposed automatically before checking.
- A lens fits when: sphere is inside `sph_min`–`sph_max`, |cyl| ≤ `cyl_max`,
  sphere+cyl is inside `combined_max` (when given), and `blank_mm` covers
  the minimum blank size typed in (when given).
- If the sphere range, `cyl_max` or `blank_mm` is missing from a row, the
  lens still shows as an option but with an amber "check the guide"
  warning — the Finder never silently assumes.
- Options are sorted cheapest first; the cheapest priced one is flagged
  **Best value**, and the verdict line says what a stock lens saves against
  the cheapest grind (or the other way round).

The Finder is built around **single-vision** ranges for now. Progressives
can live in these files too (use `design`), but adds aren't checked yet.
