---
category: Backups
updated: 2026-10-21
owner: Mark
---

# Backups and restore: getting lost data back

Something lost, wrong or won't open? **Stop typing into it and tell Mark.** Only Mark (or Monkey support on the phone) puts a backup back over live Optomate. Pick your scenario below.

> Every backup is copied to the NAS (CECNAS, `192.168.1.68`) at **04:00 on weekdays**. Saturday's work reaches the NAS on Monday morning.

## Where the backups are

- **Optomate database, automatic** - `D:\CEC-NightlySQL\Optomate_yyyyMMdd_HHmm.bak`. Mon-Sat 22:30, checked after writing, newest 7 kept.
- **Optomate's own end-of-day backup** - `D:\Optomate_*.BAK` (database) and `D:\documents_*.zip` (all documents and photos). Only when Optomate is closed on the server. About a week kept.
- **Medmont (topographer)** - `\\192.168.1.62\Optomate Touch\Backups\Medmont\bak` (database) and `...\Medmont\data` (exam files). Nightly 22:15 from the retinal PC.
- **Whole server (C: and D:)** - NAS `CECNAS`, Active Backup for Business, task `CECNAS-Default`. Weekdays 04:00, 15 versions (about 3 weeks).

## 1. Optomate database lost or damaged

Optomate won't open, says database error, or patients / invoices are missing or wrong. **Mark or Monkey only.**

1. **Stop.** Write down the time and what looks wrong. Don't keep entering data.
2. **Call Monkey: 1300 650 295** (or the **Contact Monkey** tile in Optomate). Say "system down". They may fix it without a restore.
3. **Pick the backup.** Newest file in `D:\CEC-NightlySQL` from before the problem. The log `C:\CEC\CEC-Optomate-Agent\logs\sql-nightly-backup.log` says `OK` for each good night.

> IF the D drive is lost too: download the `.bak` from the NAS first (scenario 3), folder `D:\CEC-NightlySQL`.

4. **Test it first, into a spare name** (live data untouched). In PowerShell as administrator, one line at a time, putting the real file name in:
   `sqlcmd -S .\SQLEXPRESS -E -b -Q "RESTORE VERIFYONLY FROM DISK=N'D:\CEC-NightlySQL\Optomate_FILE.bak' WITH CHECKSUM"` - must say `is valid`.
5. `sqlcmd -S .\SQLEXPRESS -E -b -Q "RESTORE DATABASE [Optomate_RestoreTest] FROM DISK=N'D:\CEC-NightlySQL\Optomate_FILE.bak' WITH MOVE 'Optomate' TO N'C:\Program Files\Microsoft SQL Server\MSSQL15.SQLEXPRESS\MSSQL\DATA\Optomate_RestoreTest.mdf', MOVE 'Optomate_log' TO N'C:\Program Files\Microsoft SQL Server\MSSQL15.SQLEXPRESS\MSSQL\DATA\Optomate_RestoreTest_log.ldf', CHECKSUM, STATS=25"`
6. `sqlcmd -S .\SQLEXPRESS -E -b -Q "DBCC CHECKDB([Optomate_RestoreTest]) WITH NO_INFOMSGS"` - no red text means the copy is good.
7. **Live restore (Mark, Monkey on the phone).** Close Optomate on **every** PC and the server. Stop the service **Optomate Touch API Host** (Services window).
8. Keep a copy of the broken one first: `sqlcmd -S .\SQLEXPRESS -E -b -Q "BACKUP DATABASE [Optomate] TO DISK=N'D:\CEC-NightlySQL\Optomate_BEFORE-RESTORE.bak' WITH COPY_ONLY, INIT"` (if it fails, carry on).
9. `sqlcmd -S .\SQLEXPRESS -E -b -d master -Q "ALTER DATABASE [Optomate] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; RESTORE DATABASE [Optomate] FROM DISK=N'D:\CEC-NightlySQL\Optomate_FILE.bak' WITH REPLACE, CHECKSUM; ALTER DATABASE [Optomate] SET MULTI_USER"`
10. Start **Optomate Touch API Host** again. Open Optomate on one PC, check the last patients and invoices, then open the rest.
11. **Re-enter** anything done after the backup time (day sheet, receipts, HICAPS slips).
12. Drop the test copy: `sqlcmd -S .\SQLEXPRESS -E -b -Q "DROP DATABASE [Optomate_RestoreTest]"`

> Proven: a backup was restored and checked table by table on 19 Oct 2026 (`docs\upgrade-41\backup-verify.ps1`).

## 2. Medmont (topographer) lost or damaged

Medmont Studio won't open or exams are missing, on the retinal PC **CONCORDEYECARE2**. **Mark or the Retina Desk only.**

1. **Close Medmont Studio.**
2. Copy the newest `MedmontStudio_yyyyMMdd-HHmm.bak` from `\\192.168.1.62\Optomate Touch\Backups\Medmont\bak` to `C:\Temp` on the retinal PC.
3. Signed in as the Windows user who normally runs Medmont (its database belongs to that user), in PowerShell:
   `sqlcmd -S "(localdb)\MSSQLLocalDB" -E -b -Q "RESTORE DATABASE [MedmontStudio] FROM DISK=N'C:\Temp\MedmontStudio_FILE.bak' WITH REPLACE"`
   [MARK: is sqlcmd installed on the retinal PC? If not, the Retina Desk runs the same line through PowerShell.]
4. **Exam files:** copy everything in `\\192.168.1.62\Optomate Touch\Backups\Medmont\data` back into `C:\ProgramData\Medmont\Medmont Studio 6\`. Copy only - never delete what is there. [MARK: confirm that is the original folder on the retinal PC.]
5. Open Medmont Studio and check a recent exam.

> Older Medmont copies are inside the NAS image of the server (scenario 3, folder `C:\Optomate Touch\Backups\Medmont`).

## 3. A single file or photo lost

A retinal photo, scan, letter or document deleted or overwritten. Mark (NAS sign-in needed).

1. Note **which file**, **which folder** (e.g. Retinal Photos) and the **last day it was OK**.
2. On any practice PC, open a browser at `https://192.168.1.68:5001` and sign in to the NAS.
3. Main menu > **Active Backup for Business Portal**. (On the server you can also use the Active Backup agent's **Recovery Portal** button.)
4. Task: the server, `CECNAS-Default`. Use the **timeline** at the bottom to pick a day before it went missing.
5. Browse: `C:` > `Optomate Touch` > `Documents` > the folder (e.g. `Retinal Photos`).
6. Tick the file > **Download**. Then copy it back into place by hand.

> Download is safer than Restore: Restore replaces a file with the same name.

> IF the file changed after this morning's 4 am copy: no backup has that version yet. Pick the newest earlier version, or ask Mark.

## 4. Whole server dead

The server won't start, or its disk has failed. Every PC loses Optomate. **Mark only.**

1. **Don't reinstall Windows** and don't wipe any disk. Call Mark.
2. Call **Monkey 1300 650 295** - server down, Optomate offline.
3. Get the **server recovery USB** (labelled, "Syno Media"). [MARK: does one exist? Where is it kept?]
4. Plug it into the server (or replacement), restart, press **F2 / F12** for the boot menu, start from the USB.
5. The **Recovery Wizard** opens. Connect to the NAS `192.168.1.68`, sign in, pick task `CECNAS-Default` and the newest good version.
6. Restore the **entire device**. Wait until it finishes, then restart from the disk.
7. Check: the server is still `192.168.1.62` (every PC looks for Optomate there). Services **SQL Server (SQLEXPRESS)** and **Optomate Touch API Host** running. Open Optomate.
8. The image is from 04:00. If the old D: disk still works, its newer `D:\CEC-NightlySQL` backup (22:30) can go back with scenario 1.

> IF there is no recovery USB: on any working Windows 10/11 PC, download **Active Backup for Business Recovery Media Creator** from Synology's Download Center (pick DS225+), insert a 1 GB+ USB, choose USB media, Create. Needs internet.

[Synology guide: making the recovery USB](https://kb.synology.com/en-global/DSM/help/ActiveBackupRecoveryMediaCreator/activebackuprecoverymediacreator?version=7)

[Synology guide: the restore portal](https://kb.synology.com/en-global/DSM/help/ActiveBackup/activebackupforbusinessportal?version=7)

## 5. Contacts, places and quick checks

- **Mark** - decides every restore. [MARK: mobile number for the printed sheet.]
- **Monkey Software (Optomate)** - `1300 650 295`, or the Contact Monkey tile.
- **Synology (NAS)** - support ticket via synology.com > Support.
- **Medmont** - [MARK: Medmont support number.]
- **IT** - the Claude server desk on the server, plus Mark.
- **Accounts (names only, no passwords here)** - server admin `ConcordeyecareServer` (Microsoft account), `cechandoff`, `trkexport`. [MARK: which NAS account signs in to the portal, and where are passwords kept?]
- **NAS** - `CECNAS`, `192.168.1.68`, sign-in `https://192.168.1.68:5001`.
- **Server** - `192.168.1.62`.

Quick checks (PowerShell on the server, read-only):

- Last nightly backup: `Get-Content C:\CEC\CEC-Optomate-Agent\logs\sql-nightly-backup.log -Tail 3` - newest line starts `OK`.
- Backups on hand: `Get-ChildItem D:\CEC-NightlySQL`
- Is a file good: `sqlcmd -S .\SQLEXPRESS -E -Q "RESTORE VERIFYONLY FROM DISK=N'D:\CEC-NightlySQL\Optomate_FILE.bak' WITH CHECKSUM"` - says `is valid`.
- Medmont copies: `Get-ChildItem "C:\Optomate Touch\Backups\Medmont\bak"`
- NAS: sign in > Active Backup for Business > Physical Server > `CECNAS-Default` - last run Successful.
- Full proof, every 3 months, Optomate closed: `powershell -ExecutionPolicy Bypass -File C:\CEC\CEC-Optomate-Agent\docs\upgrade-41\backup-verify.ps1`
