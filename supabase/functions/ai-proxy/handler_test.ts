import { assert } from "./test_helpers.ts";
import { createProxyHandler, ProxyDeps } from "./handler.ts";
import { KeyPool, KeyRecord } from "./keys.ts";

function makeDeps(overrides: Partial<ProxyDeps> = {}): {
  deps: ProxyDeps;
  usageCalls: Array<{ ownerId: string; prompt: number; completion: number }>;
  healthReports: Array<{ label: string; status: number; error: string | null; cooldown: number }>;
  logs: string[];
} {
  const usageCalls: Array<{ ownerId: string; prompt: number; completion: number }> = [];
  const healthReports: Array<{ label: string; status: number; error: string | null; cooldown: number }> = [];
  const logs: string[] = [];

  const keys: KeyRecord[] = [
    { label: "key-1 (...0001)", api_key: "nvapi-SECRETKEY0000000000000000000001", cooldown_until: null },
    { label: "key-2 (...0002)", api_key: "nvapi-SECRETKEY0000000000000000000002", cooldown_until: null },
    { label: "key-3 (...0003)", api_key: "nvapi-SECRETKEY0000000000000000000003", cooldown_until: null },
  ];

  const deps: ProxyDeps = {
    getUser: () => Promise.resolve({ id: "11111111-2222-3333-4444-555555555555" }),
    isProfileActive: () => Promise.resolve(true),
    getAllowedModel: (model) =>
      Promise.resolve(
        model === "nvidia/nemotron-3-ultra-550b-a55b"
          ? { model, enabled: true, max_tokens_cap: 8000 }
          : null,
      ),
    checkAndCount: () =>
      Promise.resolve({ allowed: true, reason: null, requests_left: 199, tokens_left: 400000 }),
    recordUsage: (ownerId, prompt, completion) => {
      usageCalls.push({ ownerId, prompt, completion });
      return Promise.resolve();
    },
    reportKey: (label, status, error, cooldown) => {
      healthReports.push({ label, status, error, cooldown });
      return Promise.resolve();
    },
    keyPool: new KeyPool(() => Promise.resolve(keys)),
    fetchFn: () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            id: "cmpl-1",
            choices: [{ message: { role: "assistant", content: "ok" } }],
            usage: { prompt_tokens: 15, completion_tokens: 5 },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    logFn: (line) => logs.push(line),
    ...overrides,
  };

  return { deps, usageCalls, healthReports, logs };
}

function makeReq(body: unknown, auth = "Bearer user-jwt"): Request {
  return new Request("http://localhost/ai-proxy", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(auth ? { Authorization: auth } : {}),
    },
    body: JSON.stringify(body),
  });
}

Deno.test("handler retries on 429 and 401 across up to 3 keys and succeeds on 3rd", async () => {
  const usedAuthHeaders: string[] = [];
  let attempt = 0;

  const { deps, usageCalls, healthReports, logs } = makeDeps({
    fetchFn: (_url, init) => {
      attempt++;
      const headers = new Headers(init?.headers);
      usedAuthHeaders.push(headers.get("Authorization") ?? "");
      if (attempt === 1) {
        return Promise.resolve(
          new Response("rate limit", { status: 429, headers: { "Retry-After": "25" } }),
        );
      }
      if (attempt === 2) {
        return Promise.resolve(new Response("unauthorized", { status: 401 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            choices: [{ message: { role: "assistant", content: "done" } }],
            usage: { prompt_tokens: 21, completion_tokens: 9 },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    },
  });

  const handler = createProxyHandler(deps);
  const resp = await handler(
    makeReq({
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "Secret user prompt that must not be logged" }],
    }),
  );

  assert.equal(resp.status, 200);
  const json = await resp.json();
  assert.equal(json.choices[0].message.content, "done");

  assert.deepEqual(usedAuthHeaders, [
    "Bearer nvapi-SECRETKEY0000000000000000000001",
    "Bearer nvapi-SECRETKEY0000000000000000000002",
    "Bearer nvapi-SECRETKEY0000000000000000000003",
  ]);
  assert.equal(healthReports.length, 3);
  assert.deepEqual(healthReports[0], {
    label: "key-1 (...0001)",
    status: 429,
    error: "rate limited",
    cooldown: 25,
  });
  assert.deepEqual(healthReports[1], {
    label: "key-2 (...0002)",
    status: 401,
    error: "auth failed",
    cooldown: 3600,
  });
  assert.deepEqual(healthReports[2], {
    label: "key-3 (...0003)",
    status: 200,
    error: null,
    cooldown: 0,
  });

  assert.deepEqual(usageCalls, [
    { ownerId: "11111111-2222-3333-4444-555555555555", prompt: 21, completion: 9 },
  ]);

  assert.equal(logs.length, 1);
  assert.equal(logs[0].includes("nvapi-"), false);
  assert.equal(logs[0].includes("Secret user prompt"), false);
  assert.equal(logs[0].includes("key-3 (...0003)"), true);
  assert.equal(logs[0].includes("tokens=21+9"), true);
});

Deno.test("handler retries on 5xx with 30s cooldown", async () => {
  let attempt = 0;
  const { deps, healthReports } = makeDeps({
    fetchFn: () => {
      attempt++;
      if (attempt === 1) {
        return Promise.resolve(new Response("bad gateway", { status: 502 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({ choices: [{ message: { content: "recovered" } }] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    },
  });

  const handler = createProxyHandler(deps);
  const resp = await handler(
    makeReq({
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "12345678" }],
    }),
  );

  assert.equal(resp.status, 200);
  assert.equal(healthReports[0].status, 502);
  assert.equal(healthReports[0].cooldown, 30);
  assert.equal(healthReports[1].status, 200);
});

Deno.test("handler returns 429 quota refusal message without calling upstream", async () => {
  let fetched = false;
  const { deps } = makeDeps({
    checkAndCount: () =>
      Promise.resolve({
        allowed: false,
        reason: "daily_requests_limit",
        requests_left: 0,
        tokens_left: 12000,
      }),
    fetchFn: () => {
      fetched = true;
      return Promise.resolve(new Response("should not happen"));
    },
  });

  const handler = createProxyHandler(deps);
  const resp = await handler(
    makeReq({
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "Hi" }],
    }),
  );

  assert.equal(resp.status, 429);
  assert.equal(fetched, false);
  const body = await resp.json();
  assert.equal(
    body.error,
    "Daily AI limit reached. It resets at midnight UTC. Ask your admin for a higher limit.",
  );
  assert.equal(body.reason, "daily_requests_limit");
});

Deno.test("handler returns 503 when no keys exist and 403 when profile is inactive", async () => {
  const { deps: noKeyDeps } = makeDeps({
    keyPool: new KeyPool(() => Promise.resolve([])),
  });
  const noKeyResp = await createProxyHandler(noKeyDeps)(
    makeReq({
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "Hi" }],
    }),
  );
  assert.equal(noKeyResp.status, 503);
  assert.equal(
    (await noKeyResp.json()).error,
    "The team has no shared AI keys yet. Ask your admin.",
  );

  const { deps: inactiveDeps } = makeDeps({
    isProfileActive: () => Promise.resolve(false),
  });
  const inactiveResp = await createProxyHandler(inactiveDeps)(
    makeReq({
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "Hi" }],
    }),
  );
  assert.equal(inactiveResp.status, 403);
  assert.equal((await inactiveResp.json()).error, "Your account is not active.");
});

Deno.test("handler streams SSE with stream_options and falls back to char estimate when usage missing", async () => {
  let sentBody: Record<string, unknown> = {};
  const enc = new TextEncoder();
  const ssePayload = [
    'data: {"choices":[{"delta":{"content":"12345678"}}]}\n\n',
    "data: [DONE]\n\n",
  ].join("");

  const { deps, usageCalls } = makeDeps({
    fetchFn: (_url, init) => {
      sentBody = JSON.parse(String(init?.body ?? "{}"));
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(enc.encode(ssePayload.slice(0, 24)));
          controller.enqueue(enc.encode(ssePayload.slice(24)));
          controller.close();
        },
      });
      return Promise.resolve(
        new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      );
    },
  });

  const handler = createProxyHandler(deps);
  const resp = await handler(
    makeReq({
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      messages: [{ role: "user", content: "abcdefgh" }],
      stream: true,
    }),
  );

  assert.equal(resp.status, 200);
  assert.equal(resp.headers.get("content-type"), "text/event-stream");
  assert.deepEqual(sentBody.stream_options, { include_usage: true });
  const text = await resp.text();
  assert.equal(text, ssePayload);

  // "abcdefgh" = 8 chars -> ceil(8/4) = 2 prompt tokens; "12345678" = 8 chars -> ceil(8/4) = 2 completion tokens.
  assert.deepEqual(usageCalls, [
    { ownerId: "11111111-2222-3333-4444-555555555555", prompt: 2, completion: 2 },
  ]);
});

Deno.test("handler rejects non-POST, missing/invalid auth, and does not retry upstream 400", async () => {
  let upstreamCalls = 0;
  const { deps, healthReports } = makeDeps({
    getUser: (token) => Promise.resolve(token === "good-token" ? { id: "11111111-2222-3333-4444-555555555555" } : null),
    fetchFn: () => {
      upstreamCalls++;
      return Promise.resolve(
        new Response(JSON.stringify({ error: "bad prompt format" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      );
    },
  });

  const handler = createProxyHandler(deps);

  const getResp = await handler(new Request("http://localhost/ai-proxy", { method: "GET" }));
  assert.equal(getResp.status, 405);

  const noAuthResp = await handler(
    makeReq({ model: "nvidia/nemotron-3-ultra-550b-a55b", messages: [{ role: "user", content: "Hi" }] }, ""),
  );
  assert.equal(noAuthResp.status, 401);

  const badAuthResp = await handler(
    makeReq({ model: "nvidia/nemotron-3-ultra-550b-a55b", messages: [{ role: "user", content: "Hi" }] }, "Bearer bad-token"),
  );
  assert.equal(badAuthResp.status, 401);

  const badReqResp = await handler(
    makeReq({ model: "nvidia/nemotron-3-ultra-550b-a55b", messages: [{ role: "user", content: "Hi" }] }, "Bearer good-token"),
  );
  assert.equal(badReqResp.status, 400);
  assert.equal(upstreamCalls, 1);
  assert.equal(healthReports.length, 0);
});

Deno.test("handler cools down key for 30s on upstream timeout", async () => {
  let attempt = 0;
  const { deps, healthReports } = makeDeps({
    fetchFn: () => {
      attempt++;
      if (attempt === 1) {
        const err = new Error("aborted");
        err.name = "AbortError";
        return Promise.reject(err);
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({ choices: [{ message: { content: "ok" } }], usage: { prompt_tokens: 5, completion_tokens: 2 } }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    },
  });

  const resp = await createProxyHandler(deps)(
    makeReq({ model: "nvidia/nemotron-3-ultra-550b-a55b", messages: [{ role: "user", content: "Hi" }] }),
  );
  assert.equal(resp.status, 200);
  assert.equal(healthReports[0].status, 504);
  assert.equal(healthReports[0].cooldown, 30);
  assert.equal(healthReports[1].status, 200);
});
