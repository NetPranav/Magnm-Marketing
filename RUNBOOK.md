# Runbook

## Run the CMO manually (now)
Start a Claude chat linked to this computer with the Magnm-Marketing folder and say:
"Act as the Magnm CMO. Read agents/cmo.md in the Magnm-Marketing folder and run one cycle. Mode: check-in" (or "Mode: plan" for the nightly planning run).

## Run on a schedule (to set up once posting days are confirmed)
Create one scheduled task with the same instruction, 4 times a day. The last run of the day uses Mode: plan.
Requirements: the Mac is awake, the Claude desktop app is open, and the task is set to automatically approve.

## Where to look
- What is happening now: state/board.md
- Why something was made: briefs/ (the brief says why)
- What each agent produced: outputs/ and reviews/
- Full handoff history: state/activity.log

## Approving and posting
Final packages appear in outputs/posts/<id>/ marked READY. You publish them. After publishing, tell the CMO (or add a line to plan/published.md) so it can track results.

## v2 (2026-10-18)
- The CMO runs once a day at 08:46 IST as a scheduled task (agents/cmo.md v2, playbooks/operating-model.md). It never makes videos.
- It sends one notification a day with numbered posts. Reply `yes`, `yes 1,3` or `no 2 <reason>` in that run's chat.
- agy tasks pile up in queue/agy/inbox/. When you're at the Mac, run: `zsh ~/Magnm-Marketing/queue/agy/agy-runner.sh` (agy must be installed and signed in once).
- Post images: `python3 templates/card.py spec.json outputs/posts/<id>/`
