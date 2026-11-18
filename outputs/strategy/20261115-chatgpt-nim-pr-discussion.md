# Architecture & PR Agency Strategy Discussion: ChatGPT Partner Sync

- **Date:** 2026-11-15
- **ChatGPT Bound Conversation:** `Magnm Marketing Team` (`6af54fae-5010-83e8-95b6-7098200c73f3`)
- **Participants:** Antigravity (Execution Lead) + ChatGPT Partner (Engineering & PR Agency Advisor)
- **Verdict:** APPROVED — Proceed to Build & Test

---

## 1. NVIDIA NIM Multi-Model Architecture Review

### Context
The CEO requested an `.env` setup supporting **NVIDIA NIM** (`https://integrate.api.nvidia.com/v1`) with dedicated model routing across all **13 PR & Marketing team roles**:
`cmo`, `strategist`, `advisor`, `researcher`, `blog-writer`, `writer`, `designer`, `video-producer`, `reviewer`, `analyst`, `listener`, `community`, `growth`.

### Partner Critique & Observations
1. **Endpoint Availability & Tool-Calling Reality:**
   - Certain legacy or experimental NIM endpoints (e.g., deprecated free endpoints for `deepseek-ai/deepseek-r1`, `nvidia/llama-3.1-nemotron-70b-instruct`, `qwen/qwen2.5-*`) may be unavailable depending on the hosted NVIDIA NIM account tier or lack native `tool_choice` support.
   - Relying blindly on unverified endpoints causes silent production failures.
2. **Workload Separation (LLMs vs. Deterministic Tools):**
   - **Designer (`meta/llama-3.2-90b-vision-instruct`)**: Multimodal text+image analysis/critique model, NOT an image pixel generator. Actual branded card generation must remain deterministic in `templates/card.py` (Pillow + Palette v3).
   - **Video Producer (`meta/llama-3.3-70b-instruct`)**: Responsible for scripts, hooks, shot lists, voiceover copy, and briefs. Actual video compilation remains in Remotion / HyperFrames / FFmpeg.
   - **Analyst (`qwen/qwen2.5-72b-instruct`)**: Responsible for interpreting metrics and explaining anomalies, while deterministic Python handles scoring and aggregation (`research_scanner.py`, `cmo_runtime.py`).
   - **Researcher (`mistralai/mixtral-8x22b-instruct-v0.1`)**: Synthesizes live scraped sources (`hn.algolia.com`, repo commits); deterministic scripts enforce source URLs and scoring (`Score = R * I * C * T`).

### Agreed Implementation Conditions
1. **Unified `.env` Schema + Per-Role Overrides:** Support both a shared `NVIDIA_NIM_API_KEY` and optional per-role `NVIDIA_NIM_API_KEY_<ROLE>` plus `NIM_MODEL_<ROLE>` across all 13 roles, alongside secondary providers (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OLLAMA_BASE_URL`).
2. **Observable Fallback in `scripts/ai_gateway.py`:** Retry unavailable/transient NIM endpoints (`404`, `410`, `502`, `503`, `504`, timeout) using `meta/llama-3.3-70b-instruct` with explicit console & UI observability (reporting `model_requested`, `model_used`, `fallback_used`, `fallback_reason`). Do NOT mask `401/403` authentication or `400` bad-request or `429` quota errors via fallback.
3. **Interactive Model Validation (`AI Models & .env Studio`):** Expose live endpoint testing (`POST /api/test_ai`) and hot-swapping directly in the Web Command Center without exposing raw API keys to client-side logs or git (`.gitignore` excludes `.env`).

---

## 2. Elevating Command Center UI into a PR & Social Media Agency Suite

### Gap Identified
The v4 dashboard operated mainly as a developer script runner rather than a complete **PR, Social Marketing & Business Research Agency OS** supporting both **MAGNM Studio** (`Cero v2.2.3`, `LazyLayout` internal R&D) and the CEO's **Freelancing Business**.

### Approved Agency Modules (Zero Emojis, Uniform SVG Vector Icons)
1. **01. Agency Overview & Presence:** Executive KPI telemetry (Active Campaigns, Pending CEO Reviews, PR Crisis Status `L0 NOMINAL`, Monitored Repos, Active NIM Models), Connected Social Handles registry (`Instagram`, `X`, `LinkedIn`, `YouTube`, `Website`), Operations Console, Live Execution Terminal & Audit Log.
2. **02. Business Research & PR Radar:** Live HN/Developer opportunity table (`Score / 625`) with 1-click **Pitch to `@cmo`** escalation to the Global Team Room, plus Monitored GitHub Repositories (`Cero v2.2.3`, `LazyLayout`) and dynamic repository linking.
3. **03. Editorial Calendar & Card Studio:** Shared slot tracker (`plan/plan.md`), pipeline stage visibility, and an interactive **Palette v3 Visual Card Studio** invoking `templates/card.py` to output `4x5`, `1x1`, `16x9`, and `9x16` brand assets straight into campaign review bundles.
4. **04. PR & Crisis Communications:** Structured **PR & Backlash Response Drafter** supporting `Valid Bug`, `Fair Criticism`, `Misunderstanding`, `Unfair/Bad-Faith`, and `Press / Freelance Client Inquiry`, backed by L1-L3 Crisis Playbook (`playbooks/crisis.md`) and `research/product-facts.md` claim enforcement.
5. **05. CEO Review & Approval Gate:** Unified review cards with image galleries, HTML5 video reel players (`20261024-film`), one-click `POSTING-KIT.md` copy, and explicit **Approve / Request Revision / Mark Published** gates.
6. **06. Global Team Room & Swarm Orchestration:** Shared multi-agent thread with `@mention` chips for all 13 roles, asset attachments, model execution metadata tags, and automatic `@cmo` delegation.
7. **07. Team Roster & Specialist Chat:** 13 specialist cards displaying role code, assigned NIM model, `CAN` / `CANNOT` boundaries, latest deliverable, and 1-on-1 slide-over chat with deterministic capability refusal/redirection and automatic CMO escalation.
8. **08. AI Models & `.env` Studio + Assets + Schedules:** Safe `.env` key editor, per-role model hot-swapper, live endpoint tester, centralized Assets Library, and systemd user timer scheduler.

---

## 3. Standing Non-Negotiables
- **Zero Emojis Policy:** No Unicode emojis or decorative check symbols in UI, agent prompts, fallbacks, or CLI output. Uniform SVG icons and monospace tags (`[OK]`, `[CMO]`, `[REV]`) everywhere.
- **Ground Truth Rule:** Market `Cero v2.2.3` strictly from verified facts (`research/product-facts.md`, `research/repo-sync.md`). Keep `LazyLayout` (`b/phase-77-expressions`) strictly confidential as internal R&D with zero unbuilt feature claims or demos.
- **Human Approval Gate:** No autonomous publishing, bounty announcements, or crisis replies without explicit CEO approval.
