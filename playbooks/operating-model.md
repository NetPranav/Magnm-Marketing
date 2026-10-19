# Operating model v2 (CEO, 2026-10-18)

Goal: get Magnm and Cero recognised worldwide, by any means that is legal, honest and not spam. Spend as few Claude tokens as possible. Every task goes to the cheapest place that can do it properly.

## 1. Who does what, where

| Work | Where it runs | Why |
|------|---------------|-----|
| Daily decisions, plan, approvals, publishing | **CMO**: one scheduled Claude run a day (08:46 IST) | needs judgement plus the browser sign-ins |
| Market/trend research, competitor scans, strategy refresh, visual-reference scouting (Pinterest etc.), first drafts of copy, Growth Lead research | **agy** (Antigravity CLI on the CEO's Mac) via `queue/agy/`; the CEO starts the runner by hand (attended mode) | uses Google's quota, not Claude tokens |
| Video renders (HyperFrames/ffmpeg) | **agy** via `queue/agy/`, only for a script the Reviewer approved | rendering was the biggest token cost |
| Text + image post images | `templates/card.py` (no model calls) | free |
| Claims check and final quality gate | **Reviewer** subagent (Claude) | the one step that must stay strict |
| Video scripts and storyboards | Writer (agy draft) then Reviewer (Claude) | scripts are fine; renders are not |

## 2. Token budget for the daily CMO run (hard caps)
- 1 run a day. Sunday's run also does the weekly mode. No other schedules.
- At most **2 subagent spawns** per run (normally the Reviewer; sometimes the Writer for an urgent fix).
- **Never** render, encode or inspect video frame by frame. Never open `reference/` videos or contact sheets.
- Read state with one call: `bash scripts/cmo-digest.sh` (prints board, plan, last log, new agy results, metrics notes).
- Metrics: read pages as text (`get_page_text`/`read_page`), not screenshots. At most 6 screenshots per run, only when posting.
- Research of any kind goes to agy as a task file. The CMO reads agy's result files the next day.
- Stop at about 40 tool calls. Write the log and leave the rest for tomorrow.

## 3. Pipeline across days (plan 2+ days ahead)
- Day N: the CMO picks a slot and topic (the Advisor's view comes from an agy draft, `advisor-draft` task) and queues agy tasks: research, copy draft, card spec.
- Day N+1: the CMO reads agy's output, renders the card with `templates/card.py`, and runs the Reviewer. APPROVE leads to publish/schedule. REJECT leads to a fix task for agy, or one Writer spawn if the slot is today.
- Video: the script is drafted by agy and reviewed by Claude. Then a `render` task goes to agy, and the Reviewer checks one contact sheet per ratio. The CEO uploads videos, or approves the upload in chat.

## 4. Publishing authority
- The CEO delegated the decision on **text and text+image posts** to the CMO: what to post, where and when. Inputs are the Advisor's view and the Reviewer's APPROVE, which is required.
- Mechanics: Claude's safety rules require an explicit "yes" in chat for each publish or schedule click, and a standing instruction cannot switch that off. So the CMO prepares everything up to the final click, ideally in the platform's own scheduler set to the best time. It then sends **one push notification a day** listing the exact posts (platform, time, text, image). The CEO replies `yes` (all), `yes 1,3`, or `no 2 <reason>`. The CMO then clicks Post or Schedule. Unanswered posts stay `AWAITING YES` and are never posted silently.
- Video posts, posts from the CEO's **personal** accounts, replies to people, DMs and anything involving money (bounties) always need the CEO's explicit yes for that item.
- agy never publishes and never touches social sign-ins.

## 5. Recognition worldwide: what is allowed
Allowed and encouraged: Show HN (only a version people can try, no upvote asking), Reddit posts that follow each subreddit's self-promotion rules, awesome-lists PRs, dev.to/Hashnode tutorials, a maintainer voice from the CEO's personal account, open-source bounties (budget approved), conference CFPs, integrations with popular tools, newsletters, creator outreach drafts the CEO sends, timely posts on news about AI agents running commands.
Never: fake accounts, bought followers/likes/views, astroturfing or sock puppets, asking for upvotes, mass unsolicited DMs or tagging, invented metrics/testimonials, competitor misrepresentation, any claim outside `research/product-facts.md`, anything about LazyLayout beyond the CEO-approved teaser line.

## 6. Growth ideas (weekly, Sunday)
The Growth Lead (as an agy task) proposes **3 non-content growth experiments** every week. At least one may cost money. Each states cost, expected payoff, risk, what we need from the CEO, and how we'd measure it. The CMO picks at most one, and the CEO approves any spend.

## 7. Weekly full-team run (CEO, 2026-10-18)
Every Sunday 09:52 IST a separate scheduled run executes the whole team with no token cap (agents/cmo.md "Weekly full-team mode"): Analyst, Listener, Researcher, Strategist, Growth Lead, Advisor, Writer, Designer, Reviewer, Community, as Claude subagents. Section 2's caps apply only to the daily run. Video renders still need the CEO's explicit "render <id>".
