# Rules for coding agents working in this repo (agy, Claude Code, Codex)

This repo is turning into **Magnm Studio**. The plan is `ROADMAP.md` (v10). Implement only the task you are given; don't jump ahead to other phases.

## Hard rules
1. **No git writes.** Don't run `git commit`, `push`, `checkout`, `reset`, `stash`, `rebase`, `merge`, or branch commands. Read-only git (`status`, `diff`, `log`) is fine. The reviewer commits.
2. **Never read, print or edit `.env`.** Use `.env.example` for the variable names. Never put real keys in any file.
3. **Don't touch content folders** unless the task says so: `outputs/`, `briefs/`, `reviews/`, `logs/`, `reference/`, `video-skills/`, `plan/`, `research/` (except files the task names).
4. **Engine dependencies:** prefer the standard library. Third-party packages are allowed only if pinned in `engine/requirements.txt`, and only when the task explicitly allows them. The engine must still start and pass its tests if an optional package is missing (import it lazily, fail with a clear message).
5. **No network calls in tests.** Mock HTTP. Tests use `unittest` and live in `engine/tests/`. Run them with `python3 -m unittest discover -s engine/tests -t .`.
6. **Don't delete user data.** Move files with care, and keep `state/` contents intact.
7. Keep the product rules: Cero is released; LazyLayout is in development, teasers only, never show features or visuals (see `config/product_rules.json` once it exists).
8. Write plain, small, readable code with short docstrings. No emojis in code, UI or prompts.
9. When done, print a short summary: files changed, how you tested, anything left undone.

## Layout (target)
```
engine/            Python engine (was scripts/): server, gateway, agents, jobs
engine/tests/      unittest tests
config/            JSON config (repos, capabilities, models, product rules)
supabase/          (later) schema, privacy rules, functions
apps/web/          (later) shared React UI
apps/desktop/      (later) Tauri app
apps/mobile/       (later) Capacitor Android app
packages/magnm-design/  (later) shared design kit (Magnm-Pitch style)
```
