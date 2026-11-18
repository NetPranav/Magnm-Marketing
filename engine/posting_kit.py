#!/usr/bin/env python3
"""Build POSTING-KIT.md bundles for finalized content items and workflow runs."""

import json
import os
import re
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths
import platforms

_HASHTAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_]+")
_ALT_TEXT_LINE_RE = re.compile(r"(?im)^\s*(?:alt[_\s-]?text)\s*:\s*(.+)$")


def _extract_alt_text(item, entry):
    """Find alt text from a file entry or content notes, returning None if missing."""
    if isinstance(entry, dict):
        direct = (entry.get("alt_text") or "").strip()
        if direct:
            return direct

    entry_path = entry.get("path", "") if isinstance(entry, dict) else str(entry or "")
    entry_name = os.path.basename(entry_path) if entry_path else ""

    candidates = []
    if item.get("notes"):
        candidates.append(item["notes"])
    for ver in reversed(item.get("versions") or []):
        if ver.get("notes") and ver["notes"] not in candidates:
            candidates.append(ver["notes"])

    for raw_notes in candidates:
        text = str(raw_notes or "").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("alt_texts"), dict):
                    by_path = parsed["alt_texts"]
                    for key in (entry_path, entry_name, "default"):
                        if key and str(by_path.get(key) or "").strip():
                            return str(by_path[key]).strip()
                if str(parsed.get("alt_text") or "").strip():
                    return str(parsed["alt_text"]).strip()
        except Exception:
            pass

        match = _ALT_TEXT_LINE_RE.search(text)
        if match:
            found = match.group(1).strip()
            if found and found.upper() != "MISSING":
                return found

    return None


def _see_more_cutoff_info(platform_norm, body):
    """Return a human-readable 'see more' cutoff preview for platforms that truncate."""
    limits = platforms.limits_for(platform_norm)
    cutoff = limits.get("hook_chars") or limits.get("description_hook_chars")
    if not cutoff:
        return None
    text = body or ""
    preview = text[:cutoff].replace("\n", " ")
    truncated = len(text) > cutoff
    suffix = " ... [cuts off here]" if truncated else " [fits before cutoff]"
    return f"~{cutoff} chars ({len(text[:cutoff])}/{cutoff}): {preview}{suffix}"


def _format_char_count(platform_norm, body):
    """Format character (and word) counts against platform limits."""
    text = body or ""
    limits = platforms.limits_for(platform_norm)
    char_len = len(text)
    max_chars = limits.get("max_chars")
    max_words = limits.get("max_words")
    if max_chars is not None:
        status = "OVER LIMIT" if char_len > max_chars else "ok"
        return f"{char_len} / {max_chars} chars ({status})"
    if max_words is not None:
        words = len(text.split())
        min_words = limits.get("min_words", 0)
        return f"{words} words (target {min_words}-{max_words}), {char_len} chars"
    return f"{char_len} chars"


def _attach_kit_path(content_id, rel_path):
    """Append posting_kit entry to content_items.files_json if not already listed."""
    item = db.get_content_item(content_id)
    if item is None:
        return
    for entry in item.get("files") or []:
        if isinstance(entry, dict) and entry.get("kind") == "posting_kit" and entry.get("path") == rel_path:
            return
    db.append_content_files(content_id, [{"kind": "posting_kit", "path": rel_path}])


def build_posting_kit(content_ids, run_id=None):
    """Write POSTING-KIT.md for content_ids, attach to files_json, and return relative path."""
    ids = [str(cid) for cid in (content_ids or []) if cid]
    if not ids:
        raise ValueError("content_ids must contain at least one content_id")

    if run_id:
        kit_abs = paths.data("outputs", "workflows", str(run_id), "POSTING-KIT.md")
    else:
        kit_abs = paths.data("outputs", "content", ids[0], "POSTING-KIT.md")
    os.makedirs(os.path.dirname(kit_abs), exist_ok=True)
    rel_path = os.path.relpath(kit_abs, paths.DATA_ROOT)

    lines = ["# Posting Kit", ""]
    if run_id:
        lines.append(f"- Workflow Run: {run_id}")
    lines.append(f"- Items: {', '.join(ids)}")
    lines.append("")

    for cid in ids:
        item = db.get_content_item(cid, include_versions=True)
        if item is None:
            continue

        raw_platform = item.get("platform") or item.get("type") or "unspecified"
        norm_platform = platforms.normalize_platform(raw_platform)
        body = item.get("body") or ""
        hashtags = _HASHTAG_RE.findall(body)
        char_summary = _format_char_count(norm_platform, body)
        cutoff_info = _see_more_cutoff_info(norm_platform, body)

        lines.extend([
            f"## {norm_platform.upper()} ({cid})",
            "",
            f"- Title: {item.get('title') or cid}",
            f"- Type: {item.get('type')} | Status: {item.get('status')} | Version: v{item.get('current_version', 1)}",
            f"- Char count vs limit: {char_summary}",
            f"- Hashtags ({len(hashtags)}): {', '.join(hashtags) if hashtags else 'none'}",
        ])
        if cutoff_info:
            lines.append(f"- See more cutoff: {cutoff_info}")

        notes_val = (item.get("notes") or "").strip()
        if notes_val:
            try:
                seo_data = json.loads(notes_val)
                if isinstance(seo_data, dict) and any(
                    k in seo_data for k in ("seo_title", "meta_description", "slug")
                ):
                    lines.append(f"- SEO metadata: {json.dumps(seo_data, ensure_ascii=False)}")
            except Exception:
                pass

        lines.extend(["", "### Final Text", "", body, ""])

        # Attached images / cards
        lines.append("### Images & Alt Text")
        lines.append("")
        seen_paths = set()
        image_entries = [
            f for f in (item.get("files") or [])
            if isinstance(f, dict) and f.get("kind") in ("card", "image") and f.get("path")
        ]
        if not image_entries:
            lines.append("- None attached")
        else:
            for entry in image_entries:
                img_path = entry["path"]
                if img_path in seen_paths:
                    continue
                seen_paths.add(img_path)
                alt_text = _extract_alt_text(item, entry)
                if alt_text:
                    lines.append(f"- {img_path} | alt text: {alt_text}")
                else:
                    lines.append(f"- {img_path} | alt text: MISSING")
        lines.append("")

        # Review verdicts & notes
        lines.append("### Reviews")
        lines.append("")
        reviews = [
            f for f in (item.get("files") or [])
            if isinstance(f, dict) and f.get("kind") == "review"
        ]
        if not reviews:
            lines.append("- No review recorded")
        else:
            for rev in reviews:
                verdict = rev.get("verdict") or "unknown"
                rev_id = rev.get("review_id") or ""
                rev_notes = (rev.get("notes") or "").strip()
                lines.append(f"- Verdict: {verdict} ({rev_id})")
                if rev_notes:
                    lines.append(f"  Notes: {rev_notes}")
        lines.append("")

        _attach_kit_path(cid, rel_path)

    with open(kit_abs, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    return rel_path
