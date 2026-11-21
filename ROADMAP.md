# Magnm Marketing Studio — Roadmap v10

Updated 2026-11-18. Magnm is a non-commercial student group; the plan is sized for that.

**Progress:**
- **Phase 0 done (2026-11-19):** built by agy (Gemini 4 Hydrogen High) and reviewed by Claude.
  - Moved the engine to `engine/`.
  - Model registry in `config/models.json`.
  - No more fake answers.
  - Confidential work goes to Ollama only.
  - Product rules in `config/product_rules.json`.
  - 30 unit tests.
- **Phase 1 done (2026-11-19):** built by agy, reviewed and tested by Claude.
  - Tauri desktop app (`apps/desktop`) runs the engine as a packaged sidecar.
  - Per-user data folder, local API token, keys in the OS keychain.
  - SQLite storage, job queue with live updates, keep-awake, setup check, import of the old folder.
  - Linux `.deb` built and launched.
  - CI workflow for Windows, macOS and Linux (not run yet, needs the updater signing key).
  - Models re-selected from live NIM tests.
  - 63 engine tests and 6 Rust tests.
- **Phase 2 done (2026-11-19):** built by agy, reviewed and live-tested by Claude.
  - 7 agents with tool calling and pausable sessions.
  - Post / blog / repurpose / weekly-plan / video-script workflows with approval gates and posting kits.
  - NVIDIA key pool (3 keys rotating).
  - Repos, "shipped this week" and fact approval, with confidential repos kept to local AI.
  - Free web research (HN, Reddit, GitHub, dev.to, ddgs) with SSRF-safe page reading and a research browser.
  - Deterministic review checks, agent memory and feedback, and the model bake-off.
  - 133 engine tests.
  - **Changes from the plan:**
    - A small built-in workflow runner instead of LangGraph.
    - Our own Playwright tools instead of browser-use.
    - GitHub via local folder, public repo or token (the GitHub App sign-in comes later).
    - Video voice-over and render moved to Phase 7.
- **Phase 3 done (2026-11-19):** built by agy (Gemini 4 Hydrogen High), reviewed and tested by Claude.
  - `packages/magnm-design`: Pitch's colours, bundled fonts (works offline), optional dark theme, CSS components
    and React components, with checks for exact Pitch values, contrast and offline use.
  - `apps/web` (Vite + React + TypeScript) served by the engine at `/`. The old dashboard stays at `/legacy`
    for now.
  - All 9 sections: Today, Team, Research, Plan, Create, Review, Library, Repos, Settings.
  - Review Studio: comments anchored to text, image pins and video timestamps; `@agent`; Apply all; suggestion
    mode (tracked changes); AI actions; versions with word diff and restore; checklist; platform previews;
    posting kit bar. AI never overwrites a draft silently, and confidential items stay on local AI.
  - Keys are saved through the desktop keychain inside the app, and are never shown, not even masked.
  - 153 engine tests, 34 web tests, 9 design-kit checks; CI runs all of them.
  - **Not done / changes from the plan:**
    - No task board yet (the engine has no task store); Plan shows the weekly plan, briefs, calendar and schedules.
    - Running agent sessions can be cancelled but not paused (no engine support).
    - "Send to my phone" is shown but disabled until Phase 4 pairing.
    - Image editing and video rendering in Review Studio wait for Phase 7.
- **Phase 3b done (2026-11-19):** built by agy (Gemini 4 Hydrogen High), reviewed and tested by Claude.
  - Magnm-Pitch pinned at `c44b7e2` in `vendor/magnm-pitch/` (unmodified, hash-checked, Pitch's rules check in CI),
    served at `/pitch/` with bundled fonts (offline) and a small adapter (`vendor-host/magnm-host.js`) that feeds it
    prices, proposals and custom items. Side by side with the real Pitch page it is visually identical.
  - Section 7 Pitch: proposals and quote forms (client details, every Pitch text field, which services each sheet
    shows, pre-selected choices, custom line items), live Pitch preview, per-proposal prices with the source of every
    price, full-screen present mode, PDF export (also saved by the engine), Draft with Writer.
  - Admin base prices in Settings (editable by anyone until roles arrive in Phase 4).
  - Pitch's rules checked before every save; agents (CMO, Writer) can draft proposals but never set prices.
  - Every proposal create/update/delete is queued in `sync_outbox` with who made it and its pricing, for the admin.
  - Security: Pitch inserts some text as HTML, so `<` and `>` are rejected in proposal text and stripped by the
    adapter (tested in a browser with an injection payload).
  - 177 engine tests, 41 web tests, 7 adapter tests, 9 design-kit checks.
  - **Waiting for Phase 4:** proposal links, quote-form links with submissions back to the sender, admin
    notifications, admin-only base prices, Send to my phone. Present mode on Android comes with Phase 6.
- **Phase 3c done (2026-11-19):** default and final quotations (asked for by the user after 3b). Built by agy
  (Gemini 4 Hydrogen High), reviewed and tested by Claude.
  - **Default quotation:** a fixed entry that is always Pitch's default planner (same options and layout, admin base
    prices); it cannot be edited or deleted, only duplicated. Each member gets their own link for it in Phase 4, so
    submissions return to whoever sent it. New quote forms now also start as Pitch's default.
  - **Final quotation:** for pricing already agreed with the client. Only the agreed services are shown, ticked and
    locked, at the agreed prices. Plans: payment plan (2 to 4 milestones summing to 100%, with amounts), payment note,
    timeline (1 to 8 steps) and service plans (one-time, monthly, quarterly, yearly; recurring plans are listed
    separately and never added to the one-time total). Any proposal or quote form can be turned into one.
  - The PDF for a final quotation follows Pitch's PDF layout with the agreed information (plans, recurring section,
    timeline). A test flags it whenever Pitch's own PDF code changes on a re-pin.
  - Its read-only web link switches on in Phase 4 with the other links.
- **Phase 3d done (2026-11-20):** pitch editing improvements asked for by the user. Built by agy (Gemini 4 Argon
  Medium from now on), reviewed and tested by Claude.
  - Remove a whole software option (Web & SaaS, Mobile app, Backend, AI & Ops) from a pitch; at least one must stay.
    Tabs and "Page N of M" labels renumber. Services a remaining sheet needs from a removed sheet appear once under
    "Included services", so the price never includes something the client cannot see. Final quotations drop empty sheets.
  - Remove any About-page section (intro, what we do, portfolio, this plan, architecture, timeline, how you pay).
  - New deliverable categories with their own services, on an existing sheet or as a new page with its own tab.
  - The preview follows the form (scroll and focus), the tab row stays visible with Previous/Next at the end of each tab,
    and the Prices tab shows the live preview with prices updating as you type.
- **Phase 4 in progress (2026-11-21).** Development uses a cloud Supabase project (`magnm-dev`); the laptop has no room
  for local Supabase. Production will be a separate project that is never reset.
  - **T4.1 done:** database tables and privacy rules: invite-only sign-up, profiles and roles (the last admin can't be
    removed), activity and device status, the workspace mirror (members write their own, the admin reads all), shared
    knowledge and Pitch base prices (admin writes), Pitch links and quote forms (clients reach them only through two
    functions, with size and rate limits), and an audit log. 45 privacy checks run on magnm-dev and in CI.
  - **T4.2 done:** Google sign-in from the app (system browser, PKCE, back to the app's local address), Settings
    Account card, admin invites. The database accepts Google sign-ups only. First admin signed in on 2026-11-21.
  - **T4.3 done:** the engine mirrors each member's work (chats, team room, drafts, research, reviews, metrics, briefs,
    tasks, posted links), activity and a device heartbeat. Confidential chats, and anything naming a confidential
    product, upload only as text-free stubs; sync pauses if the product rules can't be read. agy now runs on Gemini 4
    Hydrogen (High for auth, privacy and sync work).
  - **2026-11-21: magnm-dev now holds the team's real data** (team decision; no client data yet). It is never reset
    (`supabase/dev.sh` refuses); test data is labelled and deleted right after each test.
  - **T4.4 done:** thumbnails and WebP/480p previews made on the laptop with ffmpeg (local files and safe formats only),
    stored privately in Supabase Storage (owner and admin rules, signed links), video previews deleted after 60 days,
    storage meter.
  - **T4.5 + T4.6 done, live on https://magnm-pitch.vercel.app:** `/p/` proposal, `/q/` quote form ("Send my plan to
    magnm"), `/f/` final quotation. "Copy link" publishes (only the client's name and business plus prices and layout),
    saving keeps links current, "Withdraw link" turns them off, and quote requests arrive in the sender's Pitch section.
    The Pitch adapter now lives in Magnm-Pitch (`host/magnm-host.js`) and the Studio vendors it. Tested end to end by
    the user.
  - **T4.7 done:** Admin section (admins only): team presence, roles and deactivation, activity timeline and chart,
    read-only member workspaces with previews, members' links and quote requests, storage, audit log. Tested with a
    second Google account as a member (cleaned up afterwards).
  - **T4.8 + T4.9 done:** shared AI keys. The admin shares this laptop's NVIDIA keys once; they are stored encrypted in
    Supabase Vault and can't be read back. Laptops without their own keys use the `ai-proxy` Edge Function with their
    sign-in: daily limits per member (default 200 requests / 400k tokens), usage and key health in Admin, key rotation
    with cooldowns, a 100 s time budget, and no logging of prompts or keys. Tested live on 2026-11-22 (4 keys shared,
    test request OK in 3.5 s).

**What changed from v9:**
- **The admin sees everything again:** activity plus the actual content (text, chats, briefs, thumbnails and previews). Full-quality files still stay on the laptop.
- **Storage:** Supabase Storage (1 GB) first, with **Backblaze B2 (10 GB free, no card)** as the overflow for video previews. Details in 3.7.
- Confirmed: separate research accounts with read-only social browsing, and LazyLayout stays "In development + Teasers only".

**What changed from v8 (kept for history):**
- **Phone Lite mode:** the phone can do AI text and image work itself when the laptop is off.
- **Tailscale** for phone ↔ laptop (free Personal plan; non-commercial use fits). Iroh is kept as plan B.
- **Admin sees activity only:** who is active and what their agents are doing, not the content itself. **No file storage needed**, so no R2.
- **Product rules replace the hard-coded "LazyLayout rule".** Each product gets configurable disclosure settings.
- **Connect your own coding agents** (Claude Code, Codex, Antigravity, Gemini CLI).
- **Logged-in social browsing for research** (opt-in, read-only, with safeguards). Future posting goes through official APIs, not the browser.
- **Login: Google only.**

---

## 1. Where the project stands today (unchanged)
The current code has:
- 13 agent prompt files
- a local dashboard (`scripts/server.py`)
- an NVIDIA NIM gateway
- repo sync and an HN/Reddit scanner
- a proven video pipeline (`video-skills/magnm-video`)
- an Antigravity queue runner (`queue/agy/`)

Main gaps:
- Agents can only chat; they can't use tools.
- The daily cycle runs no AI.
- The model list is out of date.
- There are fake "offline" answers.
- The UI lives inside a 2,600-line Python string.
- State is scattered across files.
- There are no users.
- The 13 roles overlap.

---

## 2. How the software works

| Where | What |
|-------|------|
| **Each laptop** (Magnm Desktop: Tauri + Python engine; Windows, macOS, Linux) | **All heavy work:** 7 agents, web search and browsing, repos, cards, images, videos. Personal keys in the OS keychain. Local SQLite and files. Optional connected coding agents. |
| **Member's Android phone** (Magnm Mobile) | Remote control of the laptop over **Tailscale**. **Phone Lite mode** when the laptop is off. Posting from the phone. |
| **Supabase** (free plan, managed; we host nothing) | **Google sign-in** and invites, the **mirror** (activity, chats, briefs, drafts, post text), **Storage** for thumbnails and previews, push notifications (FCM), the **shared-key function**, and a small queue for when devices can't reach each other |
| **Backblaze B2** (free 10 GB, no card) | Overflow storage for video previews once Supabase's 1 GB gets close to full |
| **GitHub Releases** | Installers and app updates |

**What the admin sees (everything, as a mirror):**
- **Live status:** each member online, idle or working, and which agents are running right now.
- **Activity timeline:** briefs started, drafts written, renders done, research runs, posts marked as posted.
- **The content itself:**
  - chats with agents, briefs and tasks
  - every draft's text and its versions
  - Reviewer warnings
  - research notes and sources
  - posted links and reported results
- **Media:** a thumbnail for every image and video, a compressed **image preview** (about 1080 px WebP, ~150 KB), and a compressed **video preview** (480p, about 2–3 MB for 30 s).
- **Full-quality originals stay on the laptop.** If needed, the admin asks the member on WhatsApp or Telegram.
- Members still see **only their own** work, enforced by database privacy rules.

---

## 3. Answers and research

### 3.1 Using AI on the phone when the laptop is off: "Phone Lite"
The phone calls the AI directly for **light work**:

| Works on the phone alone | Needs the laptop (queued until it's back) |
|---|---|
| Chat with agents (CMO, Writer, Researcher, Analyst) | Web browsing and deep research |
| Write or rewrite captions, posts, hooks, threads, scripts | Reading GitHub repos |
| Quick actions (shorten, tone, translate, hashtags) | Rendering videos, voice-overs and brand cards |
| AI images (FLUX through the NIM API is just an API call) | Local AI (Ollama), connected coding agents |
| Read analytics screenshots | Anything confidential (local AI only) |

- **Keys on the phone:**
  - the member's **personal key** in Android's secure Keystore
  - the admin's **shared keys** through the same Supabase function the laptop uses
- **Context on the phone:** a small synced copy of what agents need to write well: brand rules, product rules and approved facts, the member's agent memory, and recent briefs. It's text only, a few hundred KB.
- **Sync:** work made on the phone is saved on the phone and synced to the laptop over Tailscale the next time both are online.

### 3.2 Phone ↔ laptop: Tailscale (plan B: Iroh)
- **Why Tailscale fits:**
  - The free **Personal plan** (6 users, unlimited devices) is meant for non-commercial use, and a student group that earns nothing fits that.
  - Each member uses **their own tailnet** (just their laptop and phone), so members can't reach each other's laptops at all.
  - **Zero networking code for us.** The phone just talks to the laptop's Tailscale address.
- **How pairing works:**
  - Install Tailscale on the laptop and phone with the same account.
  - In Magnm, scan the laptop's QR code. It contains the laptop's Tailscale address plus a pairing secret, so even other devices in the tailnet can't control Magnm without it.
- **Shared keys still work.** Tailscale only connects your own devices; normal internet is unaffected. The laptop (or the phone in Lite mode) calls Supabase's shared-key function as usual.
- **Things to know:**
  - **Android allows only one VPN at a time**, so a member can't run another VPN app alongside Tailscale.
  - Every member installs one extra app.
  - **If Magnm ever becomes a company that earns money**, switch to the paid plan (about $8 per user per month) or to **Iroh**, a direct link built into Magnm (free, no extra app, about 1–2 weeks of work).
- **Same Wi-Fi** works even without Tailscale (local discovery).

### 3.3 Product rules (replaces the "LazyLayout rule")
- **What it is today:** a CEO rule from 2026-10-17 in `config.md` and `research/product-facts.md`:
  > LazyLayout is NOT built. Never show, demo, describe features of, or put LazyLayout visuals in any post or video. At most a text-only "something else is coming" line, and only if the CEO approves it per post.
  
  It exists so the team never promises features that aren't built yet, and never shows fake demos.
- **New: product rules, configurable per product or repo**

  | Setting | Options |
  |---------|---------|
  | Status | Released / In development / Secret |
  | What agents may show | Everything / Text only / Teasers only ("something's coming") / Nothing |
  | Allowed claims | Only approved product facts (default) |
  | Blocked words and visuals | Free-text list (e.g. feature names not yet public) |
  | Confidential | On = this product's work uses local AI only and never goes to a cloud model |
  | Who sets it | Admin for team products; each member for their own repos |

  The Reviewer checks every draft against these rules and **warns** (it never blocks).
- **Default for LazyLayout:** In development + Teasers only. Change or remove it in settings at any time; nothing is hard-coded any more.

### 3.4 Connecting your own coding agents (Claude Code, Codex, Antigravity, Gemini CLI)
- **Why it's worth it:** these agents are great at **code-heavy marketing work**:
  - reading a repo deeply to draft product facts and "what shipped" explanations
  - technical blogs with real code
  - **building video compositions** (the HyperFrames videos in `video-skills/magnm-video` are code, and were originally made by coding agents)
  - fixing card templates
  - long research tasks
- **How:**
  1. **ACP (Agent Client Protocol):** an open standard (Apache-2.0, 25+ agents by 2026). Third-party sources list Claude Code, Codex, Gemini CLI and Antigravity among its users. Magnm implements one ACP client and can drive any of them.
  2. **Headless CLI fallback:**
     - Claude Code: `claude -p`
     - Codex: `codex exec`
     - Antigravity: `agy -p` (authenticate interactively once first; open bug reports say output can go missing when run without a terminal, so we run it inside a pseudo-terminal or prefer ACP)
     - Gemini CLI
  3. **The other direction (optional):** Magnm exposes itself as an **MCP server**, so you can drive it from your coding agent ("Magnm, draft a LinkedIn post about my last commit").
- **Settings:**
  - Magnm detects which tools are installed and signed in.
  - Per agent or per task, choose the engine: NIM / Ollama / my Claude Code / my Codex / my Antigravity.
- **Billing and terms:**
  - Magnm **runs your own installed, signed-in CLI** on your own laptop. It never handles your Claude, ChatGPT or Google login.
  - Claude's help centre says `claude -p` and the Agent SDK signed in with your plan **draw from your subscription limits**.
  - Codex `codex exec` reuses your saved ChatGPT login and uses your plan's credits.
  - Third-party apps must not offer Claude.ai login or route requests through your plan, which is exactly why Magnm calls the official CLI instead.
- **Safety:**
  - Coding agents run inside a **Magnm workspace folder**, with repos mounted **read-only**.
  - Permission mode is always explicit (e.g. Codex `--sandbox read-only`, Claude Code with restricted tools).
  - They never get Magnm's keys or social logins.
  - Every run is logged and can be stopped.

### 3.5 Logged-in social media in the browser (research now, posting later)
**The idea:** the member logs into Instagram, X and LinkedIn **once in a visible browser window**. Magnm saves that browser profile on the laptop, and agents reuse it (in the background) to research: competitors' posts, what's trending, replies, hashtags.

**Risks:**
- **All three platforms' terms forbid automated access to logged-in accounts.**
  - LinkedIn is the strictest and its detection has improved a lot. Penalties range from temporary feature locks to **permanent bans**.
  - Instagram treats non-API automation as a terms violation.
  - X bans automated likes and requires approval for AI reply bots.
- **Security:** an agent holding a logged-in session while reading untrusted pages is the riskiest setup for prompt injection. A malicious page could try to make it post, DM or follow.

**If we build it, these safeguards are required (opt-in, per platform):**
1. **Use separate research accounts**, not the main Magnm brand accounts, so a ban doesn't hurt your public presence.
2. **Read-only tools enforced in code.** The agent can open, scroll, search and read. Clicking post, like, follow, comment, DM or repost is **blocked by the tool itself**, not just by the prompt.
3. **A slow, human pace,** short sessions, and only when the user starts it (no 24/7 crawling).
4. **On any challenge** (captcha, "unusual activity", login check), **stop and hand it back to the user.** Never try to get around it.
5. The browser profile is stored encrypted on the laptop and is never synced anywhere. It can be removed with one click.
6. The user can watch it live in a visible window.

**Starting social research by message (laptop or phone):**
- Just ask in chat, for example:
  - "@researcher what did our competitors post on LinkedIn this week?"
  - "check X for what people say about AI terminals"
  - "find trending Reels about developer tools"
- From the phone, the message goes to the laptop over Tailscale, because the logged-in browser profile lives only on the laptop. If the laptop is off, the request queues and runs when it's back.
- Progress and results appear in the same chat thread on both devices.

**When the agent hits a captcha or security check (it never solves or bypasses one itself):**
1. **Pause:** the agent stops at the challenge, keeps the page open, and carries on with other sources where it can.
2. **Notify both devices at once:**
   - a desktop notification plus a push to the phone: "Researcher needs you: LinkedIn security check"
   - buttons: **Solve now / Later / Skip this platform**
   - the push itself carries no screenshot (it could contain account details); the phone fetches it from the laptop over Tailscale
3. **Solve it the easy way, from either device:** tap **Solve now** to open a **live view of the agent's browser** inside Magnm.
   - The desktop shows it as a panel, with a button to open it in a full browser window if needed.
   - The phone shows it full-screen, and your taps and typing go to that browser page on the laptop.
   - This covers checkbox and picture captchas, "enter the code we sent you", and "confirm it's you" screens.
4. **Resume automatically:** Magnm notices when the page moves past the challenge, closes the live view, and the agent continues.
   - Solving it on one device clears the alert on the other.
   - You can also tap **Done, continue**.
5. **If nobody solves it:** after 30 minutes the agent finishes with the other sources and marks the report "LinkedIn part incomplete". It offers to retry later.
6. **Logged out:** the same flow asks you to log in again inside the live view.
7. **Account safety:**
   - Frequent challenges suggest the platform is suspicious. After 2 challenges in a day, that platform is paused for 24 hours and you're told why.
   - Every challenge is recorded in the activity log, so the admin sees it too.

**Posting later: use the official APIs instead of the browser.** They're free or cheap and allowed:

| Platform | Official route | Cost and approval |
|----------|----------------|-------------------|
| LinkedIn (personal profile) | "Share on LinkedIn" (`w_member_social`) | **Self-serve, no approval.** Free. Sign-in lasts 60 days. |
| LinkedIn (company page) | Community Management API | Needs LinkedIn approval (slow) |
| Instagram (Business/Creator) | Graph API content publishing | Free. **Standard Access is enough for your own accounts** (no review). |
| X | API v2 | Pay-per-use: about $0.015 per post ($0.20 with a link). X requires showing exactly what will be published first. |

Magnm would always show the exact post and need a click to confirm.

### 3.6 Login: Google only
| | Google sign-in | Email (magic link or code) |
|---|---|---|
| For members | **One click.** Every Android phone already has a Google account. | Must open their email every time they sign in |
| Setup for us | A Google Cloud project and consent screen (basic scopes, "In production"), plus browser-redirect handling on desktop. About half a day. | Supabase's built-in email sends only **2 emails/hour** and only to project team members. It needs a custom email provider (e.g. Resend, free 100/day), which needs **a domain we own and verify**. `magnm-org.vercel.app` can't be verified, so we'd have to buy a domain. |
| Verdict | ✅ **Use this** | Skip |

Invite-only: after Google sign-in, the email is checked against the admin's invite list.

### 3.7 Free storage for previews
**What we need:**
- **private** storage (never public links)
- signed, time-limited links issued by a Supabase function after checking the person is the owner or an admin
- room for thumbnails, image previews, and video previews that are deleted after 60 days

| Option | Free amount | Card needed? | Good | Watch out |
|--------|-------------|--------------|------|-----------|
| **Supabase Storage** | 1 GB storage, 5 GB downloads/month (shared with the database) | **No** (already have it) | Same login and privacy rules as everything else. Zero extra setup. | Only 1 GB. Files above about 50 MB can't be uploaded on free (fine for previews). |
| **Backblaze B2** | **10 GB** storage, free downloads up to 3× what's stored | **No** (per Backblaze; one 2025 report says a card is needed for *public* buckets, but ours are private) | Biggest no-card free tier. S3-compatible. Signed links work. | Daily free API-call quotas. Without a card, going over means it **stops** (no surprise bill). No India-specific information found. |
| **Filebase** | 5 GB storage, 5 GB downloads/month, 1 bucket | **No** (official docs) | Simple, S3-compatible. The account goes read-only when full instead of billing you. | Small download allowance. Free accounts can't have public buckets (fine for us). |
| **Tigris** | 5 GB, 10k uploads and 100k reads per month | **No** (official pricing page; one directory disagrees) | Free downloads | Its terms say they will "eventually charge fees" for the free service, so it's less predictable |
| **ImageKit** | 20 GB downloads/month, about 3 GB storage (sources disagree), limited video | No | Image resizing built in | Video is limited; it's a media CDN, not plain storage |
| **Cloudinary** | 25 credits/month, only 1 GB of video downloads | No | Image and video transforms | Too tight for video |
| **Cloudflare R2** | 10 GB, free downloads | Payment method likely (sources disagree). **RuPay isn't accepted**; Visa or Mastercard with international payments enabled, or PayPal | Excellent product | Card friction for Indian students |
| **IDrive e2** | 10 GB | Unclear for e2 | Cheap | Unclear sign-up terms; free accounts get suspended if they go over |
| **Oracle Cloud** | 20 GB | **Yes.** A credit card (or a credit-like debit card); PIN debit, RuPay, virtual and prepaid cards are rejected | Biggest free amount | The card hurdle is well known for Indian users |

**How much space we'll actually use** (assuming 5 members, 15 videos and 50 images a week in total):

| Item | Size | Kept | Space used |
|------|------|------|------------|
| Video previews (480p, 30 s) | ~3 MB | 60 days (about 128 videos) | ~**385 MB**, steady |
| Image previews (1080 px WebP) | ~150 KB | Forever | ~**7.5 MB a week**, ~400 MB a year |
| Thumbnails | ~30 KB | Forever | Small |
| Text (chats, drafts) | — | Forever, in the database (500 MB) | Small |

That's roughly **800 MB after the first year.** Supabase's 1 GB fits for about a year.

**Recommendation:**
1. **Start with Supabase Storage only.** It needs no extra account, and the privacy rules are already there.
2. Build the storage layer so it can **switch backends with one setting** (S3-compatible), and show the admin a usage meter.
3. When Supabase storage passes about **80%**, move **video previews to Backblaze B2** (10 GB free, no card). Thumbnails and image previews stay in Supabase.
4. Keep R2 and Oracle as later options if someone has a suitable card.

### 3.8 Carried over
- **Sharing apps:** installers on GitHub Releases, no paid developer accounts.
  - Windows: Run anyway.
  - macOS: Open Anyway.
  - Android: APK; register the free limited-distribution account before 2027.
- **Free search chain:** direct sources → `ddgs` → SearXNG → browser search.
- **Browsing:** Playwright + browser-use.
- **GitHub per user:** GitHub App, token in the keychain.
- **Results:** the Analyst asks for numbers, or reads screenshots.

### 3.9 Antigravity (agy) check, 2026-11-19
- `agy` 1.5.11 is installed and signed in (stored in the system keyring) as **the Magnm team Google account (the one with access to all models)**, according to agy's own log.
  - An earlier note named a different account. That came from Gemini CLI's file (`~/.gemini/google_accounts.json`), which agy doesn't use.
- **Hydrogen works on that account:** two print-mode tests with `--model gemini-4-hydrogen-high` answered in about 5 seconds.
  - The log shows the model override "Gemini 4 Hydrogen (High)" being sent.
  - There was no fallback or Flash line.
  - The `stream-json` init event reports `model: gemini-4-hydrogen-high`.
- **JSON output from scripts works:** `json` and `stream-json` worked fine without a terminal (non-TTY), so the reported "missing output" bug didn't show up.
- **Default permissions are wide open:** agy's default here is `always-proceed`, with browser, command and file-writing tools. The Studio must always run it with `--mode plan` or `--sandbox` and an explicit working folder.
- **`agy acp` exists**, so Phase 7 can drive agy over ACP directly.
- Models available through agy include:
  - Gemini 4 Hydrogen (low / medium / high / xhigh)
  - Gemini 4 Argon
  - Gemini 3.8 Flash
  - Claude Opus 5.5
  - Claude Sonnet 5.5
  - GPT-OSS 120B
- Useful flags: `--model`, `--effort`, `--mode plan`, `--sandbox`, `--output-format json/stream-json`, `--print-timeout`.

### 3.10 Magnm-Pitch inside the Studio, and a shared design language
**What Magnm-Pitch is** (`~/padhai_in_linux/Projects/Magnm-Pitch`):
- a vanilla JS software cost planner and quotation engine
- 5 sheets: About, Web & SaaS, Mobile, Backend, AI & Ops
- a live total in ₹ and a PDF export (jsPDF)
- client routes (`/clients/<slug>`) and an `/admin` preset manager
- anti-hijacking rules (`npm run check-rules`)
- deployed on Vercel, and **already packaged as a Capacitor Android app** (the same stack we chose)

**Integration: a "Pitch" section in the Studio, desktop and Android:**
1. **Present mode:** full screen with all Studio menus hidden, so you can hand your phone or laptop to a client. It works **offline**, because Pitch is bundled inside the app.
2. **Client proposals:** create, edit, duplicate and delete client presets (the existing `/admin` features). Proposals are the member's own work, so the admin sees them in the mirror.
3. **Base prices are shared knowledge:** the admin edits them, and members' proposals use them. Pitch's rules are kept (the main planner is never changed for one client), and the check runs before anything is saved.
4. **Share options:** download the PDF, copy the public Vercel client link, or Send to my phone.
5. **AI help:**
   - "@writer draft a proposal for a telemedicine startup": the CMO or Writer picks deliverables and writes the headline and scope, and you review it in Review Studio.
   - The Researcher can look up the client first.
   - Agents never change prices.
6. **One codebase:** Magnm-Pitch stays its own repo (and stays live on Vercel for clients). The Studio pulls in a specific version at build time, so fixes made in Pitch reach both.
7. **One Android app for the team:** the Studio's Android app includes Pitch, so teammates install one app instead of two. The standalone Pitch APK can be retired, or kept for anyone who only needs quotes.

**Design language: the Studio becomes Pitch's sibling**

Pitch's style is "white, quiet, premium". The Studio's UI (Phase 3) adopts it:

| Element | Pitch value | In the Studio |
|---------|-------------|---------------|
| Background / text | `#ffffff` / `#0a0a0a`, body `#3b3b3b` | Same |
| Tints and lines | hover `#f5f5f3`, selected `#ebebe8`, lines `#e3e3df` / `#cfcfca` | Same; cards use `#f5f5f3` with 14 px corners |
| Display type | **Instrument Serif** (big lowercase "magnm" wordmark, serif headings, italic emphasis) | Page titles, the wordmark, big numbers (results, totals) |
| Body type | **Hanken Grotesk** | All UI text |
| Labels | **DM Mono**, uppercase, wide letter-spacing ("PAGE 1 OF 5") | Section labels, status, agent names, timestamps |
| Navigation | Numbered tabs with an underline ("1 About · 2 Web & SaaS") | Numbered top tabs on mobile; the same style in a slim sidebar on desktop |
| Layout | A narrow column (780 px), hairline dividers, lots of white space | Reading views keep the narrow column; workspaces (Review Studio, chat) go wider |
| Actions | A black pill button and a sticky bottom bar ("Your total ₹12,500 · See my plan") | Primary actions, the posting-kit bar, "Send to my phone" |
| Accent colour | None, just black and greys | None in the UI. Status uses a small dot plus a mono label. |

**Notes:**
- **Fonts are bundled inside the app** (all three are free open-source fonts), so the app works offline instead of loading them from Google Fonts like Pitch does.
- **Dark mode:** Pitch is light-only. The Studio is used for long sessions, so I propose an *optional* dark theme made from the same colours flipped (ink background, white text, same fonts and spacing). The default stays light.
- **Shared design kit:** the colours and fonts above plus a few common pieces (numbered tabs, sheet header, mono label, tinted card, row list, bottom bar, pill button) go into a small shared `magnm-design` folder. Both apps use it, so they stay siblings as either changes.
- **Social content isn't affected.** Cards and videos for social media keep the content brand (Palette v3 with lime). Only the software UI follows Pitch, unless you want the content brand changed too.

---

## 4. The AI team (7 agents, private to each user)

Models were chosen from **live tests on 2026-11-19** against the free NIM catalog (32 models). The source of truth is `config/models.json`.

| Agent | Job | Primary model (backup) | Why |
|-------|-----|------------------------|-----|
| CMO | Weekly plan, splits big requests into tasks, coordinates | `nvidia/nemotron-3-ultra-550b-a55b` (`mistralai/mistral-large-4-1t-a52b`) | Fast (4–5 s), reliable tool calls. It invents details when given no facts, so its prompts always include the product facts. |
| Researcher | Web search and browsing, repo facts, watch topics | `mistralai/mistral-large-4-1t-a52b` (`nemotron-3-ultra`) | Strong reasoning and tool use. Slow (90–130 s), which is fine for background research. |
| Writer | Posts, threads, captions, scripts, blogs | `minimaxai/minimax-m3.1-flash` (`nemotron-3-ultra`) | Best tone in both writing tests, stayed honest with and without facts, ~25 s |
| Designer | Cards, AI images, image edits | images: `black-forest-labs/flux.2-klein-4b` (2.3 s, clean line art); critique: `nemotron-3.5-super-vl` | FLUX.1-dev returned a blank image. The Kontext edit model isn't tested yet. |
| Video Producer | Scripts to finished videos | Writer model + Kokoro/Magpie + local render | |
| Reviewer | Product rules, facts, platform checks. Warns only. | `nvidia/nemotron-3.5-super-vl-120b-a12b` (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`) | Reads images correctly in 3–5 s, and is a different model family from the Writer |
| Analyst | Reads result screenshots, summarises | `nemotron-3.5-super-vl` (`nemotron-3-nano-omni`) | Needs vision |
| (fast tasks) | Sentiment, tagging, routing | `nvidia/nemotron-3.5-lightning-30b-a3b` (`nvidia/nemotron-3.5-bolt-e4b`) | 1–7 s. Needs strict output formats. |

**Not used, and why:**
- `deepseek-v4.1-flash` and `gpt-oss-20b` are end-of-life on NIM (HTTP 410).
- Kimi K3.1 Pro (top open model on Artificial Analysis), GLM-5.3 Flash and Muse Glimmer timed out on the free tier.
- Step-5 took 230 s.
- Gemma 4 31B is good but slow on the free tier today (55–95 s).

Re-run the bake-off when NIM's catalog changes. **Heavier work** (long blogs, video compositions, deep repo reading) can use your own connected agents through agy, e.g. Gemini 4 Hydrogen or Claude Opus 5.5 (Phase 7).

The engine can be changed per agent: NIM / Ollama / your Claude Code / Codex / Antigravity / Gemini CLI.

**Key order:** personal key → admin's shared keys (Supabase function) → Ollama.

---

## 5. Feature list (changes from v8 marked ★)

### 5.1 Desktop (Windows, macOS, Linux)
**Setup**
- Install from GitHub Releases. ★ **Sign in with Google** (invite-only). First-run setup. Automatic updates.

**Keys and engines**
- Personal AI and search keys, the shared-key allowance, model per agent, Ollama, SearXNG.
- ★ **Connect your coding agents:** auto-detect Claude Code, Codex, Antigravity and Gemini CLI; choose them as the engine per agent or task; see logs and a stop button.
- ★ **Magnm MCP server** (optional), so you can drive Magnm from your coding agent.

**Phone**
- ★ Pair over **Tailscale** by QR code (address + secret). See and unpair devices.

**Repos**
- Connect GitHub or a local folder, "shipped this week", approve facts, demo media.
- ★ **Product rules per repo** (status, what may be shown, blocked words, confidential).

**AI team**
- Team room, `@mentions`, one-to-one chats, attachments, live progress, pause or cancel, answering questions, editable memory.

**Research**
- Web research with sources, live browser view, market radar, competitor watch, watch topics, ideas.
- ★ **Logged-in social research** (opt-in): log in once in a visible window, separate research accounts, read-only, slow pace, one-click removal.
- ★ **Start social research by message** (e.g. "@researcher what did competitors post on LinkedIn this week?").
- ★ **Captcha and security-check help:**
  - a desktop notification with Solve now / Later / Skip
  - a live browser panel to solve it
  - auto-resume, a 30-minute fallback, and a 24-hour cool-down if challenges repeat

**Planning**
- Briefs, task board, weekly plan, task splitting.

**Create**
- Posts, cards, AI images, videos, blogs, repurposing, Reviewer warnings (★ now based on product rules).

**Review Studio**
- Comments (text, image pins, video timestamps), `@agent`, apply all, AI actions for text, images, video and blogs, suggestion mode, versions, platform previews, checklist.

**Posting and results**
- Posting kit, Send to my phone (over Tailscale, full quality), Mark as posted, calendar, Analyst screenshots, weekly summary.

**Data**
- Assets, offline use, export.

### 5.2 Android
- Google sign-in (native). ★ **Tailscale pairing.**
- Notifications, laptop status, start jobs on the laptop.
- ★ **Phone Lite mode (laptop off):**
  - chat with agents, write and rewrite posts, quick actions
  - AI images, read analytics screenshots
  - uses your personal key (secure Keystore) or the shared keys
  - syncs to the laptop later
- Voice-note briefs, share-in (ideas, analytics screenshots), light Review Studio.
- ★ **Start social research by message:** it runs on your laptop, with progress and results in the chat.
- ★ **Captcha alerts:**
  - push with Solve now / Later / Skip
  - a **full-screen live view of the laptop's browser** where your taps and typing go through
  - solving on one device clears the alert on the other
- Full-quality files from the laptop, Instagram Reels hand-off, share sheet, Mark as posted.
- Read digests, results and drafts (from the laptop when reachable; from the phone's own cache otherwise).

### 5.3 Admin (everything above, plus)
- Invite members (Google accounts), set roles, deactivate accounts.
- ★ **Team activity dashboard:**
  - each member's status (online, idle, working) and which agents are running now
  - a timeline of events (briefs, drafts, renders, research runs, posts)
  - activity per day and week
- ★ **Open any member's workspace (read-only):**
  - their chats, briefs, tasks, drafts and versions
  - Reviewer warnings, research notes
  - thumbnails, image and video previews
  - posted links and results
  - works even when their laptop is off
  - for full quality, ask the member directly
- ★ **Storage meter** (Supabase / B2). Video previews are auto-deleted after 60 days.
- Notifications for chosen events (push and desktop).
- Shared knowledge: brand kit, ★ **team product rules**, templates, agent instructions.
- Shared keys from multiple accounts, with daily limits per member, usage and health.
- Allowed models and engines.
- Audit log.

---

## 6. The plan

Effort estimates assume one developer working part-time.

### Phase 0 — Foundations (≈1 week)
- Live model list.
- Remove the fake answers.
- **Product rules** replace the hard-coded LazyLayout rule (migrate the current rule as LazyLayout's default).
- Clean-up.
- Monorepo: `engine/`, `supabase/`, `apps/web`, `apps/desktop`, `apps/mobile`.

### Phase 1 — Desktop app with the local engine (≈2–3 weeks)
- A Tauri 2 app with the Python engine.
- SQLite and files, keychain.
- A job runner.
- First-run setup.
- Data migration.
- Windows, macOS and Linux builds to GitHub Releases, with the auto-updater.

### Phase 2 — Agents, GitHub, web research (≈3–4 weeks) ★
- 7 agents with tools.
- Per-user GitHub App.
- The free search chain.
- Playwright + browser-use.
- Workflows.
- A Reviewer using product rules.
- A model bake-off.
- Memory.

### Phase 3 — New UI + Review Studio (≈3 weeks)
- **The `magnm-design` kit first:** Pitch's colours, fonts (bundled), numbered tabs, mono labels, tinted cards, bottom bar and pill buttons. Optional dark theme.
- All screens, comments, AI actions, suggestion mode, versions, previews, checklist, posting kit.

### Phase 3b — Pitch section (≈1 week)
- Bundle Magnm-Pitch (a pinned version, built in).
- Present mode (full screen, offline) on desktop and Android.
- Client proposals as data (per member, mirrored to the admin), with base prices as shared knowledge.
- Pitch's rules checked before saving.
- PDF export, client link, Send to my phone.
- "Draft a proposal" for the CMO and Writer (agents never change prices).
- **Decided 2026-11-19 (user):**
  - Pin Magnm-Pitch at `c44b7e2` (branch `feat/general-cost-planner`; Pitch's `main` does not have client routes or
    `/admin` yet). Re-pin with `scripts/sync_pitch.py` when Pitch changes.
  - The client-facing pitch keeps Pitch's exact look; only the information and the form contents change. The Studio
    shows the real Pitch page (vendored, unmodified) and feeds it data through a small adapter.
  - Members make their own proposals, as personalised as they like (client details, all of Pitch's text fields,
    which services each sheet shows, pre-selected choices, custom line items).
  - Base prices come from Pitch; the admin can edit base prices; members can tweak prices per proposal.
  - The admin is told who created each proposal and what pricing it has (queued locally until Phase 4).
  - Two kinds of links for clients: a **proposal link** (opens that proposal) and a **quote form link** (a blank
    planner the client fills in and submits; the submission arrives in the sender's app). Both need the hosted part
    in Phase 4.

**Milestone:** one person is fully productive.

### Phase 4 — Team connection (≈1–1.5 weeks; smaller now)
- **Google sign-in** (desktop through the system browser with PKCE) with an invite allowlist.
- Supabase tables: profiles, invites, **activity events**, laptop and agent status heartbeat, **plus the mirror** (chats, briefs, drafts and versions, research notes, posted links, results). Privacy rules: members see their own rows; the admin sees all.
- **Media mirror:**
  - thumbnails, plus image previews (WebP) and video previews (480p) generated on the laptop
  - uploaded through signed links from a Supabase function
  - a pluggable storage backend (Supabase Storage → Backblaze B2 for video when about 80% full)
  - a 60-day clean-up job for video previews, and an offline upload queue
- **Admin dashboard:** activity, a read-only view of any member's workspace, and the storage meter.
- Phase 4 grows to about **2 weeks** because of the mirror.
- Shared-key function (streaming, length caps, daily limits).
- Notifications via FCM.
- Shared knowledge sync (brand kit, team product rules, **Pitch base prices**: admin only).
- **Pitch links and quote forms** (from Phase 3b):
  - Supabase tables for published proposals (by a random `share_id`) and quote-form submissions; privacy rules so a
    client can read only the proposal behind its link and can only insert a submission.
  - A small change in Magnm-Pitch (its own repo and Vercel site): `/p/<share_id>` loads the published proposal and
    `/q/<share_id>` opens the quote form whose Submit sends the client's choices and contact details, and
    `/f/<share_id>` opens a final quotation read-only (agreed services locked, plans, PDF download). The default
    quotation uses `/q/` with each member's own share id. Same look.
  - The Studio publishes a proposal on "Copy link", and pulls submissions into the sender's Pitch section
    (with a notification).
  - Send the local `sync_outbox` (proposal created/updated with pricing) to the admin, with a notification.

**Milestone:** the whole team is on it.

### Phase 5 — Analyst (≈1 week)
- Reminders, screenshot reading with confirmation, weekly summary, learnings.

### Phase 6 — Android app (≈3 weeks)
- Capacitor app, native Google sign-in.
- **Tailscale pairing** (QR code with address + secret), same-Wi-Fi discovery.
- **Phone Lite mode:** direct AI calls with Keystore or shared keys, a synced context cache, sync back to the laptop.
- Push, share-in and share-out (Instagram Reels), voice-note briefs, light Review Studio.
- APK on GitHub Releases.

### Phase 7 — Connected coding agents (≈1–2 weeks)
- Detect installed CLIs.
- ACP client, with a headless CLI fallback (`claude -p`, `codex exec`, `agy -p` in a pseudo-terminal, Gemini CLI).
- Engine picker per agent or task.
- A sandbox workspace with repos mounted read-only, explicit permission modes, logs and a stop button.
- Optional Magnm MCP server.

### Phase 8 — Logged-in social research (≈1–2 weeks, opt-in)
- Log in once in a visible window, then save an encrypted per-platform browser profile.
- A **read-only tool layer** (posting, liking, following, commenting and DMs blocked in code).
- Pacing limits and stop-on-challenge.
- Research recipes (competitor posts, trending topics, replies to our posts).
- **Chat-started runs** from desktop or phone (the phone goes to the laptop over Tailscale, with a queue if the laptop is offline).
- **Challenge detection:** captcha, verification code, "confirm it's you", or logged out. The run pauses and the agent continues with other sources.
- **Alerts on both devices:** a desktop notification plus FCM push, with Solve now / Later / Skip. There's no screenshot in the push; the phone fetches it over Tailscale.
- **Live browser view:**
  - the laptop streams the agent's browser page to Magnm, on desktop or phone
  - your taps, clicks and typing are sent back to that page
  - on desktop, an "open in full window" option
- **Auto-resume** when the challenge clears, with the alert cleared on every device. A 30-minute timeout marks the report as partly incomplete.
- **Account-health guard:** 2 or more challenges in a day pauses that platform for 24 hours. Challenges are logged in the activity feed.
- Phase 8 grows to about **2–2.5 weeks**.

### Phase 9 (future, optional) — Posting via official APIs
- LinkedIn personal ("Share on LinkedIn", self-serve).
- Instagram Graph API (Standard Access).
- X pay-per-use.
- Always shows the exact post and needs a click to confirm.

### Phase 10 — Distribution, security, reliability (ongoing)
- Install guides.
- Backups of the signing keys.
- Key and quota alerts, plus alerts when the NIM catalog changes.
- A smoke test.

---

## 7. Timeline

```
Week 1        Phase 0  foundations + product rules
Weeks 2-4     Phase 1  desktop app + local engine + releases
Weeks 4-8     Phase 2  agents, GitHub, web research            <- biggest payoff
Weeks 7-10    Phase 3  new UI (Pitch design language) + Review Studio  -> one person fully productive
Week 10       Phase 3b Pitch section (present mode, proposals, PDF)
Weeks 10-12   Phase 4  Google sign-in, mirror, shared keys     -> whole team + admin sees everything
Week 12       Phase 5  Analyst
Weeks 12-15   Phase 6  Android app (Tailscale + Phone Lite)
Weeks 15-16   Phase 7  connected coding agents
Weeks 16-18   Phase 8  logged-in social research + captcha help (opt-in)
later         Phase 9  official-API posting (optional)
ongoing       Phase 10
```

## 8. Decisions
**Decided:**
- Supabase free plan
- **the admin sees everything** (mirror of text, chats and previews; originals stay on laptops)
- storage: Supabase first, Backblaze B2 overflow for video previews; video previews kept 60 days
- Google sign-in only
- Tailscale for phone ↔ laptop (Iroh as plan B)
- **separate research accounts, read-only social browsing** (posting, commenting, liking, following and DMs blocked in code)
- **LazyLayout stays "In development + Teasers only"**
- installers on GitHub Releases
- Windows, macOS, Linux and Android
- no paid developer accounts
- no Camoufox

- **Studio UI follows Magnm-Pitch's design language, with an optional dark mode** (light is the default)
- **Social content keeps its own brand** (Palette v3 with lime); it does not follow Pitch
- **Pitch section** inside the Studio (present mode, proposals, PDF)

**Nothing open.** Ready to start Phase 0.

## Sources
- Claude plan usage with `claude -p` and the Agent SDK: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan , https://autonomee.ai/blog/claude-code-terms-of-service-explained/
- Codex CLI: https://learn.chatgpt.com/docs/cli/reference , https://inventivehq.com/blog/codex-cli-chatgpt-login-vs-api-key
- Antigravity CLI headless: https://antigravity.google/docs/cli/headless , https://github.com/google-antigravity/antigravity-cli/issues/76
- Agent Client Protocol: https://agentclientprotocol.com/updates.md , https://www.morphllm.com/agent-client-protocol , https://mcp.directory/blog/agent-client-protocol-vs-mcp-2026
- Social automation risk: https://www.blotato.com/research/social-media-automation-rulebook , https://linkedinpreview.com/blog/linkedin-automation-guide-2026 , https://www.balistro.com/blog/instagram-and-linkedin-automation-whats-safe-and-what-gets-you-banned
- LinkedIn self-serve posting: https://opentweet.io/answers/can-i-post-to-linkedin-without-partner-approval , https://zernio.com/blog/linkedin-posting-api
- Tailscale plans and terms: https://tailscale.com/pricing , https://tailscale.com/legal/terms
- Supabase email limits: https://supabase.com/docs/guides/auth/rate-limits , https://flaviocopes.com/supabase-email-rate-limit/
- R2 and Indian payments: https://developers.cloudflare.com/billing/troubleshoot/troubleshoot-failed-payments/ , https://community.cloudflare.com/t/cloudflare-payment-is-failing-possibly-because-of-indias-rbi-e-mandate-problems/573293 , https://agentdeals.dev/backblaze-b2-vs-cloudflare-r2
- Free storage without a card: https://www.backblaze.com/cloud-storage/pricing , https://filebase.com/free/ , https://freetier.co/articles/free-object-storage-tiers-2026
- Earlier sources: see previous versions in git history.
