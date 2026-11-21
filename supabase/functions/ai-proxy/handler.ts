import {
  AllowedModel,
  estimateTokens,
  extractModelName,
  validateChatRequest,
} from "./validate.ts";
import { classifyUpstreamError, KeyPool, MAX_KEY_ATTEMPTS } from "./keys.ts";
import { createSsePassthroughStream } from "./sse.ts";

export const DEFAULT_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1";
export const DEFAULT_UPSTREAM_TIMEOUT_MS = 120_000;

export interface QuotaCheckResult {
  allowed: boolean;
  reason: string | null;
  requests_left: number;
  tokens_left: number;
}

export interface ProxyDeps {
  getUser: (token: string) => Promise<{ id: string } | null>;
  isProfileActive: (userId: string) => Promise<boolean>;
  getAllowedModel: (model: string) => Promise<AllowedModel | null>;
  checkAndCount: (ownerId: string, estimatedTokens: number) => Promise<QuotaCheckResult>;
  recordUsage: (ownerId: string, promptTokens: number, completionTokens: number) => Promise<void>;
  reportKey: (label: string, status: number, error: string | null, cooldownSeconds: number) => Promise<void>;
  keyPool: KeyPool;
  fetchFn: typeof fetch;
  nimBaseUrl?: string;
  upstreamTimeoutMs?: number;
  logFn?: (line: string) => void;
}

function jsonError(status: number, error: string, extra: Record<string, unknown> = {}): Response {
  return new Response(JSON.stringify({ error, ...extra }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function extractBearerToken(req: Request): string | null {
  const auth = req.headers.get("authorization") ?? "";
  const match = auth.match(/^Bearer\s+(.+)$/i);
  if (!match) return null;
  const token = match[1].trim();
  return token.length > 0 ? token : null;
}

function extractNonStreamUsage(
  bodyText: string,
  textChars: number,
): { promptTokens: number; completionTokens: number } {
  const fallbackPrompt = Math.ceil(Math.max(0, textChars) / 4);
  try {
    const parsed = JSON.parse(bodyText) as Record<string, unknown>;
    const usage = typeof parsed.usage === "object" && parsed.usage !== null
      ? parsed.usage as Record<string, unknown>
      : null;
    const pt = usage && typeof usage.prompt_tokens === "number" && Number.isFinite(usage.prompt_tokens)
      ? Math.max(0, Math.trunc(usage.prompt_tokens))
      : null;
    const ct = usage && typeof usage.completion_tokens === "number" && Number.isFinite(usage.completion_tokens)
      ? Math.max(0, Math.trunc(usage.completion_tokens))
      : null;

    if (pt !== null && ct !== null) {
      return { promptTokens: pt, completionTokens: ct };
    }

    let contentChars = 0;
    if (Array.isArray(parsed.choices)) {
      for (const choice of parsed.choices) {
        const msg = (choice as Record<string, unknown>)?.message as Record<string, unknown> | undefined;
        if (msg && typeof msg.content === "string") {
          contentChars += msg.content.length;
        }
      }
    }
    return {
      promptTokens: pt ?? fallbackPrompt,
      completionTokens: ct ?? Math.ceil(contentChars / 4),
    };
  } catch {
    return {
      promptTokens: fallbackPrompt,
      completionTokens: Math.ceil(bodyText.length / 4),
    };
  }
}

export function createProxyHandler(deps: ProxyDeps) {
  const nimBaseUrl = (deps.nimBaseUrl || DEFAULT_NIM_BASE_URL).replace(/\/+$/, "");
  const timeoutMs = deps.upstreamTimeoutMs ?? DEFAULT_UPSTREAM_TIMEOUT_MS;
  const log = deps.logFn ?? console.log;

  return async function handleRequest(req: Request): Promise<Response> {
    if (req.method !== "POST") {
      return jsonError(405, "Method not allowed.");
    }

    const token = extractBearerToken(req);
    if (!token) {
      return jsonError(401, "Missing authorization token.");
    }

    const user = await deps.getUser(token);
    if (!user) {
      return jsonError(401, "Unauthorized.");
    }

    const active = await deps.isProfileActive(user.id);
    if (!active) {
      return jsonError(403, "Your account is not active.");
    }

    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return jsonError(400, "Invalid JSON body.");
    }

    const requestedModel = extractModelName(body);
    if (!requestedModel) {
      return jsonError(400, "Field 'model' is required.");
    }

    const allowedModel = await deps.getAllowedModel(requestedModel);
    const validated = validateChatRequest(body, allowedModel);
    if (!validated.ok) {
      return jsonError(400, validated.error);
    }

    const { payload, model, maxTokens, textChars, stream } = validated.value;
    const estimated = estimateTokens(textChars, maxTokens);
    const quota = await deps.checkAndCount(user.id, estimated);
    if (!quota.allowed) {
      log(
        `ai-proxy user=${user.id.slice(0, 8)} model=${model} status=429 tokens=0+0 key=none reason=${quota.reason}`,
      );
      return jsonError(
        429,
        "Daily AI limit reached. It resets at midnight UTC. Ask your admin for a higher limit.",
        {
          reason: quota.reason,
          requests_left: quota.requests_left,
          tokens_left: quota.tokens_left,
        },
      );
    }

    const keys = await deps.keyPool.getKeys();
    if (keys.length === 0) {
      return jsonError(503, "The team has no shared AI keys yet. Ask your admin.");
    }

    const candidates = deps.keyPool.pickCandidates(keys, MAX_KEY_ATTEMPTS);
    if (candidates.length === 0) {
      return jsonError(503, "All shared AI keys are temporarily in cooldown. Please try again shortly.");
    }

    const upstreamPayload = stream
      ? { ...payload, stream: true, stream_options: { include_usage: true } }
      : payload;
    const upstreamUrl = `${nimBaseUrl}/chat/completions`;
    const serializedBody = JSON.stringify(upstreamPayload);

    let lastStatus = 502;
    let lastError = "Upstream AI request failed.";
    let lastLabel = "none";

    for (const candidate of candidates) {
      lastLabel = candidate.label;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);

      let resp: Response;
      try {
        resp = await deps.fetchFn(upstreamUrl, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${candidate.apiKey}`,
            "Content-Type": "application/json",
            Accept: stream ? "text/event-stream" : "application/json",
          },
          body: serializedBody,
          signal: controller.signal,
        });
      } catch (err) {
        clearTimeout(timer);
        const isAbort = err instanceof Error && err.name === "AbortError";
        lastStatus = isAbort ? 504 : 502;
        lastError = isAbort ? "Upstream AI request timed out." : "Upstream AI network error.";
        deps.keyPool.markCooldown(candidate.label, 30);
        await deps.reportKey(candidate.label, lastStatus, lastError, 30);
        continue;
      }
      clearTimeout(timer);

      if (resp.ok) {
        deps.keyPool.clearCooldown(candidate.label);
        await deps.reportKey(candidate.label, resp.status, null, 0);

        if (stream && resp.body) {
          const passthrough = createSsePassthroughStream(async (sseUsage) => {
            const promptTokens = sseUsage.promptTokens ?? Math.ceil(textChars / 4);
            const completionTokens = sseUsage.completionTokens ?? Math.ceil(sseUsage.textChars / 4);
            await deps.recordUsage(user.id, promptTokens, completionTokens);
            log(
              `ai-proxy user=${user.id.slice(0, 8)} model=${model} status=${resp.status} tokens=${promptTokens}+${completionTokens} key=${candidate.label}`,
            );
          });
          return new Response(resp.body.pipeThrough(passthrough), {
            status: resp.status,
            headers: {
              "Content-Type": "text/event-stream",
              "Cache-Control": "no-cache",
            },
          });
        }

        const respText = await resp.text();
        const usage = extractNonStreamUsage(respText, textChars);
        await deps.recordUsage(user.id, usage.promptTokens, usage.completionTokens);
        log(
          `ai-proxy user=${user.id.slice(0, 8)} model=${model} status=${resp.status} tokens=${usage.promptTokens}+${usage.completionTokens} key=${candidate.label}`,
        );
        return new Response(respText, {
          status: resp.status,
          headers: { "Content-Type": "application/json" },
        });
      }

      const errText = await resp.text().catch(() => "");
      const action = classifyUpstreamError(resp.status, resp.headers.get("retry-after"), errText);
      if (action.retryNextKey) {
        deps.keyPool.markCooldown(candidate.label, action.cooldownSeconds);
        if (action.reportStatus !== null) {
          await deps.reportKey(candidate.label, action.reportStatus, action.reportError, action.cooldownSeconds);
        }
        lastStatus = resp.status;
        lastError = action.reportError || `Upstream error (${resp.status}).`;
        continue;
      }

      log(
        `ai-proxy user=${user.id.slice(0, 8)} model=${model} status=${resp.status} tokens=0+0 key=${candidate.label}`,
      );
      const contentType = resp.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        return new Response(errText || JSON.stringify({ error: `Upstream error (${resp.status}).` }), {
          status: resp.status,
          headers: { "Content-Type": "application/json" },
        });
      }
      return jsonError(resp.status, errText.trim().slice(0, 300) || `Upstream error (${resp.status}).`);
    }

    log(
      `ai-proxy user=${user.id.slice(0, 8)} model=${model} status=${lastStatus} tokens=0+0 key=${lastLabel}`,
    );
    return jsonError(lastStatus, lastError);
  };
}
