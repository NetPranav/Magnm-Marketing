"""Instagram professional account posting provider (Graph API v27.0)."""

import json
import os
import sys
import time
import urllib.parse

_POSTING_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_POSTING_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from posting import PostingError
from posting import http_client

TOKEN_TTL_DAYS = 60
EXPIRY_WARN_DAYS = 7
MAX_CAPTION_CHARS = 2200
MAX_POLL_SECONDS = 300.0
POLL_INTERVAL_SECONDS = 15.0

GRAPH_BASE = "https://graph.instagram.com/v27.0"
REFRESH_ENDPOINT = "https://graph.instagram.com/refresh_access_token"


def verify_token(token, *, transport=None):
    """Verify pasted Instagram token via /v27.0/me with Authorization header (never query param)."""
    clean = str(token or "").strip()
    if not clean:
        raise PostingError("Instagram token is required.", status=400, code="token_required")

    _, _, raw_bytes = http_client.http_request(
        "instagram",
        "GET",
        f"{GRAPH_BASE}/me?fields=id,username,account_type",
        headers={"Authorization": f"Bearer {clean}", "Accept": "application/json"},
        transport=transport,
    )
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise PostingError(
            "Instagram /me response was not valid JSON.",
            status=502,
            code="invalid_response",
        ) from exc

    account_id = str((data or {}).get("id") or "").strip()
    if not isinstance(data, dict) or not account_id:
        raise PostingError(
            "Instagram /me response did not include an account id.",
            status=502,
            code="invalid_response",
        )

    account_type = str(data.get("account_type") or "").strip().upper()
    if account_type == "PERSONAL":
        raise PostingError(
            "Instagram personal accounts are not supported; connect a Professional (Business or Creator) account.",
            status=400,
            code="personal_account_unsupported",
        )

    username = str(data.get("username") or "").strip() or None
    return {
        "account_id": account_id,
        "username": username,
        "display_name": f"@{username}" if username else account_id,
    }


def refresh_token_explicit(token, *, transport=None):
    """Explicit user-initiated refresh via refresh_access_token. Never log the request URL."""
    clean = str(token or "").strip()
    if not clean:
        raise PostingError("Instagram token is missing.", status=400, code="token_required")

    query = urllib.parse.urlencode({
        "grant_type": "ig_refresh_token",
        "access_token": clean,
    })
    refresh_url = f"{REFRESH_ENDPOINT}?{query}"
    try:
        _, _, raw_bytes = http_client.http_request(
            "instagram",
            "GET",
            refresh_url,
            headers={"Accept": "application/json"},
            transport=transport,
        )
    except PostingError as exc:
        raise PostingError(exc.message, status=exc.status, code=exc.code, uncertain=False) from None

    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise PostingError(
            "Instagram token refresh returned invalid JSON.",
            status=502,
            code="invalid_response",
        ) from exc

    new_token = str((data or {}).get("access_token") or "").strip()
    if not isinstance(data, dict) or not new_token:
        raise PostingError(
            "Instagram token refresh response did not include an access_token.",
            status=502,
            code="invalid_response",
        )
    expires_in = data.get("expires_in")
    try:
        ttl_s = int(expires_in) if expires_in is not None else TOKEN_TTL_DAYS * 86400
    except (TypeError, ValueError):
        ttl_s = TOKEN_TTL_DAYS * 86400
    return {
        "access_token": new_token,
        "expires_in": max(60, ttl_s),
    }


def publish_post(
    token,
    account_id,
    caption,
    *,
    media_url,
    media_type,
    ai_generated=False,
    transport=None,
    clock=None,
    sleep_fn=None,
    poll_interval_s=POLL_INTERVAL_SECONDS,
    max_poll_s=MAX_POLL_SECONDS,
):
    """Create container, poll until FINISHED (<= 5 min), and publish. Never auto-retries."""
    if len(caption or "") > MAX_CAPTION_CHARS:
        raise PostingError(
            f"Instagram caption exceeds {MAX_CAPTION_CHARS} characters.",
            status=400,
            code="text_too_long",
        )
    if media_type not in ("IMAGE", "REELS"):
        raise PostingError(
            "Instagram requires a public JPEG (IMAGE) or MP4 (REELS) URL.",
            status=400,
            code="unsupported_media_type",
        )

    mono = clock or time.monotonic
    wait_fn = sleep_fn or time.sleep
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if media_type == "IMAGE":
        container_body = {
            "image_url": media_url,
            "caption": caption,
            "is_ai_generated": bool(ai_generated),
        }
    else:
        container_body = {
            "media_type": "REELS",
            "video_url": media_url,
            "caption": caption,
            "is_ai_generated": bool(ai_generated),
        }

    _, _, container_raw = http_client.http_request(
        "instagram",
        "POST",
        f"{GRAPH_BASE}/{urllib.parse.quote(str(account_id), safe='')}/media",
        headers=auth_headers,
        body=json.dumps(container_body, ensure_ascii=False),
        transport=transport,
    )
    try:
        container_data = json.loads(container_raw.decode("utf-8"))
    except Exception as exc:
        raise PostingError(
            "Instagram container response was not valid JSON.",
            status=502,
            code="invalid_response",
            uncertain=True,
        ) from exc

    container_id = str((container_data or {}).get("id") or "").strip()
    if not isinstance(container_data, dict) or not container_id:
        raise PostingError(
            "Instagram did not return a container id.",
            status=502,
            code="invalid_response",
            uncertain=True,
        )

    deadline = mono() + float(max_poll_s)
    poll_url = f"{GRAPH_BASE}/{urllib.parse.quote(container_id, safe='')}?fields=status_code"
    while True:
        _, _, status_raw = http_client.http_request(
            "instagram",
            "GET",
            poll_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport,
        )
        try:
            status_data = json.loads(status_raw.decode("utf-8"))
        except Exception as exc:
            raise PostingError(
                "Instagram container status response was not valid JSON.",
                status=502,
                code="invalid_response",
                uncertain=False,
            ) from exc

        status_code = str((status_data or {}).get("status_code") or "").strip().upper()
        if status_code == "FINISHED":
            break
        if status_code in ("ERROR", "EXPIRED"):
            raise PostingError(
                f"Instagram media processing failed ({status_code}).",
                status=502,
                code="ig_container_failed",
                uncertain=False,
            )
        if mono() >= deadline:
            raise PostingError(
                "Instagram media container did not finish processing within 5 minutes.",
                status=504,
                code="ig_poll_timeout",
                uncertain=False,
            )
        remaining = deadline - mono()
        if remaining <= 0:
            raise PostingError(
                "Instagram media container did not finish processing within 5 minutes.",
                status=504,
                code="ig_poll_timeout",
                uncertain=False,
            )
        wait_fn(min(float(poll_interval_s), remaining))

    _, _, pub_raw = http_client.http_request(
        "instagram",
        "POST",
        f"{GRAPH_BASE}/{urllib.parse.quote(str(account_id), safe='')}/media_publish",
        headers=auth_headers,
        body=json.dumps({"creation_id": container_id}),
        transport=transport,
    )
    try:
        pub_data = json.loads(pub_raw.decode("utf-8"))
    except Exception as exc:
        raise PostingError(
            "Instagram publish response was not valid JSON.",
            status=502,
            code="invalid_response",
            uncertain=True,
        ) from exc

    post_id = str((pub_data or {}).get("id") or "").strip() or None
    return {
        "post_id": post_id,
        "post_url": None,
    }
