#!/usr/bin/env python3
"""Publish Pitch proposals as public client links and receive client quote requests."""

from datetime import datetime, timezone
import json
import os
import re
import secrets
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cloud_sync
import db
import events
import paths
import pitch
import pitch_host
import team_auth

DEFAULT_SITE_URL = "https://magnm-pitch.vercel.app"
SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

SIGN_IN_REQUIRED_MESSAGE = "Sign in under Settings, Account to share links."
CONFIDENTIAL_REFUSE_MESSAGE = "This proposal names a confidential product, so it can't be shared."
RULES_UNREADABLE_MESSAGE = "Product rules could not be read, so this proposal can't be shared."

_ROUTE_PREFIX_BY_KIND = {
    "proposal": "/p/",
    "quote_form": "/q/",
    "default": "/q/",
    "final": "/f/",
}

_REMOTE_KIND_BY_KIND = {
    "proposal": "proposal",
    "quote_form": "proposal",
    "final": "final",
    "default": "default_quote",
}

_PUBLIC_MODE_BY_KIND = {
    "proposal": "present",
    "final": "present",
    "quote_form": "form",
    "default": "form",
}

_INTERNAL_PAYLOAD_KEYS = (
    "proposal_id",
    "notes",
    "created_by",
    "share_id",
    "pitch_commit",
    "published_at",
    "link_status",
    "pricing_summary",
    "changes",
    "warnings",
)


class PitchLinkError(ValueError):
    """Error raised when sharing, republishing, or withdrawing a proposal link fails."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    cleaned = ts.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_newer_iso(candidate_ts, baseline_ts):
    if not candidate_ts:
        return False
    if not baseline_ts:
        return True
    dt_cand = _parse_iso(candidate_ts)
    dt_base = _parse_iso(baseline_ts)
    if dt_cand is not None and dt_base is not None:
        return dt_cand > dt_base
    return str(candidate_ts) > str(baseline_ts)


def site_url():
    """Return the public Magnm-Pitch site URL from config/supabase.json or MAGNM_PITCH_SITE_URL."""
    env_url = os.environ.get("MAGNM_PITCH_SITE_URL")
    if env_url and env_url.strip():
        return env_url.strip().rstrip("/")

    cfg_path = paths.resource("config", "supabase.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                raw = loaded.get("pitch_site_url")
                if isinstance(raw, str) and raw.strip():
                    return raw.strip().rstrip("/")
        except Exception:
            pass

    return DEFAULT_SITE_URL


def _generate_share_id():
    for _ in range(16):
        token = secrets.token_urlsafe(16)
        if SHARE_ID_RE.match(token) and not db.pitch_share_id_exists(token):
            return token
    raise RuntimeError("Failed to generate unique share_id")


def ensure_share_id(proposal):
    """Ensure proposal has a valid 16+ char url-safe share_id, generating and saving if needed."""
    if not isinstance(proposal, dict):
        raise pitch.ValidationError("proposal must be an object")

    current = str(proposal.get("share_id") or "").strip()
    if SHARE_ID_RE.match(current):
        return current

    new_id = _generate_share_id()
    pid = proposal.get("id")
    if pid and db.get_pitch_proposal(pid) is not None:
        db.set_pitch_share_id(pid, new_id)
    proposal["share_id"] = new_id
    return new_id


def link_for(proposal):
    """Return the public client URL for proposal, replacing missing/short share_id if needed."""
    if not isinstance(proposal, dict):
        raise pitch.ValidationError("proposal must be an object")
    kind = str(proposal.get("kind") or "proposal").strip()
    prefix = _ROUTE_PREFIX_BY_KIND.get(kind)
    if not prefix:
        raise PitchLinkError(f"Unsupported proposal kind '{kind}'")
    share_id = ensure_share_id(proposal)
    return f"{site_url()}{prefix}{share_id}"


def public_payload(proposal):
    """Build sanitized public payload for proposal, refusing confidential products."""
    if not isinstance(proposal, dict):
        raise pitch.ValidationError("proposal must be an object")

    hydrated = pitch._hydrate_proposal(proposal)
    kind = str(hydrated.get("kind") or "proposal").strip()
    mode = _PUBLIC_MODE_BY_KIND.get(kind)
    if not mode:
        raise PitchLinkError(f"Unsupported proposal kind '{kind}'")

    payload = pitch_host.build_payload(
        mode=mode,
        proposal=hydrated,
        price_overrides=pitch.get_base_price_overrides(),
    )
    for key in _INTERNAL_PAYLOAD_KEYS:
        payload.pop(key, None)

    raw_contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    payload["contact"] = {
        "name": str(raw_contact.get("name") or ""),
        "business": str(raw_contact.get("business") or ""),
    }

    try:
        checker = cloud_sync.confidential_checker()
    except Exception as exc:
        raise PitchLinkError(RULES_UNREADABLE_MESSAGE, status=400, code="rules_unreadable") from exc

    serialized = json.dumps(payload, ensure_ascii=False)
    if checker(serialized):
        raise PitchLinkError(CONFIDENTIAL_REFUSE_MESSAGE, status=400, code="confidential")

    return payload


def _require_active_user_id():
    try:
        acct = team_auth.current_account()
    except team_auth.TeamAuthError as exc:
        if exc.status in (401, 503) or exc.code in ("not_signed_in", "session_expired", "not_configured"):
            raise PitchLinkError(SIGN_IN_REQUIRED_MESSAGE, status=400, code="not_signed_in") from exc
        status = 502 if exc.status in (0, None) or exc.status >= 500 else 400
        raise PitchLinkError(exc.message, status=status) from exc

    if not acct.get("configured") or not acct.get("signed_in") or acct.get("deactivated"):
        raise PitchLinkError(SIGN_IN_REQUIRED_MESSAGE, status=400, code="not_signed_in")

    user = acct.get("user") or {}
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        raise PitchLinkError(SIGN_IN_REQUIRED_MESSAGE, status=400, code="not_signed_in")
    return user_id


def _upsert_remote_proposal(prop, user_id, payload, rest_fn=None):
    kind = str(prop.get("kind") or "proposal").strip()
    remote_kind = _REMOTE_KIND_BY_KIND.get(kind)
    if not remote_kind:
        raise PitchLinkError(f"Unsupported proposal kind '{kind}'")

    caller = rest_fn or team_auth.rest
    caller(
        "POST",
        "published_proposals?on_conflict=share_id",
        body={
            "share_id": prop["share_id"],
            "owner_id": user_id,
            "local_proposal_id": prop["id"],
            "kind": remote_kind,
            "payload": payload,
            "status": "live",
            "expires_at": None,
        },
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def publish(proposal_id):
    """Publish a proposal to Supabase published_proposals and mark it live locally."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise pitch.ValidationError("proposal_id is required")

    user_id = _require_active_user_id()
    prop = db.get_pitch_proposal(pid)
    if prop is None:
        raise pitch.NotFoundError(f"Proposal '{pid}' not found")
    prop = pitch._hydrate_proposal(prop)

    url = link_for(prop)
    payload = public_payload(prop)

    try:
        _upsert_remote_proposal(prop, user_id, payload)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401 or exc.code in ("not_signed_in", "session_expired", "not_configured"):
            raise PitchLinkError(SIGN_IN_REQUIRED_MESSAGE, status=400, code="not_signed_in") from exc
        status = 502 if exc.status in (0, None) or exc.status >= 500 else 400
        raise PitchLinkError(exc.message, status=status) from exc

    published_at = _now_iso()
    db.set_pitch_link_state(prop["id"], "live", published_at=published_at, share_id=prop["share_id"])
    events.publish("pitch.updated", {"proposal_id": prop["id"], "what": "link_published", "url": url})
    return {"url": url, "published_at": published_at}


def withdraw(proposal_id):
    """Withdraw a published proposal link on Supabase and update local link_status."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise pitch.ValidationError("proposal_id is required")

    _require_active_user_id()
    prop = db.get_pitch_proposal(pid)
    if prop is None:
        raise pitch.NotFoundError(f"Proposal '{pid}' not found")

    share_id = str(prop.get("share_id") or "").strip()
    if share_id:
        q_sid = urllib.parse.quote(share_id, safe="")
        try:
            team_auth.rest(
                "PATCH",
                f"published_proposals?share_id=eq.{q_sid}",
                body={"status": "withdrawn"},
                headers={"Prefer": "return=minimal"},
            )
        except team_auth.TeamAuthError as exc:
            if exc.status == 401 or exc.code in ("not_signed_in", "session_expired", "not_configured"):
                raise PitchLinkError(SIGN_IN_REQUIRED_MESSAGE, status=400, code="not_signed_in") from exc
            status = 502 if exc.status in (0, None) or exc.status >= 500 else 400
            raise PitchLinkError(exc.message, status=status) from exc

    db.set_pitch_link_state(prop["id"], "withdrawn")
    events.publish("pitch.updated", {"proposal_id": prop["id"], "what": "link_withdrawn"})
    return {"ok": True, "proposal_id": prop["id"], "link_status": "withdrawn"}


def count_pending_republishes():
    """Return count of live proposals whose updated_at is newer than published_at."""
    count = 0
    for prop in db.list_live_pitch_proposals():
        if not prop.get("published_at") or _is_newer_iso(prop.get("updated_at"), prop.get("published_at")):
            count += 1
    return count


def sync_live_links(user_id, is_signed_out=None):
    """Republish any live proposals whose local updated_at is newer than their last publish."""
    live_props = db.list_live_pitch_proposals()
    republished = 0
    for prop in live_props:
        if is_signed_out and is_signed_out():
            raise cloud_sync._SignedOutAbort()
        if prop.get("published_at") and not _is_newer_iso(prop.get("updated_at"), prop.get("published_at")):
            continue

        hydrated = pitch._hydrate_proposal(prop)
        ensure_share_id(hydrated)
        payload = public_payload(hydrated)
        try:
            _upsert_remote_proposal(
                hydrated,
                user_id,
                payload,
                rest_fn=cloud_sync._rest_with_auth_retry,
            )
        except team_auth.TeamAuthError as exc:
            cloud_sync._handle_rest_error(exc)

        now_iso = _now_iso()
        db.set_pitch_link_state(
            hydrated["id"],
            "live",
            published_at=now_iso,
            share_id=hydrated["share_id"],
        )
        republished += 1
    return republished


def _proposal_title(prop, share_id):
    if not prop:
        return f"Proposal ({share_id[:8]})" if share_id else "Proposal"
    if prop.get("kind") == "default":
        return "Default quotation"
    parts = [str(x).strip() for x in (prop.get("business_name"), prop.get("client_name")) if str(x or "").strip()]
    if parts:
        return " · ".join(parts)
    preset = prop.get("preset") if isinstance(prop.get("preset"), dict) else {}
    return str(preset.get("name") or prop.get("slug") or share_id)


def compute_request_summary(proposal, choices):
    """Compute item list and total locally from choices.selected + custom_selected using proposal prices."""
    choices_obj = choices if isinstance(choices, dict) else {}
    raw_selected = [
        str(iid).strip()
        for iid in (choices_obj.get("selected") or [])
        if isinstance(iid, str) and iid.strip()
    ]
    raw_custom = [
        str(iid).strip()
        for iid in (choices_obj.get("custom_selected") or [])
        if isinstance(iid, str) and iid.strip()
    ]
    combined_ids = list(dict.fromkeys(raw_selected + raw_custom))

    cat = pitch_host.load_catalog()
    cat_items = cat.get("items") or {}
    standalone_fee = int(cat.get("standalone_fee", 4000))

    prices = {iid: int(item["p"]) for iid, item in cat_items.items()}
    pitch_host._apply_price_overrides(prices, pitch.get_base_price_overrides())

    custom_items = []
    service_plans = []
    if isinstance(proposal, dict):
        hydrated = pitch._hydrate_proposal(proposal)
        if hydrated.get("kind") != "default":
            pitch_host._apply_price_overrides(prices, hydrated.get("price_overrides"))
            custom_items = list(hydrated.get("custom_items") or [])
            service_plans = list(hydrated.get("service_plans") or [])

    all_items = pitch_host.build_all_items(cat_items, custom_items)
    custom_map = {
        c["id"]: c
        for c in custom_items
        if isinstance(c, dict) and c.get("id")
    }
    expanded, _ = pitch_host.expand_selected_ids(combined_ids, all_items)

    selected_items = []
    for iid in expanded:
        if iid in custom_map:
            citem = custom_map[iid]
            selected_items.append({
                "id": iid,
                "name": str(citem.get("n") or iid),
                "price": int(citem.get("p", 0)),
                "group": str(citem.get("g") or ""),
            })
        elif iid in cat_items:
            item = cat_items[iid]
            selected_items.append({
                "id": iid,
                "name": str(item.get("n") or iid),
                "price": int(prices.get(iid, item.get("p", 0))),
                "group": str(item.get("g") or ""),
            })

    computed_total = pitch_host._compute_final_total(
        combined_ids,
        prices,
        custom_items,
        standalone_fee,
        service_plans,
        cat_items,
    )

    raw_shown = choices_obj.get("total_shown")
    if isinstance(raw_shown, (int, float)) and not isinstance(raw_shown, bool):
        total_shown = int(raw_shown) if float(raw_shown).is_integer() else float(raw_shown)
        total_mismatch = float(raw_shown) != float(computed_total)
    else:
        total_shown = None
        total_mismatch = False

    return {
        "selected_items": selected_items,
        "computed_total": computed_total,
        "total_shown": total_shown,
        "total_mismatch": total_mismatch,
    }


def _enrich_quote_request(row, prop=None):
    share_id = str(row.get("share_id") or "")
    if prop is None and row.get("local_proposal_id"):
        prop = db.get_pitch_proposal(row["local_proposal_id"])
    if prop is None and share_id:
        prop = db.get_pitch_proposal_by_share_id(share_id)

    summary = compute_request_summary(prop, row.get("choices"))
    raw_contact = row.get("contact") if isinstance(row.get("contact"), dict) else {}
    contact = {
        "name": str(raw_contact.get("name") or ""),
        "business": str(raw_contact.get("business") or ""),
        "phone": str(raw_contact.get("phone") or ""),
        "notes": str(raw_contact.get("notes") or ""),
    }

    return {
        "id": row["id"],
        "share_id": share_id,
        "local_proposal_id": row.get("local_proposal_id") or (prop["id"] if prop else None),
        "proposal_title": _proposal_title(prop, share_id),
        "proposal_slug": prop.get("slug") if prop else None,
        "choices": row.get("choices") or {},
        "contact": contact,
        "selected_items": summary["selected_items"],
        "computed_total": summary["computed_total"],
        "total_shown": summary["total_shown"],
        "total_mismatch": summary["total_mismatch"],
        "created_at": row["created_at"],
        "received_at": row["received_at"],
        "read_at": row.get("read_at"),
    }


def poll_quote_requests(user_id, is_signed_out=None):
    """Fetch this user's unseen quote_submissions, store them locally, and patch seen_at.

    Always filter by owner: an admin can read every member's submissions, and without the filter the admin's
    laptop would take (and mark seen) requests meant for the member who sent the proposal.
    """
    owner = urllib.parse.quote(str(user_id or "").strip(), safe="-")
    if not owner:
        return 0
    if is_signed_out and is_signed_out():
        raise cloud_sync._SignedOutAbort()

    try:
        rows = cloud_sync._rest_with_auth_retry(
            "GET",
            f"quote_submissions?owner_id=eq.{owner}&seen_at=is.null&order=created_at.asc&limit=50",
        )
    except team_auth.TeamAuthError as exc:
        cloud_sync._handle_rest_error(exc)

    if not isinstance(rows, list) or not rows:
        return 0

    stored_ids = []
    new_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        req_id = str(row.get("id") or "").strip()
        share_id = str(row.get("share_id") or "").strip()
        if not req_id or not share_id or str(row.get("owner_id") or owner) != str(user_id):
            continue

        prop = db.get_pitch_proposal_by_share_id(share_id)
        local_proposal_id = prop["id"] if prop is not None else None
        choices = row.get("choices") if isinstance(row.get("choices"), dict) else {}
        contact = row.get("contact") if isinstance(row.get("contact"), dict) else {}
        created_at = str(row.get("created_at") or _now_iso())
        received_at = _now_iso()

        inserted = db.insert_pitch_quote_request(
            request_id=req_id,
            share_id=share_id,
            local_proposal_id=local_proposal_id,
            choices=choices,
            contact=contact,
            created_at=created_at,
            received_at=received_at,
        )
        stored_ids.append(req_id)

        if inserted:
            new_count += 1
            stored_row = db.get_pitch_quote_request(req_id) or {
                "id": req_id,
                "share_id": share_id,
                "local_proposal_id": local_proposal_id,
                "choices": choices,
                "contact": contact,
                "created_at": created_at,
                "received_at": received_at,
                "read_at": None,
            }
            enriched = _enrich_quote_request(stored_row, prop=prop)
            events.publish("pitch.quote_request", enriched)
            client_label = (contact.get("business") or contact.get("name") or "Client").strip() or "Client"
            db.log_activity(
                "Pitch",
                "CEO",
                f"Quote request from {client_label} for {enriched['proposal_title']}",
                f"pitch:{local_proposal_id or share_id}",
                ts=created_at,
            )

    if stored_ids:
        if is_signed_out and is_signed_out():
            raise cloud_sync._SignedOutAbort()
        id_list = ",".join(urllib.parse.quote(sid, safe="-_") for sid in stored_ids)
        try:
            cloud_sync._rest_with_auth_retry(
                "PATCH",
                f"quote_submissions?owner_id=eq.{owner}&id=in.({id_list})",
                body={"seen_at": _now_iso()},
                headers={"Prefer": "return=minimal"},
            )
        except team_auth.TeamAuthError as exc:
            cloud_sync._handle_rest_error(exc)

    return new_count


def list_requests(limit=200):
    """List local quote requests newest first with proposal title, totals, and unread count."""
    rows = db.list_pitch_quote_requests(limit=limit)
    prop_by_id = {}
    prop_by_share = {}
    enriched = []
    for row in rows:
        pid = row.get("local_proposal_id")
        sid = row.get("share_id")
        prop = None
        if pid:
            if pid not in prop_by_id:
                prop_by_id[pid] = db.get_pitch_proposal(pid)
            prop = prop_by_id[pid]
        if prop is None and sid:
            if sid not in prop_by_share:
                prop_by_share[sid] = db.get_pitch_proposal_by_share_id(sid)
            prop = prop_by_share[sid]
        enriched.append(_enrich_quote_request(row, prop=prop))

    return {
        "requests": enriched,
        "unread_count": db.count_unread_pitch_quote_requests(),
    }


def mark_request_read(request_id):
    """Mark a local quote request as read and return the updated record and unread count."""
    rid = str(request_id or "").strip()
    if not rid:
        raise pitch.ValidationError("request_id is required")
    row = db.mark_pitch_quote_request_read(rid)
    if row is None:
        raise pitch.NotFoundError(f"Quote request '{rid}' not found")
    return {
        "ok": True,
        "request": _enrich_quote_request(row),
        "unread_count": db.count_unread_pitch_quote_requests(),
    }
