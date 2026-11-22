"""Challenge detection, 24h cooldown guard, social_challenge notifications, and activity logging."""

from datetime import datetime, timedelta, timezone
import os
import re
import sys
import time
import urllib.parse
import uuid

_SOCIAL_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_SOCIAL_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import events
import notifications
from social import SocialError, normalize_platform

CHALLENGE_KINDS = (
    "captcha",
    "checkpoint",
    "verification_code",
    "confirm_its_you",
    "unusual_activity",
    "logged_out",
)

SESSION_COOKIE_NAMES = {
    "instagram": "sessionid",
    "x": "auth_token",
    "linkedin": "li_at",
}

COOLDOWN_CHALLENGE_THRESHOLD = 2
COOLDOWN_HOURS = 24

_CONTENT_SLUG_ROOTS = frozenset({"p", "reel", "tv", "posts", "in", "search", "explore"})

_CAPTCHA_SEGMENTS = frozenset({"captcha", "funcaptcha", "recaptcha", "hcaptcha", "arkose"})
_CHECKPOINT_SEGMENTS = frozenset({"checkpoint", "challenge"})
_VERIFICATION_SEGMENTS = frozenset({"two_factor", "two-factor", "2fa", "verify", "otp", "email_verify", "phone_verify"})
_CONFIRM_SEGMENTS = frozenset({"confirm", "confirm_identity", "identity"})
_UNUSUAL_SEGMENTS = frozenset({"unusual_activity", "suspicious", "account_locked", "locked"})
_LOGGED_OUT_SEGMENTS = frozenset({"login", "signin", "authwall"})

_TEXT_MARKERS = (
    ("captcha", ("verify you are human", "i'm not a robot", "i am not a robot", "solve this puzzle", "complete the captcha", "security captcha")),
    ("verification_code", ("enter the code", "enter your verification code", "enter the verification code", "verification code required", "two-factor authentication required", "enter your two-factor", "we sent a code", "6-digit code", "enter confirmation code")),
    ("confirm_its_you", ("confirm it's you", "confirm it is you", "confirm your identity", "help us confirm you own this account")),
    ("unusual_activity", ("unusual activity detected", "unusual login activity", "suspicious activity detected", "temporarily locked", "automated behavior detected")),
    ("checkpoint", ("security check", "account checkpoint", "let's check your account", "suspicious login attempt")),
)

_LOGGED_OUT_TEXT_MARKERS = (
    "sign in to continue",
    "log in to continue",
    "you must log in",
    "join now to see",
    "log in or sign up",
)


def _detect_url_challenge(raw_url):
    parsed = urllib.parse.urlsplit(str(raw_url or "").strip())
    norm_path = urllib.parse.unquote(parsed.path or "/").lower()
    segments = [s for s in norm_path.split("/") if s]
    if not segments:
        return None

    # Never confuse handles, post slugs, or search routes with challenge paths.
    if segments[0] in _CONTENT_SLUG_ROOTS:
        return None
    if len(segments) >= 3 and segments[1] == "status":
        return None

    if segments[:2] == ["challenge", "captcha"] or any(seg in _CAPTCHA_SEGMENTS for seg in segments):
        return "captcha"
    if (
        segments[:2] == ["account", "access"]
        or segments[:3] == ["i", "flow", "consent"]
        or any(seg in _CHECKPOINT_SEGMENTS for seg in segments)
    ):
        return "checkpoint"
    if segments[:2] == ["accounts", "two_factor"] or any(seg in _VERIFICATION_SEGMENTS for seg in segments):
        return "verification_code"
    if any(seg in _CONFIRM_SEGMENTS for seg in segments):
        return "confirm_its_you"
    if any(seg in _UNUSUAL_SEGMENTS for seg in segments):
        return "unusual_activity"
    if (
        segments[:2] == ["accounts", "login"]
        or segments[:3] == ["i", "flow", "login"]
        or segments[:2] == ["uas", "login"]
        or any(seg in _LOGGED_OUT_SEGMENTS for seg in segments)
    ):
        return "logged_out"
    return None


def _is_content_route(raw_url):
    segments = [s for s in urllib.parse.urlsplit(str(raw_url or "")).path.lower().split("/") if s]
    return bool(segments) and (segments[0] in _CONTENT_SLUG_ROOTS or (len(segments) >= 3 and segments[1] == "status"))


def detect_challenge_from_snapshot(
    url,
    title="",
    body_text="",
    has_password_input=False,
    has_captcha_frame=False,
    has_otp_input=False,
    has_content_posts=False,
):
    """Pure detector for unit tests and live page snapshots; returns challenge kind or None."""
    url_kind = _detect_url_challenge(url)
    if url_kind is not None:
        return url_kind

    if has_captcha_frame:
        return "captcha"
    if has_otp_input:
        return "verification_code"
    if has_password_input:
        return "logged_out"

    if not has_content_posts:
        # Before posts render, search and post pages echo the query or the post in the title, and the query in the body.
        shown_title = "" if _is_content_route(url) else (title or "")
        combined = f"{shown_title}\n{body_text or ''}".lower()
        searched = urllib.parse.unquote_plus(urllib.parse.urlsplit(str(url or "")).query).lower()
        for kind, phrases in _TEXT_MARKERS:
            if any(phrase in combined and phrase not in searched for phrase in phrases):
                return kind
        if any(phrase in combined and phrase not in searched for phrase in _LOGGED_OUT_TEXT_MARKERS):
            return "logged_out"

    return None


def inspect_page(page):
    """Collect URL and visible DOM challenge markers from a Playwright page."""
    js = """() => {
        const isVisible = (el) => {
            if (!el) return false;
            if (el.hidden) return false;
            const style = window.getComputedStyle(el);
            if (!style) return false;
            if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) {
                return false;
            }
            const rect = el.getBoundingClientRect();
            return Boolean(rect && rect.width > 0 && rect.height > 0);
        };
        const anyVisible = (sel) => Array.from(document.querySelectorAll(sel)).some(isVisible);
        const title = document.title || '';
        const bodyText = document.body ? (document.body.innerText || '').slice(0, 4000) : '';
        const hasPasswordInput = anyVisible('input[type="password"]');
        const hasCaptchaFrame = anyVisible(
            'iframe[title*="captcha" i], iframe[src*="captcha" i], iframe[src*="arkose" i], iframe[src*="hcaptcha" i], iframe[src*="recaptcha" i], [data-challenge="captcha"]'
        );
        const hasOtpInput = anyVisible(
            'input[name*="verification_code" i], input[name*="otp" i], input[autocomplete="one-time-code"]'
        );
        const contentRoot = document.querySelector(
            '[data-testid="primaryColumn"], .scaffold-layout__main, main[role="main"], main, [role="main"]'
        );
        const hasContentPosts = Boolean(
            contentRoot && isVisible(contentRoot) && Array.from(
                contentRoot.querySelectorAll('article, [data-testid="tweet"], .feed-shared-update-v2, [role="feed"] > *')
            ).some(isVisible)
        );
        return { title, bodyText, hasPasswordInput, hasCaptchaFrame, hasOtpInput, hasContentPosts };
    }"""
    info = None
    for attempt in range(2):
        try:
            info = page.evaluate(js)
            break
        except Exception as exc:
            msg = str(exc).lower()
            if attempt == 0 and ("execution context was destroyed" in msg or "navigation" in msg):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                continue
            raise SocialError("Failed to inspect page state.", status=500, code="inspect_failed") from exc
    url = page.url or ""
    return {
        "url": url,
        "title": (info or {}).get("title") or "",
        "body_text": (info or {}).get("bodyText") or "",
        "has_password_input": bool((info or {}).get("hasPasswordInput")),
        "has_captcha_frame": bool((info or {}).get("hasCaptchaFrame")),
        "has_otp_input": bool((info or {}).get("hasOtpInput")),
        "has_content_posts": bool((info or {}).get("hasContentPosts")),
    }


def detect_challenge(page):
    """Inspect a Playwright page and return detected challenge kind or None."""
    snap = inspect_page(page)
    return detect_challenge_from_snapshot(
        snap["url"],
        title=snap["title"],
        body_text=snap["body_text"],
        has_password_input=snap["has_password_input"],
        has_captcha_frame=snap["has_captcha_frame"],
        has_otp_input=snap["has_otp_input"],
        has_content_posts=snap["has_content_posts"],
    )


def has_session_cookie(page, policy):
    """Return True if the required non-expired platform session cookie is present in context."""
    ctx = getattr(page, "context", None)
    cookies_fn = getattr(ctx, "cookies", None) if ctx is not None else None
    if not callable(cookies_fn):
        return False
    try:
        cookies = cookies_fn()
    except Exception:
        return False
    expected = SESSION_COOKIE_NAMES.get(policy.platform)
    if not expected:
        return False

    app_hosts_no_port = {h.split(":", 1)[0].lower() for h in policy._app_hosts}
    now_ts = time.time()
    for cookie in cookies or ():
        if not isinstance(cookie, dict):
            continue
        if cookie.get("name") != expected:
            continue
        val = str(cookie.get("value") or "").strip()
        if not val:
            continue
        exp = cookie.get("expires")
        if isinstance(exp, (int, float)) and exp > 0 and exp <= now_ts:
            continue
        dom = str(cookie.get("domain") or "").strip().lower().lstrip(".")
        if dom:
            if not any(h == dom or h.endswith("." + dom) for h in app_hosts_no_port):
                continue
        return True
    return False


def is_logged_in(page, policy):
    """Return True if page is on an allowed app host, has the session cookie, and has no challenge."""
    url = page.url or ""
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname or not policy.is_app_host(parsed.netloc, parsed.hostname):
        return False
    if not has_session_cookie(page, policy):
        return False
    return detect_challenge(page) is None


def record_and_notify(platform, run_id, challenge_kind, now_dt=None):
    """Record a challenge, emit platform+kind-only notification & activity, and pause 24h on 2nd in 24h."""
    plat = normalize_platform(platform)
    kind = str(challenge_kind or "checkpoint").strip().lower()
    if kind not in CHALLENGE_KINDS:
        kind = "checkpoint"

    now = now_dt or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    since_iso = (now - timedelta(hours=COOLDOWN_HOURS)).isoformat()

    challenge_id = f"sch_{uuid.uuid4().hex[:16]}"
    db.record_social_challenge(challenge_id, plat, run_id, kind, now_iso)
    recent_count = db.count_recent_social_challenges(plat, since_iso)

    # Notification and activity log contain ONLY platform and challenge kind.
    notif = notifications.notify(
        "social_challenge",
        title=f"Social check on {plat}",
        body=f"{plat}: {kind}",
        route="#/research",
        created_at=now_iso,
    )
    db.log_activity(
        actor="researcher",
        target=plat,
        task="social_challenge",
        file=kind,
        ts=now_iso,
    )

    paused_until = None
    if recent_count >= COOLDOWN_CHALLENGE_THRESHOLD:
        paused_until = (now + timedelta(hours=COOLDOWN_HOURS)).isoformat()
        db.set_social_platform_paused(
            plat,
            paused_until=paused_until,
            pause_reason=f"Paused for {COOLDOWN_HOURS} hours after {recent_count} security checks in 24 hours",
            updated_at=now_iso,
        )

    return {
        "challenge_id": challenge_id,
        "challenge_kind": kind,
        "notification_id": notif["id"] if notif else None,
        "recent_count_24h": recent_count,
        "paused_until": paused_until,
    }


def clear_challenge_alert(platform, run_id, notif_id=None, resolution="cleared", now_dt=None):
    """Mark the challenge alert resolved/read and notify listeners."""
    plat = normalize_platform(platform)
    now_iso = (now_dt or datetime.now(timezone.utc)).isoformat()
    db.resolve_social_challenges_for_run(run_id, now_iso, resolution)
    if notif_id:
        try:
            db.mark_notifications_read(ids=[notif_id])
        except Exception:
            pass
    events.publish(
        "social_challenge_cleared",
        {
            "platform": plat,
            "run_id": run_id,
            "notification_id": notif_id,
            "resolution": resolution,
        },
    )
