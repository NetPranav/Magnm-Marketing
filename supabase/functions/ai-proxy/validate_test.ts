import { assert } from "./test_helpers.ts";
import {
  AllowedModel,
  estimateTokens,
  MAX_IMAGE_DATA_BYTES,
  MAX_MESSAGES,
  MAX_TEXT_CHARS,
  validateChatRequest,
} from "./validate.ts";

const MODEL_ALLOWED: AllowedModel = {
  model: "nvidia/nemotron-3-ultra-550b-a55b",
  enabled: true,
  max_tokens_cap: 8000,
};

const MODEL_SMALL_CAP: AllowedModel = {
  model: "nvidia/nemotron-3.5-lightning-30b-a3b",
  enabled: true,
  max_tokens_cap: 1000,
};

Deno.test("validateChatRequest strips unknown fields and caps max_tokens", () => {
  const res = validateChatRequest(
    {
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "Hello world" }],
      max_tokens: 12000,
      temperature: 0.3,
      stream: false,
      unknown_secret_field: "drop-me",
      api_key: "never-pass-through",
    },
    MODEL_ALLOWED,
  );

  assert.equal(res.ok, true);
  if (!res.ok) return;
  assert.equal(res.value.maxTokens, 8000);
  assert.equal(res.value.payload.max_tokens, 8000);
  assert.equal(res.value.payload.temperature, 0.3);
  assert.equal("unknown_secret_field" in res.value.payload, false);
  assert.equal("api_key" in res.value.payload, false);
  assert.equal(res.value.textChars, 11);
  assert.equal(estimateTokens(11, 8000), 8003);
});

Deno.test("validateChatRequest defaults max_tokens to 1500 or model cap", () => {
  const res1 = validateChatRequest(
    {
      model: MODEL_ALLOWED.model,
      messages: [{ role: "user", content: "Hi" }],
    },
    MODEL_ALLOWED,
  );
  assert.equal(res1.ok, true);
  if (res1.ok) assert.equal(res1.value.maxTokens, 1500);

  const res2 = validateChatRequest(
    {
      model: MODEL_SMALL_CAP.model,
      messages: [{ role: "user", content: "Hi" }],
    },
    MODEL_SMALL_CAP,
  );
  assert.equal(res2.ok, true);
  if (res2.ok) assert.equal(res2.value.maxTokens, 1000);
});

Deno.test("validateChatRequest rejects missing or disabled model", () => {
  const missing = validateChatRequest(
    { model: "openai/gpt-4o", messages: [{ role: "user", content: "Hi" }] },
    null,
  );
  assert.equal(missing.ok, false);

  const disabled = validateChatRequest(
    { model: MODEL_ALLOWED.model, messages: [{ role: "user", content: "Hi" }] },
    { ...MODEL_ALLOWED, enabled: false },
  );
  assert.equal(disabled.ok, false);
});

Deno.test("validateChatRequest enforces message count and text character caps", () => {
  const tooMany = Array.from({ length: MAX_MESSAGES + 1 }, () => ({
    role: "user",
    content: "x",
  }));
  const resCount = validateChatRequest(
    { model: MODEL_ALLOWED.model, messages: tooMany },
    MODEL_ALLOWED,
  );
  assert.equal(resCount.ok, false);

  const hugeText = "a".repeat(MAX_TEXT_CHARS + 1);
  const resChars = validateChatRequest(
    {
      model: MODEL_ALLOWED.model,
      messages: [{ role: "user", content: [{ type: "text", text: hugeText }] }],
    },
    MODEL_ALLOWED,
  );
  assert.equal(resChars.ok, false);
});

Deno.test("validateChatRequest enforces 4 MB limit on image data URLs", () => {
  const okDataUrl = "data:image/png;base64," + "A".repeat(300_000);
  const okRes = validateChatRequest(
    {
      model: MODEL_ALLOWED.model,
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "Describe this" },
            { type: "image_url", image_url: { url: okDataUrl } },
          ],
        },
      ],
    },
    MODEL_ALLOWED,
  );
  assert.equal(okRes.ok, true);
  if (okRes.ok) {
    assert.equal(okRes.value.textChars, "Describe this".length);
  }

  const hugeDataUrl = "data:image/png;base64," + "A".repeat(MAX_IMAGE_DATA_BYTES);
  const badRes = validateChatRequest(
    {
      model: MODEL_ALLOWED.model,
      messages: [
        {
          role: "user",
          content: [{ type: "image_url", image_url: { url: hugeDataUrl } }],
        },
      ],
    },
    MODEL_ALLOWED,
  );
  assert.equal(badRes.ok, false);
});
