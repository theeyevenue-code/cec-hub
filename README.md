# CEC Hub

The staff home screen for Concord Eyecare. One local web app with big tiles:
How-To Guides (SOPs) · Referral Letters · Google Reviews · Stock Orders · Lens Finder.

Built for everyone at the front desk — big text, big buttons, plain words, nothing scary.

## What it is (and isn't)

- A **local Flask app** on `http://localhost:5680`. No internet accounts, no logins.
- **No patient data, ever.** Optomate stays the system of record — the Hub is the procedures-and-buttons layer on top.
- The only thing the Hub ever *writes* outside its own folder is renaming a stock
  proposal CSV to `*.approved.csv` when someone presses Approve (plus its own `hub.log`
  and the lens price CSVs saved into its own `lenses\` folder).
- The "Who's using this?" picker in the corner is optional — it just puts a name on
  the Hub's own log lines (e.g. who approved a stock order). It's a cookie, not an account.

## Setting it up on a machine

1. Install Python from python.org (tick "Add to PATH") if it isn't already there.
2. Copy this whole `CEC-Hub` folder onto the machine (anywhere is fine).
   *Deployment is folder-copy for now; a git repo comes later.*
3. Double-click **INSTALL.bat** once.
4. Double-click **START.bat** — the browser opens the Hub by itself.
   Keep the black window open while the Hub is in use.
5. Optional: right-click START.bat → Send to → Desktop (create shortcut), and
   rename the shortcut "CEC Hub".

### Keeping it up (the server machine)

On the machine that everyone else browses to, run **INSTALL-WATCHDOG.bat** once.
It adds a Scheduled Task ("CEC Hub Watchdog") that checks every 5 minutes and
restarts the Hub if it has stopped — no admin needed. This matters because the
Startup shortcut only fires at logon, so without the watchdog a Hub that dies
mid-day stays dead until a human notices (it happened: down 3 hours). Only real
restarts get written to `watchdog.log`, so any line in there means it caught one.

To check it: `schtasks /Query /TN "CEC Hub Watchdog"`, or stop the Hub and watch
it come back within 5 minutes.

**Never start the Hub from inside a Claude session.** A process launched from a
Claude tool call is a child of the Claude desktop app, so when Claude restarts
it takes the Hub down with it — silently, no error, the log just stops. That is
exactly what happened on 2026-07-17: the Hub, the referral tool and SightTrack
all died when Claude restarted at 14:23 and stayed down ~3 hours. To start it
safely from a Claude session, go through the task (it's idempotent):

```
Start-ScheduledTask -TaskName "CEC Hub Watchdog"
```

The Startup-folder shortcut is fine (Explorer owns it), it just only fires at
logon — which is why the watchdog is what actually keeps the Hub up.

### Connecting the other systems on this machine

Open `config\integrations.json` in Notepad and check the paths:

| Section | Points at | Used by |
|---|---|---|
| `review_bot` | the review bot's `sent_log.json`, `review_bot.log`, `config.json` | Reviews page |
| `optomate_agent` | the agent's `inventory\proposals\`, `logs\lens-jobs.jsonl` | Stock page + Lens Finder's "Recent lens jobs" |
| `scorecard_drop` | the folder Karen saves the Friday scorecard photo into | referenced by the scorecard guide |

If a path doesn't exist on this machine, the matching page simply says
"not connected" — nothing breaks. The defaults assume the Optomate agent at
`C:\CEC\CEC-Optomate-Agent` (practice server) and the review bot at its
current location on Mark's machine; adjust per machine.

Other config files: `config\tiles.json` (the home-screen tiles) and
`config\staff.json` (names in the picker). All three are plain JSON read fresh
on every page load — edit, save, refresh the browser. No restart needed.

## Adding or editing a How-To Guide (SOP)

Drop a markdown file in `sops\` following the conventions in **`sops\README.md`**
(frontmatter, numbered steps, `> IF condition: action` decision boxes,
`[MARK: ...]` for anything unconfirmed). The Hub picks it up on the next page
load. Any Claude session can do this — point it at `sops\README.md`.

## The Lens Finder

Type an Rx (one eye at a time, optional cyl and minimum blank size) and the
Hub lists every lens in the catalogue that can make the job, cheapest first —
including when a dearer-index **stock** lens beats a 1.50 **grind** on price.
The catalogue is plain CSV files in `lenses\` (one per supplier price guide),
uploaded from the page or dropped into the folder. The column layout — and
how to turn a supplier's PDF guide into a CSV with a Claude session — is in
**`lenses\README.md`**. No patient data: powers only, nothing is stored.

On machines that can see the Optomate agent, the page also shows **Recent
lens jobs**: spectacle orders the agent extracts to `logs\lens-jobs.jsonl`
(order numbers and Rx/frame numbers only), each re-checked against the
price files — in range? blank big enough? marked Grind when a stock lens
would do? The same check is callable at `POST /api/lenses/check` for any
future helper. It's a second pair of eyes only — it never changes an order.

## The Stock approve button — what it actually does

Pressing "Approved — mark for entry" renames the proposal file from
`name.csv` to `name.approved.csv` in the agent's proposals folder and logs who
pressed it. **That's all.** The actual entry into Optomate stays a
supervised/CLI step run by Mark/Claude. Nothing is ordered automatically.

## For developers / future Claude sessions

```
app.py                  Flask app (port 5680) — routes only, no business logic
hub\sop_parser.py       SOP markdown -> structured blocks (the renderer contract)
hub\integrations.py     read-only views of the other systems, all graceful
hub\lenses.py           lens catalogue CSVs -> best-option finder (stock vs grind)
config\*.json           tiles, integration paths, staff names
sops\                   the guides + README.md (authoring contract) + images\
lenses\                 lens price CSVs + README.md (column contract) + _template.csv
static\                 index.html / style.css / app.js — no build step
tests\                  mocked pytest suite (no network, no real integration paths)
```

Run the tests from the `CEC-Hub` folder:

```
python -m pytest tests -q
```

Environment overrides (used by the tests, handy for odd setups):
`CEC_HUB_INTEGRATIONS` (path to an integrations.json),
`CEC_HUB_SOPS_DIR` (path to a sops folder) and
`CEC_HUB_LENSES_DIR` (path to a lens CSV folder).

Style rules baked into `static\style.css`: minimum 18px body text (19px used),
1.6 line height, CEC greens (`#438F73` accents, `#0f2b21` headings, `#e8f2ee`
background, `#2e6a52` for button/link fills so white text passes WCAG AA),
Urbanist/Work Sans with system fallbacks, touch targets ≥48px.

## If something goes wrong

- Page says "not connected" — normal on machines that don't run that system.
- Browser can't reach the Hub — the black window probably got closed.
  Double-click START.bat again.
- Anything else: close the black window, START.bat again. Still stuck? Ask Mark.
