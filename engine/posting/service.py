"""Orchestration layer for official-API posting with human-only prepare/confirm gate."""

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import uuid

_POSTING_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_POSTING_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cloud_sync
import db
import events
import paths
import posting_kit
import private_files
import product_rules
import repos
import retired_repos
import review
import review_studio
from posting import PLATFORMS, PostingError, http_client, instagram, linkedin, normalize_platform, vault as vault_mod, x_provider

CONFIRM_TTL_SECONDS = 300.0
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")
LINKEDIN_MAX_IMAGE_BYTES = 10 * 1024 * 1024

SETUP_STEPS = {
    "linkedin": [
        "Open the LinkedIn Developer Portal Token Generator for your app.",
        "Select scopes: openid, profile, w_member_social (personal profile only).",
        "Paste the generated token below straight away. It lasts 60 days; Magnm counts from pasting and warns 7 days before.",
    ],
    "instagram": [
        "Use an Instagram Professional account (Business or Creator).",
        "Generate a User access token in the Meta Developer Dashboard.",
        "Paste the token below (lasts 60 days). Use Refresh token when needed, and provide a public https:// media URL when posting.",
    ],
    "x": [
        "Create an OAuth 2.0 App in the X Developer Portal (public client, no client secret).",
        "Add redirect URI: http://127.0.0.1:47831/callback",
        "Enter your Client ID and click Connect with X. Pay-per-use API fees apply per post.",
    ],
}


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fb = paths.resource("config", "product_rules.json")
    return fb if os.path.exists(fb) else cand


def _confidential_patterns():
    """Load confidential product and repo patterns; fail closed if unreadable."""
    try:
        rules = product_rules.load_rules(_rules_path())
    except Exception as exc:
        raise PostingError(
            "Product rules could not be read; refusing API posting.",
            status=503,
            code="product_rules_unreadable",
        ) from exc

    conf_pids = set()
    patterns = []
    for prod in rules.get("products") or []:
        if not prod.get("confidential"):
            continue
        pid = str(prod.get("id") or "").strip()
        name = str(prod.get("name") or "").strip()
        if pid:
            conf_pids.add(pid.lower())
            patterns.append(product_rules._word_pattern(pid))
        if name and name.lower() != pid.lower():
            patterns.append(product_rules._word_pattern(name))

    try:
        for repo in repos.list_repos():
            pid = str(repo.get("product_id") or "").strip().lower()
            rname = str(repo.get("name") or "")
            is_conf = (
                bool(repo.get("confidential"))
                or (pid in conf_pids)
                or product_rules.any_match(patterns, rname)
            )
            if not is_conf:
                continue
            for term in (repo.get("name"), repo.get("id"), repo.get("product_id"), repo.get("github")):
                txt = str(term or "").strip()
                if txt:
                    patterns.append(product_rules._word_pattern(txt))
        # Removed or no longer shared confidential repos stay guarded.
        for txt in retired_repos.terms():
            patterns.append(product_rules._word_pattern(txt))
    except Exception as exc:
        raise PostingError(
            "Connected repositories could not be read; refusing API posting.",
            status=503,
            code="repos_unreadable",
        ) from exc

    return conf_pids, patterns


def _is_text_confidential(patterns, text):
    raw = str(text or "")
    unquoted = urllib.parse.unquote_plus(raw)
    return bool(
        product_rules.any_match(patterns, raw)
        or product_rules.any_match(patterns, unquoted)
    )


def _check_not_confidential(item, *, account=None, media_url=None, extra_texts=()):
    """Fail-closed check ensuring confidential items, text, files, or accounts never post."""
    conf_pids, patterns = _confidential_patterns()

    pid = str(item.get("product_id") or "").strip().lower()
    if pid and pid in conf_pids:
        raise PostingError(
            "Confidential products can never be posted through official APIs.",
            status=403,
            code="confidential_item",
        )

    sess_id = item.get("session_id")
    if sess_id:
        try:
            sessions = db.list_all_agent_sessions()
            conf_sids = cloud_sync._confidential_session_ids(sessions)
        except Exception as exc:
            raise PostingError(
                "Could not verify session confidentiality; refusing API posting.",
                status=503,
                code="confidentiality_check_failed",
            ) from exc
        if sess_id in conf_sids:
            raise PostingError(
                "Confidential items can never be posted through official APIs.",
                status=403,
                code="confidential_item",
            )

    item_serialized = json.dumps(
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "body": item.get("body"),
            "notes": item.get("notes"),
            "product_id": item.get("product_id"),
            "files": item.get("files"),
        },
        ensure_ascii=False,
    )
    if _is_text_confidential(patterns, item_serialized):
        raise PostingError(
            "Confidential text or file references can never be posted through official APIs.",
            status=403,
            code="confidential_content",
        )

    if media_url and _is_text_confidential(patterns, str(media_url)):
        raise PostingError(
            "Confidential media URLs can never be posted through official APIs.",
            status=403,
            code="confidential_media",
        )

    for txt in extra_texts:
        if txt and _is_text_confidential(patterns, str(txt)):
            raise PostingError(
                "Confidential content can never be posted through official APIs.",
                status=403,
                code="confidential_content",
            )

    try:
        _, conf_cids, _ = cloud_sync.confidential_scope()
    except Exception as exc:
        raise PostingError(
            "Could not verify confidentiality; refusing API posting.",
            status=503,
            code="confidentiality_check_failed",
        ) from exc
    if item.get("id") in conf_cids:
        raise PostingError(
            "Confidential items can never be posted through official APIs.",
            status=403,
            code="confidential_item",
        )

    if account is not None:
        acct_serialized = json.dumps(
            {
                "account_id": account.get("account_id"),
                "username": account.get("username"),
                "display_name": account.get("display_name"),
            },
            ensure_ascii=False,
        )
        if _is_text_confidential(patterns, acct_serialized):
            raise PostingError(
                "Confidential accounts can never be used for API posting.",
                status=403,
                code="confidential_account",
            )


def _detect_image_mime(raw_bytes):
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def _resolve_local_media(item, platform, selected_paths=None):
    """Read and hash attached local image files owned by item; refuse private or outside-root paths."""
    entries = [
        f for f in (item.get("files") or [])
        if isinstance(f, dict) and f.get("kind") in ("card", "image") and f.get("path")
    ]
    by_path = {}
    ordered_paths = []
    for entry in entries:
        p = str(entry["path"]).strip()
        if p and p not in by_path:
            by_path[p] = entry
            ordered_paths.append(p)

    if selected_paths is None:
        chosen = ordered_paths[: (1 if platform == "linkedin" else x_provider.MAX_MEDIA_COUNT)]
    else:
        if not isinstance(selected_paths, list):
            raise PostingError("media_paths must be a list of attachment paths.", status=400, code="invalid_media")
        chosen = []
        for p in selected_paths:
            clean = str(p or "").strip()
            if clean not in by_path:
                raise PostingError(
                    f"Media path '{clean}' is not attached to this content item.",
                    status=400,
                    code="invalid_media",
                )
            if clean not in chosen:
                chosen.append(clean)

    if platform == "linkedin" and len(chosen) > 1:
        raise PostingError("LinkedIn supports at most 1 attached image.", status=400, code="too_many_media")
    if platform == "x" and len(chosen) > x_provider.MAX_MEDIA_COUNT:
        raise PostingError(
            f"X supports at most {x_provider.MAX_MEDIA_COUNT} attached images.",
            status=400,
            code="too_many_media",
        )

    data_root = os.path.realpath(paths.DATA_ROOT)
    resolved = []
    for rel_path in chosen:
        candidate = rel_path if os.path.isabs(rel_path) else os.path.join(data_root, rel_path)
        real_path = os.path.realpath(candidate)
        try:
            inside = os.path.commonpath([data_root, real_path]) == data_root
        except ValueError:
            inside = False
        if not inside or private_files.is_private(real_path, data_root):
            raise PostingError(
                "Media file path is private or outside workspace.",
                status=403,
                code="private_media_blocked",
            )
        if not os.path.isfile(real_path):
            raise PostingError(
                f"Attached media file was not found: {rel_path}",
                status=400,
                code="missing_media_file",
            )
        size_cap = LINKEDIN_MAX_IMAGE_BYTES if platform == "linkedin" else x_provider.MAX_IMAGE_BYTES
        with open(real_path, "rb") as fh:
            raw = fh.read(size_cap + 1)
        mime = _detect_image_mime(raw)
        if platform == "linkedin" and mime not in ("image/jpeg", "image/png"):
            raise PostingError(
                "LinkedIn supports only JPG or PNG images.",
                status=400,
                code="unsupported_media_type",
            )
        if platform == "linkedin" and len(raw) > LINKEDIN_MAX_IMAGE_BYTES:
            raise PostingError("LinkedIn image exceeds 10 MB limit.", status=400, code="media_too_large")
        if platform == "x":
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                raise PostingError(
                    "Posting to X supports JPG, PNG or WEBP images. Post GIFs manually.",
                    status=400,
                    code="unsupported_media_type",
                )
            if len(raw) > x_provider.MAX_IMAGE_BYTES:
                raise PostingError("X image exceeds 5 MB limit.", status=400, code="media_too_large")

        alt_text = posting_kit._extract_alt_text(item, by_path[rel_path]) or ""
        resolved.append({
            "kind": "image",
            "source": "local",
            "path": rel_path,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mime_type": mime,
            "alt_text": alt_text,
            "_raw_bytes": raw,
        })
    return resolved


def _compute_payload_hash(
    *,
    content_id,
    content_version,
    platform,
    account_id,
    plain_text,
    wire_text,
    media_entries,
    ai_generated,
    cost_usd,
):
    canonical_media = [
        {
            "source": m["source"],
            "path": m.get("path"),
            "url": m.get("url"),
            "bytes": int(m["bytes"]),
            "sha256": m["sha256"],
            "mime_type": m["mime_type"],
            "media_type": m.get("media_type"),
            "alt_text": m.get("alt_text") or "",
        }
        for m in media_entries
    ]
    payload = {
        "content_id": str(content_id),
        "content_version": int(content_version),
        "platform": str(platform),
        "account_id": str(account_id),
        "plain_text": str(plain_text),
        "wire_text": str(wire_text),
        "media": canonical_media,
        "ai_generated": bool(ai_generated),
        "cost_usd": float(cost_usd),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _public_media_view(media_entries):
    """The media as the person will see it before confirming: the bytes themselves, not only their hash."""
    out = []
    for m in media_entries:
        view = {k: v for k, v in m.items() if k != "_raw_bytes"}
        if m.get("_raw_bytes") is not None:
            encoded = base64.b64encode(m["_raw_bytes"]).decode("ascii")
            view["preview_data_uri"] = f"data:{m['mime_type']};base64,{encoded}"
        out.append(view)
    return out


def _clean_token(token):
    clean = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(clean):
        raise PostingError("That doesn't look like a token. Paste it as one line.", status=400, code="invalid_token")
    return clean


def _public_attempt(attempt):
    if attempt is None:
        return None
    return {k: v for k, v in attempt.items() if k != "confirmation_id"}


class PostingService:
    """Manages connected posting accounts, prepare/confirm gate, and non-retrying API writes."""

    def __init__(
        self,
        *,
        data_root=None,
        key_store=None,
        transport=None,
        media_fetcher=None,
        clock=None,
        wall_clock=None,
        sleep_fn=None,
        oauth_port=x_provider.OAUTH_CALLBACK_PORT,
        ig_poll_interval_s=instagram.POLL_INTERVAL_SECONDS,
        ig_max_poll_s=instagram.MAX_POLL_SECONDS,
    ):
        self.vault = vault_mod.PostingVault(data_root=data_root, key_store=key_store)
        self._transport = transport
        self._media_fetcher = media_fetcher
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._sleep_fn = sleep_fn or time.sleep
        self._ig_poll_interval_s = float(ig_poll_interval_s)
        self._ig_max_poll_s = float(ig_max_poll_s)
        self._x_refresh_lock = threading.Lock()
        self._x_oauth = x_provider.XOAuthManager(
            callback_port=oauth_port,
            clock=self._clock,
            wall_clock=self._wall_clock,
            transport=self._transport,
        )

    def _now(self):
        return self._wall_clock()

    def _now_iso(self):
        return self._now().isoformat()

    def _expiry_meta(self, plat, acct):
        if not acct or not acct.get("expires_at"):
            return {"days_until_expiry": None, "expiring_soon": False, "expired": False}
        exp_dt = _parse_iso(acct["expires_at"])
        if exp_dt is None:
            return {"days_until_expiry": None, "expiring_soon": False, "expired": False}
        delta = exp_dt - self._now()
        secs = delta.total_seconds()
        days = int(secs // 86400) if secs >= 0 else -1
        if plat in ("linkedin", "instagram"):
            expired = secs <= 0
            expiring_soon = (not expired) and (secs <= linkedin.EXPIRY_WARN_DAYS * 86400)
        else:
            # X access token is short-lived and rotates automatically via refresh_token under lock.
            expired = False
            expiring_soon = False
        return {
            "days_until_expiry": max(0, days) if not expired else 0,
            "expiring_soon": expiring_soon,
            "expired": expired,
        }

    def get_status(self):
        """Return per-platform connection state, expiry, and setup steps (never tokens/secrets)."""
        accounts = db.list_posting_accounts()
        platforms_out = {}
        for plat in PLATFORMS:
            acct = accounts.get(plat)
            has_tok = self.vault.has_token(plat)
            connected = bool(acct) and has_tok
            exp_meta = self._expiry_meta(plat, acct if connected else None)
            platforms_out[plat] = {
                "platform": plat,
                "connected": connected,
                "account_id": acct.get("account_id") if connected else None,
                "username": acct.get("username") if connected else None,
                "display_name": acct.get("display_name") if connected else None,
                "client_id": acct.get("client_id") if acct else None,
                "connected_at": acct.get("connected_at") if connected else None,
                # X's two-hour access token renews itself, so showing it as an expiry would alarm for nothing.
                "expires_at": acct.get("expires_at") if connected and plat != "x" else None,
                "last_refreshed_at": acct.get("last_refreshed_at") if connected else None,
                "days_until_expiry": exp_meta["days_until_expiry"] if plat != "x" else None,
                "expiring_soon": exp_meta["expiring_soon"],
                "expired": exp_meta["expired"],
                "setup_steps": list(SETUP_STEPS[plat]),
                "oauth_active": self._x_oauth.is_active() if plat == "x" else False,
            }
        return {"platforms": platforms_out}

    def connect_linkedin(self, token):
        """Verify pasted LinkedIn token via /v2/userinfo, encrypt in vault, and store account."""
        token = _clean_token(token)
        info = linkedin.verify_token(token, transport=self._transport)
        now_dt = self._now()
        now_iso = now_dt.isoformat()
        exp_iso = (now_dt + timedelta(days=linkedin.TOKEN_TTL_DAYS)).isoformat()
        self.vault.save_token(
            "linkedin",
            {
                "access_token": str(token).strip(),
                "obtained_at": now_iso,
                "expires_at": exp_iso,
            },
        )
        acct = db.upsert_posting_account(
            "linkedin",
            account_id=info["account_id"],
            username=info["username"],
            display_name=info["display_name"],
            connected_at=now_iso,
            expires_at=exp_iso,
            updated_at=now_iso,
        )
        events.publish("posting.updated", {"platform": "linkedin", "action": "connected"})
        return {"ok": True, "account": acct}

    def connect_instagram(self, token):
        """Verify pasted Instagram token via /v27.0/me, encrypt in vault, and store account."""
        token = _clean_token(token)
        info = instagram.verify_token(token, transport=self._transport)
        now_dt = self._now()
        now_iso = now_dt.isoformat()
        exp_iso = (now_dt + timedelta(days=instagram.TOKEN_TTL_DAYS)).isoformat()
        self.vault.save_token(
            "instagram",
            {
                "access_token": str(token).strip(),
                "obtained_at": now_iso,
                "expires_at": exp_iso,
            },
        )
        acct = db.upsert_posting_account(
            "instagram",
            account_id=info["account_id"],
            username=info["username"],
            display_name=info["display_name"],
            connected_at=now_iso,
            expires_at=exp_iso,
            updated_at=now_iso,
        )
        events.publish("posting.updated", {"platform": "instagram", "action": "connected"})
        return {"ok": True, "account": acct}

    def refresh_instagram(self):
        """Explicit user tap to refresh the Instagram token."""
        acct = db.get_posting_account("instagram")
        if not acct or not self.vault.has_token("instagram"):
            raise PostingError("Instagram is not connected.", status=404, code="not_connected")
        tok = self.vault.load_token("instagram")
        res = instagram.refresh_token_explicit(tok.get("access_token"), transport=self._transport)
        now_dt = self._now()
        now_iso = now_dt.isoformat()
        exp_iso = (now_dt + timedelta(seconds=res["expires_in"])).isoformat()
        self.vault.save_token(
            "instagram",
            {
                "access_token": res["access_token"],
                "obtained_at": tok.get("obtained_at") or now_iso,
                "expires_at": exp_iso,
                "refreshed_at": now_iso,
            },
        )
        updated = db.upsert_posting_account(
            "instagram",
            account_id=acct["account_id"],
            username=acct["username"],
            display_name=acct["display_name"],
            connected_at=acct["connected_at"],
            expires_at=exp_iso,
            last_refreshed_at=now_iso,
            updated_at=now_iso,
        )
        events.publish("posting.updated", {"platform": "instagram", "action": "refreshed"})
        return {"ok": True, "account": updated}

    def start_x_oauth(self, client_id):
        """Start one-shot X OAuth 2.0 PKCE listener on 127.0.0.1:47831/callback."""
        def _on_complete(flow_client_id, tok_data, me):
            try:
                now_dt = self._now()
                now_iso = now_dt.isoformat()
                try:
                    ttl_s = int(tok_data.get("expires_in") or 7200)
                except (TypeError, ValueError):
                    ttl_s = 7200
                exp_iso = (now_dt + timedelta(seconds=max(60, ttl_s))).isoformat()
                self.vault.save_token(
                    "x",
                    {
                        "access_token": str(tok_data["access_token"]),
                        "refresh_token": str(tok_data.get("refresh_token") or ""),
                        "client_id": flow_client_id,
                        "obtained_at": now_iso,
                        "expires_at": exp_iso,
                    },
                )
                db.upsert_posting_account(
                    "x",
                    account_id=me["account_id"],
                    username=me["username"],
                    display_name=me["display_name"],
                    client_id=flow_client_id,
                    connected_at=now_iso,
                    expires_at=exp_iso,
                    updated_at=now_iso,
                )
                events.publish("posting.updated", {"platform": "x", "action": "connected"})
            finally:
                db.close_connection()

        return self._x_oauth.start(client_id, _on_complete)

    def cancel_x_oauth(self):
        """Close active X OAuth listener without connecting."""
        return self._x_oauth.cancel()

    def disconnect(self, platform):
        """Delete encrypted token blob, keychain key, and DB account metadata."""
        plat = normalize_platform(platform)
        if plat == "x":
            self._x_oauth.cancel()
        # A refresh in flight saves its new token under this lock, so removing under it can't be undone by one.
        with self._x_refresh_lock:
            vault_res = self.vault.remove(plat)
            deleted_db = db.delete_posting_account(plat)
        events.publish("posting.updated", {"platform": plat, "action": "disconnected"})
        return {"ok": True, "platform": plat, "removed": bool(vault_res["removed"] or deleted_db)}

    def list_attempts(self, content_id=None, platform=None, limit=50):
        """List posting attempts (never contains secrets)."""
        plat = normalize_platform(platform) if platform else None
        rows = db.list_posting_attempts(content_id=content_id, platform=plat, limit=limit)
        return [_public_attempt(a) for a in rows]

    def _require_connected(self, plat):
        acct = db.get_posting_account(plat)
        if not acct or not self.vault.has_token(plat):
            raise PostingError(
                f"{plat} is not connected. Connect it in Settings before posting.",
                status=409,
                code="not_connected",
            )
        exp = self._expiry_meta(plat, acct)
        if exp["expired"]:
            raise PostingError(
                f"Your {plat} token has expired. Reconnect or refresh it in Settings.",
                status=401,
                code="token_expired",
            )
        return acct, exp

    def _build_prepared_spec(
        self,
        *,
        content_id,
        platform,
        media_url=None,
        media_paths=None,
        ai_generated=False,
        acknowledge_repost=False,
        acknowledge_unknown=False,
        recheck_item=None,
        exclude_attempt_id=None,
    ):
        plat = normalize_platform(platform)
        item = recheck_item or db.get_content_item(content_id, include_versions=True)
        if item is None:
            raise PostingError(f"Content item '{content_id}' not found.", status=404, code="not_found")

        acct, exp_meta = self._require_connected(plat)
        _check_not_confidential(
            item,
            account=acct,
            media_url=media_url,
            extra_texts=[str(p) for p in (media_paths or [])],
        )

        plain_text = str(item.get("body") or "")
        if plat in ("linkedin", "x") and not plain_text.strip():
            raise PostingError("Post text cannot be empty.", status=400, code="empty_body")

        executed = [
            a for a in db.list_executed_posting_attempts(item["id"], plat)
            if a["id"] != exclude_attempt_id
        ]
        last_attempt = executed[0] if executed else None
        # The item's status can be undone, or fail to save. A live post can't.
        already_posted = item.get("status") == "posted" or any(a["status"] == "posted" for a in executed)
        if last_attempt and last_attempt["status"] == "sending":
            raise PostingError(
                "A post attempt for this item is currently sending.",
                status=409,
                code="already_sending",
            )
        if last_attempt and last_attempt["status"] == "unknown" and not acknowledge_unknown:
            raise PostingError(
                "The previous post attempt has status 'unknown'. Check the platform before trying again.",
                status=409,
                code="previous_unknown",
            )

        if already_posted and not acknowledge_repost:
            raise PostingError(
                "This item is already marked as posted. Acknowledge 'post again' to prepare another post.",
                status=409,
                code="already_posted",
            )

        warnings = []
        auto_res = review.auto_review(item)
        for f in auto_res.get("findings") or []:
            warnings.append({
                "code": f.get("code") or "review_warning",
                "severity": f.get("severity") or "warn",
                "message": f.get("message") or "",
            })

        if exp_meta["expiring_soon"]:
            warnings.append({
                "code": "token_expiring_soon",
                "severity": "warn",
                "message": f"{plat} token expires in {exp_meta['days_until_expiry']} day(s). Reconnect or refresh soon.",
            })

        if already_posted:
            warnings.append({
                "code": "already_posted",
                "severity": "warn",
                "message": "This item is already marked as posted.",
            })
        if last_attempt and last_attempt["status"] == "unknown":
            warnings.append({
                "code": "previous_unknown",
                "severity": "warn",
                "message": "Last attempt status is unknown; confirm on the platform that it did not publish before posting.",
            })

        media_entries = []
        wire_text = plain_text
        if plat == "linkedin":
            wire_text = linkedin.escape_little_text(plain_text)
            if len(wire_text) > linkedin.MAX_COMMENTARY_CHARS:
                raise PostingError(
                    f"LinkedIn post exceeds {linkedin.MAX_COMMENTARY_CHARS} characters ({len(wire_text)}).",
                    status=400,
                    code="text_too_long",
                )
            media_entries = _resolve_local_media(item, "linkedin", selected_paths=media_paths)
            cost = {
                "estimated_usd": 0.0,
                "label": "$0.00",
                "summary": "No per-post fee on LinkedIn API.",
            }
        elif plat == "instagram":
            if len(plain_text) > instagram.MAX_CAPTION_CHARS:
                raise PostingError(
                    f"Instagram caption exceeds {instagram.MAX_CAPTION_CHARS} characters ({len(plain_text)}).",
                    status=400,
                    code="text_too_long",
                )
            clean_url = str(media_url or "").strip()
            if not clean_url:
                raise PostingError(
                    "Instagram requires a public https:// JPEG or MP4 URL (Magnm never uploads local files).",
                    status=400,
                    code="media_url_required",
                )
            fetched = http_client.fetch_public_media(clean_url, fetcher=self._media_fetcher)
            media_entries = [{
                "kind": "media_url",
                "source": "public_url",
                "url": clean_url,
                "bytes": fetched["bytes"],
                "sha256": fetched["sha256"],
                "mime_type": fetched["mime_type"],
                "media_type": fetched["media_type"],
                "alt_text": "",
                "_raw_bytes": fetched.get("preview_bytes"),
            }]
            warnings.append({
                "code": "ig_host_mutable",
                "severity": "warn",
                "message": "The host of this public URL could still change the file before Instagram fetches it.",
            })
            cost = {
                "estimated_usd": 0.0,
                "label": "$0.00",
                "summary": "No per-post fee on Instagram Graph API.",
            }
        else:
            wlen = x_provider.weighted_length(plain_text)
            if wlen > x_provider.MAX_WEIGHTED_CHARS:
                raise PostingError(
                    f"X post exceeds {x_provider.MAX_WEIGHTED_CHARS} weighted characters ({wlen}).",
                    status=400,
                    code="text_too_long",
                )
            media_entries = _resolve_local_media(item, "x", selected_paths=media_paths)
            cost = x_provider.estimate_cost(plain_text, has_media=bool(media_entries))

        payload_hash = _compute_payload_hash(
            content_id=item["id"],
            content_version=int(item.get("current_version") or 1),
            platform=plat,
            account_id=acct["account_id"],
            plain_text=plain_text,
            wire_text=wire_text,
            media_entries=media_entries,
            ai_generated=bool(ai_generated),
            cost_usd=cost["estimated_usd"],
        )

        return {
            "item": item,
            "account": acct,
            "plain_text": plain_text,
            "wire_text": wire_text,
            "media_entries": media_entries,
            "ai_generated": bool(ai_generated),
            "warnings": warnings,
            "cost": cost,
            "payload_hash": payload_hash,
            "last_attempt": last_attempt,
            "media_url": str(media_url or "").strip() or None,
            "media_paths": media_paths,
        }

    def prepare(
        self,
        content_id,
        platform,
        *,
        media_url=None,
        media_paths=None,
        ai_generated=False,
        acknowledge_repost=False,
        acknowledge_unknown=False,
    ):
        """Prepare a human-confirmed post: returns account, text, media sha256/bytes, cost, warnings, confirmation_id."""
        spec = self._build_prepared_spec(
            content_id=content_id,
            platform=platform,
            media_url=media_url,
            media_paths=media_paths,
            ai_generated=bool(ai_generated),
            acknowledge_repost=bool(acknowledge_repost),
            acknowledge_unknown=bool(acknowledge_unknown),
        )
        now_dt = self._now()
        now_iso = now_dt.isoformat()
        exp_iso = (now_dt + timedelta(seconds=CONFIRM_TTL_SECONDS)).isoformat()
        confirmation_id = f"pconf_{secrets.token_urlsafe(24)}"
        attempt_id = f"post_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

        db.create_posting_attempt({
            "id": attempt_id,
            "content_id": spec["item"]["id"],
            "platform": normalize_platform(platform),
            "account_id": spec["account"]["account_id"],
            "confirmation_id": confirmation_id,
            "payload_hash": spec["payload_hash"],
            "options": {
                "media_url": spec["media_url"],
                "media_paths": media_paths,
                "ai_generated": spec["ai_generated"],
            },
            "status": "prepared",
            "prepared_at": now_iso,
            "expires_at": exp_iso,
            "updated_at": now_iso,
        })

        return {
            "attempt_id": attempt_id,
            "confirmation_id": confirmation_id,
            "payload_hash": spec["payload_hash"],
            "expires_at": exp_iso,
            "expires_in_s": int(CONFIRM_TTL_SECONDS),
            "content_id": spec["item"]["id"],
            "content_version": int(spec["item"].get("current_version") or 1),
            "item_status": spec["item"].get("status"),
            "platform": normalize_platform(platform),
            "account": {
                "account_id": spec["account"]["account_id"],
                "username": spec["account"].get("username"),
                "display_name": spec["account"].get("display_name"),
            },
            "final_text": spec["plain_text"],
            "media": _public_media_view(spec["media_entries"]),
            "ai_generated": spec["ai_generated"],
            "warnings": spec["warnings"],
            "cost": spec["cost"],
            "last_attempt": _public_attempt(spec["last_attempt"]),
            "media_url": spec["media_url"],
            "media_paths": [m["path"] for m in spec["media_entries"] if m.get("path")] if spec["media_paths"] is not None or spec["media_entries"] else spec["media_paths"],
        }

    def confirm(
        self,
        confirmation_id,
        *,
        expected_hash=None,
        media_url=None,
        media_paths=None,
        ai_generated=None,
        acknowledge_repost=False,
        acknowledge_unknown=False,
    ):
        """Re-read and re-hash everything, consume single-use confirmation_id atomically, and publish once."""
        cid_token = str(confirmation_id or "").strip()
        if not cid_token:
            raise PostingError("confirmation_id is required.", status=400, code="confirmation_required")

        attempt = db.get_posting_attempt_by_confirmation(cid_token)
        if attempt is None:
            raise PostingError("Confirmation request not found.", status=404, code="confirmation_not_found")

        if attempt["status"] != "prepared":
            raise PostingError(
                f"Confirmation has already been used or is no longer valid (status={attempt['status']}).",
                status=409,
                code="confirmation_already_used",
            )

        now_dt = self._now()
        exp_dt = _parse_iso(attempt["expires_at"])
        if exp_dt is None or now_dt > exp_dt:
            db.update_posting_attempt(attempt["id"], status="expired", finished_at=now_dt.isoformat())
            raise PostingError("Confirmation expired after 5 minutes; prepare the post again.", status=410, code="confirmation_expired")

        if not expected_hash or not hmac.compare_digest(str(expected_hash).strip().encode("utf-8"), attempt["payload_hash"].encode("utf-8")):
            raise PostingError("Confirmation hash mismatch; prepare the post again.", status=409, code="payload_changed")

        stored_opts = attempt.get("options") or {}
        eff_media_url = stored_opts.get("media_url") if media_url is None else media_url
        eff_media_paths = stored_opts.get("media_paths") if media_paths is None else media_paths
        eff_ai_gen = bool(stored_opts.get("ai_generated", False)) if ai_generated is None else bool(ai_generated)

        # Re-read item, account, confidential rules, and re-read/re-fetch media & re-hash everything.
        spec = self._build_prepared_spec(
            content_id=attempt["content_id"],
            platform=attempt["platform"],
            media_url=eff_media_url,
            media_paths=eff_media_paths,
            ai_generated=eff_ai_gen,
            acknowledge_repost=bool(acknowledge_repost),
            acknowledge_unknown=bool(acknowledge_unknown),
            exclude_attempt_id=attempt["id"],
        )
        if not hmac.compare_digest(spec["payload_hash"], attempt["payload_hash"]):
            db.update_posting_attempt(
                attempt["id"],
                status="cancelled",
                error="Draft, account, media, or options changed after prepare.",
                finished_at=now_dt.isoformat(),
            )
            raise PostingError(
                "Content, account, media, or options changed since prepare; prepare the post again.",
                status=409,
                code="payload_changed",
            )

        plat = attempt["platform"]
        tok = self.vault.load_token(plat)
        access_token = str(tok.get("access_token") or "").strip()
        if not access_token:
            raise PostingError(f"Saved {plat} token is empty; reconnect in Settings.", status=401, code="missing_token")

        if (db.get_posting_account(plat) or {}).get("account_id") != spec["account"]["account_id"]:
            raise PostingError("The connected account changed; prepare the post again.", status=409, code="payload_changed")

        acquired = db.acquire_posting_attempt_atomic(
            cid_token,
            attempt["payload_hash"],
            attempt["content_id"],
            plat,
            now_dt.isoformat(),
        )
        if acquired is None:
            raise PostingError(
                "Confirmation was already used or another send is already in progress.",
                status=409,
                code="confirmation_already_used",
            )

        events.publish(
            "posting.updated",
            {"platform": plat, "content_id": attempt["content_id"], "attempt_id": attempt["id"], "status": "sending"},
        )

        try:
            if plat == "linkedin":
                media = spec["media_entries"][0] if spec["media_entries"] else None
                result = linkedin.publish_post(
                    access_token,
                    spec["account"]["account_id"],
                    spec["plain_text"],
                    media_bytes=media["_raw_bytes"] if media else None,
                    media_mime=media["mime_type"] if media else None,
                    alt_text=media["alt_text"] if media else None,
                    transport=self._transport,
                )
            elif plat == "instagram":
                media = spec["media_entries"][0]
                result = instagram.publish_post(
                    access_token,
                    spec["account"]["account_id"],
                    spec["plain_text"],
                    media_url=media["url"],
                    media_type=media["media_type"],
                    ai_generated=spec["ai_generated"],
                    transport=self._transport,
                    clock=self._clock,
                    sleep_fn=self._sleep_fn,
                    poll_interval_s=self._ig_poll_interval_s,
                    max_poll_s=self._ig_max_poll_s,
                )
            else:
                refreshed = x_provider.refresh_token_locked(
                    self.vault,
                    self._x_refresh_lock,
                    now_fn=self._wall_clock,
                    transport=self._transport,
                )
                x_token = str(refreshed.get("access_token") or "").strip()
                media_items = [
                    {"bytes_data": m["_raw_bytes"], "mime_type": m["mime_type"]}
                    for m in spec["media_entries"]
                ]
                result = x_provider.publish_tweet(
                    x_token,
                    spec["plain_text"],
                    media_items=media_items,
                    made_with_ai=spec["ai_generated"],
                    username=spec["account"].get("username"),
                    transport=self._transport,
                )
        except PostingError as exc:
            fin_iso = self._now_iso()
            if exc.uncertain:
                msg = "Post status is unknown; check the platform before trying again."
                updated_attempt = db.update_posting_attempt(
                    attempt["id"],
                    status="unknown",
                    error=msg,
                    finished_at=fin_iso,
                )
                events.publish(
                    "posting.updated",
                    {"platform": plat, "content_id": attempt["content_id"], "attempt_id": attempt["id"], "status": "unknown"},
                )
                raise PostingError(msg, status=exc.status, code=exc.code, uncertain=True) from None

            updated_attempt = db.update_posting_attempt(
                attempt["id"],
                status="failed",
                error=exc.message,
                finished_at=fin_iso,
            )
            events.publish(
                "posting.updated",
                {"platform": plat, "content_id": attempt["content_id"], "attempt_id": attempt["id"], "status": "failed"},
            )
            raise
        except Exception:
            fin_iso = self._now_iso()
            msg = "Post status is unknown (unexpected error while sending); check the platform before trying again."
            db.update_posting_attempt(
                attempt["id"],
                status="unknown",
                error=msg,
                finished_at=fin_iso,
            )
            events.publish(
                "posting.updated",
                {"platform": plat, "content_id": attempt["content_id"], "attempt_id": attempt["id"], "status": "unknown"},
            )
            raise PostingError(msg, status=500, code="send_unknown", uncertain=True) from None

        fin_iso = self._now_iso()
        post_id = result.get("post_id")
        post_url = result.get("post_url")
        updated_attempt = db.update_posting_attempt(
            attempt["id"],
            status="posted",
            post_id=post_id,
            post_url=post_url,
            finished_at=fin_iso,
        )
        try:
            marked = review_studio.mark_posted(
                attempt["content_id"],
                url=post_url,
                platform=plat,
            )
            warning = None
        except Exception:
            # It is live: reporting an error here would invite posting it twice.
            marked = {"item": db.get_content_item(attempt["content_id"]), "status": "posted"}
            warning = "Posted, but the draft could not be marked as posted. Use Mark as posted."
        events.publish(
            "posting.updated",
            {
                "platform": plat,
                "content_id": attempt["content_id"],
                "attempt_id": attempt["id"],
                "status": "posted",
                "post_id": post_id,
                "post_url": post_url,
            },
        )
        return {
            "ok": True,
            "warning": warning,
            "attempt": _public_attempt(updated_attempt),
            "post_id": post_id,
            "post_url": post_url,
            "item": marked["item"],
            "status": marked["status"],
        }


_default_service_lock = threading.Lock()
_default_service = None


def get_default_service():
    global _default_service
    with _default_service_lock:
        if _default_service is None:
            _default_service = PostingService()
        return _default_service


def reset_default_service():
    global _default_service
    with _default_service_lock:
        if _default_service is not None:
            try:
                _default_service.cancel_x_oauth()
            except Exception:
                pass
        _default_service = None


def recover_stale_attempts():
    return db.recover_stale_posting_attempts()


def get_status():
    return get_default_service().get_status()


def list_attempts(content_id=None, platform=None, limit=50):
    return get_default_service().list_attempts(content_id=content_id, platform=platform, limit=limit)


def connect_linkedin(token):
    return get_default_service().connect_linkedin(token)


def connect_instagram(token):
    return get_default_service().connect_instagram(token)


def refresh_instagram():
    return get_default_service().refresh_instagram()


def start_x_oauth(client_id):
    return get_default_service().start_x_oauth(client_id)


def cancel_x_oauth():
    return get_default_service().cancel_x_oauth()


def disconnect(platform):
    return get_default_service().disconnect(platform)


def prepare(*args, **kwargs):
    return get_default_service().prepare(*args, **kwargs)


def confirm(*args, **kwargs):
    return get_default_service().confirm(*args, **kwargs)
