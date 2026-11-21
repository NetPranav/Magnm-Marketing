import { assert } from "./test_helpers.ts";
import { createSsePassthroughStream, SseUsage, SseUsageTracker } from "./sse.ts";

const enc = new TextEncoder();

Deno.test("SseUsageTracker extracts usage when chunk boundaries split lines", () => {
  const tracker = new SseUsageTracker();
  const full = [
    'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"world!"}}]}\n\n',
    'data: {"choices":[],"usage":{"prompt_tokens":42,"completion_tokens":7}}\n\n',
    "data: [DONE]\n\n",
  ].join("");

  // Split into awkward small byte slices right through JSON and newlines.
  const bytes = enc.encode(full);
  for (let i = 0; i < bytes.length; i += 11) {
    tracker.push(bytes.subarray(i, i + 11));
  }
  const usage = tracker.finish();
  assert.equal(usage.promptTokens, 42);
  assert.equal(usage.completionTokens, 7);
  assert.equal(usage.textChars, 12);
});

Deno.test("createSsePassthroughStream preserves exact bytes and falls back when usage is omitted", async () => {
  const sseText = [
    'data: {"choices":[{"delta":{"content":"ABCD"}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"EFGH"}}]}\n\n',
    "data: [DONE]\n\n",
  ].join("");

  const chunks = [
    enc.encode(sseText.slice(0, 19)),
    enc.encode(sseText.slice(19, 55)),
    enc.encode(sseText.slice(55)),
  ];

  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(c);
      controller.close();
    },
  });

  let recorded: SseUsage | null = null;
  const passthrough = createSsePassthroughStream((u) => {
    recorded = u;
  });

  const outText = await new Response(source.pipeThrough(passthrough)).text();
  assert.equal(outText, sseText);
  if (recorded === null) {
    throw new Error("Expected SSE usage callback to be invoked");
  }
  const finalUsage: SseUsage = recorded;
  assert.equal(finalUsage.promptTokens, null);
  assert.equal(finalUsage.completionTokens, null);
  assert.equal(finalUsage.textChars, 8);
});
