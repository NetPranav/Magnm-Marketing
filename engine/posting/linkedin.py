"""LinkedIn personal posting provider (Token Generator OAuth2 + Share on LinkedIn REST API)."""

import json
import os
import re
import sys

_POSTING_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_POSTING_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from posting import PostingError
from posting import http_client

TOKEN_TTL_DAYS = 60
EXPIRY_WARN_DAYS = 7
MAX_COMMENTARY_CHARS = 3000

LINKEDIN_VERSION = "202611"
RESTLI_PROTOCOL_VERSION = "2.0.0"

USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
INITIALIZE_UPLOAD_URL = "https://api.linkedin.com/rest/images?action=initializeUpload"
POSTS_URL = "https://api.linkedin.com/rest/posts"

# Match either a standalone one-word hashtag (#word) or any single LinkedIn little-text special char.
_LITTLE_TEXT_RE = re.compile(r"(?<![\w#])#[A-Za-z0-9_]+(?![\w#-])|[\\|{}@\[\]()<>#*~_]")


def escape_little_text(text):
    """Escape LinkedIn little-text special characters except # starting a one-word hashtag."""
    raw = str(text or "")

    def _repl(match):
        tok = match.group(0)
        if len(tok) > 1 and tok.startswith("#"):
            return tok
        return "\\" + tok

    return _LITTLE_TEXT_RE.sub(_repl, raw)


def _rest_headers(token, *, content_type=None):
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": RESTLI_PROTOCOL_VERSION,
    }
    if content_type:
        hdrs["Content-Type"] = content_type
    return hdrs


def verify_token(token, *, transport=None):
    """Verify pasted LinkedIn token via /v2/userinfo and return non-secret account metadata."""
    clean = str(token or "").strip()
    if not clean:
        raise PostingError("LinkedIn token is required.", status=400, code="token_required")

    _, _, raw_bytes = http_client.http_request(
        "linkedin",
        "GET",
        USERINFO_URL,
        headers={"Authorization": f"Bearer {clean}", "Accept": "application/json"},
        transport=transport,
    )
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise PostingError(
            "LinkedIn userinfo response was not valid JSON.",
            status=502,
            code="invalid_response",
        ) from exc

    sub = str((data or {}).get("sub") or "").strip()
    if not isinstance(data, dict) or not sub:
        raise PostingError(
            "LinkedIn userinfo response did not include a member id (sub).",
            status=502,
            code="invalid_response",
        )

    name = str(data.get("name") or "").strip()
    if not name:
        given = str(data.get("given_name") or "").strip()
        family = str(data.get("family_name") or "").strip()
        name = f"{given} {family}".strip()

    account_id = f"urn:li:person:{sub}"
    return {
        "account_id": account_id,
        "username": None,
        "display_name": name or account_id,
    }


def publish_post(
    token,
    account_id,
    plain_text,
    *,
    media_bytes=None,
    media_mime=None,
    alt_text=None,
    transport=None,
):
    """Publish a personal LinkedIn post (and optional single JPG/PNG). Never auto-retries."""
    commentary = escape_little_text(plain_text)
    if len(commentary) > MAX_COMMENTARY_CHARS:
        raise PostingError(
            f"LinkedIn post exceeds {MAX_COMMENTARY_CHARS} characters after formatting ({len(commentary)}).",
            status=400,
            code="text_too_long",
        )

    media_block = None
    if media_bytes is not None:
        if media_mime not in ("image/jpeg", "image/png"):
            raise PostingError(
                "LinkedIn supports a single JPG or PNG image.",
                status=400,
                code="unsupported_media_type",
            )
        init_payload = json.dumps(
            {"initializeUploadRequest": {"owner": account_id}},
            separators=(",", ":"),
        )
        _, _, init_raw = http_client.http_request(
            "linkedin",
            "POST",
            INITIALIZE_UPLOAD_URL,
            headers=_rest_headers(token, content_type="application/json"),
            body=init_payload,
            transport=transport,
        )
        try:
            init_data = json.loads(init_raw.decode("utf-8"))
        except Exception as exc:
            raise PostingError(
                "LinkedIn upload initialization returned invalid JSON.",
                status=502,
                code="invalid_response",
                uncertain=True,
            ) from exc

        val = init_data.get("value") if isinstance(init_data, dict) else None
        upload_url = (val or {}).get("uploadUrl") if isinstance(val, dict) else None
        image_urn = (val or {}).get("image") if isinstance(val, dict) else None
        if not upload_url or not image_urn:
            raise PostingError(
                "LinkedIn upload initialization response was incomplete.",
                status=502,
                code="invalid_response",
                uncertain=True,
            )

        http_client.http_request(
            "linkedin",
            "PUT",
            upload_url,
            # LinkedIn requires the token on image uploads; the host was checked to be *.linkedin.com.
            headers={"Authorization": f"Bearer {token}", "Content-Type": media_mime},
            body=bytes(media_bytes),
            allow_linkedin_upload=True,
            transport=transport,
        )
        media_obj = {"id": str(image_urn)}
        if alt_text and str(alt_text).strip():
            media_obj["altText"] = str(alt_text).strip()
        media_block = {"media": media_obj}

    post_body = {
        "author": account_id,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if media_block is not None:
        post_body["content"] = media_block

    status, resp_headers, _ = http_client.http_request(
        "linkedin",
        "POST",
        POSTS_URL,
        headers=_rest_headers(token, content_type="application/json"),
        body=json.dumps(post_body, ensure_ascii=False),
        transport=transport,
    )
    if status not in (200, 201):
        raise PostingError(
            f"LinkedIn returned unexpected status HTTP {status}.",
            status=502,
            code="unexpected_status",
            uncertain=True,
        )

    post_id = (resp_headers.get("x-restli-id") or "").strip() or None
    post_url = f"https://www.linkedin.com/feed/update/{post_id}/" if post_id else None
    return {
        "post_id": post_id,
        "post_url": post_url,
    }
