#!/usr/bin/env python3
"""Proposals, base prices, Pitch rules validation, outbox events, and PDF export."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import re
import secrets
import sys
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import events
import paths
import pitch_host
import product_rules

VALID_KINDS = ("proposal", "quote_form")
VALID_STATUSES = ("draft", "ready", "sent", "archived")
VALID_SECTION_KEYS = ("p2", "p3", "p4", "p5")

# RULES.md #1 & #2: '/', 'general', 'admin', 'clients', and built-in presets are reserved.
RESERVED_STATIC_SLUGS = frozenset({"general", "admin", "clients"})

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
CUSTOM_ID_RE = re.compile(r"^custom_[a-zA-Z0-9_-]{1,56}$")

MIN_PRICE = 0
MAX_PRICE = 10_000_000
MAX_PDF_BYTES = 10 * 1024 * 1024

MAX_SHORT_TEXT = 200
MAX_MEDIUM_TEXT = 1000
MAX_LONG_TEXT = 4000

_GROUP_TO_SECTION = {
    "Web & SaaS": "p2",
    "Mobile app": "p3",
    "Backend": "p4",
    "AI & Automation": "p5",
    "DevOps & Maintenance": "p5",
}


class NotFoundError(ValueError):
    """Raised when a requested proposal, preset, catalog item, or PDF does not exist."""


class ValidationError(ValueError):
    """Raised when input or Pitch anti-hijacking rules fail validation."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        if errors is not None:
            self.errors = list(errors)
        else:
            self.errors = [{"field": "", "rule": "validation", "message": str(message)}]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _is_int(val):
    return isinstance(val, int) and not isinstance(val, bool)


def _rules_path():
    candidate = paths.data("config", "product_rules.json")
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource("config", "product_rules.json")
    return fallback if os.path.exists(fallback) else candidate


def _local_user_label():
    """Return local user label for outbox payloads.

    No local 'who am I' identity setting exists in the engine yet (Google
    sign-in and user profiles arrive in Phase 4), so this returns None.
    """
    return None


def _publish(proposal_id, what, extra=None):
    payload = {"proposal_id": proposal_id, "what": str(what)}
    if isinstance(extra, dict):
        payload.update(extra)
    events.publish("pitch.updated", payload)


def pitch_version():
    """Return vendored PITCH_VERSION.json metadata without file hashes."""
    version_path = os.path.join(pitch_host.vendor_dir(), "PITCH_VERSION.json")
    with open(version_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if k != "files"}


def _raw_catalog():
    return pitch_host.load_catalog()


def reserved_slugs():
    """Return the set of reserved slugs that proposals cannot use."""
    raw = _raw_catalog()
    preset_slugs = set((raw.get("presets") or {}).keys())
    return set(RESERVED_STATIC_SLUGS) | preset_slugs


def get_base_price_overrides():
    """Return {item_id: int_price} for all admin base price overrides."""
    rows = db.list_pitch_base_prices()
    return {item_id: int(entry["price"]) for item_id, entry in rows.items()}


def catalog():
    """Return Pitch catalog.json merged with admin base price overrides."""
    raw = _raw_catalog()
    raw_items = raw.get("items") or {}
    overrides = db.list_pitch_base_prices()

    merged_items = {}
    for item_id, item in raw_items.items():
        base_price = int(item["p"])
        ovr = overrides.get(item_id)
        eff_price = int(ovr["price"]) if ovr is not None else base_price
        merged_items[item_id] = {
            "g": item["g"],
            "n": item["n"],
            "d": item["d"],
            "r": list(item.get("r") or []),
            "p": eff_price,
            "base_price": base_price,
            "price": eff_price,
            "overridden": ovr is not None,
        }

    return {
        "items": merged_items,
        "groups": list(raw.get("groups") or []),
        "standalone_fee": int(raw.get("standalone_fee", 4000)),
        "standalone_label": str(raw.get("standalone_label") or ""),
        "presets": deepcopy(raw.get("presets") or {}),
    }


def set_base_price(item_id, price, actor="admin"):
    """Set an admin base price override for a catalog item.

    There are no user roles yet; Phase 4 restricts this operation to admins.
    """
    iid = str(item_id or "").strip()
    if not iid:
        raise ValidationError("item_id is required")

    raw_items = _raw_catalog().get("items") or {}
    if iid not in raw_items:
        raise NotFoundError(f"Catalog item '{iid}' not found")

    if not _is_int(price) or price < MIN_PRICE or price > MAX_PRICE:
        raise ValidationError(
            f"price must be an integer between {MIN_PRICE} and {MAX_PRICE}",
            errors=[{
                "field": "price",
                "rule": "price_range",
                "message": f"price must be an integer between {MIN_PRICE} and {MAX_PRICE}",
            }],
        )

    base_price = int(raw_items[iid]["p"])
    existing = db.get_pitch_base_price(iid)
    old_price = int(existing["price"]) if existing is not None else base_price

    db.set_pitch_base_price(iid, int(price), updated_by=str(actor or "admin"))
    db.append_sync_outbox(
        "base_price.updated",
        {
            "item_id": iid,
            "old_price": old_price,
            "new_price": int(price),
            "base_price": base_price,
            "updated_by": str(actor or "admin"),
            "user_label": _local_user_label(),
            "reset": False,
        },
    )
    _publish(None, "base_price.updated", {"item_id": iid})

    merged = catalog()["items"][iid]
    return {
        "admin_only": True,
        "item_id": iid,
        "item": merged,
        "old_price": old_price,
        "new_price": int(price),
    }


def reset_base_price(item_id, actor="admin"):
    """Reset an admin base price override back to Pitch's catalog price.

    There are no user roles yet; Phase 4 restricts this operation to admins.
    """
    iid = str(item_id or "").strip()
    if not iid:
        raise ValidationError("item_id is required")

    raw_items = _raw_catalog().get("items") or {}
    if iid not in raw_items:
        raise NotFoundError(f"Catalog item '{iid}' not found")

    base_price = int(raw_items[iid]["p"])
    existing = db.get_pitch_base_price(iid)
    old_price = int(existing["price"]) if existing is not None else base_price
    removed = db.delete_pitch_base_price(iid)

    if removed:
        db.append_sync_outbox(
            "base_price.updated",
            {
                "item_id": iid,
                "old_price": old_price,
                "new_price": base_price,
                "base_price": base_price,
                "updated_by": str(actor or "admin"),
                "user_label": _local_user_label(),
                "reset": True,
            },
        )
        _publish(None, "base_price.reset", {"item_id": iid})

    merged = catalog()["items"][iid]
    return {
        "admin_only": True,
        "item_id": iid,
        "item": merged,
        "old_price": old_price,
        "new_price": base_price,
        "reset": removed,
    }


def _generate_unique_share_id():
    for _ in range(16):
        token = secrets.token_urlsafe(12)
        if not db.pitch_share_id_exists(token):
            return token
    raise RuntimeError("Failed to generate unique share_id")


def _default_preset_template(kind="proposal", slug=""):
    general = deepcopy((_raw_catalog().get("presets") or {}).get("general") or {})
    return {
        "slug": slug,
        "name": "",
        "badge": "",
        "clientName": "",
        "businessName": "",
        "title": "magnm Build Planner",
        "lead": general.get("lead", ""),
        "whatWeDo": general.get("whatWeDo", ""),
        "portfolio": general.get("portfolio", "magnm-org.vercel.app"),
        "thisPlan": "" if kind == "quote_form" else general.get("thisPlan", ""),
        "architectureBlock": deepcopy(general.get("architectureBlock") or {"heading": "", "sub": "", "roles": []}),
        "initialSelected": [],
        "sections": deepcopy(general.get("sections") or {"p2": [], "p3": [], "p4": [], "p5": []}),
    }


def _add_violation(violations, field, rule, message):
    violations.append({"field": field, "rule": rule, "message": message})


def _trim_and_check_len(val, field, limit, violations, required=False, forbid_angle_brackets=False):
    if val is None:
        text = ""
    elif not isinstance(val, str):
        _add_violation(violations, field, "text_type", f"{field} must be a string")
        return ""
    else:
        text = val.strip()

    if required and not text:
        _add_violation(violations, field, "required", f"{field} is required")
    if forbid_angle_brackets and ("<" in text or ">" in text):
        _add_violation(violations, field, "unsafe_characters", "Text cannot contain < or >")
    if len(text) > limit:
        _add_violation(violations, field, "text_length", f"{field} exceeds maximum length of {limit} characters")
    return text


def _expand_initial_selected(initial_ids, all_items):
    """Port Pitch's addWithDeps(): expand initialSelected with transitive dependencies r."""
    selected = []
    seen = set()
    added = []

    def add_with_deps(item_id, is_dep=False):
        if item_id in seen or item_id not in all_items:
            return
        seen.add(item_id)
        selected.append(item_id)
        if is_dep:
            added.append(item_id)
        for dep_id in all_items[item_id].get("r") or []:
            add_with_deps(dep_id, is_dep=True)

    for iid in initial_ids:
        add_with_deps(iid, is_dep=False)
    return selected, added


def _validate_and_normalize(candidate, existing_id=None):
    """Validate proposal fields against ported Pitch rules; collect all violations at once."""
    violations = []
    raw_cat = _raw_catalog()
    cat_items = raw_cat.get("items") or {}
    groups = list(raw_cat.get("groups") or [])
    reserved = reserved_slugs()

    # Rule: kind and status validation.
    kind = str(candidate.get("kind") or "proposal").strip()
    if kind not in VALID_KINDS:
        _add_violation(violations, "kind", "invalid_kind", f"kind must be one of: {', '.join(VALID_KINDS)}")

    status = str(candidate.get("status") or "draft").strip()
    if status not in VALID_STATUSES:
        _add_violation(violations, "status", "invalid_status", f"status must be one of: {', '.join(VALID_STATUSES)}")

    # Rule (RULES.md #1 & #2, check-rules.js #1 & #2): Slug format, reserved slugs, and uniqueness.
    # General planner ('general') and '/admin', '/clients', and built-in presets are never modified.
    raw_slug = candidate.get("slug")
    if not isinstance(raw_slug, str):
        _add_violation(violations, "slug", "slug_format", "slug must be a string")
        slug = ""
    else:
        slug = raw_slug.strip()
        if not SLUG_RE.match(slug):
            _add_violation(
                violations,
                "slug",
                "slug_format",
                "slug must match ^[a-z0-9][a-z0-9-]{1,62}$ (lowercase letters, digits, hyphens, 2-63 chars)",
            )
        if slug in reserved:
            _add_violation(
                violations,
                "slug",
                "reserved_slug",
                f"slug '{slug}' is reserved; general planner and built-in routes cannot be overwritten",
            )
        if slug:
            dup = db.get_pitch_proposal_by_slug(slug)
            if dup is not None and dup["id"] != existing_id:
                _add_violation(violations, "slug", "slug_unique", f"slug '{slug}' is already in use")

    # Rule (text hygiene): Trim and length-limit proposal text fields.
    client_name = _trim_and_check_len(
        candidate.get("client_name"), "client_name", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
    )
    business_name = _trim_and_check_len(
        candidate.get("business_name"), "business_name", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
    )
    notes = _trim_and_check_len(candidate.get("notes"), "notes", MAX_LONG_TEXT, violations)

    raw_contact = candidate.get("contact")
    contact = {}
    if raw_contact is not None:
        if not isinstance(raw_contact, dict):
            _add_violation(violations, "contact", "invalid_type", "contact must be an object")
        else:
            for cfield, climit in (("name", MAX_SHORT_TEXT), ("email", MAX_SHORT_TEXT), ("phone", MAX_SHORT_TEXT), ("notes", MAX_MEDIUM_TEXT)):
                if cfield in raw_contact and raw_contact[cfield] is not None:
                    contact[cfield] = _trim_and_check_len(raw_contact[cfield], f"contact.{cfield}", climit, violations)

    # Rule (check-rules.js #3): Validate custom items (id prefix custom_, 5 groups, integer price, non-empty n/d).
    raw_custom = candidate.get("custom_items")
    custom_items = []
    custom_by_id = {}
    if raw_custom is None:
        raw_custom = []
    if not isinstance(raw_custom, list):
        _add_violation(violations, "custom_items", "invalid_type", "custom_items must be an array")
        raw_custom = []

    for idx, item in enumerate(raw_custom):
        prefix = f"custom_items[{idx}]"
        if not isinstance(item, dict):
            _add_violation(violations, prefix, "invalid_type", f"{prefix} must be an object")
            continue
        cid = item.get("id")
        if not isinstance(cid, str) or not CUSTOM_ID_RE.match(cid.strip()):
            _add_violation(
                violations,
                f"{prefix}.id",
                "custom_item_id",
                f"{prefix}.id must start with 'custom_' and use only letters, digits, '-' or '_' (max 63 chars)",
            )
            cid_clean = ""
        else:
            cid_clean = cid.strip()
            if cid_clean in cat_items:
                _add_violation(violations, f"{prefix}.id", "custom_item_id", f"{prefix}.id collides with catalog item '{cid_clean}'")
            elif cid_clean in custom_by_id:
                _add_violation(violations, f"{prefix}.id", "custom_item_id", f"Duplicate custom item id '{cid_clean}'")

        grp = item.get("g")
        if not isinstance(grp, str) or grp.strip() not in groups:
            _add_violation(
                violations,
                f"{prefix}.g",
                "custom_item_group",
                f"{prefix}.g must be one of: {', '.join(groups)}",
            )
            grp_clean = grp.strip() if isinstance(grp, str) else ""
        else:
            grp_clean = grp.strip()

        n_val = _trim_and_check_len(
            item.get("n"), f"{prefix}.n", MAX_SHORT_TEXT, violations, required=True, forbid_angle_brackets=True
        )
        d_val = _trim_and_check_len(
            item.get("d"), f"{prefix}.d", MAX_MEDIUM_TEXT, violations, required=True, forbid_angle_brackets=True
        )

        p_val = item.get("p")
        if not _is_int(p_val) or p_val < MIN_PRICE or p_val > MAX_PRICE:
            _add_violation(
                violations,
                f"{prefix}.p",
                "price_range",
                f"{prefix}.p must be an integer between {MIN_PRICE} and {MAX_PRICE}",
            )
            p_clean = 0
        else:
            p_clean = int(p_val)

        r_raw = item.get("r", [])
        if r_raw is None:
            r_raw = []
        if not isinstance(r_raw, list):
            _add_violation(violations, f"{prefix}.r", "invalid_type", f"{prefix}.r must be an array")
            r_clean = []
        else:
            r_clean = [str(dep).strip() for dep in r_raw if isinstance(dep, str) and dep.strip()]
            if len(r_clean) != len(r_raw):
                _add_violation(violations, f"{prefix}.r", "invalid_type", f"{prefix}.r entries must be non-empty strings")

        normalized_custom = {
            "id": cid_clean,
            "g": grp_clean,
            "n": n_val,
            "d": d_val,
            "p": p_clean,
            "r": r_clean,
        }
        custom_items.append(normalized_custom)
        if cid_clean and cid_clean not in custom_by_id:
            custom_by_id[cid_clean] = normalized_custom

    all_items = dict(cat_items)
    all_items.update(custom_by_id)

    # Rule (check-rules.js #3): Every dependency id in custom_items[].r must exist in catalog or custom_items.
    for idx, citem in enumerate(custom_items):
        for dep in citem["r"]:
            if dep not in all_items:
                _add_violation(
                    violations,
                    f"custom_items[{idx}].r",
                    "unknown_item_id",
                    f"Custom item '{citem['id']}' depends on unknown item '{dep}'",
                )

    # Rule (pricing bounds & item existence): Validate proposal price_overrides.
    raw_overrides = candidate.get("price_overrides")
    price_overrides = {}
    if raw_overrides is None:
        raw_overrides = {}
    if not isinstance(raw_overrides, dict):
        _add_violation(violations, "price_overrides", "invalid_type", "price_overrides must be an object")
    else:
        for oid, oprice in raw_overrides.items():
            oid_str = str(oid or "").strip()
            if oid_str not in cat_items:
                _add_violation(
                    violations,
                    f"price_overrides.{oid_str}",
                    "unknown_item_id",
                    f"price_overrides references unknown catalog item '{oid_str}'",
                )
            if not _is_int(oprice) or oprice < MIN_PRICE or oprice > MAX_PRICE:
                _add_violation(
                    violations,
                    f"price_overrides.{oid_str}",
                    "price_range",
                    f"price_overrides['{oid_str}'] must be an integer between {MIN_PRICE} and {MAX_PRICE}",
                )
            else:
                price_overrides[oid_str] = int(oprice)

    # Rule (check-rules.js #2 & #3): Validate preset fields, sections, initialSelected, and architectureBlock.
    raw_preset = candidate.get("preset")
    if not isinstance(raw_preset, dict):
        _add_violation(violations, "preset", "invalid_type", "preset must be an object")
        raw_preset = {}

    preset = {
        "slug": slug,
        "name": _trim_and_check_len(
            raw_preset.get("name"), "preset.name", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
        ),
        "badge": _trim_and_check_len(
            raw_preset.get("badge"), "preset.badge", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
        ),
        "clientName": _trim_and_check_len(
            raw_preset.get("clientName"), "preset.clientName", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
        ),
        "businessName": _trim_and_check_len(
            raw_preset.get("businessName"), "preset.businessName", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
        ),
        "title": _trim_and_check_len(
            raw_preset.get("title"), "preset.title", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
        ),
        "lead": _trim_and_check_len(
            raw_preset.get("lead"), "preset.lead", MAX_MEDIUM_TEXT, violations, forbid_angle_brackets=True
        ),
        "whatWeDo": _trim_and_check_len(
            raw_preset.get("whatWeDo"), "preset.whatWeDo", MAX_MEDIUM_TEXT, violations, forbid_angle_brackets=True
        ),
        "portfolio": _trim_and_check_len(
            raw_preset.get("portfolio"), "preset.portfolio", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
        ),
        "thisPlan": _trim_and_check_len(
            raw_preset.get("thisPlan"), "preset.thisPlan", MAX_MEDIUM_TEXT, violations, forbid_angle_brackets=True
        ),
    }

    raw_arch = raw_preset.get("architectureBlock")
    arch_clean = {"heading": "", "sub": "", "roles": []}
    if raw_arch is not None:
        if not isinstance(raw_arch, dict):
            _add_violation(violations, "preset.architectureBlock", "invalid_type", "preset.architectureBlock must be an object")
        else:
            arch_clean["heading"] = _trim_and_check_len(
                raw_arch.get("heading"),
                "preset.architectureBlock.heading",
                MAX_SHORT_TEXT,
                violations,
                forbid_angle_brackets=True,
            )
            arch_clean["sub"] = _trim_and_check_len(
                raw_arch.get("sub"),
                "preset.architectureBlock.sub",
                MAX_MEDIUM_TEXT,
                violations,
                forbid_angle_brackets=True,
            )
            raw_roles = raw_arch.get("roles") or []
            if not isinstance(raw_roles, list):
                _add_violation(
                    violations, "preset.architectureBlock.roles", "invalid_type", "architectureBlock.roles must be an array"
                )
            else:
                for ridx, role in enumerate(raw_roles):
                    rfield = f"preset.architectureBlock.roles[{ridx}]"
                    if not isinstance(role, dict):
                        _add_violation(violations, rfield, "invalid_type", f"{rfield} must be an object")
                        continue
                    rlabel = _trim_and_check_len(
                        role.get("label"), f"{rfield}.label", MAX_SHORT_TEXT, violations, forbid_angle_brackets=True
                    )
                    raw_pts = role.get("points") or []
                    pts_clean = []
                    if not isinstance(raw_pts, list):
                        _add_violation(violations, f"{rfield}.points", "invalid_type", f"{rfield}.points must be an array")
                    else:
                        for pidx, pt in enumerate(raw_pts):
                            pts_clean.append(
                                _trim_and_check_len(
                                    pt,
                                    f"{rfield}.points[{pidx}]",
                                    MAX_MEDIUM_TEXT,
                                    violations,
                                    forbid_angle_brackets=True,
                                )
                            )
                    arch_clean["roles"].append({"label": rlabel, "points": pts_clean})
    preset["architectureBlock"] = arch_clean

    # Sections check: keys must be in p2..p5 and every id must exist in catalog or custom_items.
    raw_sections = raw_preset.get("sections")
    if raw_sections is None:
        raw_sections = {"p2": [], "p3": [], "p4": [], "p5": []}
    sections_clean = {"p2": [], "p3": [], "p4": [], "p5": []}
    if not isinstance(raw_sections, dict):
        _add_violation(violations, "preset.sections", "invalid_type", "preset.sections must be an object")
    else:
        for skey, svals in raw_sections.items():
            if skey not in VALID_SECTION_KEYS:
                _add_violation(
                    violations,
                    f"preset.sections.{skey}",
                    "invalid_section",
                    f"Section key '{skey}' must be one of: {', '.join(VALID_SECTION_KEYS)}",
                )
                continue
            if not isinstance(svals, list):
                _add_violation(
                    violations,
                    f"preset.sections.{skey}",
                    "invalid_type",
                    f"preset.sections.{skey} must be an array",
                )
                continue
            seen_sec = set()
            for idx, sid in enumerate(svals):
                if not isinstance(sid, str) or not sid.strip():
                    _add_violation(
                        violations,
                        f"preset.sections.{skey}[{idx}]",
                        "invalid_type",
                        f"preset.sections.{skey}[{idx}] must be a non-empty string",
                    )
                    continue
                sid_clean = sid.strip()
                if sid_clean not in all_items:
                    _add_violation(
                        violations,
                        f"preset.sections.{skey}",
                        "unknown_item_id",
                        f"preset.sections.{skey} references unknown item '{sid_clean}'",
                    )
                elif sid_clean not in seen_sec:
                    seen_sec.add(sid_clean)
                    sections_clean[skey].append(sid_clean)
    preset["sections"] = sections_clean

    # initialSelected check: every id must exist, then expand required dependencies (addWithDeps).
    raw_initial = raw_preset.get("initialSelected")
    if raw_initial is None:
        raw_initial = []
    initial_valid = []
    if not isinstance(raw_initial, list):
        _add_violation(violations, "preset.initialSelected", "invalid_type", "preset.initialSelected must be an array")
    else:
        for idx, sid in enumerate(raw_initial):
            if not isinstance(sid, str) or not sid.strip():
                _add_violation(
                    violations,
                    f"preset.initialSelected[{idx}]",
                    "invalid_type",
                    f"preset.initialSelected[{idx}] must be a non-empty string",
                )
                continue
            sid_clean = sid.strip()
            if sid_clean not in all_items:
                _add_violation(
                    violations,
                    "preset.initialSelected",
                    "unknown_item_id",
                    f"preset.initialSelected references unknown item '{sid_clean}'",
                )
            else:
                initial_valid.append(sid_clean)

    if violations:
        summary = "; ".join(v["message"] for v in violations)
        raise ValidationError(summary, errors=violations)

    expanded_selected, added_deps = _expand_initial_selected(initial_valid, all_items)
    preset["initialSelected"] = expanded_selected

    normalized = {
        "slug": slug,
        "kind": kind,
        "status": status,
        "client_name": client_name,
        "business_name": business_name,
        "contact": contact,
        "preset": preset,
        "price_overrides": price_overrides,
        "custom_items": custom_items,
        "notes": notes,
    }
    return normalized, added_deps


def check_proposal_product_rules(proposal):
    """Run product_rules.check_text across all proposal text fields (warn-only, never blocks)."""
    rules_file = _rules_path()
    if not os.path.exists(rules_file):
        return []

    parts = [
        proposal.get("client_name") or "",
        proposal.get("business_name") or "",
        proposal.get("notes") or "",
    ]
    contact = proposal.get("contact") or {}
    if isinstance(contact, dict):
        for val in contact.values():
            if isinstance(val, str) and val.strip():
                parts.append(val)

    preset = proposal.get("preset") or {}
    if isinstance(preset, dict):
        for key in ("name", "badge", "clientName", "businessName", "title", "lead", "whatWeDo", "portfolio", "thisPlan"):
            val = preset.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val)
        arch = preset.get("architectureBlock") or {}
        if isinstance(arch, dict):
            if isinstance(arch.get("heading"), str) and arch["heading"].strip():
                parts.append(arch["heading"])
            if isinstance(arch.get("sub"), str) and arch["sub"].strip():
                parts.append(arch["sub"])
            for role in arch.get("roles") or []:
                if isinstance(role, dict):
                    if isinstance(role.get("label"), str) and role["label"].strip():
                        parts.append(role["label"])
                    for pt in role.get("points") or []:
                        if isinstance(pt, str) and pt.strip():
                            parts.append(pt)

    for citem in proposal.get("custom_items") or []:
        if isinstance(citem, dict):
            if isinstance(citem.get("n"), str) and citem["n"].strip():
                parts.append(citem["n"])
            if isinstance(citem.get("d"), str) and citem["d"].strip():
                parts.append(citem["d"])

    combined = "\n".join(p for p in parts if p)
    return list(product_rules.check_text(combined, path=rules_file))


def pricing_summary(proposal):
    """Compute effective pricing lines, standalone fee, total, and differs_from_base for proposal."""
    cat = catalog()
    cat_items = cat["items"]
    standalone_amount = int(cat["standalone_fee"])
    standalone_label = cat["standalone_label"]

    preset = proposal.get("preset") or {}
    selected_ids = list(preset.get("initialSelected") or [])
    prop_overrides = proposal.get("price_overrides") or {}
    custom_map = {
        c["id"]: c
        for c in (proposal.get("custom_items") or [])
        if isinstance(c, dict) and c.get("id")
    }

    lines = []
    custom_lines = []
    selected_groups = []
    seen_selected = set()

    for iid in selected_ids:
        if iid in seen_selected:
            continue
        seen_selected.add(iid)

        if iid in cat_items:
            item = cat_items[iid]
            base_p = int(item["base_price"])
            admin_p = int(item["price"])
            if iid in prop_overrides:
                eff_p = int(prop_overrides[iid])
                source = "proposal"
            elif item["overridden"]:
                eff_p = admin_p
                source = "admin"
            else:
                eff_p = base_p
                source = "base"
            selected_groups.append(item["g"])
            lines.append({
                "item": iid,
                "item_id": iid,
                "name": item["n"],
                "group": item["g"],
                "base": base_p,
                "effective": eff_p,
                "delta": eff_p - base_p,
                "source": source,
            })
        elif iid in custom_map:
            citem = custom_map[iid]
            cprice = int(citem["p"])
            selected_groups.append(citem["g"])
            custom_lines.append({
                "item": iid,
                "item_id": iid,
                "name": citem["n"],
                "group": citem["g"],
                "price": cprice,
                "base": cprice,
                "effective": cprice,
                "delta": 0,
                "source": "custom",
            })

    # Pitch standaloneFee() rule: applied when Mobile app items are selected without Web & SaaS items.
    has_mobile = any(g == "Mobile app" for g in selected_groups)
    has_web = any(g == "Web & SaaS" for g in selected_groups)
    standalone_fee = standalone_amount if (has_mobile and not has_web) else 0

    subtotal = sum(ln["effective"] for ln in lines) + sum(cl["effective"] for cl in custom_lines)
    total = subtotal + standalone_fee

    differs_from_base = []
    checked_diff = set()
    for iid in list(seen_selected) + list(prop_overrides.keys()):
        if iid in checked_diff or iid not in cat_items:
            continue
        checked_diff.add(iid)
        item = cat_items[iid]
        base_p = int(item["base_price"])
        admin_p = int(item["price"])
        if iid in prop_overrides:
            eff_p = int(prop_overrides[iid])
            source = "proposal"
        elif item["overridden"]:
            eff_p = admin_p
            source = "admin"
        else:
            eff_p = base_p
            source = "base"
        if eff_p != base_p:
            differs_from_base.append({
                "item": iid,
                "item_id": iid,
                "name": item["n"],
                "base": base_p,
                "effective": eff_p,
                "base_price": base_p,
                "effective_price": eff_p,
                "delta": eff_p - base_p,
                "source": source,
                "selected": iid in seen_selected,
            })

    return {
        "lines": lines,
        "custom_items": custom_lines,
        "subtotal": subtotal,
        "standalone_fee": standalone_fee,
        "standalone_label": standalone_label,
        "total": total,
        "differs_from_base": differs_from_base,
    }


def _enrich_proposal(proposal, added_dependencies=None):
    pricing = pricing_summary(proposal)
    warnings = check_proposal_product_rules(proposal)
    out = {
        **proposal,
        "pricing_summary": pricing,
        "warnings": warnings,
        "pitch_version": pitch_version(),
    }
    if added_dependencies is not None:
        out["added_dependencies"] = list(added_dependencies)
    return out


def _build_create_candidate(data, strip_prices=False):
    raw_cat = _raw_catalog()
    built_in_presets = raw_cat.get("presets") or {}
    kind = str(data.get("kind") or "proposal").strip()

    from_preset = data.get("from_preset")
    duplicate_of = data.get("duplicate_of")
    if from_preset and duplicate_of:
        raise ValidationError("Specify at most one of from_preset or duplicate_of")

    if from_preset:
        pslug = str(from_preset).strip()
        if pslug not in built_in_presets:
            raise NotFoundError(f"Built-in preset '{pslug}' not found")
        base_preset = deepcopy(built_in_presets[pslug])
        candidate = {
            "slug": data.get("slug", ""),
            "kind": data.get("kind", "proposal"),
            "status": data.get("status", "draft"),
            "client_name": data.get("client_name", base_preset.get("clientName", "")),
            "business_name": data.get("business_name", base_preset.get("businessName", "")),
            "contact": data.get("contact") if "contact" in data else {},
            "preset": base_preset,
            "price_overrides": {} if strip_prices else (data.get("price_overrides") or {}),
            "custom_items": [] if strip_prices else (data.get("custom_items") or []),
            "notes": data.get("notes", ""),
        }
    elif duplicate_of:
        src = db.get_pitch_proposal(str(duplicate_of).strip())
        if src is None:
            raise NotFoundError(f"Source proposal '{duplicate_of}' not found")
        candidate = {
            "slug": data.get("slug", ""),
            "kind": data.get("kind", src["kind"]),
            "status": data.get("status", "draft"),
            "client_name": data.get("client_name", src["client_name"]),
            "business_name": data.get("business_name", src["business_name"]),
            "contact": data.get("contact") if "contact" in data else deepcopy(src["contact"]),
            "preset": deepcopy(src["preset"]),
            "price_overrides": {} if strip_prices else deepcopy(data.get("price_overrides", src["price_overrides"])),
            "custom_items": [] if strip_prices else deepcopy(data.get("custom_items", src["custom_items"])),
            "notes": data.get("notes", src["notes"]),
        }
    else:
        base_preset = _default_preset_template(kind=kind)
        candidate = {
            "slug": data.get("slug", ""),
            "kind": kind,
            "status": data.get("status", "draft"),
            "client_name": data.get("client_name", ""),
            "business_name": data.get("business_name", ""),
            "contact": data.get("contact") if "contact" in data else {},
            "preset": base_preset,
            "price_overrides": {} if strip_prices else (data.get("price_overrides") or {}),
            "custom_items": [] if strip_prices else (data.get("custom_items") or []),
            "notes": data.get("notes", ""),
        }

    # Merge explicit preset fields over template if provided.
    if "preset" in data:
        if not isinstance(data["preset"], dict):
            candidate["preset"] = data["preset"]
        else:
            merged_preset = dict(candidate["preset"])
            merged_preset.update(data["preset"])
            candidate["preset"] = merged_preset

    if isinstance(candidate["preset"], dict):
        if "client_name" in data and not candidate["preset"].get("clientName"):
            candidate["preset"]["clientName"] = data["client_name"]
        if "business_name" in data and not candidate["preset"].get("businessName"):
            candidate["preset"]["businessName"] = data["business_name"]

    if strip_prices:
        candidate["price_overrides"] = {}
        candidate["custom_items"] = []

    return candidate


def _outbox_base_payload(proposal, pricing):
    return {
        "proposal_id": proposal["id"],
        "slug": proposal["slug"],
        "kind": proposal["kind"],
        "client_name": proposal["client_name"],
        "created_by": proposal["created_by"],
        "user_label": _local_user_label(),
        "pricing_summary": pricing,
        "differs_from_base": pricing["differs_from_base"],
    }


def create_proposal(data, created_by="user", strip_prices=False):
    """Create a proposal from blank, built-in preset, or duplicate, validating Pitch rules."""
    if not isinstance(data, dict):
        raise ValidationError("Proposal payload must be an object")

    candidate = _build_create_candidate(data, strip_prices=strip_prices)
    normalized, added_deps = _validate_and_normalize(candidate)

    proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
    share_id = _generate_unique_share_id()
    commit = pitch_version().get("commit") or ""

    created = db.create_pitch_proposal(
        proposal_id=proposal_id,
        slug=normalized["slug"],
        kind=normalized["kind"],
        status=normalized["status"],
        client_name=normalized["client_name"],
        business_name=normalized["business_name"],
        contact=normalized["contact"],
        preset=normalized["preset"],
        price_overrides=normalized["price_overrides"],
        custom_items=normalized["custom_items"],
        notes=normalized["notes"],
        created_by=str(created_by or "user"),
        share_id=share_id,
        pitch_commit=commit,
    )

    pricing = pricing_summary(created)
    db.append_sync_outbox("proposal.created", _outbox_base_payload(created, pricing))
    _publish(created["id"], "created")
    return _enrich_proposal(created, added_dependencies=added_deps)


def get_proposal(proposal_id):
    """Fetch a single proposal enriched with pricing_summary, warnings, and pitch_version."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    prop = db.get_pitch_proposal(pid)
    if prop is None:
        raise NotFoundError(f"Proposal '{pid}' not found")
    return _enrich_proposal(prop)


def list_proposals(status=None, kind=None, limit=100):
    """List proposals optionally filtered by status and kind."""
    if status is not None and status != "":
        if status not in VALID_STATUSES:
            raise ValidationError(f"status must be one of: {', '.join(VALID_STATUSES)}")
        norm_status = status
    else:
        norm_status = None

    if kind is not None and kind != "":
        if kind not in VALID_KINDS:
            raise ValidationError(f"kind must be one of: {', '.join(VALID_KINDS)}")
        norm_kind = kind
    else:
        norm_kind = None

    rows = db.list_pitch_proposals(status=norm_status, kind=norm_kind, limit=limit)
    return [
        {
            **row,
            "pricing_summary": pricing_summary(row),
        }
        for row in rows
    ]


def update_proposal(proposal_id, updates):
    """Update a proposal after running all Pitch rules checks and record an outbox event."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    if not isinstance(updates, dict):
        raise ValidationError("Update payload must be an object")

    existing = db.get_pitch_proposal(pid)
    if existing is None:
        raise NotFoundError(f"Proposal '{pid}' not found")

    candidate = {
        "slug": updates.get("slug", existing["slug"]),
        "kind": updates.get("kind", existing["kind"]),
        "status": updates.get("status", existing["status"]),
        "client_name": updates.get("client_name", existing["client_name"]),
        "business_name": updates.get("business_name", existing["business_name"]),
        "contact": updates.get("contact", existing["contact"]),
        "preset": deepcopy(existing["preset"]),
        "price_overrides": updates.get("price_overrides", existing["price_overrides"]),
        "custom_items": updates.get("custom_items", existing["custom_items"]),
        "notes": updates.get("notes", existing["notes"]),
    }

    if "preset" in updates:
        if not isinstance(updates["preset"], dict):
            candidate["preset"] = updates["preset"]
        else:
            merged_preset = dict(candidate["preset"])
            merged_preset.update(updates["preset"])
            candidate["preset"] = merged_preset

    normalized, added_deps = _validate_and_normalize(candidate, existing_id=pid)

    changed_fields = []
    for field in (
        "slug",
        "kind",
        "status",
        "client_name",
        "business_name",
        "contact",
        "preset",
        "price_overrides",
        "custom_items",
        "notes",
    ):
        if normalized[field] != existing[field]:
            changed_fields.append(field)

    updated = db.update_pitch_proposal(pid, normalized)
    pricing = pricing_summary(updated)
    outbox_payload = _outbox_base_payload(updated, pricing)
    outbox_payload["changed_fields"] = changed_fields
    db.append_sync_outbox("proposal.updated", outbox_payload)
    _publish(updated["id"], "updated")
    return _enrich_proposal(updated, added_dependencies=added_deps)


def delete_proposal(proposal_id, hard=False):
    """Soft-archive a proposal (status='archived') or hard-delete when hard=True."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    existing = db.get_pitch_proposal(pid)
    if existing is None:
        raise NotFoundError(f"Proposal '{pid}' not found")

    pricing = pricing_summary(existing)
    if hard:
        db.delete_pitch_proposal(pid)
        result = {
            "deleted": True,
            "hard": True,
            "proposal_id": pid,
            "status": "deleted",
        }
    else:
        updated = db.update_pitch_proposal(pid, {"status": "archived"})
        result = {
            "deleted": True,
            "hard": False,
            "proposal_id": pid,
            "status": updated["status"],
            "proposal": updated,
        }

    outbox_payload = _outbox_base_payload(existing, pricing)
    outbox_payload["hard"] = bool(hard)
    db.append_sync_outbox("proposal.deleted", outbox_payload)
    _publish(pid, "deleted")
    return result


def duplicate_proposal(proposal_id, slug, created_by="user"):
    """Duplicate an existing proposal onto a new slug with a fresh share_id and draft status."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    return create_proposal(
        {"duplicate_of": pid, "slug": slug, "status": "draft"},
        created_by=created_by,
    )


def save_proposal_pdf(proposal_id, raw_bytes, content_type=""):
    """Validate and save PDF bytes under outputs/pitch/<slug>/<slug>-<timestamp>.pdf."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    prop = db.get_pitch_proposal(pid)
    if prop is None:
        raise NotFoundError(f"Proposal '{pid}' not found")

    ctype = str(content_type or "").split(";")[0].strip().lower()
    if ctype != "application/pdf":
        raise ValidationError("Content-Type must be application/pdf")

    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise ValidationError("PDF body must not be empty")
    if len(raw_bytes) > MAX_PDF_BYTES:
        raise ValidationError("PDF exceeds maximum allowed size of 10 MB")
    if not bytes(raw_bytes).startswith(b"%PDF-"):
        raise ValidationError("Uploaded bytes are not a valid PDF (missing %PDF- header)")

    slug = prop["slug"]
    if not SLUG_RE.match(slug):
        raise ValidationError("Proposal slug is invalid")

    pitch_out_root = os.path.realpath(paths.data("outputs", "pitch"))
    slug_dir = os.path.realpath(os.path.join(pitch_out_root, slug))
    if os.path.commonpath([pitch_out_root, slug_dir]) != pitch_out_root or slug_dir == pitch_out_root:
        raise ValidationError("Invalid proposal output path")
    os.makedirs(slug_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    filename = f"{slug}-{ts}.pdf"
    dest_path = os.path.realpath(os.path.join(slug_dir, filename))
    if os.path.commonpath([slug_dir, dest_path]) != slug_dir:
        raise ValidationError("Invalid PDF destination path")

    with open(dest_path, "wb") as f:
        f.write(raw_bytes)

    rel_path = os.path.relpath(dest_path, paths.DATA_ROOT).replace("\\", "/")
    _publish(pid, "pdf_saved", {"path": rel_path})
    return {
        "ok": True,
        "proposal_id": pid,
        "slug": slug,
        "filename": filename,
        "path": rel_path,
        "url": f"/api/pitch/proposals/{pid}/pdf/{filename}",
        "size": len(raw_bytes),
    }


def list_proposal_pdfs(proposal_id):
    """List saved PDF exports for proposal_id, newest first."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    prop = db.get_pitch_proposal(pid)
    if prop is None:
        raise NotFoundError(f"Proposal '{pid}' not found")

    slug = prop["slug"]
    slug_dir = os.path.realpath(paths.data("outputs", "pitch", slug))
    pdfs = []
    if os.path.isdir(slug_dir):
        entries = []
        for name in os.listdir(slug_dir):
            if not name.lower().endswith(".pdf"):
                continue
            full_path = os.path.realpath(os.path.join(slug_dir, name))
            if os.path.commonpath([slug_dir, full_path]) != slug_dir or not os.path.isfile(full_path):
                continue
            st = os.stat(full_path)
            entries.append((st.st_mtime, name, full_path, st.st_size))
        entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for mtime, name, full_path, size in entries:
            rel_path = os.path.relpath(full_path, paths.DATA_ROOT).replace("\\", "/")
            pdfs.append({
                "filename": name,
                "path": rel_path,
                "url": f"/api/pitch/proposals/{pid}/pdf/{name}",
                "size": size,
                "created_at": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
            })

    return {
        "proposal_id": pid,
        "slug": slug,
        "pdfs": pdfs,
    }


def resolve_proposal_pdf(proposal_id, filename):
    """Safely resolve a saved PDF path inside outputs/pitch/<slug>/ for serving."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValidationError("proposal_id is required")
    prop = db.get_pitch_proposal(pid)
    if prop is None:
        raise NotFoundError(f"Proposal '{pid}' not found")

    fname = str(filename or "")
    if (
        not fname
        or "\x00" in fname
        or "/" in fname
        or "\\" in fname
        or fname in (".", "..")
        or not fname.lower().endswith(".pdf")
    ):
        raise ValidationError("Invalid PDF filename")

    slug_dir = os.path.realpath(paths.data("outputs", "pitch", prop["slug"]))
    data_root = os.path.realpath(paths.DATA_ROOT)
    candidate = os.path.realpath(os.path.join(slug_dir, fname))
    try:
        inside_slug = os.path.commonpath([slug_dir, candidate]) == slug_dir and candidate != slug_dir
        inside_root = os.path.commonpath([data_root, candidate]) == data_root
    except ValueError:
        inside_slug = False
        inside_root = False
    if not inside_slug or not inside_root:
        raise ValidationError("Forbidden PDF path")
    if not os.path.isfile(candidate):
        raise NotFoundError("PDF file not found")
    return candidate


def list_outbox(pending_only=False, limit=100):
    """Return outbox events and pending count for the Studio sync status view."""
    items = db.list_sync_outbox(pending_only=pending_only, limit=limit)
    pending_count = len(items) if pending_only else len(db.list_sync_outbox(pending_only=True, limit=500))
    return {
        "events": items,
        "pending_count": pending_count,
    }


def section_for_group(group_name):
    """Return Pitch section key ('p2'..'p5') for a catalog group name."""
    return _GROUP_TO_SECTION.get(group_name, "p5")
