#!/usr/bin/env python3
"""Pitch on the phone without the laptop.

Two halves:

* mirror_records() gives the cloud mirror one record per proposal (kind pitch_proposal): everything the phone needs
  to list, present and edit it, plus the version (the proposal's updated_at) the phone's edits are based on. A
  confidential proposal is only a stub.
* apply() carries out what the phone queued (pitch_create, pitch_update, pitch_finalize, pitch_link) through the same
  functions and checks as the web app (pitch.create_proposal, update_proposal, finalize_proposal, pitch_links).

Everything that comes from the cloud is hostile: only whitelisted fields are used, texts may not contain "<" or ">",
prices must be whole numbers a person typed, and nothing is applied to a confidential proposal or with text naming a
confidential product. If the laptop copy changed since the phone's version, the phone's edit becomes a new copy
marked "(phone)": both are kept and nothing is overwritten.
"""

import json
import os
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import confidential_guard
import db
import phone_server
import pitch
import pitch_host
import pitch_links

KINDS = ("pitch_create", "pitch_update", "pitch_finalize", "pitch_link")
MIRROR_KIND = "pitch_proposal"
MAX_MIRRORED = 100
CREATED_BY = "phone"
COPY_SUFFIX = " (phone)"
MARKER_KIND = "_phone_pitch_marker"
MAX_VERSION_CHARS = 64

CONFIDENTIAL_REASON = "Confidential items can only be changed on the laptop."
CONFIDENTIAL_TEXT_REASON = "Not applied: the text names a confidential product."
NOT_FOUND_REASON = "Not applied: it was not found on this laptop."

_CREATE_KEYS = frozenset({"kind", "from_preset"})
_FIELD_KEYS = frozenset(
    {"client_name", "business_name", "contact", "notes", "preset", "hidden_sheets", "hidden_about", "price_overrides"}
)
_CONTACT_KEYS = frozenset({"name", "business", "email", "phone", "notes"})
_PRESET_TEXT_KEYS = frozenset({"title", "lead", "whatWeDo", "portfolio", "thisPlan"})
_PRESET_KEYS = _PRESET_TEXT_KEYS | {"initialSelected", "sections"}
_PRESET_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_BAD_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UUID_HEX_RE = re.compile(r"[^0-9a-f]")


class Reject(Exception):
    """An action that must not be applied, with a short reason that is safe to show."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# --- Checking what the phone sent ---

def _text(value, what="text"):
    """One text from the phone: a string without control characters or angle brackets."""
    if not isinstance(value, str) or _BAD_CHARS_RE.search(value):
        raise Reject(f"Not applied: the {what} was malformed.")
    if "<" in value or ">" in value:
        raise Reject("Not applied: text cannot contain < or >.")
    return value.strip()


def _string_list(value, what, allowed=None, limit=200):
    if not isinstance(value, list) or len(value) > limit:
        raise Reject(f"Not applied: {what} was malformed.")
    out = []
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item) <= 80 or _BAD_CHARS_RE.search(item) or "<" in item or ">" in item:
            raise Reject(f"Not applied: {what} was malformed.")
        if allowed is not None and item not in allowed:
            raise Reject(f"Not applied: {what} was malformed.")
        out.append(item)
    return out


def _clean_fields(fields, allow_create_fields):
    """Rebuild the whitelisted fields of a proposal from a hostile dict, or raise Reject."""
    allowed = _FIELD_KEYS | (_CREATE_KEYS if allow_create_fields else frozenset())
    if not isinstance(fields, dict) or not set(fields) <= allowed:
        raise Reject("Not applied: the edit has fields a phone may not change.")
    if not fields and not allow_create_fields:
        raise Reject("Not applied: the edit was empty.")
    out = {}
    for key in ("client_name", "business_name", "notes"):
        if key in fields:
            out[key] = _text(fields[key], key.replace("_", " "))
    if "contact" in fields:
        contact = fields["contact"]
        if not isinstance(contact, dict) or not set(contact) <= _CONTACT_KEYS:
            raise Reject("Not applied: the contact details were malformed.")
        out["contact"] = {k: _text(v, "contact detail") for k, v in contact.items()}
    if "preset" in fields:
        preset = fields["preset"]
        if not isinstance(preset, dict) or not set(preset) <= _PRESET_KEYS:
            raise Reject("Not applied: the plan texts were malformed.")
        clean = {}
        for key, value in preset.items():
            if key in _PRESET_TEXT_KEYS:
                clean[key] = _text(value, "plan text")
            elif key == "initialSelected":
                clean[key] = _string_list(value, "the selected services")
            else:
                if not isinstance(value, dict) or not set(value) <= set(pitch.VALID_SECTION_KEYS):
                    raise Reject("Not applied: the sheet services were malformed.")
                clean[key] = {sheet: _string_list(ids, "the sheet services") for sheet, ids in value.items()}
        out["preset"] = clean
    if "hidden_sheets" in fields:
        out["hidden_sheets"] = _string_list(fields["hidden_sheets"], "the hidden sheets", set(pitch.VALID_SECTION_KEYS), 8)
    if "hidden_about" in fields:
        out["hidden_about"] = _string_list(fields["hidden_about"], "the hidden sections", set(pitch.VALID_ABOUT_KEYS), 16)
    if "price_overrides" in fields:
        prices = fields["price_overrides"]
        if not isinstance(prices, dict) or len(prices) > 100:
            raise Reject("Not applied: the prices were malformed.")
        clean = {}
        for item_id, price in prices.items():
            if (
                not isinstance(item_id, str) or not 1 <= len(item_id) <= 64 or _BAD_CHARS_RE.search(item_id)
                or isinstance(price, bool) or not isinstance(price, int) or not pitch.MIN_PRICE <= price <= pitch.MAX_PRICE
            ):
                raise Reject("Not applied: a price must be a whole number from 0 to 10,000,000.")
            clean[item_id] = price
        out["price_overrides"] = clean
    if "kind" in fields:
        if fields["kind"] not in ("proposal", "quote_form"):
            raise Reject("Not applied: a phone can only make a proposal or a quote form.")
        out["kind"] = fields["kind"]
    if "from_preset" in fields:
        slug = fields["from_preset"]
        if not isinstance(slug, str) or not _PRESET_SLUG_RE.fullmatch(slug):
            raise Reject("Not applied: the preset was malformed.")
        out["from_preset"] = slug
    return out


def validate_payload(kind, payload):
    """Return the checked parameters for a pitch kind, or raise Reject. Never trusts the shape it is given."""
    if kind == "pitch_create":
        return {"fields": _clean_fields(payload, True)}
    if kind == "pitch_update":
        if not isinstance(payload, dict) or set(payload) != {"base_version", "fields"}:
            raise Reject("Not applied: the edit was malformed.")
        base = payload["base_version"]
        if not isinstance(base, str) or not 1 <= len(base) <= MAX_VERSION_CHARS or _BAD_CHARS_RE.search(base):
            raise Reject("Not applied: the proposal version was malformed.")
        return {"base_version": base, "fields": _clean_fields(payload["fields"], False)}
    if kind == "pitch_finalize":
        if payload != {}:
            raise Reject("Not applied: the final quotation details were malformed.")
        return {}
    if kind == "pitch_link":
        if not isinstance(payload, dict) or set(payload) != {"action"} or payload["action"] not in ("publish", "withdraw"):
            raise Reject("Not applied: the link details were malformed.")
        return {"action": payload["action"]}
    raise Reject("Not applied: this laptop does not support that kind of action.")


def _strings(value):
    """Every string in a nested value, keys included, for the confidentiality check."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _names_confidential(guard, value):
    """True if any text in value names a confidential product or repo."""
    _, _, _, checker, repo_patterns = guard
    for text in _strings(value):
        if text and (phone_server._text_is_confidential(text, checker, repo_patterns) or confidential_guard.mentions(text)):
            return True
    return False


def _is_confidential(prop, guard):
    """True if a stored proposal, or the payload built from it, trips the confidential guard."""
    conf_sids, conf_cids, conf_pids, checker, repo_patterns = guard
    hydrated = pitch._hydrate_proposal(prop) if prop.get("kind") == "default" else prop
    payload = pitch_host.build_proposal_payload(hydrated, mode="present")
    return phone_server._pitch_proposal_is_confidential(
        hydrated, payload, conf_sids, conf_cids, conf_pids, checker, repo_patterns
    )


def _describe(exc):
    """Why the Pitch rules refused something: field names and rules only, never the text that was sent."""
    errors = getattr(exc, "errors", None) or []
    parts = []
    for err in errors[:4]:
        field = re.sub(r"[^A-Za-z0-9_.\[\]]", "", str(err.get("field") or ""))[:60]
        rule = re.sub(r"[^A-Za-z0-9_]", "", str(err.get("rule") or ""))[:40]
        if field or rule:
            parts.append(f"{field} ({rule})" if field and rule else field or rule)
    detail = ", ".join(dict.fromkeys(parts)) or "the Pitch rules"
    return f"Not applied: the Pitch rules refused {detail}."[:300]


def _tail(action_id):
    return _UUID_HEX_RE.sub("", str(action_id).lower())[:8] or "0"


def _slug_from(base, action_id, fallback):
    words = re.sub(r"[^a-z0-9]+", "-", str(base or "").lower()).strip("-")[:40].strip("-")
    return f"{words or fallback}-{_tail(action_id)}"


def _marker(user_id, action_id):
    raw = db.get_cloud_sync_value(user_id, MARKER_KIND, action_id)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _set_marker(user_id, action_id, **state):
    db.upsert_cloud_sync_state(
        user_id, [(MARKER_KIND, action_id, json.dumps(state), datetime.now(timezone.utc).isoformat(), None)]
    )


def _note_count(result, proposal):
    warnings = proposal.get("warnings") if isinstance(proposal, dict) else None
    count = len(warnings) if isinstance(warnings, list) else 0
    return result + (f" {count} product rule note{'s' if count != 1 else ''}: check it on the laptop." if count else "")


# --- Applying ---

def _existing(target):
    prop = db.get_pitch_proposal(target)
    if prop is None:
        raise Reject(NOT_FOUND_REASON)
    return prop


def _create(action_id, params, guard):
    fields = dict(params["fields"])
    if _names_confidential(guard, fields):
        raise Reject(CONFIDENTIAL_TEXT_REASON)
    slug = _slug_from(fields.get("business_name") or fields.get("client_name"), action_id, "phone-proposal")
    found = db.get_pitch_proposal_by_slug(slug)
    if found is not None and found.get("created_by") == CREATED_BY:
        return "applied", "Already created."
    data = dict(fields, slug=slug)
    try:
        made = pitch.create_proposal(data, created_by=CREATED_BY)
    except pitch.NotFoundError:
        raise Reject("Not applied: that preset was not found on this laptop.")
    except pitch.ValidationError as exc:
        raise Reject(_describe(exc))
    return "applied", _note_count("Proposal created.", made)


def _updates_from(existing, fields):
    """The dict for pitch.update_proposal: contact is merged into the laptop's, everything else replaces."""
    updates = {}
    for key, value in fields.items():
        if key == "contact":
            updates["contact"] = {**(existing.get("contact") or {}), **value}
        else:
            updates[key] = deepcopy(value)
    return updates


def _matches(existing, fields):
    """True if the laptop copy already holds every value the phone is setting."""
    for key, value in fields.items():
        if key == "contact":
            have = existing.get("contact") or {}
            if any(str(have.get(k, "")).strip() != v for k, v in value.items()):
                return False
        elif key == "preset":
            have = existing.get("preset") or {}
            for pkey, pvalue in value.items():
                cur = have.get(pkey)
                if isinstance(pvalue, str):
                    if str(cur or "").strip() != pvalue:
                        return False
                elif cur != pvalue:
                    return False
        elif key in ("client_name", "business_name", "notes"):
            if str(existing.get(key) or "").strip() != value:
                return False
        elif existing.get(key) != value:
            return False
    return True


def _copy_names(existing, fields):
    """Names for a conflict copy: the phone's edit, marked "(phone)" at the end of what the proposal is called."""
    client = fields.get("client_name", existing.get("client_name") or "")
    business = fields.get("business_name", existing.get("business_name") or "")
    limit = 200 - len(COPY_SUFFIX)
    if client.strip():
        return {"client_name": client.strip()[:limit] + COPY_SUFFIX, "business_name": business}
    if business.strip():
        return {"client_name": client, "business_name": business.strip()[:limit] + COPY_SUFFIX}
    return {"client_name": "Proposal" + COPY_SUFFIX, "business_name": business}


def _update(user_id, action_id, target, params, guard):
    existing = _existing(target)
    if existing.get("kind") == "default":
        raise Reject("Not applied: the default quotation cannot be edited.")
    if _is_confidential(existing, guard):
        raise Reject(CONFIDENTIAL_REASON)
    fields = params["fields"]
    if _names_confidential(guard, fields):
        raise Reject(CONFIDENTIAL_TEXT_REASON)

    marker = _marker(user_id, action_id)
    if marker is not None:
        # This laptop began this very edit before: if the version moved on, it was applied. Never apply it twice.
        if marker.get("state") == "done" or existing.get("updated_at") != marker.get("base"):
            _set_marker(user_id, action_id, state="done")
            return "applied", "Already applied."

    if existing.get("updated_at") == params["base_version"]:
        _set_marker(user_id, action_id, state="started", base=existing.get("updated_at"))
        try:
            saved = pitch.update_proposal(target, _updates_from(existing, fields))
        except pitch.ValidationError as exc:
            _set_marker(user_id, action_id, state="done")
            raise Reject(_describe(exc))
        _set_marker(user_id, action_id, state="done")
        return "applied", _note_count("Saved on the laptop.", saved)

    if _matches(existing, fields):
        return "applied", "Already the same on the laptop."

    # The laptop copy changed since the phone's version: keep both. The phone's edit becomes a new copy.
    slug = f"{str(existing['slug'])[:44].rstrip('-')}-phone-{_tail(action_id)}"
    found = db.get_pitch_proposal_by_slug(slug)
    if found is not None and found.get("created_by") == CREATED_BY:
        return "applied", "Already saved as a copy."
    data = _updates_from(existing, fields)
    data.update(_copy_names(existing, fields))
    data.update({"duplicate_of": target, "slug": slug, "status": "draft"})
    try:
        made = pitch.create_proposal(data, created_by=CREATED_BY)
    except pitch.ValidationError as exc:
        raise Reject(_describe(exc))
    return "applied", _note_count(
        'Kept both: your edit is a new copy marked "(phone)", because the proposal changed on the laptop first.', made
    )


def _finalize(action_id, target, guard):
    src = _existing(target)
    if src.get("kind") == "default":
        raise Reject("Not applied: the default quotation cannot be made final from the phone.")
    if src.get("kind") == "final":
        raise Reject("Not applied: it is already a final quotation.")
    if _is_confidential(src, guard):
        raise Reject(CONFIDENTIAL_REASON)
    slug = f"{str(src['slug'])[:44].rstrip('-')}-final-{_tail(action_id)}"
    found = db.get_pitch_proposal_by_slug(slug)
    if found is not None and found.get("created_by") == CREATED_BY:
        return "applied", "Already made."
    try:
        made = pitch.finalize_proposal(target, overrides={"slug": slug}, created_by=CREATED_BY)
    except pitch.ValidationError as exc:
        raise Reject(_describe(exc))
    return "applied", _note_count("Final quotation created. The original is kept.", made)


def _link(target, params, guard):
    prop = _existing(target)
    if _is_confidential(prop, guard):
        raise Reject(CONFIDENTIAL_REASON)
    publish = params["action"] == "publish"
    try:
        if publish:
            pitch_links.publish(target)
        else:
            pitch_links.withdraw(target)
    except pitch_links.PitchLinkError as exc:
        if exc.code == "confidential":
            raise Reject(CONFIDENTIAL_REASON)
        if exc.status and 400 <= exc.status < 500 and exc.code not in ("not_signed_in", "rules_unreadable"):
            raise Reject("Not applied: the link could not be changed.")
        raise RuntimeError("pitch link change failed")  # retried a few times by the queue, then given up
    except pitch.ValidationError as exc:
        raise Reject(_describe(exc))
    return "applied", "Link published." if publish else "Link withdrawn."


def apply(kind, user_id, action_id, target, params, guard):
    """Apply one checked pitch action. Returns (outcome, result); raises Reject to refuse it. Safe to repeat."""
    try:
        if kind == "pitch_create":
            return _create(action_id, params, guard)
        if kind == "pitch_update":
            return _update(user_id, action_id, target, params, guard)
        if kind == "pitch_finalize":
            return _finalize(action_id, target, guard)
        if kind == "pitch_link":
            return _link(target, params, guard)
    except pitch.NotFoundError:
        raise Reject(NOT_FOUND_REASON)
    raise Reject("Not applied: this laptop does not support that kind of action.")


def target_exists(kind, target_id):
    """True if the proposal an action is about exists in THIS laptop's data (a new proposal has no target)."""
    if kind == "pitch_create":
        return True
    return db.get_pitch_proposal(target_id) is not None


# --- The cloud mirror ---

def _catalog_for_phone(admin_overrides):
    """Item names, groups and base prices (with the team's admin prices) and the built-in presets, for the editor."""
    cat = pitch_host.load_catalog()
    prices = {iid: int(item["p"]) for iid, item in (cat.get("items") or {}).items()}
    pitch_host._apply_price_overrides(prices, admin_overrides)
    return {
        "items": {
            iid: {"g": str(item.get("g") or ""), "n": str(item.get("n") or iid), "p": prices[iid]}
            for iid, item in (cat.get("items") or {}).items()
        },
        "groups": list(cat.get("groups") or []),
        "standalone_fee": cat.get("standalone_fee"),
        "presets": [{"slug": slug, "name": str(p.get("name") or slug)} for slug, p in (cat.get("presets") or {}).items()],
    }


def _link_url(prop):
    if prop.get("link_status") != "live":
        return None
    prefix = pitch_links._ROUTE_PREFIX_BY_KIND.get(str(prop.get("kind") or ""))
    share = str(prop.get("share_id") or "")
    if not prefix or not pitch_links.SHARE_ID_RE.match(share):
        return None
    return f"{pitch_links.site_url()}{prefix}{share}"


def _record_data(prop, catalog, admin_overrides):
    hydrated = pitch._hydrate_proposal(prop) if prop.get("kind") == "default" else prop
    preset = hydrated.get("preset") if isinstance(hydrated.get("preset"), dict) else {}
    editable = hydrated.get("kind") != "default"
    return {
        "name": phone_server._pitch_proposal_name(hydrated),
        "kind": hydrated.get("kind") or "proposal",
        "status": hydrated.get("status"),
        "version": hydrated.get("updated_at"),
        "editable": editable,
        "client_name": hydrated.get("client_name") or "",
        "business_name": hydrated.get("business_name") or "",
        "contact": hydrated.get("contact") or {},
        "notes": hydrated.get("notes") or "",
        "preset": {
            "title": preset.get("title") or "",
            "lead": preset.get("lead") or "",
            "whatWeDo": preset.get("whatWeDo") or "",
            "portfolio": preset.get("portfolio") or "",
            "thisPlan": preset.get("thisPlan") or "",
            "initialSelected": list(preset.get("initialSelected") or []),
            "sections": preset.get("sections") or {},
        },
        "hidden_sheets": list(hydrated.get("hidden_sheets") or []),
        "hidden_about": list(hydrated.get("hidden_about") or []),
        "price_overrides": dict(hydrated.get("price_overrides") or {}),
        "share_id": hydrated.get("share_id"),
        "link_status": hydrated.get("link_status"),
        "published_at": hydrated.get("published_at"),
        "link_url": _link_url(hydrated),
        "created_at": hydrated.get("created_at"),
        "updated_at": hydrated.get("updated_at"),
        "payload": pitch_host.build_proposal_payload(hydrated, mode="present", price_overrides=admin_overrides),
        "catalog": catalog,
    }


def mirror_records(make_record, fallback_ts):
    """One mirror record per proposal. A confidential proposal, or any one that cannot be checked, is a bare stub."""
    default = pitch.ensure_default_proposal()
    rows = db.list_pitch_proposals(limit=MAX_MIRRORED)
    if not any(r["id"] == default["id"] for r in rows):
        rows = [default] + rows[: MAX_MIRRORED - 1]
    try:
        guard = phone_server._build_confidential_guard()
    except Exception:
        guard = None
    admin_overrides = pitch_host.admin_base_price_overrides()
    catalog = _catalog_for_phone(admin_overrides)
    records = []
    for prop in rows:
        stamp = prop.get("updated_at") or prop.get("created_at") or fallback_ts
        stub = make_record(
            MIRROR_KIND, prop["id"], None, "Confidential item",
            {"confidential": True, "updated_at": prop.get("updated_at")}, stamp,
        )
        try:
            if guard is None or _is_confidential(prop, guard):
                records.append(stub)
                continue
            data = _record_data(prop, catalog, admin_overrides)
            if _names_confidential(guard, data):
                records.append(stub)
                continue
            records.append(make_record(MIRROR_KIND, prop["id"], None, data["name"], data, stamp))
        except Exception:
            records.append(stub)
    return records
