# CEC Hub - session rules (read me first)

The staff home screen for Concord Eyecare: a local Flask app, live on `http://localhost:5680` on the practice
server, run from `C:\Users\ConcordeyecareServer\Claude\cec-hub`. Server Desk (the practice-server agent) owns it.

**This repo has no rulebook of its own. The rules are the practice agent's:** read
`C:\CEC\CEC-Optomate-Agent\CLAUDE.md` (hard guardrails, session ritual, build conventions, readability standard)
and follow it here too. Where the two ever disagree, that file wins. The standing orders below are the same
block every repo carries.

<!-- standing-orders:v2 2026-11-15 | canonical copy: CEC-Optomate-Agent docs/standing-orders.md | paste verbatim, never edit a copy -->
## Standing orders (every repo, both PCs - Mark, 15 Nov 2026)

Chain of command: **Mark -> two generals** (Front Desk = home PC, Server Desk = practice server) **-> no-AI sergeants** (runner, daily check, repair jobs: they watch jobs, they never set priorities or command builders) **-> lieutenants** (jobs, apps, builders, subagents). Everyone has ONE boss. Mark may speak to any desk: an order he gives at a desk is logged there in his words the same day, and its general reads it at the next check-in. A general writes only in the repos it owns (Server: agent, Hub, referral tool; Home: brain, website, weekly report) and reads the other's through a read-only clone.

1. **Read the brief first.** At session start: reds, what is live, open work, Mark's decisions not yet filed (Server: `docs/reports/server-ledger.md`; Home: `STATE.md`).
2. **Quote the order.** A plan or builder brief opens with Mark's words, their date and the decision id. Before a write, send or merge, re-check that the decision still stands. A report without them goes back.
3. **Check the map before you build or flip a switch.** Server: `python -m system.map impact <job|file|gate>` in `C:\CEC\CEC-Optomate-Agent`. Home: `practice-map.part.json`. Paste Shared with / Reaches / Leaves the network / Rules tripped into the plan and into any "merge it" or "turn on" ask. A shared resource has one writer in the map; a change at a JOIN is agreed with its owner first.
4. **Extend, never duplicate.** One tool, job, sender and alert path per job type. Server: problems reach Mark through the daily check, never a new email. Home: problems reach Mark through the Front Desk check-in; a job's own deliverable (the Monday brief, the weekly report, his phone list) is not an alert.
5. **Register every effect in the same commit.** Server: `system/jobs.json` + `system/resources.json`. Home: `AUTOMATIONS.md` + the map part file.
6. **Report to your one boss before you stop.** Builders and subagents report to the desk that started them, never to Mark. Mark's decisions are written once, where the work lives, in his words with the date. What waits on Mark goes on ONE list: the brain register (`runway.json`). Server receipts, answers, flags, decisions and open items reach it through the server ledger, each with a stable id; Front acks them on `desks/_sync/FRONT-TO-SERVER.md`.
7. **Stop at Mark's lines.** Server: merges, turning on a live write or patient send, money and anything staff notice need his words, said in that session; a switch he has already turned on runs as approved until he says otherwise. Home: red actions need the S24 preflight and his go; a tick on his phone list is his word. Privacy guardrails beat everything here.
<!-- /standing-orders -->

## Mark's preferences: read them at session start (one source, never copied here)
Mark, 16 Nov 2026: "my preferences and my stuff that I like from the front desk might still somehow, if it's
relevant ... be linked to the referral generator and sidetrack and everything". The Hub is one node of the
practice, not an entity in itself. Claude Code keeps memory per folder, so a session opened here does NOT see what
Mark taught other sessions unless it reads it. Before any page, staff-facing or reply work:
1. Server Desk's index `C:/Users/ConcordeyecareServer/.claude/projects/C--CEC-CEC-Optomate-Agent/memory/MEMORY.md`;
   open every file it links that touches the work (they sit beside it).
2. Front Desk's index `C:/CEC/brain-readonly/shared-memory/MEMORY.md` (read-only clone: `git -C C:/CEC/brain-readonly
   pull --ff-only` first); open the ones that apply here (design, wording, privacy).
3. Mark's decisions: "Decided here" in `C:/CEC/CEC-Optomate-Agent/docs/reports/server-ledger.md`, and the agent
   `logs/CHANGELOG.md` decision / LIVE / merge headings that name the Hub.

Likely to apply here: readability and the 3-second skim, the simplicity rule (Kat's rule), staff product names,
live dropdown search, full-name patient search, nobody checks the Hub (act-on items go by email), one staff change
at a time, staff updates in the weekly email, no window flash, privacy (names fine, clinical detail never), quote
before spending. The agent CLAUDE.md beats a preference. If a Server and a Front preference disagree, the Server
one applies here and the clash goes to Front Desk as a ledger flag.

A new preference Mark gives in a session here is saved once, in the Server Desk memory folder above (file + its
MEMORY.md line), never in this repo or a per-folder memory. Front Desk's files are read-only from here: to change
one, flag it (`python -m system.ledger add flag --for Front`, run in the agent repo).

## Hub specifics

| Rule | Why |
|---|---|
| Build in a worktree on a `session/<date>-<topic>` branch off `origin/main`; never work on the live checkout's `main` | The live Hub runs from that folder |
| Test a branch on the test copy (`RUN-TEST-COPY.bat`, port 5699); the live Hub on 5680 is untouched | Staff use 5680 all day |
| `pytest` green before any push | |
| Merge to `main` only on Mark's word ("merge it"); a live restart after a merge is planned (`RESTART-HUB.bat`) | `main` is what the live Hub runs |
| Record what changed in the agent repo's `logs/CHANGELOG.md` (this repo has no changelog) and keep its `NEXT-SESSION.md` current | One history, one open list for Server Desk |
| Effects (Optomate writes, patient sends, emails) are declared in the agent repo's `system/jobs.json` / `resources.json` under the `hub` app | The practice map sees the Hub only through those entries |
| Never commit per-machine config or data (`config/integrations.json`, `data/`, backups): `.gitignore` blocks them; never `git add -f` | They hold the staff code, tokens and patient-level data |
| Patient data only on screen on the practice network; no patient names in git or in logs | Guardrail 1 |
| Staff pages use product names (Digital, Ultra HDV, Superb, Individual); patient pages use tier names | Mark, 22 Sep 2026 |
| Nothing new reaches staff without Mark picking it (agent repo `IDEAS.md`, one at a time) | Mark, 7 Nov 2026 |
