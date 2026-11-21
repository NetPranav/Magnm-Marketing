import { assert } from "./test_helpers.ts";
import {
  classifyUpstreamError,
  KeyPool,
  KeyRecord,
  parseRetryAfterSeconds,
} from "./keys.ts";

Deno.test("KeyPool caches keys for 60s and rotates start index", async () => {
  let now = 1_000_000;
  let loads = 0;
  const rows: KeyRecord[] = [
    { label: "key-1 (...0001)", api_key: "nvapi-k1", cooldown_until: null },
    { label: "key-2 (...0002)", api_key: "nvapi-k2", cooldown_until: null },
    { label: "key-3 (...0003)", api_key: "nvapi-k3", cooldown_until: null },
  ];

  const pool = new KeyPool(
    () => {
      loads++;
      return Promise.resolve(rows);
    },
    { ttlMs: 60_000, nowFn: () => now },
  );

  const k1 = await pool.getKeys();
  const c1 = pool.pickCandidates(k1, 3);
  assert.deepEqual(c1.map((k) => k.label), [
    "key-1 (...0001)",
    "key-2 (...0002)",
    "key-3 (...0003)",
  ]);

  now += 10_000;
  const k2 = await pool.getKeys();
  const c2 = pool.pickCandidates(k2, 3);
  assert.equal(loads, 1);
  assert.deepEqual(c2.map((k) => k.label), [
    "key-2 (...0002)",
    "key-3 (...0003)",
    "key-1 (...0001)",
  ]);

  now += 60_000;
  await pool.getKeys();
  assert.equal(loads, 2);
});

Deno.test("KeyPool skips keys in cooldown and resumes when cooldown expires", async () => {
  let now = 2_000_000;
  const rows: KeyRecord[] = [
    { label: "key-1 (...0001)", api_key: "nvapi-k1", cooldown_until: new Date(now + 30_000).toISOString() },
    { label: "key-2 (...0002)", api_key: "nvapi-k2", cooldown_until: null },
    { label: "key-3 (...0003)", api_key: "nvapi-k3", cooldown_until: null },
  ];

  const pool = new KeyPool(() => Promise.resolve(rows), {
    ttlMs: 60_000,
    nowFn: () => now,
  });

  const keys = await pool.getKeys();
  const c1 = pool.pickCandidates(keys, 3);
  assert.deepEqual(c1.map((k) => k.label), [
    "key-2 (...0002)",
    "key-3 (...0003)",
  ]);

  pool.markCooldown("key-2 (...0002)", 15);
  const c2 = pool.pickCandidates(keys, 3);
  assert.deepEqual(c2.map((k) => k.label), ["key-3 (...0003)"]);

  now += 35_000;
  const c3 = pool.pickCandidates(keys, 3);
  assert.equal(c3.length, 3);
});

Deno.test("parseRetryAfterSeconds handles numeric, HTTP-date, and missing values", () => {
  const now = Date.parse("2026-11-22T00:00:00Z");
  assert.equal(parseRetryAfterSeconds(null, 60, now), 60);
  assert.equal(parseRetryAfterSeconds("12", 60, now), 12);
  assert.equal(parseRetryAfterSeconds("0", 60, now), 1);
  assert.equal(
    parseRetryAfterSeconds("Sun, 22 Nov 2026 00:00:45 GMT", 60, now),
    45,
  );
  assert.equal(parseRetryAfterSeconds("invalid", 60, now), 60);
});

Deno.test("classifyUpstreamError maps 401/403, 429, 5xx, and 400", () => {
  const auth = classifyUpstreamError(401, null, "bad key");
  assert.equal(auth.retryNextKey, true);
  assert.equal(auth.cooldownSeconds, 3600);

  const rl = classifyUpstreamError(429, "17", "slow down");
  assert.equal(rl.retryNextKey, true);
  assert.equal(rl.cooldownSeconds, 17);

  const srv = classifyUpstreamError(503, null, "unavailable");
  assert.equal(srv.retryNextKey, true);
  assert.equal(srv.cooldownSeconds, 30);

  const badReq = classifyUpstreamError(400, null, "bad request");
  assert.equal(badReq.retryNextKey, false);
});
