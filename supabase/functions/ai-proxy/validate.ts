export const MAX_MESSAGES = 200;
export const MAX_TEXT_CHARS = 200_000;
export const MAX_IMAGE_DATA_BYTES = 4 * 1024 * 1024;
export const DEFAULT_MAX_TOKENS = 1500;

const ALLOWED_FIELDS = new Set([
  "model",
  "messages",
  "max_tokens",
  "temperature",
  "top_p",
  "stream",
  "tools",
  "tool_choice",
  "stop",
]);

export interface AllowedModel {
  model: string;
  enabled: boolean;
  max_tokens_cap: number;
}

export interface ValidatedRequest {
  payload: Record<string, unknown>;
  model: string;
  maxTokens: number;
  textChars: number;
  stream: boolean;
}

export type ValidationResult =
  | { ok: true; value: ValidatedRequest }
  | { ok: false; error: string };

function isPlainObject(val: unknown): val is Record<string, unknown> {
  return typeof val === "object" && val !== null && !Array.isArray(val);
}

export function extractModelName(body: unknown): string | null {
  if (!isPlainObject(body)) return null;
  if (typeof body.model !== "string") return null;
  const trimmed = body.model.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function estimateTokens(textChars: number, maxTokens: number): number {
  return Math.ceil(Math.max(0, textChars) / 4) + Math.max(0, maxTokens);
}

export function validateChatRequest(
  body: unknown,
  allowedModel: AllowedModel | null,
): ValidationResult {
  if (!isPlainObject(body)) {
    return { ok: false, error: "Request body must be a JSON object." };
  }

  const model = extractModelName(body);
  if (!model) {
    return { ok: false, error: "Field 'model' is required." };
  }
  if (!allowedModel || !allowedModel.enabled) {
    return { ok: false, error: "Requested model is not allowed or is disabled." };
  }

  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return { ok: false, error: "Field 'messages' must be a non-empty array." };
  }
  if (body.messages.length > MAX_MESSAGES) {
    return { ok: false, error: `At most ${MAX_MESSAGES} messages are allowed.` };
  }

  let textChars = 0;
  let imageBytes = 0;

  for (const msg of body.messages) {
    if (!isPlainObject(msg)) {
      return { ok: false, error: "Each message must be an object." };
    }
    if (typeof msg.role !== "string" || msg.role.trim().length === 0) {
      return { ok: false, error: "Each message must include a non-empty 'role'." };
    }

    const content = msg.content;
    if (typeof content === "string") {
      textChars += content.length;
    } else if (Array.isArray(content)) {
      for (const part of content) {
        if (typeof part === "string") {
          textChars += part.length;
        } else if (isPlainObject(part)) {
          if (part.type === "text") {
            if (typeof part.text !== "string") {
              return { ok: false, error: "Text content parts must have a string 'text' field." };
            }
            textChars += part.text.length;
          } else if (part.type === "image_url") {
            const rawUrl = typeof part.image_url === "string"
              ? part.image_url
              : isPlainObject(part.image_url)
              ? part.image_url.url
              : undefined;
            if (typeof rawUrl !== "string" || rawUrl.length === 0) {
              return { ok: false, error: "Image content parts must provide a valid URL." };
            }
            if (rawUrl.startsWith("data:")) {
              imageBytes += rawUrl.length;
            }
          }
        } else {
          return { ok: false, error: "Invalid message content part." };
        }
      }
    } else if (content !== undefined && content !== null) {
      return { ok: false, error: "Message 'content' must be a string, array, or null." };
    }

    if (textChars > MAX_TEXT_CHARS) {
      return { ok: false, error: `Total message text exceeds ${MAX_TEXT_CHARS} characters.` };
    }
    if (imageBytes > MAX_IMAGE_DATA_BYTES) {
      return { ok: false, error: "Total image data URL size exceeds 4 MB." };
    }
  }

  const cap = Math.max(1, Math.min(32768, Math.trunc(allowedModel.max_tokens_cap)));
  let maxTokens = Math.min(DEFAULT_MAX_TOKENS, cap);
  if (body.max_tokens !== undefined && body.max_tokens !== null) {
    if (typeof body.max_tokens !== "number" || !Number.isFinite(body.max_tokens) || body.max_tokens < 1) {
      return { ok: false, error: "Field 'max_tokens' must be a positive number." };
    }
    maxTokens = Math.min(Math.trunc(body.max_tokens), cap);
  }

  let stream = false;
  if (body.stream !== undefined && body.stream !== null) {
    if (typeof body.stream !== "boolean") {
      return { ok: false, error: "Field 'stream' must be a boolean." };
    }
    stream = body.stream;
  }

  if (
    body.temperature !== undefined &&
    body.temperature !== null &&
    (typeof body.temperature !== "number" || !Number.isFinite(body.temperature))
  ) {
    return { ok: false, error: "Field 'temperature' must be a finite number." };
  }

  if (
    body.top_p !== undefined &&
    body.top_p !== null &&
    (typeof body.top_p !== "number" || !Number.isFinite(body.top_p))
  ) {
    return { ok: false, error: "Field 'top_p' must be a finite number." };
  }

  if (body.tools !== undefined && body.tools !== null && !Array.isArray(body.tools)) {
    return { ok: false, error: "Field 'tools' must be an array." };
  }

  if (body.stop !== undefined && body.stop !== null) {
    const validStop = typeof body.stop === "string" ||
      (Array.isArray(body.stop) && body.stop.every((s) => typeof s === "string"));
    if (!validStop) {
      return { ok: false, error: "Field 'stop' must be a string or an array of strings." };
    }
  }

  const payload: Record<string, unknown> = {};
  for (const key of ALLOWED_FIELDS) {
    if (body[key] !== undefined) {
      payload[key] = body[key];
    }
  }
  payload.model = model;
  payload.max_tokens = maxTokens;

  return {
    ok: true,
    value: {
      payload,
      model,
      maxTokens,
      textChars,
      stream,
    },
  };
}
