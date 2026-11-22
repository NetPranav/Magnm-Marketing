#!/usr/bin/env python3
"""Model bake-off runner, blind rating sheet generator, and scorer."""

from datetime import datetime, timezone
import json
import os
import random
import statistics
import sys
import time
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agents
import ai_gateway
import confidential_guard
import db
import paths
import product_rules
import review


def _resolve_readable_path(*parts):
    candidate = paths.data(*parts)
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource(*parts)
    return fallback if os.path.exists(fallback) else candidate


def load_briefs(limit=5, path=None):
    """Load up to `limit` built-in bake-off briefs from config/bakeoff_briefs.json."""
    cfg_path = path or _resolve_readable_path("config", "bakeoff_briefs.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    briefs = data.get("briefs") if isinstance(data, dict) else data
    if not isinstance(briefs, list):
        raise ValueError("bakeoff_briefs.json must contain a 'briefs' list")
    n = max(1, int(limit or len(briefs)))
    return [dict(b) for b in briefs[:n]]


def _grounding_facts_block(product_id="cero"):
    """Load verified product facts and product rules as a plain-text prompt block."""
    parts = []
    facts_path = _resolve_readable_path("research", "product-facts.md")
    if os.path.exists(facts_path):
        with open(facts_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        # Facts of confidential products never go to the cloud models being compared.
        raw = confidential_guard.drop_confidential_sections(raw).strip()
        if raw:
            parts.append(f"Verified product-facts.md:\n{raw}")

    try:
        approved = db.list_product_facts(product_id=product_id, status="approved", limit=200)
        if confidential_guard.product_is_confidential(product_id):
            approved = []
        if approved:
            lines = []
            for f in approved:
                src = f" (source: {f['source_url']})" if f.get("source_url") else ""
                lines.append(f"- {f['text']}{src}")
            parts.append("Approved database facts:\n" + "\n".join(lines))
    except Exception:
        pass

    rules_path = _resolve_readable_path("config", "product_rules.json")
    if os.path.exists(rules_path):
        try:
            parts.append(product_rules.prompt_block(rules_path))
        except Exception:
            pass

    return "\n\n".join(parts)


def _label_for_index(idx):
    """Convert 0-based index into A, B, ..., Z, AA, AB, ..."""
    n = int(idx)
    out = ""
    while True:
        out = chr(ord("A") + (n % 26)) + out
        n = n // 26 - 1
        if n < 0:
            break
    return out


def _build_blind_sheet_markdown(role, timestamp, briefs, outputs_by_brief):
    """Render a blind Markdown evaluation sheet with zero model names."""
    lines = [
        "# Model Bake-Off Rating Sheet",
        "",
        f"- Role: {role}",
        f"- Timestamp: {timestamp}",
        f"- Briefs: {len(briefs)}",
        "",
        "Rate each option from 1 (poor) to 5 (ready to publish).",
        'Save ratings as JSON: `{"<brief_id>": {"A": 4, "B": 3, ...}}`.',
        "",
    ]

    for brief in briefs:
        bid = brief["id"]
        lines.extend([
            f"## {bid}: {brief.get('title') or bid}",
            "",
            f"- Platform: {brief.get('platform') or 'unspecified'} | Type: {brief.get('type') or 'post'}",
            f"- Prompt: {brief.get('prompt') or ''}",
            "",
        ])
        for entry in outputs_by_brief.get(bid, []):
            label = entry["label"]
            auto = entry.get("auto_review") or {}
            findings = auto.get("findings") or []
            warn_count = sum(1 for f in findings if f.get("severity") == "warn")
            lines.extend([
                f"### Option {label}",
                "",
                f"- Chars: {entry.get('chars', 0)} | Auto-review: {auto.get('verdict', 'ok')} ({warn_count} warnings)",
            ])
            if findings:
                for f in findings:
                    lines.append(f"  - [{f.get('severity')}] {f.get('message')}")
            if entry.get("error"):
                lines.extend(["", f"[GENERATION ERROR: {entry['error']}]", ""])
            else:
                lines.extend(["", entry.get("text") or "", ""])
            lines.extend([f"Rating {bid} / {label} (1-5): ___", ""])

    return "\n".join(lines).rstrip() + "\n"


def _compute_per_model_stats(models, briefs, outputs_by_brief):
    stats = {}
    for m in models:
        latencies = []
        warn_findings = 0
        warn_briefs = 0
        errors = 0
        for brief in briefs:
            for entry in outputs_by_brief.get(brief["id"], []):
                if entry.get("model") != m:
                    continue
                latencies.append(int(entry.get("latency_ms") or 0))
                if entry.get("error"):
                    errors += 1
                auto = entry.get("auto_review") or {}
                wf = sum(1 for f in (auto.get("findings") or []) if f.get("severity") == "warn")
                warn_findings += wf
                if auto.get("verdict") == "warn":
                    warn_briefs += 1
        stats[m] = {
            "model": m,
            "count": len(latencies),
            "median_latency_ms": int(round(statistics.median(latencies))) if latencies else 0,
            "auto_review_warns": warn_findings,
            "warn_briefs": warn_briefs,
            "errors": errors,
        }
    return stats


def run_bakeoff(role, models, briefs_count=5, seed=None):
    """Run `briefs_count` briefs across `models` for `role`, writing results.json and blind sheet.md."""
    ag = agents.get_agent(role)
    if ag is None:
        raise ValueError(f"Unknown role '{role}'")
    canon_role = ag["id"]

    model_list = [str(m).strip() for m in (models or []) if str(m).strip()]
    if not model_list:
        raise ValueError("At least one model must be specified")

    paths.ensure_data_root()
    db.ensure_init()

    system_prompt = agents.build_system_prompt(canon_role)
    briefs = load_briefs(limit=briefs_count)
    rng = random.Random(seed)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = paths.data("outputs", "bakeoff", ts)
    if os.path.exists(out_dir):
        ts = f"{ts}_{uuid.uuid4().hex[:4]}"
        out_dir = paths.data("outputs", "bakeoff", ts)
    os.makedirs(out_dir, exist_ok=True)

    outputs_by_brief = {}
    key_map = {}

    for brief in briefs:
        bid = brief["id"]
        product_id = brief.get("product_id") or "cero"
        grounding = _grounding_facts_block(product_id=product_id)
        user_prompt = (
            f"Task ({brief.get('platform') or 'general'} / {brief.get('type') or 'post'}): {brief['prompt']}\n\n"
            "Do not call any tools or output tool calls. Output only the final copy.\n\n"
            f"### Grounding Material\n{grounding}"
        )

        raw_entries = []
        for model_name in model_list:
            t0 = time.monotonic()
            error_msg = None
            text = ""
            model_used = None
            try:
                meta = ai_gateway.generate_completion_with_meta(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    agent_role=canon_role,
                    force_live_only=True,
                    model=model_name,
                )
                text = (meta.get("text") or "").strip()
                model_used = meta.get("model_used") or model_name
            except Exception as exc:
                error_msg = str(exc)
            latency_ms = int((time.monotonic() - t0) * 1000)

            if error_msg and not text:
                auto_res = {
                    "verdict": "warn",
                    "findings": [
                        {
                            "code": "generation_error",
                            "severity": "warn",
                            "message": error_msg,
                        }
                    ],
                }
            else:
                auto_res = review.auto_review(
                    {
                        "type": brief.get("type") or "post",
                        "platform": brief.get("platform") or "",
                        "title": brief.get("title") or "",
                        "body": text,
                        "product_id": product_id,
                    }
                )

            raw_entries.append({
                "model": model_name,
                "model_used": model_used,
                "latency_ms": latency_ms,
                "chars": len(text),
                "auto_review": auto_res,
                "text": text,
                "error": error_msg,
            })

        indices = list(range(len(raw_entries)))
        rng.shuffle(indices)

        labeled = []
        brief_key = {}
        for pos, raw_idx in enumerate(indices):
            label = _label_for_index(pos)
            entry = dict(raw_entries[raw_idx])
            entry["label"] = label
            labeled.append(entry)
            brief_key[label] = entry["model"]

        outputs_by_brief[bid] = labeled
        key_map[bid] = brief_key

    per_model = _compute_per_model_stats(model_list, briefs, outputs_by_brief)
    results_payload = {
        "timestamp": ts,
        "role": canon_role,
        "models": model_list,
        "briefs": briefs,
        "outputs": outputs_by_brief,
        "key": key_map,
        "summary": per_model,
    }

    results_path = os.path.join(out_dir, "results.json")
    sheet_path = os.path.join(out_dir, "sheet.md")

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, ensure_ascii=False)

    sheet_md = _build_blind_sheet_markdown(canon_role, ts, briefs, outputs_by_brief)
    with open(sheet_path, "w", encoding="utf-8") as f:
        f.write(sheet_md)

    return {
        "timestamp": ts,
        "role": canon_role,
        "results_path": results_path,
        "sheet_path": sheet_path,
        "results": results_payload,
        "summary": per_model,
    }


def _find_latest_results_path():
    base_dir = paths.data("outputs", "bakeoff")
    if not os.path.isdir(base_dir):
        return None
    dirs = sorted(
        [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))],
        reverse=True,
    )
    for d in dirs:
        cand = os.path.join(base_dir, d, "results.json")
        if os.path.isfile(cand):
            return cand
    return None


def get_latest_results(reveal=False):
    """Load the most recent bakeoff results.json, omitting key/model mappings unless reveal=True."""
    res_path = _find_latest_results_path()
    if not res_path:
        return None
    with open(res_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if reveal:
        return data

    blind = {
        "timestamp": data.get("timestamp"),
        "role": data.get("role"),
        "briefs": data.get("briefs") or [],
        "outputs": {},
    }
    for bid, entries in (data.get("outputs") or {}).items():
        blind_entries = []
        for entry in entries or []:
            blind_entries.append({
                "label": entry.get("label"),
                "latency_ms": entry.get("latency_ms"),
                "chars": entry.get("chars"),
                "auto_review": entry.get("auto_review"),
                "text": entry.get("text"),
                "error": entry.get("error"),
            })
        blind["outputs"][bid] = blind_entries
    return blind


def score_bakeoff(ratings_path, results_path=None):
    """Score human ratings JSON against bakeoff results and suggest primary/backup models."""
    with open(ratings_path, "r", encoding="utf-8") as f:
        ratings = json.load(f)
    if not isinstance(ratings, dict):
        raise ValueError("Ratings file must contain a JSON object {brief_id: {label: score}}")

    resolved_results = results_path
    if not resolved_results:
        sibling = os.path.join(os.path.dirname(os.path.abspath(ratings_path)), "results.json")
        if os.path.isfile(sibling):
            resolved_results = sibling
        else:
            resolved_results = _find_latest_results_path()
    if not resolved_results or not os.path.isfile(resolved_results):
        raise FileNotFoundError("Could not locate bakeoff results.json to score against")

    with open(resolved_results, "r", encoding="utf-8") as f:
        results = json.load(f)

    role = results.get("role") or "writer"
    models = list(results.get("models") or [])
    key_map = results.get("key") or {}
    outputs_by_brief = results.get("outputs") or {}
    briefs = results.get("briefs") or []

    scores_by_model = {m: [] for m in models}
    for bid, label_scores in ratings.items():
        if not isinstance(label_scores, dict):
            continue
        brief_key = key_map.get(bid) or {}
        for label, score_val in label_scores.items():
            model_name = brief_key.get(label)
            if not model_name:
                continue
            try:
                num = float(score_val)
            except (TypeError, ValueError):
                continue
            scores_by_model.setdefault(model_name, []).append(num)

    base_stats = _compute_per_model_stats(list(scores_by_model.keys()), briefs, outputs_by_brief)
    ranking = []
    for m, st in base_stats.items():
        m_scores = scores_by_model.get(m) or []
        avg = round(sum(m_scores) / len(m_scores), 2) if m_scores else 0.0
        ranking.append({
            "model": m,
            "avg_score": avg,
            "rated_count": len(m_scores),
            "auto_review_warns": st["auto_review_warns"],
            "warn_briefs": st["warn_briefs"],
            "median_latency_ms": st["median_latency_ms"],
            "errors": st["errors"],
        })

    ranking.sort(
        key=lambda r: (
            -r["avg_score"],
            r["auto_review_warns"],
            r["median_latency_ms"],
            r["errors"],
            r["model"],
        )
    )

    suggested_primary = ranking[0]["model"] if ranking else None
    suggested_backup = ranking[1]["model"] if len(ranking) >= 2 else None

    return {
        "role": role,
        "results_path": resolved_results,
        "ranking": ranking,
        "suggested_primary": suggested_primary,
        "suggested_backup": suggested_backup,
    }
