# agy task queue

The CMO (Claude, once a day) writes task files here. A small runner on the CEO's Mac hands each one to **agy** (Antigravity CLI). agy does the work and writes the result file. The CMO reads results on its next run. This keeps research and rendering off Claude's token budget.

## Folders
- `inbox/`    new tasks (`<YYYYMMDD>-<type>-<slug>.md`, one task per file, written from TEMPLATE.md)
- `running/`  the task agy is on right now
- `done/`     finished tasks plus `<task>.log` (agy's stdout/stderr)
- `failed/`   tasks that errored or produced no output file; the CMO decides whether to retry

## Task types
`research` · `trend-scan` · `visual-refs` · `strategy-refresh` · `growth-ideas` · `advisor-draft` · `copy-draft` · `script-draft` · `render` · `verify-facts`

## Runner (attended)
When you're at the Mac, run `zsh ~/Magnm-Marketing/queue/agy/agy-runner.sh`. It works through up to 3 tasks, one at a time. agy keeps its normal permission prompts, so you approve risky steps. Web-reading tasks run in agy's sandbox. Needs agy installed and signed in once (`agy`). Unattended mode was declined by Claude's safety check on 2026-10-18.

Rules for agy (also in every task prompt): work only inside ~/Magnm-Marketing; never publish, post, log in, DM or spend money; never invent facts, quotes or numbers; cite URLs for everything; write the result to the exact output path; no LazyLayout visuals or features.
