# Runbook

This workspace runs on Linux with the Python engine in `engine/`.

## Configuration and API keys

Copy `.env.example` to `.env` and fill in your API keys (such as `NVIDIA_NIM_API_KEY`).
Real keys go only in `.env`, which is gitignored and must never be committed.

## Starting the dashboard

Run the local dashboard server:

```bash
python3 engine/server.py
```

The dashboard is served at `http://127.0.0.1:5050`.

## Running a job manually

Run any job from the repository root with:

```bash
python3 engine/cmo_runtime.py run-job <name>
```

Available job names in `engine/cmo_runtime.py`:
- `daily_cycle` — Daily CMO Cycle (Digest, Board & Plan Check)
- `market_scan` — Live Developer & Market Opportunity Scanner
- `repo_sync` — Codebase & GitHub Intelligence Sync
- `generate_blog` — Generate Technical Blog & Repurposed Assets
- `render_cards` — Render Visual Social Cards

## Installing systemd timers (Linux)

The unit files in `systemd/` are configured for `systemctl --user` (user-session timers):

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now magnm-cmo_daily.timer magnm-market_scan.timer magnm-repo_sync.timer
```

To regenerate the unit files from `state/schedules.json`, run `python3 engine/scheduler.py --generate-systemd`.

## Approving and posting

Final packages appear in `outputs/posts/<id>/`: `POSTING-KIT.md` (captions per platform), the card images, and `manifest.json` (status).
The team approves packages and posts by hand from `outputs/posts/<id>/`, then updates the post status in the dashboard or via `python3 engine/cmo_runtime.py publish <id>`.

## What is coming

See [ROADMAP.md](ROADMAP.md) for the Magnm Studio roadmap across desktop, mobile, and team sync phases.
