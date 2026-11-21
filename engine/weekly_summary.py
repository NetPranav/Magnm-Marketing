#!/usr/bin/env python3
"""Deterministic weekly performance summary builder, highlights math, and grounded AI narrative."""

from datetime import date, datetime, timedelta, timezone
import math
import os
import re
import sys
from zoneinfo import ZoneInfo

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import cloud_sync
import db
import notifications
import paths
import platforms
import product_rules

IST = ZoneInfo("Asia/Kolkata")
NARRATIVE_SKIPPED_NOTE = "Narrative skipped: it mentioned numbers that are not in the data."

_CORE_METRIC_KEYS = (
    "views",
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "engagement",
)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_LEADING_LIST_NUM_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")
_TEXT_NUM_RE = re.compile(r"(?<![A-Za-z0-9_])(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?(?![A-Za-z0-9])")


class WeeklySummaryError(Exception):
    """Typed error for weekly summary validation and lookup failures."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)


def _as_utc(dt=None):
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_dt(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_week_start(week_start=None, now=None):
    """Return the IST Monday `date` for week_start (defaulting to last completed IST week)."""
    if week_start is None or (isinstance(week_start, str) and not week_start.strip()):
        now_ist = _as_utc(now).astimezone(IST)
        this_monday = now_ist.date() - timedelta(days=now_ist.weekday())
        return this_monday - timedelta(days=7)

    if isinstance(week_start, datetime):
        dt = week_start.astimezone(IST) if week_start.tzinfo else week_start.replace(tzinfo=IST)
        d = dt.date()
        return d - timedelta(days=d.weekday())

    if isinstance(week_start, date):
        return week_start - timedelta(days=week_start.weekday())

    raw = str(week_start).strip()
    try:
        d = date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise WeeklySummaryError("week_start must be YYYY-MM-DD", status=400) from exc
    return d - timedelta(days=d.weekday())


def week_bounds_ist(monday_date):
    """Return (start_ist, end_ist) covering Monday 00:00 IST through Sunday 23:59:59.999999 IST."""
    start_ist = datetime(monday_date.year, monday_date.month, monday_date.day, 0, 0, 0, tzinfo=IST)
    end_ist = start_ist + timedelta(days=7)
    return start_ist, end_ist


def format_week_range(week_start):
    """Format a Monday YYYY-MM-DD or date into a concise range such as '17-23 Nov'."""
    monday = normalize_week_start(week_start)
    sunday = monday + timedelta(days=6)
    if monday.year == sunday.year and monday.month == sunday.month:
        return f"{monday.day}-{sunday.day} {sunday.strftime('%b')}"
    if monday.year == sunday.year:
        return f"{monday.day} {monday.strftime('%b')}-{sunday.day} {sunday.strftime('%b')}"
    return f"{monday.day} {monday.strftime('%b %Y')}-{sunday.day} {sunday.strftime('%b %Y')}"


def format_week_title(week_start):
    """Return mirror/notification title such as 'Weekly summary 17-23 Nov'."""
    return f"Weekly summary {format_week_range(week_start)}"


def _in_ist_window(ts_raw, start_ist, end_ist):
    dt = _parse_iso_dt(ts_raw)
    if dt is None:
        return False, None
    dt_ist = dt.astimezone(IST)
    return (start_ist <= dt_ist < end_ist), dt_ist


def _latest_metrics_map(all_metrics):
    """Return {content_id: latest_metric_dict} ordered by captured_at, created_at, row order."""
    best = {}
    for idx, met in enumerate(all_metrics or []):
        cid = met.get("content_id")
        if not cid:
            continue
        cap_dt = _parse_iso_dt(met.get("captured_at")) or datetime.min.replace(tzinfo=timezone.utc)
        cre_dt = _parse_iso_dt(met.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
        key = (cap_dt, cre_dt, idx)
        prev = best.get(cid)
        if prev is None or key > prev[0]:
            best[cid] = (key, met)
    return {cid: pair[1] for cid, pair in best.items()}


def _clean_metric_snapshot(met):
    if not met:
        return None
    cleaned = {}
    for k, v in (met.get("metrics") or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            num = float(v)
            cleaned[str(k)] = int(num) if num.is_integer() else round(num, 4)
    return {
        "id": met["id"],
        "platform": platforms.normalize_platform(met.get("platform") or ""),
        "metrics": cleaned,
        "source": met.get("source"),
        "captured_at": met.get("captured_at"),
    }


def _collect_posts_in_window(content_items, latest_by_cid, start_ist, end_ist):
    posts = []
    for item in content_items:
        if item.get("status") != "posted":
            continue
        ok, dt_ist = _in_ist_window(item.get("posted_at"), start_ist, end_ist)
        if not ok:
            continue
        plat = platforms.normalize_platform(item.get("platform") or "") or "unknown"
        met = _clean_metric_snapshot(latest_by_cid.get(item["id"]))
        posts.append({
            "content_id": item["id"],
            "title": item.get("title") or item["id"],
            "type": item.get("type") or "post",
            "platform": plat,
            "posted_at": item.get("posted_at"),
            "posted_at_ist": dt_ist.isoformat(),
            "posted_url": item.get("posted_url") or "",
            "product_id": item.get("product_id"),
            "latest_metric": met,
        })
    posts.sort(key=lambda p: (p["posted_at_ist"], p["content_id"]))
    return posts


def build_week_data(week_start=None, now=None):
    """Collect pure deterministic numbers and lists for the IST week Monday 00:00 to Sunday 23:59."""
    monday = normalize_week_start(week_start, now=now)
    sunday = monday + timedelta(days=6)
    prev_monday = monday - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)

    start_ist, end_ist = week_bounds_ist(monday)
    prev_start_ist, prev_end_ist = week_bounds_ist(prev_monday)
    now_ist = _as_utc(now).astimezone(IST)
    cutoff_ist = min(end_ist, now_ist)

    content_items = db.list_all_content_items()
    all_metrics = db.list_all_metrics()
    latest_by_cid = _latest_metrics_map(all_metrics)
    research_notes = db.list_all_research_notes()
    all_reminders = db.list_all_reminders()

    posts = _collect_posts_in_window(content_items, latest_by_cid, start_ist, end_ist)
    prev_posts = _collect_posts_in_window(content_items, latest_by_cid, prev_start_ist, prev_end_ist)

    drafts_created = []
    for item in content_items:
        if item.get("type") in ("review", "plan"):
            continue
        ok, _ = _in_ist_window(item.get("created_at"), start_ist, end_ist)
        if ok:
            drafts_created.append({
                "content_id": item["id"],
                "title": item.get("title") or item["id"],
                "type": item.get("type") or "post",
                "platform": platforms.normalize_platform(item.get("platform") or "") or None,
                "status": item.get("status") or "draft",
                "created_at": item.get("created_at"),
            })

    notes_added = []
    for note in research_notes:
        ok, _ = _in_ist_window(note.get("created_at"), start_ist, end_ist)
        if ok:
            notes_added.append({
                "id": note["id"],
                "title": note.get("title") or note["id"],
                "source": note.get("source"),
                "url": note.get("url") or "",
                "agent_id": note.get("agent_id"),
                "created_at": note.get("created_at"),
            })

    reminders_done = []
    reminders_missed = []
    for rem in all_reminders:
        done_at = rem.get("done_at")
        if done_at:
            done_ok, _ = _in_ist_window(done_at, start_ist, end_ist)
            if done_ok:
                reminders_done.append({
                    "id": rem["id"],
                    "title": rem.get("title") or rem["id"],
                    "kind": rem.get("kind") or "custom",
                    "due_at": rem.get("due_at"),
                    "done_at": done_at,
                })
        else:
            due_ok, due_ist = _in_ist_window(rem.get("due_at"), start_ist, end_ist)
            if due_ok and due_ist is not None and due_ist <= cutoff_ist:
                reminders_missed.append({
                    "id": rem["id"],
                    "title": rem.get("title") or rem["id"],
                    "kind": rem.get("kind") or "custom",
                    "due_at": rem.get("due_at"),
                    "content_id": rem.get("content_id"),
                })

    return {
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "week_label": format_week_range(monday),
        "start_at": start_ist.isoformat(),
        "end_at": (end_ist - timedelta(seconds=1)).isoformat(),
        "posts": posts,
        "previous_week": {
            "week_start": prev_monday.isoformat(),
            "week_end": prev_sunday.isoformat(),
            "week_label": format_week_range(prev_monday),
            "posts": prev_posts,
        },
        "drafts_created": drafts_created,
        "research_notes": notes_added,
        "reminders": {
            "done": reminders_done,
            "missed": reminders_missed,
        },
    }


def _num_val(metrics, key):
    val = (metrics or {}).get(key)
    if isinstance(val, bool) or val is None:
        return 0.0
    try:
        num = float(val)
        return num if math.isfinite(num) else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_engagement(metrics):
    """Return engagement = likes + comments + shares + saves."""
    total = (
        _num_val(metrics, "likes")
        + _num_val(metrics, "comments")
        + _num_val(metrics, "shares")
        + _num_val(metrics, "saves")
    )
    return int(total) if total.is_integer() else round(total, 4)


def compute_post_score(metrics):
    """Return (score, per_view_used) using engagement per view when views exist (> 0)."""
    engagement = float(compute_engagement(metrics))
    views = _num_val(metrics, "views")
    if views > 0:
        return round(engagement / views, 6), True
    return round(engagement, 4), False


def _clean_number(val):
    if isinstance(val, int):
        return val
    num = float(val)
    if num.is_integer():
        return int(num)
    return round(num, 2)


def _aggregate_posts(posts):
    with_results = [p for p in posts if p.get("latest_metric") and (p["latest_metric"].get("metrics") or {})]
    totals = {k: 0.0 for k in _CORE_METRIC_KEYS}
    for post in with_results:
        metrics = post["latest_metric"]["metrics"]
        for k in _CORE_METRIC_KEYS:
            if k == "engagement":
                totals[k] += float(compute_engagement(metrics))
            else:
                totals[k] += _num_val(metrics, k)
        for k, v in metrics.items():
            if k not in totals and isinstance(v, (int, float)) and not isinstance(v, bool):
                totals[k] = totals.get(k, 0.0) + float(v)

    clean_totals = {k: _clean_number(v) for k, v in totals.items()}
    averages = {}
    if with_results:
        n = float(len(with_results))
        averages = {k: _clean_number(v / n) for k, v in totals.items()}

    return {
        "posts_count": len(posts),
        "with_results_count": len(with_results),
        "totals": clean_totals,
        "averages": averages,
    }


def _pct_change(curr_val, prev_val, has_prev_data):
    if not has_prev_data:
        return "n/a"
    if prev_val is None or prev_val <= 0:
        return "n/a"
    pct = ((float(curr_val) - float(prev_val)) / float(prev_val)) * 100.0
    return round(pct, 1)


def _compute_wow(curr_agg, prev_agg):
    has_prev_posts = prev_agg["posts_count"] > 0
    has_prev_metrics = prev_agg["with_results_count"] > 0
    wow = {
        "posts_count": _pct_change(curr_agg["posts_count"], prev_agg["posts_count"], has_prev_posts),
    }
    for k in _CORE_METRIC_KEYS:
        wow[k] = _pct_change(
            curr_agg["totals"].get(k, 0),
            prev_agg["totals"].get(k, 0),
            has_prev_metrics,
        )
    return wow


def compute_highlights(data):
    """Compute deterministic per-platform totals/averages, WoW %, top/bottom post, and missing results."""
    posts = list(data.get("posts") or [])
    prev_posts = list((data.get("previous_week") or {}).get("posts") or [])

    curr_by_plat = {}
    for p in posts:
        plat = p.get("platform") or "unknown"
        curr_by_plat.setdefault(plat, []).append(p)

    prev_by_plat = {}
    for p in prev_posts:
        plat = p.get("platform") or "unknown"
        prev_by_plat.setdefault(plat, []).append(p)

    all_plats = sorted(
        curr_by_plat.keys(),
        key=lambda k: (-len(curr_by_plat[k]), k),
    )

    platforms_map = {}
    platform_rows = []
    for plat in all_plats:
        c_agg = _aggregate_posts(curr_by_plat.get(plat, []))
        p_agg = _aggregate_posts(prev_by_plat.get(plat, []))
        row = {
            "platform": plat,
            "posts_count": c_agg["posts_count"],
            "with_results_count": c_agg["with_results_count"],
            "totals": c_agg["totals"],
            "averages": c_agg["averages"],
            "prev_posts_count": p_agg["posts_count"],
            "prev_with_results_count": p_agg["with_results_count"],
            "prev_totals": p_agg["totals"],
            "wow_change": _compute_wow(c_agg, p_agg),
        }
        platforms_map[plat] = row
        platform_rows.append(row)

    overall_curr = _aggregate_posts(posts)
    overall_prev = _aggregate_posts(prev_posts)
    overall = {
        "posts_count": overall_curr["posts_count"],
        "with_results_count": overall_curr["with_results_count"],
        "totals": overall_curr["totals"],
        "averages": overall_curr["averages"],
        "prev_posts_count": overall_prev["posts_count"],
        "prev_totals": overall_prev["totals"],
        "wow_change": _compute_wow(overall_curr, overall_prev),
    }

    scored_posts = []
    missing_results = []
    for post in posts:
        met = post.get("latest_metric")
        metrics_dict = (met or {}).get("metrics") or {}
        if not met or not metrics_dict:
            missing_results.append({
                "content_id": post["content_id"],
                "title": post.get("title") or post["content_id"],
                "platform": post.get("platform") or "unknown",
                "posted_at": post.get("posted_at"),
                "posted_url": post.get("posted_url") or "",
            })
            continue
        engagement = compute_engagement(metrics_dict)
        score, per_view = compute_post_score(metrics_dict)
        views = _num_val(metrics_dict, "views")
        scored_posts.append({
            "content_id": post["content_id"],
            "metric_id": met.get("id"),
            "title": post.get("title") or post["content_id"],
            "platform": post.get("platform") or "unknown",
            "posted_at": post.get("posted_at"),
            "posted_url": post.get("posted_url") or "",
            "metrics": dict(metrics_dict),
            "engagement": engagement,
            "views": _clean_number(views) if views > 0 else None,
            "score": score,
            "score_unit": "per_view" if per_view else "engagement",
            "per_view": per_view,
        })

    top_post = None
    bottom_post = None
    if scored_posts:
        all_have_views = all(sp["per_view"] for sp in scored_posts)
        if all_have_views:
            ranked = sorted(
                scored_posts,
                key=lambda sp: (sp["score"], sp["engagement"], sp["views"] or 0),
                reverse=True,
            )
            top_post = ranked[0]
            bottom_post = ranked[-1] if len(ranked) >= 2 else None
        else:
            ranked = sorted(
                scored_posts,
                key=lambda sp: (sp["engagement"], sp["views"] or 0),
                reverse=True,
            )
            top_post = dict(ranked[0], score=ranked[0]["engagement"], score_unit="engagement")
            if len(ranked) >= 2:
                bottom_post = dict(ranked[-1], score=ranked[-1]["engagement"], score_unit="engagement")

    drafts = list(data.get("drafts_created") or [])
    notes = list(data.get("research_notes") or [])
    rem_obj = data.get("reminders") or {}
    rem_done = list(rem_obj.get("done") or [])
    rem_missed = list(rem_obj.get("missed") or [])

    counts = {
        "posts_posted": len(posts),
        "posts_with_results": len(scored_posts),
        "posts_missing_results": len(missing_results),
        "drafts_created": len(drafts),
        "research_notes_added": len(notes),
        "reminders_done": len(rem_done),
        "reminders_missed": len(rem_missed),
        "prev_posts_posted": len(prev_posts),
    }

    return {
        "platforms": platforms_map,
        "platform_rows": platform_rows,
        "overall": overall,
        "top_post": top_post,
        "bottom_post": bottom_post,
        "missing_results": missing_results,
        "counts": counts,
    }


def _add_number_variants(allowed, num, *, include_rounded_int=False):
    if num is None or isinstance(num, bool):
        return
    try:
        val = float(num)
    except (TypeError, ValueError):
        return
    if not math.isfinite(val):
        return
    for candidate in (val, abs(val)):
        allowed.add(round(candidate, 6))
        allowed.add(round(candidate, 4))
        allowed.add(round(candidate, 2))
        allowed.add(round(candidate, 1))
        if candidate.is_integer():
            allowed.add(float(int(candidate)))
        elif include_rounded_int:
            allowed.add(float(round(candidate)))


def _add_dates_from_range(allowed, week_start_str):
    if not week_start_str:
        return
    try:
        start = date.fromisoformat(str(week_start_str)[:10])
    except ValueError:
        return
    for offset in range(7):
        d = start + timedelta(days=offset)
        allowed.add(float(d.year))
        allowed.add(float(d.month))
        allowed.add(float(d.day))


def _walk_allowed_numbers(node, allowed, *, allow_rounded_floats=False):
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        _add_number_variants(allowed, node, include_rounded_int=allow_rounded_floats)
        return
    if isinstance(node, str):
        # Extract date numbers and numeric tokens from input strings (titles, timestamps)
        cleaned = _URL_RE.sub(" ", node)
        for match in _TEXT_NUM_RE.finditer(cleaned):
            raw_num = match.group(1).replace(",", "")
            suffix = (match.group(2) or "").lower()
            try:
                val = float(raw_num)
            except ValueError:
                continue
            if suffix == "k":
                val *= 1_000.0
            elif suffix == "m":
                val *= 1_000_000.0
            _add_number_variants(allowed, val, include_rounded_int=False)
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_allowed_numbers(v, allowed, allow_rounded_floats=allow_rounded_floats)
        return
    if isinstance(node, list):
        for item in node:
            _walk_allowed_numbers(item, allowed, allow_rounded_floats=allow_rounded_floats)


def _collect_urls(node, out):
    if isinstance(node, str):
        for m in _URL_RE.finditer(node):
            out.add(m.group(0))
    elif isinstance(node, dict):
        for v in node.values():
            _collect_urls(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_urls(item, out)


def collect_allowed_numbers(data, highlights):
    """Collect the set of allowed float values grounded in data, highlights, and dates."""
    allowed = set()
    _walk_allowed_numbers(data, allowed, allow_rounded_floats=False)
    _walk_allowed_numbers(highlights, allowed, allow_rounded_floats=True)
    _add_dates_from_range(allowed, (data or {}).get("week_start"))
    _add_dates_from_range(allowed, ((data or {}).get("previous_week") or {}).get("week_start"))
    return allowed


def extract_numbers_from_text(text, data=None):
    """Extract numeric magnitudes from generated text, stripping verbatim data URLs and list bullets."""
    cleaned = str(text or "")
    if data:
        urls = set()
        _collect_urls(data, urls)
        for url in sorted(urls, key=len, reverse=True):
            cleaned = cleaned.replace(url, " ")
    cleaned = _LEADING_LIST_NUM_RE.sub(" ", cleaned)
    found = []
    for match in _TEXT_NUM_RE.finditer(cleaned):
        raw = match.group(1).replace(",", "")
        suffix = (match.group(2) or "").lower()
        try:
            val = float(raw)
        except ValueError:
            continue
        if suffix == "k":
            val *= 1_000.0
        elif suffix == "m":
            val *= 1_000_000.0
        if math.isfinite(val):
            found.append(val)
    return found


def text_uses_only_allowed_numbers(text, data, highlights):
    """Return True if every number cited in text appears in data/highlights (or allowed dates/counts)."""
    allowed = collect_allowed_numbers(data, highlights)
    for num in extract_numbers_from_text(text, data=data):
        if round(num, 6) in allowed or round(num, 4) in allowed or round(num, 2) in allowed or round(num, 1) in allowed:
            continue
        if any(abs(num - cand) <= 1e-4 for cand in allowed):
            continue
        return False
    return True


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    return cand if os.path.exists(cand) else paths.resource("config", "product_rules.json")


def _data_mentions_confidential(data):
    try:
        checker = cloud_sync.confidential_checker()
    except Exception:
        return True
    parts = [data.get("week_label") or ""]
    for post in data.get("posts") or []:
        parts.append(post.get("title") or "")
        parts.append(post.get("product_id") or "")
    for post in (data.get("previous_week") or {}).get("posts") or []:
        parts.append(post.get("title") or "")
        parts.append(post.get("product_id") or "")
    for draft in data.get("drafts_created") or []:
        parts.append(draft.get("title") or "")
    for note in data.get("research_notes") or []:
        parts.append(note.get("title") or "")
    return checker("\n".join(parts))


def _build_narrative_prompts(data, highlights):
    rules_text = ""
    rp = _rules_path()
    if os.path.exists(rp):
        try:
            rules_text = product_rules.prompt_block(rp)
        except Exception:
            rules_text = ""

    system_parts = [
        "You are the Analyst for Magnm Studio.",
        "Write a weekly performance summary from the verified numbers and lists below.",
        "Rules:",
        "- Write 3 to 6 sentences summarizing performance, followed by 2 to 3 concrete recommendations.",
        "- Never invent numbers. Only cite numbers, dates, counts, and percentages that appear in the input data.",
        "- Follow the product rules strictly. Use zero emojis.",
    ]
    if rules_text:
        system_parts.append(rules_text)

    payload_lines = [
        f"Week (IST): {data['week_start']} to {data['week_end']} ({data['week_label']})",
        f"Previous week (IST): {data['previous_week']['week_start']} to {data['previous_week']['week_end']}",
        f"Counts: {highlights['counts']}",
        "Per-platform highlights:",
    ]
    for row in highlights.get("platform_rows") or []:
        payload_lines.append(
            f"- {row['platform']}: posts={row['posts_count']}, with_results={row['with_results_count']}, "
            f"totals={row['totals']}, averages={row['averages']}, wow_change_pct={row['wow_change']}"
        )
    if not highlights.get("platform_rows"):
        payload_lines.append("- (no posts marked posted this week)")

    if highlights.get("top_post"):
        tp = highlights["top_post"]
        payload_lines.append(
            f"Top post: {tp['title']} ({tp['platform']}) score={tp['score']} ({tp['score_unit']}), metrics={tp['metrics']}"
        )
    if highlights.get("bottom_post"):
        bp = highlights["bottom_post"]
        payload_lines.append(
            f"Bottom post: {bp['title']} ({bp['platform']}) score={bp['score']} ({bp['score_unit']}), metrics={bp['metrics']}"
        )
    if highlights.get("missing_results"):
        missing_titles = [f"{m['title']} ({m['platform']})" for m in highlights["missing_results"]]
        payload_lines.append(f"Posts missing results: {missing_titles}")
    if data.get("drafts_created"):
        payload_lines.append(
            f"Drafts created ({len(data['drafts_created'])}): {[d['title'] for d in data['drafts_created']]}"
        )
    if data.get("research_notes"):
        payload_lines.append(
            f"Research notes added ({len(data['research_notes'])}): {[n['title'] for n in data['research_notes']]}"
        )
    rem = data.get("reminders") or {}
    payload_lines.append(
        f"Reminders done: {len(rem.get('done') or [])}, missed: {len(rem.get('missed') or [])}"
    )

    return "\n".join(system_parts), "\n".join(payload_lines)


def generate_summary(week_start=None, *, notify=True, propose_learnings=True, now=None):
    """Build weekly tables, optionally request grounded AI narrative + learnings, store and notify."""
    data = build_week_data(week_start=week_start, now=now)
    highlights = compute_highlights(data)
    sys_prompt, user_prompt = _build_narrative_prompts(data, highlights)
    is_conf = _data_mentions_confidential(data)

    narrative = ""
    status = "tables_only"
    ai_ok = False
    try:
        res = ai_gateway.generate_completion_with_meta(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            agent_role="analyst",
            confidential=is_conf,
        )
        raw_text = str((res or {}).get("text") or "").strip()
        if raw_text:
            ai_ok = True
            if text_uses_only_allowed_numbers(raw_text, data, highlights):
                narrative = raw_text
                status = "ready"
            else:
                narrative = NARRATIVE_SKIPPED_NOTE
                status = "narrative_skipped"
    except Exception:
        narrative = ""
        status = "tables_only"

    summary = db.upsert_weekly_summary(
        week_start=data["week_start"],
        data=data,
        highlights=highlights,
        narrative=narrative,
        status=status,
    )

    proposed_learnings = []
    if propose_learnings and ai_ok:
        import learnings
        proposed_learnings = learnings.propose_for_summary(
            summary,
            data=data,
            highlights=highlights,
            notify=notify,
        )

    if notify:
        title = format_week_title(data["week_start"])
        counts = highlights.get("counts") or {}
        body = (
            f"{counts.get('posts_posted', 0)} posts, "
            f"{counts.get('posts_with_results', 0)} with results, "
            f"{counts.get('posts_missing_results', 0)} missing results"
        )
        notifications.notify(
            "weekly_summary",
            title,
            body=body,
            route=f"#/today/summary/{summary['id']}",
        )

    return {
        "summary": summary,
        "learnings": proposed_learnings,
    }


def list_summaries(limit=52):
    """Return stored weekly summaries newest week first."""
    return db.list_weekly_summaries(limit=limit)


def get_summary(summary_id):
    """Return a weekly summary by ID or raise WeeklySummaryError(404)."""
    summary = db.get_weekly_summary(summary_id)
    if summary is None:
        raise WeeklySummaryError("Weekly summary not found", status=404)
    return summary
