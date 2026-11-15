# CEC Hub - session rules (read me first)

The staff home screen for Concord Eyecare: a local Flask app, live on `http://localhost:5680` on the practice
server, run from `C:\Users\ConcordeyecareServer\Claude\cec-hub`. Server Desk (the practice-server agent) owns it.

**This repo has no rulebook of its own. The rules are the practice agent's:** read
`C:\CEC\CEC-Optomate-Agent\CLAUDE.md` (hard guardrails, session ritual, build conventions, readability standard)
and follow it here too. Where the two ever disagree, that file wins. The standing orders below are the same
block every repo carries.

<!-- standing-orders:v1 2026-11-15 | canonical copy: CEC-Optomate-Agent docs/standing-orders.md | paste verbatim, never edit a copy -->
## Standing orders (every repo, both PCs - Mark, 15 Nov 2026)

Chain of command: **Mark -> two generals** (Front Desk = home PC, Server Desk = practice server) **-> no-AI sergeants** (runner, daily check, repair jobs) **-> lieutenants** (jobs, apps, builders, subagents). Everyone has ONE boss. A general writes only in its own repo and reads the other's read-only clone.

1. **Read the brief first.** At session start: reds, what is live, open work, Mark's decisions in the last 7 days.
2. **Quote the order.** A plan or builder brief opens with Mark's words and their date. A report without them goes back.
3. **Check the map before you build or flip a switch.** Server: `python -m system.map impact <job|file|gate>` in `C:\CEC\CEC-Optomate-Agent`. Home: the joined practice map. Paste Shared with / Reaches / Leaves the network / Rules tripped into the plan and into any "merge it" or "turn on" ask.
4. **Extend, never duplicate.** One tool, job, sender and alert path per job type. Problems reach Mark through the daily check, never a new email.
5. **Register every effect in the same commit.** Server: `system/jobs.json` + `system/resources.json`. Home: `AUTOMATIONS.md` + the map part file.
6. **Report to your one boss before you stop.** Builders and subagents report to the desk that started them, never to Mark. Mark's decisions are written once, where the work lives, in his words with the date. What waits on Mark goes on ONE list: the brain register (`runway.json`). Server items reach it through the server ledger (`docs/reports/server-ledger.md`).
7. **Stop at Mark's lines.** Live writes, merges, patient sends, money and anything staff notice need his words, said in that session. Privacy guardrails beat everything here.
<!-- /standing-orders -->

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
