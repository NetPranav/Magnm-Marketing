// The seven configured agents on the phone, and the one decision the phone makes about a request: can the agent do
// it with text alone (Phone Lite answers), or does it need one of the agent's laptop tools (queue it for the laptop)?
//
// The decision is made here from the agent's explicit tool list (config/agents.json, bundled at build time) and a
// fixed table of what a request for each tool looks like. It uses only the agent id and the owner's own words.
// The model's text is never read for it and is never an instruction to the phone.

import agentsConfig from '@magnm/config/agents.json';

export interface ConfiguredAgent {
  id: string;
  name: string;
  code: string;
  role: string;
  tools: string[];
}

const AGENT_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

function loadAgents(): ConfiguredAgent[] {
  const raw = (agentsConfig as { agents?: unknown }).agents;
  if (!Array.isArray(raw)) return [];
  const out: ConfiguredAgent[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const a = item as Record<string, unknown>;
    if (typeof a.id !== 'string' || !AGENT_ID_RE.test(a.id)) continue;
    if (typeof a.name !== 'string' || !a.name.trim() || typeof a.role !== 'string' || !a.role.trim()) continue;
    const tools = Array.isArray(a.tools) ? a.tools.filter((t): t is string => typeof t === 'string') : [];
    out.push({ id: a.id, name: a.name.trim(), code: typeof a.code === 'string' ? a.code : a.id.toUpperCase(), role: a.role.trim(), tools });
  }
  return out;
}

const AGENTS = loadAgents();

/** The configured agents, in their configured order. */
export function configuredAgents(): ConfiguredAgent[] {
  return AGENTS.map((a) => ({ ...a, tools: [...a.tools] }));
}

/** The agent with exactly this configured id (not a legacy id, not another spelling), or null. */
export function getConfiguredAgent(id: string): ConfiguredAgent | null {
  if (typeof id !== 'string' || !AGENT_ID_RE.test(id)) return null;
  const found = AGENTS.find((a) => a.id === id);
  return found ? { ...found, tools: [...found.tools] } : null;
}

/**
 * Tools that need nothing from the laptop to answer: the agent can do the same job in text. A request for one of
 * these never queues anything (the phone already holds the product rules for check_text, and an agent that wants
 * to ask the owner something just asks in its reply).
 */
export const PHONE_HANDLED_TOOLS: readonly string[] = ['ask_user', 'check_text', 'read_product_facts'];

/**
 * What a request for each laptop-only tool looks like. A request matches a tool only if the agent really has it.
 * Patterns are plain, anchored on word boundaries and free of nested repetition, so a long text cannot stall them.
 */
export const LAPTOP_TOOL_INTENTS: Record<string, RegExp[]> = {
  // Web and social research.
  web_search: [/\b(?:search|google|look\s?up|browse|news|trending|competitors?|reddit|hacker\s?news|on the web|web\s?(?:search|page|site)|internet)\b/i, /\bhttps?:\/\/\S+/i, /\bwww\.\S+/i],
  read_page: [/\bhttps?:\/\/\S+/i, /\bwww\.\S+/i, /\b(?:read|open|summari[sz]e)\s+(?:this|that|the)\s+(?:page|link|url|article|site)\b/i],
  screenshot_page: [/\bscreenshot\s+(?:of\s+)?(?:this|that|the)\s+(?:page|site|link|url)\b/i, /\bhttps?:\/\/\S+/i],
  social_research: [/\bsocial\s+(?:media\s+)?(?:research|scan|listening)\b/i, /\b(?:scan|listen\s+(?:to|on))\s+(?:reddit|twitter|x|linkedin|instagram|hacker\s?news)\b/i, /\breddit\b/i, /\bsentiment\b/i],
  search_research: [/\b(?:research|market\s+signals?|saved\s+research|research\s+notes?)\b/i, /\bwhat\s+(?:are\s+)?(?:people|developers|users)\s+(?:saying|think)\b/i],
  save_research_note: [/\b(?:save|store|file|add)\b[^.\n]{0,40}\bresearch\s+notes?\b/i],
  // Repos.
  read_repo_changes: [/\b(?:repos?|repositor(?:y|ies)|commits?|pull\s+requests?|prs?|changelog|release\s+notes?|merged|github|codebase|source\s+code|code\s+changes)\b/i],
  read_repo_file: [/\b(?:repos?|repositor(?:y|ies)|codebase|source\s+code|readme)\b/i],
  shipped_this_week: [/\b(?:shipped|ships|what\s+shipped|what\s+we\s+shipped|released\s+this\s+week)\b/i],
  list_repo_media: [/\brepo\s+(?:media|images?|screenshots?|assets?)\b/i, /\b(?:b-?roll|repo\s+media)\b/i],
  list_repos: [/\b(?:repos?|repositor(?:y|ies)|connected\s+repos?)\b/i],
  draft_repo_facts: [/\b(?:draft|propose|write)\b[^.\n]{0,30}\b(?:repo\s+)?facts?\b/i],
  propose_fact: [/\b(?:propose|add|suggest)\b[^.\n]{0,30}\bfacts?\b/i],
  // Drafts and review.
  list_content: [/\b(?:my|our|the|latest|recent|saved|existing)\s+drafts?\b/i, /\bcnt_[A-Za-z0-9_-]+\b/],
  read_content: [/\b(?:my|our|the|latest|recent|saved|existing)\s+drafts?\b/i, /\bcnt_[A-Za-z0-9_-]+\b/],
  write_draft: [/\b(?:save|store|file|add|put)\b[^.\n]{0,40}\b(?:as|to|in|into)\b[^.\n]{0,20}\bdrafts?\b/i, /\bsave\s+(?:it|this|that)\b/i],
  write_review: [/\b(?:save|file|record|attach)\b[^.\n]{0,30}\breview\b/i],
  // Images.
  generate_image: [/\b(?:images?|pictures?|photos?|illustrations?|graphics?|thumbnails?|logos?|banners?|posters?|infographics?|visuals?|mockups?)\b/i],
  render_card: [/\b(?:cards?|quote\s+cards?|banners?|thumbnails?|graphics?)\b/i],
  check_image: [/\b(?:images?|pictures?|photos?|screenshots?|thumbnails?|graphics?)\b/i],
  // Numbers.
  record_metrics: [/\b(?:record|log|save|store|enter|add)\b[^.\n]{0,40}\b(?:metrics?|numbers?|stats|impressions|likes|views|engagement|clicks)\b/i],
  read_metrics: [/\b(?:metrics?|stats|analytics|performance\s+numbers|impressions)\b/i],
  // Delegation.
  delegate: [/\b(?:delegate|hand\s+(?:it|this|that)\s+off|assign\s+(?:this|it|that|to))\b/i, /\b(?:have|ask|tell)\s+the\s+(?:writer|designer|reviewer|researcher|analyst|video\s+producer|cmo)\b/i],
  // Memory and reminders.
  remember: [/\bremember\s+(?:that|this|to)\b/i, /\b(?:save|add)\s+(?:to|in)\s+memory\b/i],
  recall_memory: [/\b(?:recall|from\s+memory|what\s+did\s+we\s+(?:decide|say))\b/i],
  set_reminder: [/\bremind\s+me\b/i, /\b(?:set|add|create)\s+(?:a\s+)?reminders?\b/i],
  list_reminders: [/\b(?:my|our|the|upcoming|open)\s+reminders?\b/i],
  // Pitch.
  list_pitch_catalog: [/\b(?:pitch|price\s+list|pricing\s+catalog|service\s+catalog)\b/i],
  draft_proposal: [/\b(?:proposal|quotation|quote\s+for)\b/i],
};

/** Every tool the phone has an explicit decision about. A tool missing here fails the test, never silently skips. */
export const ALL_CLASSIFIED_TOOLS: readonly string[] = [...Object.keys(LAPTOP_TOOL_INTENTS), ...PHONE_HANDLED_TOOLS];

export interface RouteDecision {
  route: 'phone' | 'laptop';
  /** The agent's tools that made the request need the laptop, from the fixed table. Empty for 'phone'. */
  tools: string[];
}

/**
 * Decide where a request is answered. Depends only on the agent id and the owner's text: an agent that is not
 * configured throws, a request that matches none of the agent's laptop tools is answered on the phone.
 */
export function decideRoute(agentId: string, text: string): RouteDecision {
  const agent = getConfiguredAgent(agentId);
  if (!agent) {
    throw new Error(`"${String(agentId).slice(0, 40)}" is not a configured agent.`);
  }
  const body = typeof text === 'string' ? text : '';
  const tools = agent.tools.filter((tool) => LAPTOP_TOOL_INTENTS[tool]?.some((re) => re.test(body)));
  return tools.length > 0 ? { route: 'laptop', tools } : { route: 'phone', tools: [] };
}
