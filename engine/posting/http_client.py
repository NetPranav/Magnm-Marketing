"""Strict allowlisted HTTPS client and public-only media fetcher for official posting APIs."""

import hashlib
import http.client
import ipaddress
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_POSTING_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_POSTING_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from posting import PostingError
from research import egress, fetch as research_fetch

DEFAULT_TIMEOUT_S = 15
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 10 * 1024 * 1024
MAX_PUBLIC_MEDIA_BYTES = 100 * 1024 * 1024  # 100 MB

_ALLOWED_ENDPOINTS = {
    "linkedin": (
        ("api.linkedin.com", re.compile(r"^/(?:v2/userinfo|rest/posts|rest/images)$")),
    ),
    "instagram": (
        (
            "graph.instagram.com",
            re.compile(r"^(?:/refresh_access_token|/v27\.0/(?:me|[A-Za-z0-9._-]+(?:/media|/media_publish)?))$"),
        ),
    ),
    "x": (
        (
            "api.x.com",
            re.compile(
                r"^/2/(?:oauth2/token|users/me|tweets|media/upload/initialize|media/upload/[A-Za-z0-9_-]+/(?:append|finalize))$"
            ),
        ),
    ),
}

_ALLOWED_MEDIA_MIME = frozenset(("image/jpeg", "image/jpg", "video/mp4", "application/octet-stream"))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse all HTTP redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PostingError(
            "Redirects are refused by posting HTTP policy.",
            status=502,
            code="redirect_blocked",
            uncertain=False,
        )


def _is_ip_literal(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def validate_linkedin_upload_url(url):
    """Ensure a LinkedIn uploadUrl is https:// on *.linkedin.com with no userinfo or fragment."""
    raw = str(url or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise PostingError("LinkedIn upload URL must use https.", status=502, code="invalid_upload_url")
    if parsed.username or parsed.password or parsed.fragment:
        raise PostingError("LinkedIn upload URL contains disallowed components.", status=502, code="invalid_upload_url")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host or _is_ip_literal(host):
        raise PostingError("LinkedIn upload URL host is invalid.", status=502, code="invalid_upload_url")
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise PostingError("LinkedIn upload URL must be on *.linkedin.com.", status=502, code="invalid_upload_url")
    if parsed.port not in (None, 443):
        raise PostingError("LinkedIn upload URL port is invalid.", status=502, code="invalid_upload_url")
    return raw


def validate_provider_url(provider, url, *, allow_linkedin_upload=False):
    """Enforce https-only fixed host and path allowlist per provider."""
    if allow_linkedin_upload and provider == "linkedin":
        return validate_linkedin_upload_url(url)

    raw = str(url or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise PostingError("Provider URL must use https.", status=400, code="disallowed_url")
    if parsed.username or parsed.password or parsed.fragment:
        raise PostingError("Provider URL contains disallowed components.", status=400, code="disallowed_url")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if parsed.port not in (None, 443):
        raise PostingError("Provider URL port is not allowlisted.", status=400, code="disallowed_url")

    rules = _ALLOWED_ENDPOINTS.get(provider) or ()
    path = parsed.path or "/"
    for allowed_host, path_re in rules:
        if host == allowed_host and path_re.fullmatch(path):
            return raw

    raise PostingError(
        f"Provider URL host or path is not allowlisted for {provider}.",
        status=400,
        code="disallowed_url",
    )


def _read_capped(resp, max_bytes):
    cl = resp.headers.get("Content-Length") if hasattr(resp, "headers") and resp.headers else None
    if cl is not None:
        try:
            if int(cl) > max_bytes:
                raise PostingError("Provider response exceeded size limit.", status=502, code="response_too_large")
        except ValueError:
            pass
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PostingError("Provider response exceeded size limit.", status=502, code="response_too_large")
    return data


def _headers_dict(raw_headers):
    out = {}
    if raw_headers is None:
        return out
    items = raw_headers.items() if hasattr(raw_headers, "items") else ()
    for k, v in items:
        out[str(k).lower()] = str(v)
    return out


def http_request(
    provider,
    method,
    url,
    *,
    headers=None,
    body=None,
    timeout=DEFAULT_TIMEOUT_S,
    max_bytes=MAX_API_RESPONSE_BYTES,
    allow_linkedin_upload=False,
    transport=None,
):
    """Send a single allowlisted HTTPS request without redirects or proxies. Never auto-retries."""
    clean_url = validate_provider_url(provider, url, allow_linkedin_upload=allow_linkedin_upload)
    req_method = str(method or "GET").strip().upper()
    req_headers = dict(headers or {})
    raw_body = body.encode("utf-8") if isinstance(body, str) else body
    # Once a write is sent, an unreadable, redirected or timed-out answer says nothing about whether it happened.
    is_write = req_method != "GET"

    if transport is not None:
        status, resp_headers, resp_body = transport(req_method, clean_url, req_headers, raw_body, timeout)
        status = int(status)
        norm_headers = {str(k).lower(): str(v) for k, v in (resp_headers or {}).items()}
        if isinstance(resp_body, str):
            resp_bytes = resp_body.encode("utf-8")
        elif resp_body is None:
            resp_bytes = b""
        else:
            resp_bytes = bytes(resp_body)
        if len(resp_bytes) > max_bytes:
            raise PostingError(
                "Provider response exceeded size limit.",
                status=502,
                code="response_too_large",
                uncertain=True,
            )
        if 300 <= status < 400:
            raise PostingError(
                "Redirects are refused by posting HTTP policy.",
                status=502,
                code="redirect_blocked",
                uncertain=is_write,
            )
        if status == 408 or 500 <= status <= 599:
            raise PostingError(
                f"{provider} returned a server error (HTTP {status}).",
                status=502,
                code="provider_5xx",
                uncertain=True,
            )
        if status >= 400:
            raise PostingError(
                f"{provider} rejected the request (HTTP {status}).",
                status=status if status in (400, 401, 403, 404, 409, 422, 429) else 400,
                code=f"provider_http_{status}",
                uncertain=False,
            )
        return status, norm_headers, resp_bytes

    req = urllib.request.Request(clean_url, data=raw_body, headers=req_headers, method=req_method)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(),
        _NoRedirectHandler(),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode() or 200)
            norm_headers = _headers_dict(getattr(resp, "headers", None))
            resp_bytes = _read_capped(resp, max_bytes)
            if 300 <= status < 400:
                raise PostingError(
                    "Redirects are refused by posting HTTP policy.",
                    status=502,
                    code="redirect_blocked",
                    uncertain=False,
                )
            return status, norm_headers, resp_bytes
    except PostingError as exc:
        exc.uncertain = is_write
        raise
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 500) or 500)
        try:
            exc.close()
        except Exception:
            pass
        if 300 <= code < 400:
            raise PostingError(
                "Redirects are refused by posting HTTP policy.",
                status=502,
                code="redirect_blocked",
                uncertain=is_write,
            ) from exc
        if code == 408 or 500 <= code <= 599:
            raise PostingError(
                f"{provider} returned a server error (HTTP {code}).",
                status=502,
                code="provider_5xx",
                uncertain=True,
            ) from exc
        raise PostingError(
            f"{provider} rejected the request (HTTP {code}).",
            status=code if code in (400, 401, 403, 404, 409, 422, 429) else 400,
            code=f"provider_http_{code}",
            uncertain=False,
        ) from exc
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as exc:
        # ValueError included: its text can quote a header, and with it the token.
        raise PostingError(
            f"Network error communicating with {provider}.",
            status=504,
            code="network_error",
            uncertain=True,
        ) from exc


def sniff_instagram_media_type(data, content_type=""):
    """Return ('IMAGE', 'image/jpeg') or ('REELS', 'video/mp4') from magic bytes, or raise PostingError."""
    ct = str(content_type or "").split(";", 1)[0].strip().lower()
    if ct and ct not in _ALLOWED_MEDIA_MIME:
        raise PostingError(
            "Instagram media URL must return JPEG or MP4 content.",
            status=400,
            code="unsupported_media_type",
        )
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "IMAGE", "image/jpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "REELS", "video/mp4"
    raise PostingError(
        "Instagram media URL did not return a valid JPEG image or MP4 video.",
        status=400,
        code="unsupported_media_type",
    )


def fetch_public_media(
    url,
    *,
    max_bytes=MAX_PUBLIC_MEDIA_BYTES,
    timeout=20,
    fetcher=None,
):
    """Fetch an already-public https media URL via public-only egress guard (no redirects, JPEG/MP4, <= 100 MB)."""
    raw = str(url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        parsed.port  # raises on a malformed port
    except ValueError:
        raise PostingError("Instagram media URL is invalid.", status=400, code="invalid_media_url") from None
    if parsed.scheme.lower() != "https":
        raise PostingError(
            "Instagram media URL must be a public https:// URL.",
            status=400,
            code="invalid_media_url",
        )
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise PostingError(
            "Instagram media URL is invalid.",
            status=400,
            code="invalid_media_url",
        )

    if fetcher is not None:
        resp_bytes, content_type = fetcher(raw, max_bytes, timeout)
        data = bytes(resp_bytes or b"")
        if len(data) > max_bytes:
            raise PostingError(
                "Instagram media exceeds the 100 MB limit.",
                status=400,
                code="media_too_large",
            )
    else:
        try:
            research_fetch.validate_url(raw, check_blocklist=False)
        except research_fetch.FetchError as exc:
            raise PostingError(
                "Instagram media URL must point to a public address.",
                status=400,
                code="blocked_media_url",
            ) from exc

        req = urllib.request.Request(raw, headers={"User-Agent": "MagnmStudio/1.0"}, method="GET")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            egress.PublicHTTPSHandler(),
            _NoRedirectHandler(),
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", None) or resp.getcode() or 200)
                if 300 <= status < 400:
                    raise PostingError(
                        "Redirects are not allowed when fetching Instagram media URL.",
                        status=400,
                        code="redirect_blocked",
                    )
                final_url = resp.geturl() if hasattr(resp, "geturl") else raw
                if final_url != raw:
                    raise PostingError(
                        "Redirects are not allowed when fetching Instagram media URL.",
                        status=400,
                        code="redirect_blocked",
                    )
                content_type = (resp.headers.get("Content-Type") if resp.headers else "") or ""
                data = _read_capped(resp, max_bytes)
        except PostingError:
            raise
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            raise PostingError(
                f"Could not fetch Instagram media URL (HTTP {getattr(exc, 'code', 0)}).",
                status=400,
                code="media_fetch_failed",
            ) from exc
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            raise PostingError(
                "Could not fetch Instagram media URL.",
                status=502,
                code="media_fetch_failed",
            ) from exc

    media_type, mime_type = sniff_instagram_media_type(data, content_type=content_type)
    sha256_hex = hashlib.sha256(data).hexdigest()
    return {
        "preview_bytes": data if media_type == "IMAGE" and len(data) <= MAX_PREVIEW_BYTES else None,
        "url": raw,
        "bytes": len(data),
        "sha256": sha256_hex,
        "mime_type": mime_type,
        "media_type": media_type,
    }
