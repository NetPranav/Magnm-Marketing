# Cero bounty pilot: plan (Growth Lead, 2026-10-18)
Status: **AWAITING CEO** (budget + rules). No public post about bounties until the CEO approves.
Brief: briefs/BRIEF-20261018-bounty_growth.md · Idea source: CEO (suggested by a friend)

## Why it can work
- It gives developers a concrete reason to clone Cero, run it and talk about it, which is earned attention rather than ads.
- It fills a real gap. Cero's own README says Linux and Windows have **not yet** been run by hand or with the 120-request headless suite (README test table). `docs/releases/linux.md` says Linux packages "were not run on a Linux desktop for this release". Bounties turn that weakness into community testing.
- It's a story we can post honestly and repeatedly: issue opened, someone claims it, fix merged, paid.

## Candidate bounty issues (all real, from the Cero repo)
| # | Issue to open | Acceptance criteria | Tier |
|---|---------------|---------------------|------|
| B1 | Linux desktop test of the release packages (.deb/AppImage, `.flow` file type, icon registration) | Written test report following docs/LINUX_TEST_REPORT.md format, screenshots, distro + version; bugs filed separately | M |
| B2 | Windows hand test of the release build plus the headless request suite | Report with the pass/fail table like the macOS row in the README; logs attached | M |
| B3 | Windows support for `system.info` (tools/system/info README: "Windows not yet supported") | PR with tests passing on Windows CI or a recorded run | M |
| B4 | Windows support for `wifi.scan` and `bluetooth.list` | Same as B3 | M each |
| — | Docs: replace old `Sentinal-Terminal` links (e.g. docs/releases/linux.md) | `good first issue`, **no money**; use it as a warm-up | 0 |

## Budget (CEO to choose; amounts are suggestions)
- Option A, small pilot: B1 + B2 at about ₹1,500 ($18) each, plus B3 at ₹2,500 ($30). Total cap about **₹5,500**.
- Option B, recognition push: B1-B4 at ₹2,500-4,000 each. Total cap about **₹15,000**.
- Global contributors think in USD. Show both, and pay in the contributor's currency through the platform.

## Platform choice (to verify; an agy task is queued)
- **Algora:** comment `/bounty $X` on a GitHub issue. The contributor puts `/claim #N` in the PR, and you pay through Stripe from Algora's dashboard (docs.algora.io/bounties/workflow). Fees and supported countries are not on that page; agy is checking.
- **Opire:** a similar GitHub bounty bot. Terms still to be checked.
- **Direct:** UPI for Indian contributors, PayPal or Wise abroad, with written terms in the issue. This is the simplest option while the company is unregistered. Keep receipts, and ask a CA about tax treatment. (Not legal or tax advice.)

## Rules draft (goes into each bounty issue)
1. One reward per issue. It goes to the first PR or report that meets the acceptance criteria and is merged or accepted by a maintainer.
2. Comment to claim before starting. A claim lapses after 7 days without progress.
3. Reports must contain real logs and screenshots. Generated or unverified submissions are closed.
4. Payout within 7 days of merge, through the platform or a method agreed in the issue.
5. Maintainers decide. Amounts can't be split or negotiated after merge. Void where payments are restricted by the provider.

## Launch sequence (after approval)
1. Open the issues with the `bounty` label, plus a pinned "Cero bounty pilot" issue listing them.
2. Day 1: a text+image post on X and LinkedIn (card: "Get paid to break Cero on Linux"), and a personal post from the CEO.
3. Share in communities only where the rules allow (check r/opensource, r/linux, r/commandline self-promotion rules first). Add a mention in the Show HN post once it's ready.
4. Post each merge as proof ("B1 done by @user, paid"), but only after the payment has gone out.

## Risks and mitigations
- **Spam PRs:** clear acceptance criteria, the claim-first rule, and closing low-effort PRs quickly.
- **Review time:** cap at 3 open bounties. The CEO or a teammate owns reviews.
- **Small amounts can look cheap:** frame it as a "testing bounty pilot", not big money.
- **Unregistered company:** pay as an individual, with written terms and kept records.

## Decisions for the CEO
1. Budget cap (A, B or your own figure).
2. Platform: Algora, Opire or direct.
3. Public repo: Cero-Terminal or Sentinal-Terminal (the docs still link to the old name).
4. Who reviews bounty PRs.
