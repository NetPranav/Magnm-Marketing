# Skill: Technical Blog Writing

When writing long-form engineering articles and deep dives:
- Adopt a developer-first, code-centric voice: explain how and why architectural decisions were made rather than writing surface-level marketing prose.
- Target 1,200 to 2,000 words with clear section headings and a concise TL;DR near the top.
- Ground every command, API behavior, file format, and architectural detail strictly in `read_product_facts`, `read_repo_changes`, and `search_research`. Never invent benchmarks, performance numbers, security guarantees, or testimonials.
- When helpful, include a compact repurposed pack in the draft notes or follow-up items: a short X thread summary, a LinkedIn engineering takeaway, or a punchy card headline idea.
- Save the article via `write_draft` with `type="blog"`, `platform="blog"`, then run `check_text` and fix any warnings before finishing.
