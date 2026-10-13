---
category: Medicare
updated: 2026-10-13
owner: Mark
---

# Medicare rejections: the weekly clear-down

Every Tuesday. The Hub's **Medicare** page lists every rejected or short-paid claim, what each
one needs, and the last day Medicare will accept it. Paid claims leave the list by themselves.

## Each Tuesday

1. **Refresh the data.** Optomate > Institutions > Medicare > **Receive Claim Reports**.
2. **Open the Hub > Medicare** and pick your name at the top.
3. **Work the Do now list from the top.** Red date = under a month left. Each row says what to do.
4. **Fix the claim in Optomate** (paths below). Only when Optomate confirms it was sent, press
   **Resubmitted** on the row. Write-offs: do it in Optomate, then press **Written off**.
5. **Leave anything in Awaiting Mark.** Once Mark decides, it moves to Do now with the item to use.

## What each row asks for

- **Old card number** - update the card on the patient record (new issue number), then resubmit.
- **Check the card number / make name, birth date and card match** - copy them exactly from the
  card into the record, then resubmit.
- **Resubmit with the time of each visit** - add the visit times, then resubmit.
- **Change to (an item number)** - Mark has decided: change the item, add his claim note if
  shown, resubmit.
- **Write off** - write it off in Optomate.
- **Rejected again / Still unpaid** - check the reason in Optomate. If the fix isn't clear,
  leave it for Mark.
- **Lodged, no report yet** - pull the reports again. Still missing after 2 weeks? Tell Mark.

## Optomate paths

- **Resubmit or change the item:** Institutions > **Adjust Batch** > type the claim number in the
  filter row > OK > select the patient's line > **Change MBS Code for Resubmission** > type the
  item > **check the patient name in the box** > OK > Close > Yes.
- **Send:** **Create Batch** (tick only the lines you mean to send) > **Send Electronic Claims**.
  The claim keeps the original visit date, even though the list shows today's date.
- **Card details:** fix the patient record first, then resubmit.

> IF a line is already paid: never resubmit it. Work from the Hub page, not the batch list.
> The page only shows unpaid lines.

## The folded sections at the bottom

- **Waiting for payment** - resubmitted; it closes itself when Medicare pays.
- **Paid since** - Medicare has paid. Nothing to do.
- **Written off** - done in Optomate.
- **Expired** - past Medicare's time limit (1 year for visits from 5 Sep 2025, 2 years before).

## For Mark: the review

1. Pick **Mark** at the top of the Hub, then open Medicare. Your review sits above Do now.
2. For each claim, open that patient's exam on the **Seen** date in Optomate. The row shows a
   short extract of the notes.
3. Press **Accept** on the item the notes support (e.g. 10913 new symptoms, 10914 progressive,
   10916 brief visit) or **Not eligible**. **Another item...** lists the rest.
4. Type your 4-digit **PIN** and Confirm.
   - Accept: the row moves to Karen's Do now with the new item and your claim note.
   - Not eligible: the row moves to Do now as a write-off. Medicare is not asked again.
   - **Undo** works until Karen ticks it done (needs the PIN).

> IF the page says "set your PIN": at the server, in PowerShell, from C:\CEC\CEC-Optomate-Agent run
> `python -m medicare.pin set`. Changing it later asks for the current PIN.

## If something goes wrong

- **Red "Pull reports in Optomate first"** - the reports are over a week old. Do step 1, then reload.
- **"Pick your name at the top first"** - pick your name, press the button again.
- **Pressed the wrong button** - open Waiting for payment or Written off and press **Undo**.
- **Shared computer** - change the name at the top back before you walk away.
