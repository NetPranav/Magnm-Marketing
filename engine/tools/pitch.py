#!/usr/bin/env python3
"""Agent tools for reading the Pitch catalog (without prices) and drafting proposals."""

from copy import deepcopy
import os
import re
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import pitch
import pitch_host
from tools import tool

_PRICE_KEYS = frozenset({
    "price",
    "prices",
    "p",
    "price_overrides",
    "price_overrides_json",
    "base_prices",
    "custom_items",
    "custom_items_json",
    "subtotal",
    "total",
    "standalone_fee",
})


def _slugify(text):
    raw = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    if len(raw) < 2:
        raw = f"proposal-{uuid.uuid4().hex[:6]}"
    raw = raw[:54].rstrip("-")
    if not raw or raw[0] == "-":
        raw = "p-" + raw.lstrip("-")
    return raw


def _unique_slug(seed_text):
    base = _slugify(seed_text)
    reserved = pitch.reserved_slugs()
    candidate = base
    if candidate in reserved or db.get_pitch_proposal_by_slug(candidate) is not None:
        candidate = f"{base[:50].rstrip('-')}-{uuid.uuid4().hex[:6]}"
    return candidate


def _ensure_selected_in_sections(sections, selected_ids, raw_items):
    """Ensure every selected catalog item appears in its matching Pitch section."""
    out = {k: list(sections.get(k) or []) for k in pitch.VALID_SECTION_KEYS}
    present = {sid for vals in out.values() for sid in vals}
    for sid in selected_ids:
        if sid in present or sid not in raw_items:
            continue
        sec_key = pitch.section_for_group(raw_items[sid].get("g", ""))
        out[sec_key].append(sid)
        present.add(sid)
    return out


@tool(
    name="list_pitch_catalog",
    description="List Pitch deliverable groups and items (ids, names, descriptions, dependencies) without prices.",
    parameters_schema={
        "type": "object",
        "properties": {
            "group": {
                "type": "string",
                "description": "Optional group name filter (e.g. 'Web & SaaS', 'Mobile app', 'Backend', 'AI & Automation', 'DevOps & Maintenance').",
            },
        },
        "required": [],
    },
    side_effects=False,
)
def list_pitch_catalog(ctx, group=None):
    raw = pitch_host.load_catalog()
    groups = list(raw.get("groups") or [])
    raw_items = raw.get("items") or {}

    group_filter = str(group).strip() if group else None
    items_out = []
    for item_id, item in raw_items.items():
        if group_filter and item.get("g") != group_filter:
            continue
        items_out.append({
            "id": item_id,
            "group": item.get("g", ""),
            "name": item.get("n", ""),
            "description": item.get("d", ""),
            "requires": list(item.get("r") or []),
        })

    return {
        "groups": groups,
        "items": items_out,
    }


@tool(
    name="draft_proposal",
    description="Draft a client proposal in Pitch using catalog item IDs and tailored copy (no pricing).",
    parameters_schema={
        "type": "object",
        "properties": {
            "client_name": {"type": "string", "description": "Client contact or recipient name."},
            "business_name": {"type": "string", "description": "Client business or startup name."},
            "headline": {"type": "string", "description": "Proposal headline / title."},
            "lead": {"type": "string", "description": "Introductory lead paragraph tailored to the client."},
            "this_plan": {"type": "string", "description": "One-line scope summary for this plan."},
            "what_we_do": {"type": "string", "description": "Concise summary of capabilities offered."},
            "selected_item_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of Pitch catalog item IDs from list_pitch_catalog.",
            },
            "architecture_roles": {
                "type": "array",
                "description": "Optional list of {label, points[]} blocks describing system roles.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "points": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "architecture_heading": {"type": "string", "description": "Optional heading for the architecture block."},
            "architecture_sub": {"type": "string", "description": "Optional subtitle for the architecture block."},
            "slug": {"type": "string", "description": "Optional URL slug (lowercase alphanumeric and hyphens)."},
            "notes": {"type": "string", "description": "Optional internal notes on scope or assumptions."},
        },
        "required": [
            "client_name",
            "business_name",
            "headline",
            "lead",
            "this_plan",
            "what_we_do",
            "selected_item_ids",
        ],
    },
    side_effects=True,
)
def draft_proposal(
    ctx,
    client_name,
    business_name,
    headline,
    lead,
    this_plan,
    what_we_do,
    selected_item_ids,
    architecture_roles=None,
    architecture_heading=None,
    architecture_sub=None,
    slug=None,
    notes=None,
    **extra_kwargs,
):
    # Strip any price-related fields an agent might attempt to pass.
    for pkey in list(extra_kwargs.keys()):
        if pkey in _PRICE_KEYS or "price" in str(pkey).lower():
            extra_kwargs.pop(pkey, None)

    raw = pitch_host.load_catalog()
    raw_items = raw.get("items") or {}
    valid_ids = list(raw_items.keys())

    if not isinstance(selected_item_ids, list):
        return {
            "error": "selected_item_ids must be an array of catalog item IDs",
            "valid_ids": valid_ids,
        }

    cleaned_ids = []
    unknown_ids = []
    for sid in selected_item_ids:
        if not isinstance(sid, str) or not sid.strip():
            unknown_ids.append(str(sid))
            continue
        clean = sid.strip()
        if clean not in raw_items:
            unknown_ids.append(clean)
        else:
            cleaned_ids.append(clean)

    if unknown_ids:
        return {
            "error": f"Unknown item id(s): {', '.join(unknown_ids)}. Valid item ids: {', '.join(valid_ids)}",
            "unknown_ids": unknown_ids,
            "valid_ids": valid_ids,
        }

    general = deepcopy((raw.get("presets") or {}).get("general") or {})
    default_arch = deepcopy(general.get("architectureBlock") or {"heading": "", "sub": "", "roles": []})
    arch_block = {
        "heading": str(architecture_heading if architecture_heading is not None else default_arch.get("heading", "")),
        "sub": str(architecture_sub if architecture_sub is not None else default_arch.get("sub", "")),
        "roles": architecture_roles if architecture_roles is not None else default_arch.get("roles", []),
    }

    target_slug = str(slug).strip() if slug else _unique_slug(business_name or client_name or headline)
    sections = _ensure_selected_in_sections(
        general.get("sections") or {"p2": [], "p3": [], "p4": [], "p5": []},
        cleaned_ids,
        raw_items,
    )

    proposal_name = str(business_name or client_name or headline or "Client Proposal").strip()
    agent_id = (ctx.agent_id if ctx and ctx.agent_id else "agent").strip()

    try:
        created = pitch.create_proposal(
            {
                "slug": target_slug,
                "kind": "proposal",
                "status": "draft",
                "client_name": client_name,
                "business_name": business_name,
                "notes": notes or "",
                "preset": {
                    "name": proposal_name,
                    "badge": f"Proposal for {proposal_name}",
                    "clientName": client_name,
                    "businessName": business_name,
                    "title": headline,
                    "lead": lead,
                    "whatWeDo": what_we_do,
                    "portfolio": general.get("portfolio", "magnm-org.vercel.app"),
                    "thisPlan": this_plan,
                    "architectureBlock": arch_block,
                    "initialSelected": cleaned_ids,
                    "sections": sections,
                },
            },
            created_by=agent_id,
            strip_prices=True,
        )
    except pitch.ValidationError as exc:
        return {"error": str(exc), "errors": exc.errors}

    # Re-run section placement in case dependency auto-add added items outside general sections.
    expanded_ids = created["preset"]["initialSelected"]
    updated_sections = _ensure_selected_in_sections(created["preset"]["sections"], expanded_ids, raw_items)
    if updated_sections != created["preset"]["sections"]:
        created = pitch.update_proposal(
            created["id"],
            {"preset": {"sections": updated_sections}},
        )

    return {
        "proposal_id": created["id"],
        "slug": created["slug"],
        "status": created["status"],
        "created_by": created["created_by"],
        "selected_items": created["preset"]["initialSelected"],
        "added_dependencies": created.get("added_dependencies", []),
        "warnings": created.get("warnings", []),
    }
