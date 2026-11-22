# Magnm Studio

Local-first AI marketing studio for the Magnm team (desktop, web UI, Python engine, and Android companion).

## Quick start

- **Teammate install & setup guide:** [docs/INSTALL.md](docs/INSTALL.md) (running from source on Linux/macOS/Windows, Android debug APK, phone pairing, backups, and troubleshooting)
- **Desktop releasing & updater key backup:** [apps/desktop/RELEASING.md](apps/desktop/RELEASING.md)
- **Android app & keystore backup:** [apps/mobile/README.md](apps/mobile/README.md)

## Repository layout

- `engine/`: Python engine (HTTP server, AI gateway, key pool, model registry, agents, jobs, workflows, cloud sync, phone pairing, social research, official-API posting)
- `apps/web/`: React + TypeScript Studio UI served by the engine at `/`
- `apps/desktop/`: Tauri 2 desktop shell supervising the Python engine sidecar and OS keychain
- `apps/mobile/`: Capacitor Android companion app (Tailscale pairing, Phone Lite, offline inbox, Pitch present mode)
- `packages/magnm-design/`: Shared monochrome design kit (Magnm-Pitch sibling)
- `config/`: Model registry (`models.json`), product rules (`product_rules.json`), capabilities, and Supabase client config
- `supabase/`: Database schema, RLS privacy policies, Edge Functions, and SQL privacy tests
- `agents/`, `playbooks/`, `templates/`, `research/`: Team prompts, crisis/launch playbooks, templates, and approved product facts
- `scripts/`: Build (`build_engine.py`), dev setup (`dev_setup.py`), smoke test (`smoke_test.py`), and helper scripts
- `ROADMAP.md` & `AGENTS.md`: Project roadmap and coding-agent rules

## Hard rules for every agent
1. Publishing (CEO, 2026-10-18): only the CMO publishes, and only Reviewer-approved text/text+image posts after the CEO's daily "yes" (playbooks/operating-model.md section 4). All other agents and agy are read-only on social platforms: never post, comment, reply, DM, follow, like or submit.
2. Never create accounts or enter passwords.
3. Never invent metrics, testimonials, quotes, or product features. Cite where each fact came from.
4. Everything you do is written to a file in this folder.
5. The products are spelled Cero and LazyLayout.
- video-skills/    video generator (magnm-video, forked from brag) and vendored engines/skills
# Magnm-Marketing
