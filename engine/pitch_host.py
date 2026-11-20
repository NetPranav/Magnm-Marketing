#!/usr/bin/env python3
"""Serve the pinned Magnm-Pitch bundle at /pitch/ with offline fonts and Studio host payload."""

from copy import deepcopy
from html.parser import HTMLParser
import json
import os
import re
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}

ALLOWED_PITCH_FILES = frozenset({
    "style.css",
    "app.js",
    "presets.js",
    "logo.png",
    "vendor/jspdf.umd.min.js",
})

VALID_MODES = ("preview", "present", "form")
VALID_SHEET_KEYS = ("p2", "p3", "p4", "p5")
VALID_ABOUT_KEYS = ("lead", "what_we_do", "portfolio", "this_plan", "architecture", "timeline", "payment")

GROUP_TO_SHEET = {
    "Web & SaaS": "p2",
    "Mobile app": "p3",
    "Backend": "p4",
    "AI & Automation": "p5",
    "DevOps & Maintenance": "p5",
}

_GOOGLE_FONTS_RE = re.compile(
    r'<link\s+rel="preconnect"\s+href="https://fonts\.googleapis\.com"\s*>\s*'
    r'<link\s+rel="preconnect"\s+href="https://fonts\.gstatic\.com"\s+crossorigin\s*>\s*'
    r'<link\s+rel="stylesheet"\s+href="https://fonts\.googleapis\.com/[^"]+"\s*>',
    re.IGNORECASE,
)

_APP_SCRIPT_RE = re.compile(r'<script\s+src="app\.js"\s*>\s*</script>', re.IGNORECASE)


def vendor_dir():
    """Return the directory containing the vendored Magnm-Pitch files."""
    return paths.resource("vendor", "magnm-pitch")


def host_js_path():
    """Return the path to our Studio host adapter script."""
    return paths.resource("vendor-host", "magnm-host.js")


def font_paths():
    """Return (fonts_css_path, fonts_dir) for offline font serving."""
    if getattr(sys, "frozen", False):
        return (
            paths.resource("pitch-fonts", "fonts.css"),
            paths.resource("pitch-fonts", "fonts"),
        )
    return (
        paths.resource("packages", "magnm-design", "fonts.css"),
        paths.resource("packages", "magnm-design", "fonts"),
    )


def mime_type(file_path):
    """Return the MIME type for a served Pitch asset path."""
    _, ext = os.path.splitext(file_path.lower())
    return MIME_TYPES.get(ext, "application/octet-stream")


def load_catalog():
    """Load and parse catalog.json from the vendored Pitch directory."""
    catalog_path = os.path.join(vendor_dir(), "catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _apply_price_overrides(prices, overrides):
    """Apply valid non-negative integer price overrides onto prices in place."""
    if not isinstance(overrides, dict):
        return
    for item_id, raw_price in overrides.items():
        if item_id not in prices:
            continue
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            continue
        if raw_price < 0:
            continue
        prices[item_id] = int(raw_price)


class _PitchIndexDefaultsParser(HTMLParser):
    """Strict HTML parser extracting default timeline and payment plan blocks from Pitch index.html."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._block = None
        self._block_div_depth = 0
        self._in_steps = False
        self._step = None
        self._in_pay = False
        self._pay_div_depth = 0
        self._pay_item = None
        self._capture = None
        self._buf = []

    def _classes(self, attrs):
        for k, v in attrs:
            if k == "class" and v:
                return set(v.split())
        return set()

    def handle_starttag(self, tag, attrs):
        classes = self._classes(attrs)
        if tag == "div":
            if self._block is None:
                if "block" in classes:
                    self._block = {"heading": "", "steps": [], "pay": [], "hints": []}
                    self._block_div_depth = 1
                return
            self._block_div_depth += 1
            if "pay" in classes:
                self._in_pay = True
                self._pay_div_depth = 1
            elif self._in_pay:
                self._pay_div_depth += 1
                if self._pay_div_depth == 2:
                    self._pay_item = {"percent_text": "", "label": ""}
            return

        if self._block is None:
            return

        if tag == "h2" and not self._block["heading"]:
            self._capture = "heading"
            self._buf = []
        elif tag == "ol" and "steps" in classes:
            self._in_steps = True
        elif tag == "li" and self._in_steps:
            self._step = {"title": "", "when": "", "detail": ""}
        elif self._step is not None:
            if tag == "span":
                self._capture = "step_when" if "t" in classes else "step_title"
                self._buf = []
            elif tag == "small":
                self._capture = "step_detail"
                self._buf = []
        elif self._pay_item is not None:
            if tag == "b":
                self._capture = "pay_pct"
                self._buf = []
            elif tag == "span":
                self._capture = "pay_label"
                self._buf = []
        elif tag == "p" and "hint" in classes:
            self._capture = "hint"
            self._buf = []

    def handle_data(self, data):
        if self._capture is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._block is None:
            return

        if self._capture is not None:
            text = " ".join("".join(self._buf).split())
            if self._capture == "heading" and tag == "h2":
                self._block["heading"] = text
                self._capture = None
            elif self._capture == "step_when" and tag == "span" and self._step is not None:
                self._step["when"] = text
                self._capture = None
            elif self._capture == "step_title" and tag == "span" and self._step is not None:
                self._step["title"] = text
                self._capture = None
            elif self._capture == "step_detail" and tag == "small" and self._step is not None:
                self._step["detail"] = text
                self._capture = None
            elif self._capture == "pay_pct" and tag == "b" and self._pay_item is not None:
                self._pay_item["percent_text"] = text
                self._capture = None
            elif self._capture == "pay_label" and tag == "span" and self._pay_item is not None:
                self._pay_item["label"] = text
                self._capture = None
            elif self._capture == "hint" and tag == "p":
                if text:
                    self._block["hints"].append(text)
                self._capture = None

        if tag == "li" and self._step is not None:
            self._block["steps"].append(self._step)
            self._step = None
        elif tag == "ol" and self._in_steps:
            self._in_steps = False
        elif tag == "div":
            if self._in_pay:
                if self._pay_div_depth == 2 and self._pay_item is not None:
                    self._block["pay"].append(self._pay_item)
                    self._pay_item = None
                self._pay_div_depth -= 1
                if self._pay_div_depth <= 0:
                    self._in_pay = False
                    self._pay_div_depth = 0
            self._block_div_depth -= 1
            if self._block_div_depth <= 0:
                self.blocks.append(self._block)
                self._block = None
                self._block_div_depth = 0


def parse_index_defaults(html_text=None):
    """Parse default payment_plan, payment_note, and timeline from vendored index.html strictly."""
    if html_text is None:
        index_path = os.path.join(vendor_dir(), "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            html_text = f.read()

    parser = _PitchIndexDefaultsParser()
    parser.feed(html_text)
    parser.close()

    steps_block = None
    pay_block = None
    for blk in parser.blocks:
        h = blk["heading"].strip().lower()
        if h == "how your software gets built":
            steps_block = blk
        elif h == "how you pay":
            pay_block = blk

    if steps_block is None or not steps_block["steps"]:
        raise RuntimeError("Could not parse 'How your software gets built' timeline from Pitch index.html")
    timeline = []
    for idx, st in enumerate(steps_block["steps"]):
        title = st["title"].strip()
        when = st["when"].strip()
        detail = st["detail"].strip()
        if not title or not when or not detail:
            raise RuntimeError(f"Incomplete timeline step {idx} in Pitch index.html")
        timeline.append({"title": title, "when": when, "detail": detail})
    if not (1 <= len(timeline) <= 8):
        raise RuntimeError(f"Expected 1..8 timeline steps in Pitch index.html, got {len(timeline)}")

    if pay_block is None or not pay_block["pay"] or not pay_block["hints"]:
        raise RuntimeError("Could not parse 'How you pay' payment plan or hint from Pitch index.html")
    payment_plan = []
    for idx, row in enumerate(pay_block["pay"]):
        m = re.match(r"^(\d+)\s*%$", row["percent_text"].strip())
        if not m:
            raise RuntimeError(f"Invalid payment percent {row['percent_text']!r} at row {idx} in Pitch index.html")
        pct = int(m.group(1))
        label = row["label"].strip()
        if pct < 1 or pct > 100 or not label:
            raise RuntimeError(f"Invalid payment row {idx} in Pitch index.html")
        payment_plan.append({"percent": pct, "label": label})
    if not (2 <= len(payment_plan) <= 4) or sum(r["percent"] for r in payment_plan) != 100:
        raise RuntimeError("Invalid payment_plan row count or percent sum in Pitch index.html")

    payment_note = pay_block["hints"][0].strip()
    if not payment_note:
        raise RuntimeError("Missing payment_note hint in Pitch index.html")

    return {
        "payment_plan": payment_plan,
        "payment_note": payment_note,
        "timeline": timeline,
    }


def expand_selected_ids(initial_ids, all_items):
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

    for iid in initial_ids or []:
        add_with_deps(iid, is_dep=False)
    return selected, added


def compute_payment_plan_amounts(payment_plan, total):
    """Compute rupee amounts for each payment plan row, putting rounding remainder on the last row."""
    rows = list(payment_plan or [])
    if not rows:
        return []
    out = []
    running = 0
    last_idx = len(rows) - 1
    total_int = int(total)
    for idx, row in enumerate(rows):
        pct = int(row.get("percent", 0))
        label = str(row.get("label") or "")
        if idx == last_idx:
            amt = total_int - running
        else:
            amt = round(total_int * pct / 100)
            running += amt
        out.append({"percent": pct, "label": label, "amount": amt})
    return out


def _extract_contact(proposal, preset):
    raw_contact = proposal.get("contact") if isinstance(proposal.get("contact"), dict) else {}
    preset_dict = preset if isinstance(preset, dict) else {}
    name = (
        str(proposal.get("client_name") or "").strip()
        or str(raw_contact.get("name") or "").strip()
        or str(preset_dict.get("clientName") or "").strip()
    )
    business = (
        str(proposal.get("business_name") or "").strip()
        or str(raw_contact.get("business") or "").strip()
        or str(preset_dict.get("businessName") or "").strip()
    )
    return {
        "name": name,
        "business": business,
        "phone": str(raw_contact.get("phone") or "").strip(),
        "email": str(raw_contact.get("email") or "").strip(),
        "notes": str(raw_contact.get("notes") or "").strip(),
    }


def item_sheet(item_id, all_items, custom_categories=None):
    """Return the home sheet key ('p2'..'p5' or 'cat_<slug>') for an item."""
    cat_by_name = {
        c["name"]: c
        for c in (custom_categories or [])
        if isinstance(c, dict) and c.get("name")
    }
    item = (all_items or {}).get(item_id) or {}
    grp = item.get("g", "")
    if grp in cat_by_name:
        cat = cat_by_name[grp]
        placement = cat.get("placement", "")
        if placement in VALID_SHEET_KEYS:
            return placement
        return cat.get("id") or "new_sheet"
    return GROUP_TO_SHEET.get(grp, "p5")


def build_all_items(cat_items, custom_items):
    """Merge catalog items and proposal custom items into one lookup map."""
    all_items = dict(cat_items or {})
    for citem in custom_items or []:
        if isinstance(citem, dict) and citem.get("id"):
            all_items[citem["id"]] = citem
    return all_items


def compute_effective_hidden_sheets(proposal, all_items=None):
    """Return effective hidden_sheets (including auto-hidden empty sheets for final quotations)."""
    if not isinstance(proposal, dict) or proposal.get("kind") == "default":
        return []
    stored = []
    seen = set()
    for s in proposal.get("hidden_sheets") or []:
        if isinstance(s, str) and s.strip() in VALID_SHEET_KEYS:
            skey = s.strip()
            if skey not in seen:
                seen.add(skey)
                stored.append(skey)
    if proposal.get("kind") != "final":
        return stored

    if all_items is None:
        cat_items = (load_catalog().get("items") or {})
        all_items = build_all_items(cat_items, proposal.get("custom_items"))

    custom_categories = proposal.get("custom_categories") or []
    preset = proposal.get("preset") if isinstance(proposal.get("preset"), dict) else {}
    raw_selected = [
        iid
        for iid in (preset.get("initialSelected") or [])
        if isinstance(iid, str) and iid in all_items
    ]
    visible_seeds = [
        iid
        for iid in raw_selected
        if item_sheet(iid, all_items, custom_categories) not in seen
    ]
    expanded, _ = expand_selected_ids(visible_seeds, all_items)
    sheets_with_agreed = {
        item_sheet(iid, all_items, custom_categories)
        for iid in expanded
        if item_sheet(iid, all_items, custom_categories) not in seen
    }
    for skey in VALID_SHEET_KEYS:
        if skey not in seen and skey not in sheets_with_agreed:
            seen.add(skey)
    return [k for k in VALID_SHEET_KEYS if k in seen]


def compute_included_from_hidden(proposal, all_items=None, effective_hidden_sheets=None):
    """Compute required items from hidden sheets needed by items on visible sheets."""
    if not isinstance(proposal, dict) or proposal.get("kind") == "default":
        return []
    if all_items is None:
        cat_items = (load_catalog().get("items") or {})
        all_items = build_all_items(cat_items, proposal.get("custom_items"))
    if effective_hidden_sheets is None:
        effective_hidden_sheets = compute_effective_hidden_sheets(proposal, all_items=all_items)

    hidden_set = set(effective_hidden_sheets or [])
    if not hidden_set:
        return []

    kind = proposal.get("kind") or "proposal"
    preset = proposal.get("preset") if isinstance(proposal.get("preset"), dict) else {}
    custom_items = [c for c in (proposal.get("custom_items") or []) if isinstance(c, dict) and c.get("id")]
    custom_categories = [c for c in (proposal.get("custom_categories") or []) if isinstance(c, dict) and c.get("name")]

    visible_sheets = []
    if kind == "final":
        raw_selected = [
            iid
            for iid in (preset.get("initialSelected") or [])
            if isinstance(iid, str) and iid in all_items
        ]
        visible_seeds = [
            iid
            for iid in raw_selected
            if item_sheet(iid, all_items, custom_categories) not in hidden_set
        ]
        expanded, _ = expand_selected_ids(visible_seeds, all_items)
        agreed_set = set(expanded)
        for skey in VALID_SHEET_KEYS:
            if skey in hidden_set:
                continue
            sheet_items = [
                iid
                for iid in expanded
                if item_sheet(iid, all_items, custom_categories) == skey
            ]
            if sheet_items:
                visible_sheets.append((skey, sheet_items))
        for cat in custom_categories:
            if cat.get("placement") == "new_sheet" and cat.get("id"):
                sheet_items = [
                    c["id"]
                    for c in custom_items
                    if c.get("g") == cat.get("name") and c["id"] in agreed_set
                ]
                if sheet_items:
                    visible_sheets.append((cat["id"], sheet_items))
    else:
        sections = preset.get("sections") if isinstance(preset.get("sections"), dict) else {}
        initial_sel = [
            iid
            for iid in (preset.get("initialSelected") or [])
            if isinstance(iid, str) and iid in all_items
        ]
        for skey in VALID_SHEET_KEYS:
            if skey in hidden_set:
                continue
            sheet_items = []
            seen_on_sheet = set()
            for iid in (sections.get(skey) or []):
                if isinstance(iid, str) and iid in all_items and iid not in seen_on_sheet:
                    if item_sheet(iid, all_items, custom_categories) not in hidden_set:
                        seen_on_sheet.add(iid)
                        sheet_items.append(iid)
            for c in custom_items:
                cid = c["id"]
                if cid not in seen_on_sheet and item_sheet(cid, all_items, custom_categories) == skey:
                    seen_on_sheet.add(cid)
                    sheet_items.append(cid)
            for iid in initial_sel:
                if iid not in seen_on_sheet and item_sheet(iid, all_items, custom_categories) == skey:
                    seen_on_sheet.add(iid)
                    sheet_items.append(iid)
            visible_sheets.append((skey, sheet_items))
        for cat in custom_categories:
            if cat.get("placement") == "new_sheet" and cat.get("id"):
                sheet_items = [
                    c["id"]
                    for c in custom_items
                    if c.get("g") == cat.get("name")
                ]
                if sheet_items:
                    visible_sheets.append((cat["id"], sheet_items))

    out = []
    for sheet_key, sheet_items in visible_sheets:
        entries_by_id = {}
        ordered_ids = []
        for vid in sheet_items:
            vitem = all_items.get(vid)
            if not vitem:
                continue
            vname = str(vitem.get("n") or vid)
            for dep_id in vitem.get("r") or []:
                if dep_id not in all_items:
                    continue
                if item_sheet(dep_id, all_items, custom_categories) in hidden_set:
                    if dep_id not in entries_by_id:
                        entries_by_id[dep_id] = {
                            "item_id": dep_id,
                            "name": str(all_items[dep_id].get("n") or dep_id),
                            "sheet_shown_on": sheet_key,
                            "needed_for": [vname],
                        }
                        ordered_ids.append(dep_id)
                    elif vname not in entries_by_id[dep_id]["needed_for"]:
                        entries_by_id[dep_id]["needed_for"].append(vname)

        idx = 0
        while idx < len(ordered_ids):
            hid = ordered_ids[idx]
            idx += 1
            parent_needed = entries_by_id[hid]["needed_for"]
            for trans_id in (all_items.get(hid) or {}).get("r") or []:
                if trans_id not in all_items:
                    continue
                if item_sheet(trans_id, all_items, custom_categories) in hidden_set and trans_id not in entries_by_id:
                    entries_by_id[trans_id] = {
                        "item_id": trans_id,
                        "name": str(all_items[trans_id].get("n") or trans_id),
                        "sheet_shown_on": sheet_key,
                        "needed_for": list(parent_needed),
                    }
                    ordered_ids.append(trans_id)

        for did in ordered_ids:
            out.append(entries_by_id[did])

    return out


def build_payload_custom_categories(custom_categories, custom_items, kind=None, agreed_ids=None):
    """Return custom_categories list with each category's item_ids in order."""
    out = []
    for cat in custom_categories or []:
        if not isinstance(cat, dict):
            continue
        cat_name = str(cat.get("name") or "")
        item_ids = []
        for c in custom_items or []:
            if isinstance(c, dict) and c.get("id") and c.get("g") == cat_name:
                if kind == "final" and agreed_ids is not None and c["id"] not in agreed_ids:
                    continue
                item_ids.append(c["id"])
        out.append({
            "id": str(cat.get("id") or ""),
            "name": cat_name,
            "placement": str(cat.get("placement") or ""),
            "sheet_title": str(cat.get("sheet_title") or ""),
            "sheet_lead": str(cat.get("sheet_lead") or ""),
            "item_ids": item_ids,
        })
    return out


def _compute_final_total(selected_ids, prices, custom_items, standalone_fee, service_plans, cat_items):
    all_items = dict(cat_items)
    custom_map = {}
    for citem in custom_items:
        if isinstance(citem, dict) and citem.get("id"):
            cid = citem["id"]
            custom_map[cid] = citem
            all_items[cid] = citem

    expanded, _ = expand_selected_ids(selected_ids, all_items)
    groups = []
    subtotal = 0
    for iid in expanded:
        if iid in custom_map:
            citem = custom_map[iid]
            subtotal += int(citem.get("p", 0))
            groups.append(citem.get("g", ""))
        elif iid in prices:
            subtotal += int(prices[iid])
            groups.append((cat_items.get(iid) or {}).get("g", ""))

    has_mobile = any(g == "Mobile app" for g in groups)
    has_web = any(g == "Web & SaaS" for g in groups)
    fee = int(standalone_fee) if (has_mobile and not has_web) else 0
    one_time_plans = sum(
        int(sp.get("price", 0))
        for sp in (service_plans or [])
        if isinstance(sp, dict) and sp.get("period") == "one_time"
    )
    return subtotal + fee + one_time_plans


def build_payload(mode="preview", proposal=None, price_overrides=None):
    """Build the host payload injected into the composed Pitch page."""
    norm_mode = mode if mode in VALID_MODES else "preview"
    catalog = load_catalog()
    items = catalog.get("items") or {}
    prices = {item_id: int(item["p"]) for item_id, item in items.items()}
    _apply_price_overrides(prices, price_overrides)

    proposal_id = None
    kind = None
    locked = False
    preset = None
    custom_items = []
    contact = {"name": "", "business": "", "phone": "", "email": "", "notes": ""}
    payment_plan = []
    payment_note = ""
    timeline = []
    service_plans = []
    hidden_sheets = []
    hidden_about = []
    custom_categories = []
    included_from_hidden = []

    if isinstance(proposal, dict):
        proposal_id = proposal.get("id") or proposal.get("proposal_id")
        kind = proposal.get("kind") or "proposal"
        if kind == "default":
            norm_mode = "form"
            preset = deepcopy((catalog.get("presets") or {}).get("general") or {})
        else:
            raw_preset = proposal.get("preset")
            if isinstance(raw_preset, dict):
                preset = dict(raw_preset)
                if proposal.get("slug") and not preset.get("slug"):
                    preset["slug"] = proposal["slug"]
            else:
                preset = raw_preset
            custom_items = list(proposal.get("custom_items") or [])
            _apply_price_overrides(prices, proposal.get("price_overrides"))
            contact = _extract_contact(proposal, preset)
            all_items = build_all_items(items, custom_items)
            hidden_sheets = compute_effective_hidden_sheets(proposal, all_items=all_items)
            seen_about = set()
            for akey in proposal.get("hidden_about") or []:
                if isinstance(akey, str) and akey.strip() in VALID_ABOUT_KEYS and akey.strip() not in seen_about:
                    seen_about.add(akey.strip())
                    hidden_about.append(akey.strip())

            agreed_ids_set = None
            if kind == "final":
                locked = True
                service_plans = [dict(sp) for sp in (proposal.get("service_plans") or []) if isinstance(sp, dict)]
                selected_ids = list((preset or {}).get("initialSelected") or []) if isinstance(preset, dict) else []
                expanded_sel, _ = expand_selected_ids(selected_ids, all_items)
                agreed_ids_set = set(expanded_sel)
                total = _compute_final_total(
                    selected_ids,
                    prices,
                    custom_items,
                    catalog.get("standalone_fee", 4000),
                    service_plans,
                    items,
                )
                payment_plan = compute_payment_plan_amounts(proposal.get("payment_plan") or [], total)
                payment_note = str(proposal.get("payment_note") or "")
                timeline = [dict(st) for st in (proposal.get("timeline") or []) if isinstance(st, dict)]

            custom_categories = build_payload_custom_categories(
                proposal.get("custom_categories"),
                custom_items,
                kind=kind,
                agreed_ids=agreed_ids_set,
            )
            included_from_hidden = compute_included_from_hidden(
                proposal,
                all_items=all_items,
                effective_hidden_sheets=hidden_sheets,
            )

    return {
        "mode": norm_mode,
        "kind": kind,
        "locked": locked,
        "prices": prices,
        "custom_items": custom_items,
        "preset": preset,
        "proposal_id": proposal_id,
        "contact": contact,
        "payment_plan": payment_plan,
        "payment_note": payment_note,
        "timeline": timeline,
        "service_plans": service_plans,
        "hidden_sheets": hidden_sheets,
        "hidden_about": hidden_about,
        "custom_categories": custom_categories,
        "included_from_hidden": included_from_hidden,
    }


def preview_payload(proposal_like):
    """Validate proposal_like without saving and return the Pitch payload with violations and derived fields."""
    import pitch

    return pitch.preview_payload(proposal_like)


def safe_json_for_script(payload):
    """Serialize payload as JSON safe for embedding inside an HTML <script> tag."""
    raw = json.dumps(payload, ensure_ascii=True)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def compose_index_html(payload):
    """Build the hosted Pitch HTML page from vendored index.html with two exact substitutions."""
    index_path = os.path.join(vendor_dir(), "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        raw_html = f.read()

    html, font_subs = _GOOGLE_FONTS_RE.subn(
        '<link rel="stylesheet" href="fonts/fonts.css">',
        raw_html,
    )
    if font_subs != 1:
        raise RuntimeError(f"Expected 1 Google Fonts block in Pitch index.html, matched {font_subs}")

    safe_json = safe_json_for_script(payload)
    replacement = (
        '<script src="app.js"></script>'
        f'<script type="application/json" id="magnm-pitch-data">{safe_json}</script>'
        '<script src="magnm-host.js"></script>'
    )
    html, app_subs = _APP_SCRIPT_RE.subn(lambda _match: replacement, html)
    if app_subs != 1:
        raise RuntimeError(f"Expected 1 app.js script tag in Pitch index.html, matched {app_subs}")

    return html


def _safe_join_inside(root_dir, *parts):
    """Resolve parts under root_dir, returning realpath only if strictly inside and a file."""
    root_real = os.path.realpath(root_dir)
    candidate_real = os.path.realpath(os.path.join(root_real, *parts))
    try:
        inside = os.path.commonpath([root_real, candidate_real]) == root_real
    except ValueError:
        return None
    if not inside or candidate_real == root_real or not os.path.isfile(candidate_real):
        return None
    return candidate_real


def _extract_rel_path(url_path):
    """Validate URL path under /pitch/ and return relative path segments, or None."""
    if not isinstance(url_path, str) or "\x00" in url_path or "\\" in url_path:
        return None
    decoded = urllib.parse.unquote(url_path)
    if "\x00" in decoded or "\\" in decoded:
        return None
    if not decoded.startswith("/pitch/") or decoded.startswith("//"):
        return None

    rel = decoded[len("/pitch/"):]
    if rel in ("", "index.html"):
        return rel
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return rel


def resolve_asset(url_path):
    """Resolve an allowlisted static asset under /pitch/, or return None."""
    rel = _extract_rel_path(url_path)
    if rel is None or rel in ("", "index.html"):
        return None

    if rel == "magnm-host.js":
        target = os.path.realpath(host_js_path())
        return target if os.path.isfile(target) else None

    fonts_css, fonts_dir = font_paths()
    if rel == "fonts/fonts.css":
        target = os.path.realpath(fonts_css)
        return target if os.path.isfile(target) else None

    parts = rel.split("/")
    if len(parts) == 3 and parts[0] == "fonts" and parts[1] == "fonts" and parts[2].lower().endswith(".woff2"):
        return _safe_join_inside(fonts_dir, parts[2])
    if len(parts) == 2 and parts[0] == "fonts" and parts[1].lower().endswith(".woff2"):
        return _safe_join_inside(fonts_dir, parts[1])

    if rel in ALLOWED_PITCH_FILES:
        return _safe_join_inside(vendor_dir(), *parts)

    return None


def _send_bytes(handler, data, content_type, cache_ctrl):
    """Write a 200 response with security headers."""
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", cache_ctrl)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def serve(handler, url):
    """Serve /pitch/ composed page or allowlisted Pitch assets; return False if not found."""
    if url.path == "/pitch":
        target = "/pitch/" + (f"?{url.query}" if url.query else "")
        handler.send_response(302)
        handler.send_header("Location", target)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    rel = _extract_rel_path(url.path)
    if rel is None:
        return False

    if rel in ("", "index.html"):
        import db

        qs = urllib.parse.parse_qs(url.query)
        mode = (qs.get("mode", ["preview"])[0] or "preview").strip().lower()
        proposal_id = (qs.get("proposal", [""])[0] or "").strip()

        admin_overrides = {}
        if os.path.isfile(db.get_db_path()):
            admin_overrides = {
                iid: int(entry["price"])
                for iid, entry in db.list_pitch_base_prices().items()
            }

        proposal_payload = None
        if proposal_id:
            prop = db.get_pitch_proposal(proposal_id)
            if prop is None:
                return False
            if prop.get("kind") == "default":
                proposal_payload = {
                    "id": prop["id"],
                    "slug": prop["slug"],
                    "kind": "default",
                }
                mode = "form"
            else:
                preset_copy = dict(prop.get("preset") or {})
                preset_copy["slug"] = prop["slug"]
                proposal_payload = {
                    "id": prop["id"],
                    "slug": prop["slug"],
                    "kind": prop["kind"],
                    "client_name": prop.get("client_name") or "",
                    "business_name": prop.get("business_name") or "",
                    "contact": prop.get("contact") or {},
                    "preset": preset_copy,
                    "price_overrides": prop.get("price_overrides") or {},
                    "custom_items": prop.get("custom_items") or [],
                    "hidden_sheets": prop.get("hidden_sheets") or [],
                    "hidden_about": prop.get("hidden_about") or [],
                    "custom_categories": prop.get("custom_categories") or [],
                    "payment_plan": prop.get("payment_plan") or [],
                    "payment_note": prop.get("payment_note") or "",
                    "timeline": prop.get("timeline") or [],
                    "service_plans": prop.get("service_plans") or [],
                }
                if prop.get("kind") == "quote_form":
                    mode = "form"

        payload = build_payload(
            mode=mode,
            proposal=proposal_payload,
            price_overrides=admin_overrides,
        )
        html = compose_index_html(payload).encode("utf-8")
        _send_bytes(handler, html, "text/html; charset=utf-8", "no-store")
        return True

    asset_path = resolve_asset(url.path)
    if asset_path is None:
        return False

    with open(asset_path, "rb") as f:
        data = f.read()
    _send_bytes(handler, data, mime_type(asset_path), "no-cache")
    return True
