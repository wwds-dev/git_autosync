# Review: feat/restore-and-continue (code-reviewer), 2026-10-21

At 09ef3ed. No P0/P1. Suite 177 passed. Saved by the orchestrator (reviewer is read-only).

P2 (all fixed after, each with a test):
1. A clean main worktree on a detached HEAD was restored one commit back (`$wip^`), its
   changes showing as uncommitted — a clean worktree's wip ref *is* HEAD. Now a snapshot is
   told apart by its fixed message.
2. The CLI and the scheduled runs used different locks (STATE_DIR differed) and could overlap.
   The lock is now always in App Support.
3. The 03:30 run silently skipped the night when the hourly backup held the lock. Full runs
   now wait up to 10 minutes; only backup-only gives up at once.
4. A repo on an unborn branch could never be backed up: the zero SHA of an unborn worktree HEAD
   went into the captured refs. Filtered.

P3 fixed: pid-less lock grace, takeover races and release-only-own-lock; restore reports
checkout/restore failures and the branch it really ended on; a failed fetch removes its clone;
README now clones the tool to a temporary path; ssh GitHub origins restored over https;
credential stripping for every scheme; a global insteadOf cannot redirect the manifest;
run.lock excluded from the Drive copy; non-SQLite `*.db` files copied as is. Not fixed (noted):
an hourly failure keeps the tray on attention until the next good full run; the manifest's
branch is read at the end of the run, the snapshot at the start (rare mid-run switch); Drive
`DRY_RUN=1` still creates folders.
