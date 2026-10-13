# Hub readability pass: decisions on Codex's review (13 Oct 2026)

Source: `cec-second-brain` branch `hub-design-review-2026-10-13`, `codex-reply.md` (a7ebc9b7).
Tests applied to every item: Mark's readability standard and 3-second skim rule (CLAUDE.md), the taste
rules (`page-factory/MARK-TASTE-RULES.md`: L1 spacing inside blocks, L2 no information lost, no emoji,
answer first), and "no risk to the Medicare PIN flow, the check-in staff-code guard or recall send gating".

**Count: 20 ACCEPT · 6 MODIFY · 7 REJECT** (33 lines below; Codex's 12 items plus his palette notes,
split where one item bundles separate calls).

| # | Codex item | Call | Why |
|---|---|---|---|
| 1 | Style `.lens-table` (padding, hairlines, 17px, left headers 14px) | ACCEPT | Recalls, Payments and invoice detail render unstyled today; pure CSS. |
| 1b | Numbers right-aligned, tabular figures in those tables | ACCEPT | Mark's rule: numbers right, tabular, money to the cent. Done with a `num` class on the cells. |
| 2 | Phone To-do / Credits rows as a 3-column grid, card padding 16px 12px | ACCEPT | Fixes word-by-word wrapping at 390px; CSS only. |
| 3 | Controls 48px high, 17px/600, radius 6px | ACCEPT | Mark's floor (17px) and a proper tap target; replaces 58-62px blocks. |
| 3b | `.btn-back` as a plain underlined link | ACCEPT | A boxed "Home" button was the heaviest thing on most pages. Kept 44px tall for touch. |
| 4 | White surfaces, hairlines, no shadows, 6px corners, dark focus ring | ACCEPT | "Hierarchy by size, weight and space, not boxes and fills. Black on white." |
| 4b | Topbar: white, hide CEC badge and "Concord Eyecare", keep "Staff Hub" + picker | ACCEPT | Less chrome; the picker stays visible. Its label hides only on phones (still read by screen readers). |
| 5 | Six-step type scale (14/17/18/22/28/32), one system font | MODIFY | Scale adopted as written, but as ONE dated override section at the end of style.css instead of editing ~30 scattered rules: lower regression risk, one block to revert, and it respects the test that guards the Medicare CSS. |
| 5b | Remove the companion panel's small sizes too | REJECT (this pass) | `#/panel` is a deliberately compact corner window (its own 11-15px scale), not reviewed by Codex and not in the screenshots. Flagged for Mark. |
| 6 | Delete "What is this?" cards, backstory, reassurances | MODIFY | Folded under a small "How it works", never deleted (taste rule L2: shorten the visible layer, lose nothing). |
| 6b | Keep sending state, errors, identity cautions, payment discrepancies, statement confirmation visible | ACCEPT | All stay on screen. |
| 6c | Remove decorative emoji and inline accent bars | ACCEPT | Mark: no emoji. Every emoji in app.js templates replaced by a word or nothing; inline `border-left` bars gone. |
| 6d | Completed tasks under "Done (n)" fold; invoice Mark-pile under "For Mark (n)" fold | ACCEPT | Detail behind a click. |
| 7 | Home: one "Needs attention" table (Area / What / Count / Open), tools as a plain list | ACCEPT | Mark called the tile grid "a bunch of boxes"; a queue reads in 3 seconds. Every link, the edit-layout mode and external-tab cues kept. |
| 7b | Delete the attention items' secondary lines ("added by", "how") | MODIFY | Keep the `how` line: it carries the invoice numbers and amounts that make the row actionable. |
| 8 | One short instruction line under each title | ACCEPT | Codex's wording used, lightly cut. |
| 8b | Check-in: move iPad state / Today above the search | REJECT | Reorders a flow staff use with a patient waiting; the guard and send flow stay exactly where they are. Only the line under the title changes. |
| 8c | Lens Finder: move the result above the form | REJECT | Changes how the page works (six rounds with Mark set this layout). CSS-only tidy instead. |
| 8d | Medicare: keep as is | ACCEPT | Codex's own "strongest keep". JS untouched; it only inherits the new palette and controls. |
| 9 | Invoices: frame invoices as a table Supplier / Invoice / Amount | ACCEPT | Parsed from the existing line; amounts keep the "~" (approximate). Unparsed lines show in full. |
| 9b | Letters as a File / Status table | MODIFY | There is no status field; a one-column table is a list. Shown as hairline rows with the complete filename, no monospace. |
| 9c | Credits as a 5-column table | MODIFY | Same columns as an aligned grid inside the existing list rows (amount right-aligned, author, actions), so the tick/delete handlers keep their markup. |
| 10 | Phone: Check-in and Payments rows stacked; wide tables scroll inside their box with a "Swipe for more" cue | ACCEPT | Stops the page itself scrolling sideways at 390px. CSS plus wrapper divs only. |
| 10b | `data-label`s on every table cell | MODIFY | Added only where a stacked cell would be ambiguous (Payments); Check-in cells explain themselves (time, name, form, status chip, button). |
| 11 | Shorter button labels ("Approve list", "Approve", "Cancel", "Add credit", "Print list", "Create list", "Open latest", "Print guide") | ACCEPT | Verbs, one or two words. Same buttons, same handlers. |
| 11b | Guide list as hairline title rows, owner/date chips off the list | ACCEPT | Owner and date stay on the guide page itself. |
| 11c | Delete the guide summaries on the list | REJECT | The summary is how staff choose the guide; one line, muted. |
| 11d | Keep step uncertainty visible ("Confirm: ...") | ACCEPT | Kept as an amber line in each step; text comes from the guide file unchanged. |
| 12 | Persistent nav bar (Home / Check-in / To-do / Guides + More) | REJECT (this pass) | Structural, new chrome on every page, and not part of Mark's ask; the plain "Home" link does the job for now. Offered to Mark as a follow-up. |
| P1 | Neutral palette; red = needs action, green = confirmed, grey = pending | ACCEPT | Matches Mark's own rule (green earned, grey unproven). |
| P2 | Credit found stays neutral until confirmed on the statement | ACCEPT | Green only after it is ticked off; "Arrived - check statement" is bold neutral. |
| P3 | Remove Lens Finder colours (supplier hues, stock/grind green-amber, violet price note) | REJECT | Mark's standing rule (20 Sep 2026): "each supplier its own hue, a price note violet... Never strip colour entirely: monochrome made it harder to tell what's what." Kept; only sub-14px text raised to 14px. |
| P4 | Remove check-in status colours (blue on iPad, amber ready, green saved) | REJECT | Set in the 9 Oct check-in review and used at the desk; colour has a job there. |

## What stays exactly as it was (functional)
- Every route, handler, fetch and confirm dialog. No Python changed.
- Medicare: `renderMedicare` and all `mc*` functions untouched (PIN dialog, Resubmitted confirm, Written-off confirm).
- Check-in: staff-code unlock, send / replace / take-off / resume flow, 10-second refresh, test-mode banner.
- Recalls: send area shows the disabled button unless `live_enabled`; the live button, its red colour, its
  `confirm()` wording and the deselect save are unchanged.
