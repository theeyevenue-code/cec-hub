# How to add an SOP to CEC Hub

*This file is the contract between the SOP files and the Hub's renderer. Any Claude session (or human) adding a guide follows this — nothing else is needed; the Hub picks up new files automatically on the next page load.*

## Where and what

- One markdown file per guide, in this folder: `sops\<kebab-case-name>.md` (e.g. `ordering-hylo-forte.md`).
- Pictures go in `sops\images\` and are referenced by filename (see below).
- `README.md` (this file) is ignored by the app.
- **Never put patient names or any patient data in an SOP.**

## File format

```markdown
---
category: Ordering
updated: 2026-07-09
owner: Angie
---

# Ordering HYLO-Forte

One or two plain sentences saying when to use this guide.

## Steps

1. First thing to do. Keep each step one action.
2. Second thing. **Bold** the part that matters most.

> IF the order total is over $108: check the free-of-charge card folder BEFORE ordering.

3. Carry on with the next step.

[Open the supplier's ordering page](https://example.com.au)

![What the order book looks like](images/order-book.jpg)
```

## The rules, one by one

| You write | Staff see |
|---|---|
| `---` frontmatter with `category`, `updated`, `owner` | Category sidebar grouping + "Updated" / "Owner" chips. All three optional; category defaults to General. |
| `# Title` (exactly one, first) | The big page title. |
| `## Section` | A section heading between steps. |
| `1.` `2.` `3.` numbered lines | Big tappable checklist steps. Your numbers are shown as written — number them yourself, in order. A step can wrap onto following lines until a blank line. |
| `> IF <condition>: <what to do>` | A highlighted yellow DECISION box: "Does this apply? YES → do this / NO → carry on." One decision per line. Must start with `IF` and contain a colon. |
| `> plain text` (no IF) | A blue "good to know" note box. |
| `[Button label](https://...)` | A big green button that opens the link in a new tab. Only http/https links become buttons; anything else stays plain text. |
| `![caption](images/photo.jpg)` | The picture, full width, from `sops\images\`. Local images only — web image URLs are not fetched. |
| `**bold**`, `` `code` `` | Bold text; boxed text for exact things to type or folder paths. |
| `[MARK: question]` | An amber "Still to confirm with Mark: …" chip, and the guide gets flagged in the guide list. Use it anywhere you are not 100% sure of a detail — an honest gap beats a made-up fact. |
| `- item` bullet lines | A bulleted list (for definitions, not actions — actions should be numbered steps). |

## Style rules for the words themselves

- Write for someone in a hurry who is not technical. Short sentences. One action per step.
- Plain words: "the black window", not "the terminal". No jargon, no acronyms without a translation.
- Nothing scary: if something can go wrong, say what to do about it calmly ("Close it and try again. Still stuck? Ask Mark.").
- Australian English (colour, organise, cheque).
- Exact paths, exact folder names, exact button labels — in `` `code` `` marks.

## Checklist when adding or editing a guide

1. File saved in `sops\` with a kebab-case name.
2. Frontmatter present; `updated:` set to today.
3. Exactly one `# Title`.
4. Every uncertainty marked `[MARK: ...]` rather than guessed.
5. Open the Hub → How-To Guides and read it once as if you were Angie.
