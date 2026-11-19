/** Check whether a URL is a valid http: or https: URL (rejects javascript:, data:, etc.). */
export function isSafeHttpUrl(raw: string | null | undefined): boolean {
  if (!raw) return false;
  try {
    const parsed = new URL(raw.trim());
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/** Extract clean domain name (without www.) from a URL, or empty string if invalid. */
export function extractDomain(raw: string | null | undefined): string {
  if (!isSafeHttpUrl(raw)) return '';
  try {
    const host = new URL((raw || '').trim()).hostname.toLowerCase();
    return host.startsWith('www.') ? host.slice(4) : host;
  } catch {
    return '';
  }
}

/** Format an ISO timestamp into a short readable string for mono metadata. */
export function formatTimestamp(raw: string | null | undefined): string {
  if (!raw) return '';
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return String(raw);
  return dt.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Extract unique safe http(s) URLs from markdown or plain text. */
export function extractUrls(text: string): Array<{ url: string; label: string; domain: string }> {
  const seen = new Set<string>();
  const out: Array<{ url: string; label: string; domain: string }> = [];

  const mdLinkRe = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let match: RegExpExecArray | null;
  while ((match = mdLinkRe.exec(text)) !== null) {
    const label = match[1].trim();
    const url = match[2].trim();
    if (isSafeHttpUrl(url) && !seen.has(url)) {
      seen.add(url);
      out.push({ url, label: label || url, domain: extractDomain(url) });
    }
  }

  const bareUrlRe = /https?:\/\/[^\s<>"')]+/g;
  while ((match = bareUrlRe.exec(text)) !== null) {
    const url = match[0].replace(/[.,;:!?]+$/, '');
    if (isSafeHttpUrl(url) && !seen.has(url)) {
      seen.add(url);
      out.push({ url, label: url, domain: extractDomain(url) });
    }
  }

  return out;
}
