import { createClient } from "npm:@supabase/supabase-js@2";
import { createProxyHandler, DEFAULT_NIM_BASE_URL } from "./handler.ts";
import { KeyPool, KeyRecord } from "./keys.ts";

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const supabaseAnonKey = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const supabaseServiceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const nimBaseUrl = Deno.env.get("NIM_BASE_URL") || DEFAULT_NIM_BASE_URL;

const userClient = createClient(supabaseUrl, supabaseAnonKey, {
  auth: { persistSession: false, autoRefreshToken: false },
});

const serviceClient = createClient(supabaseUrl, supabaseServiceKey, {
  auth: { persistSession: false, autoRefreshToken: false },
});

const keyPool = new KeyPool(async (): Promise<KeyRecord[]> => {
  const [keysRes, healthRes] = await Promise.all([
    serviceClient.rpc("ai_get_keys"),
    serviceClient.from("ai_key_health").select("label, cooldown_until"),
  ]);

  if (keysRes.error) {
    throw new Error(`ai_get_keys failed: ${keysRes.error.message}`);
  }
  const cooldownMap = new Map<string, string | null>();
  if (!healthRes.error && Array.isArray(healthRes.data)) {
    for (const row of healthRes.data) {
      cooldownMap.set(String(row.label), row.cooldown_until ?? null);
    }
  }
  const rows = Array.isArray(keysRes.data) ? keysRes.data : [];
  return rows.map((row: { label: string; api_key: string }) => ({
    label: row.label,
    api_key: row.api_key,
    cooldown_until: cooldownMap.get(row.label) ?? null,
  }));
});

const handler = createProxyHandler({
  nimBaseUrl,
  keyPool,
  fetchFn: fetch,
  getUser: async (token) => {
    const { data, error } = await userClient.auth.getUser(token);
    if (error || !data?.user) return null;
    return { id: data.user.id };
  },
  isProfileActive: async (userId) => {
    const { data, error } = await serviceClient
      .from("profiles")
      .select("active")
      .eq("id", userId)
      .maybeSingle();
    if (error || !data) return false;
    return data.active === true;
  },
  getAllowedModel: async (model) => {
    const { data, error } = await serviceClient
      .from("ai_allowed_models")
      .select("model, enabled, max_tokens_cap")
      .eq("model", model)
      .maybeSingle();
    if (error || !data) return null;
    return {
      model: data.model,
      enabled: data.enabled === true,
      max_tokens_cap: Number(data.max_tokens_cap),
    };
  },
  checkAndCount: async (ownerId, estimatedTokens) => {
    const { data, error } = await serviceClient.rpc("ai_check_and_count", {
      p_owner: ownerId,
      p_estimated_tokens: estimatedTokens,
    });
    if (error || !data) {
      throw new Error(`ai_check_and_count failed: ${error?.message ?? "empty response"}`);
    }
    return data;
  },
  recordUsage: async (ownerId, promptTokens, completionTokens) => {
    await serviceClient.rpc("ai_record_usage", {
      p_owner: ownerId,
      p_prompt_tokens: promptTokens,
      p_completion_tokens: completionTokens,
    });
  },
  reportKey: async (label, status, errorMsg, cooldownSeconds) => {
    await serviceClient.rpc("ai_report_key", {
      p_label: label,
      p_status: status,
      p_error: errorMsg,
      p_cooldown_seconds: cooldownSeconds,
    });
  },
});

Deno.serve(handler);
