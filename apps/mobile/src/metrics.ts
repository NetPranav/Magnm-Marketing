/** Validate edited analytics metric inputs without silently dropping invalid values. */
export function validateMetricInputs(
  rawMetrics: Record<string, string>,
): { ok: true; metrics: Record<string, number | null> } | { ok: false; error: string } {
  const metrics: Record<string, number | null> = {};
  for (const [key, raw] of Object.entries(rawMetrics)) {
    const trimmed = String(raw ?? '').trim();
    if (!trimmed) {
      metrics[key] = null;
      continue;
    }
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed) || parsed < 0) {
      return {
        ok: false,
        error: `Invalid value for "${key}": enter a non-negative number or leave blank.`,
      };
    }
    metrics[key] = parsed;
  }
  return { ok: true, metrics };
}
