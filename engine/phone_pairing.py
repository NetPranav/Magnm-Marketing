#!/usr/bin/env python3
"""In-memory pairing flow and SHA-256 device token auth for Magnm Mobile."""

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import events
import team_auth

PAIRING_TTL_S = 300.0
RATE_WINDOW_S = 60.0
MAX_BAD_CODES_PER_MIN = 5
# The code is 256-bit, so the overall cap only limits noise. Kept high so one device can't lock out pairing.
MAX_BAD_CODES_ALL_IPS_PER_MIN = 60
MAX_DEVICE_NAME_LEN = 80
QR_PREFIX = "magnm-pair:"

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")
_DUMMY_CODE = "0" * 43


class PairingError(Exception):
    """Typed error for pairing validation, expiration, rate limiting, and auth."""

    def __init__(self, message, status=400, code="pairing_error"):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = str(code)


def hash_token(token):
    """Return the hex SHA-256 digest of a raw device bearer token."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def build_qr_payload(host, port, code):
    """Encode pairing coordinates and one-time code as magnm-pair:<base64url JSON>."""
    payload_obj = {
        "v": 1,
        "host": str(host),
        "port": int(port),
        "code": str(code),
    }
    raw = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{QR_PREFIX}{encoded}"


def parse_qr_payload(qr_text):
    """Decode a magnm-pair:<base64url JSON> string into a dict (helper for tests/clients)."""
    text = str(qr_text or "").strip()
    if not text.startswith(QR_PREFIX):
        raise ValueError("Invalid QR payload prefix")
    b64 = text[len(QR_PREFIX):]
    padded = b64 + "=" * (-len(b64) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("Unsupported pairing payload version")
    return data


def _sanitize_device_name(name):
    cleaned = _CONTROL_CHAR_RE.sub(" ", str(name or ""))
    cleaned = " ".join(cleaned.split()).strip()
    if len(cleaned) > MAX_DEVICE_NAME_LEN:
        cleaned = cleaned[:MAX_DEVICE_NAME_LEN].strip()
    return cleaned


def _iso_from_ts(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def get_current_owner_user_id():
    """Return the locally signed-in user_id, or empty string if signed out."""
    try:
        sess = team_auth.get_session_info()
    except Exception:
        sess = None
    if not isinstance(sess, dict):
        return ""
    return str(sess.get("user_id") or "").strip()


def _publish_phone_event(what, extra=None):
    """Publish a secret-free SSE notification for UI updates."""
    payload = {"what": str(what)}
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        events.publish("phone.updated", payload)
    except Exception:
        pass


class PairingManager:
    """Thread-safe in-memory pairing code, pending approval, and rate-limit state."""

    def __init__(self, now_fn=None):
        self._lock = threading.Lock()
        self._now_fn = now_fn or time.time
        self._active_code = None
        self._pending_requests = {}
        self._bad_attempts_global = []
        self._bad_attempts_by_ip = {}

    def _now(self, now=None):
        return float(self._now_fn() if now is None else now)

    def _purge_expired_locked(self, now_ts):
        if self._active_code is not None and now_ts >= self._active_code["expires_at"]:
            self._active_code = None

        expired_ids = [
            rid for rid, req in self._pending_requests.items()
            if now_ts >= req["expires_at"]
        ]
        for rid in expired_ids:
            req = self._pending_requests.pop(rid, None)
            if req is not None:
                req["device_token"] = None

        cutoff = now_ts - RATE_WINDOW_S
        self._bad_attempts_global = [t for t in self._bad_attempts_global if t > cutoff]
        for ip in list(self._bad_attempts_by_ip):
            kept = [t for t in self._bad_attempts_by_ip[ip] if t > cutoff]
            if kept:
                self._bad_attempts_by_ip[ip] = kept
            else:
                del self._bad_attempts_by_ip[ip]

    def reset(self):
        """Clear all in-memory pairing codes, requests, and rate-limit counters."""
        with self._lock:
            self._active_code = None
            for req in self._pending_requests.values():
                req["device_token"] = None
            self._pending_requests.clear()
            self._bad_attempts_global.clear()
            self._bad_attempts_by_ip.clear()

    def start_pairing(self, host, port, *, ttl_s=PAIRING_TTL_S, now=None):
        """Create a fresh 256-bit one-time pairing code valid for 5 minutes."""
        now_ts = self._now(now)
        code = secrets.token_urlsafe(32)
        expires_at = now_ts + float(ttl_s)
        qr_payload = build_qr_payload(host, port, code)

        with self._lock:
            self._purge_expired_locked(now_ts)
            self._active_code = {
                "code": code,
                "host": str(host),
                "port": int(port),
                "qr_payload": qr_payload,
                "created_at": now_ts,
                "expires_at": expires_at,
            }

        _publish_phone_event("pairing_started", {"expires_at": _iso_from_ts(expires_at)})
        return {
            "active": True,
            "qr_payload": qr_payload,
            "expires_at": _iso_from_ts(expires_at),
            "expires_in": max(0, int(expires_at - now_ts)),
        }

    def cancel_pairing(self, *, now=None):
        """Invalidate any active pairing code and clear pending requests."""
        now_ts = self._now(now)
        with self._lock:
            self._active_code = None
            for req in self._pending_requests.values():
                req["device_token"] = None
            self._pending_requests.clear()
            self._purge_expired_locked(now_ts)
        _publish_phone_event("pairing_cancelled")
        return self.get_status(now=now_ts)

    def get_status(self, *, now=None):
        """Return pairing state for the loopback Settings UI."""
        now_ts = self._now(now)
        with self._lock:
            self._purge_expired_locked(now_ts)
            active = self._active_code
            pending = [
                {
                    "request_id": req["request_id"],
                    "device_name": req["device_name"],
                    "source_ip": req["source_ip"],
                    "created_at": _iso_from_ts(req["created_at"]),
                    "expires_at": _iso_from_ts(req["expires_at"]),
                }
                for req in self._pending_requests.values()
                if req["status"] == "pending"
            ]
            if active is None:
                return {
                    "active": False,
                    "qr_payload": None,
                    "expires_at": None,
                    "expires_in": 0,
                    "pending_requests": pending,
                }
            return {
                "active": True,
                "qr_payload": active["qr_payload"],
                "expires_at": _iso_from_ts(active["expires_at"]),
                "expires_in": max(0, int(active["expires_at"] - now_ts)),
                "pending_requests": pending,
            }

    def submit_pair_request(self, code, device_name, source_ip, *, now=None):
        """Validate a 256-bit pairing code in constant time, consume it, and stage approval."""
        now_ts = self._now(now)
        ip_key = str(source_ip or "unknown").strip() or "unknown"
        clean_name = _sanitize_device_name(device_name)
        if not clean_name:
            raise PairingError("device_name is required", status=400, code="invalid_device_name")
        if not isinstance(code, str) or not code.strip():
            raise PairingError("code is required", status=400, code="invalid_code")
        provided_code = code.strip()

        with self._lock:
            self._purge_expired_locked(now_ts)
            if (
                len(self._bad_attempts_global) >= MAX_BAD_CODES_ALL_IPS_PER_MIN
                or len(self._bad_attempts_by_ip.get(ip_key, ())) >= MAX_BAD_CODES_PER_MIN
            ):
                raise PairingError(
                    "Too many invalid pairing attempts. Please wait a minute and try again.",
                    status=429,
                    code="rate_limited",
                )

            active = self._active_code
            expected = active["code"] if active is not None else _DUMMY_CODE
            matched = hmac.compare_digest(provided_code, expected) and (active is not None)
            if not matched:
                self._bad_attempts_global.append(now_ts)
                self._bad_attempts_by_ip.setdefault(ip_key, []).append(now_ts)
                raise PairingError(
                    "Invalid or expired pairing code.",
                    status=401,
                    code="invalid_code",
                )

            # Consume the one-time code immediately; pending request inherits its expiry.
            code_expires_at = active["expires_at"]
            self._active_code = None

            request_id = f"preq_{secrets.token_hex(12)}"
            poll_secret = secrets.token_urlsafe(32)
            self._pending_requests[request_id] = {
                "request_id": request_id,
                "device_name": clean_name,
                "source_ip": ip_key,
                "poll_secret": poll_secret,
                "created_at": now_ts,
                "expires_at": code_expires_at,
                "status": "pending",
                "device_id": None,
                "device_token": None,
            }

        _publish_phone_event(
            "pairing_requested",
            {
                "request_id": request_id,
                "device_name": clean_name,
                "source_ip": ip_key,
            },
        )
        return {
            "request_id": request_id,
            "poll_secret": poll_secret,
            "expires_at": _iso_from_ts(code_expires_at),
        }

    def approve_request(self, request_id, *, now=None):
        """Approve a pending pairing request, persist sha256(token), and stage one-time token delivery."""
        now_ts = self._now(now)
        rid = str(request_id or "").strip()
        if not rid:
            raise PairingError("request_id is required", status=400, code="invalid_request")

        with self._lock:
            self._purge_expired_locked(now_ts)
            req = self._pending_requests.get(rid)
            if req is None:
                raise PairingError("Pairing request not found or expired.", status=404, code="not_found")
            if req["status"] != "pending":
                raise PairingError(
                    f"Pairing request is already {req['status']}.",
                    status=409,
                    code="invalid_state",
                )

            owner_uid = get_current_owner_user_id()
            if not owner_uid:
                raise PairingError(
                    "Sign in under Settings, Account before pairing a phone.", status=400, code="sign_in_required"
                )
            token = secrets.token_urlsafe(32)
            token_hash = hash_token(token)
            device_id = f"pdev_{secrets.token_hex(12)}"

            device = db.create_phone_device(
                device_id=device_id,
                name=req["device_name"],
                token_hash=token_hash,
                created_at=_iso_from_ts(now_ts),
                last_ip=req["source_ip"],
                owner_user_id=owner_uid,
            )

            req["status"] = "approved"
            req["device_id"] = device_id
            req["device_token"] = token

        db.log_activity("CEO", "phone", f"phone_paired:{device_id}", req["device_name"])
        _publish_phone_event("device_approved", {"device_id": device_id, "name": req["device_name"]})
        return device

    def deny_request(self, request_id, *, now=None):
        """Deny a pending pairing request."""
        now_ts = self._now(now)
        rid = str(request_id or "").strip()
        if not rid:
            raise PairingError("request_id is required", status=400, code="invalid_request")

        with self._lock:
            self._purge_expired_locked(now_ts)
            req = self._pending_requests.get(rid)
            if req is None:
                raise PairingError("Pairing request not found or expired.", status=404, code="not_found")
            if req["status"] != "pending":
                raise PairingError(
                    f"Pairing request is already {req['status']}.",
                    status=409,
                    code="invalid_state",
                )
            req["status"] = "denied"
            req["device_token"] = None
            name = req["device_name"]

        db.log_activity("CEO", "phone", "phone_pairing_denied", name)
        _publish_phone_event("pairing_denied", {"request_id": rid})
        return {"ok": True, "request_id": rid, "status": "denied"}

    def poll_request(self, request_id, poll_secret, *, now=None):
        """Return status for a pairing request; deliver the 256-bit token exactly once after approval."""
        now_ts = self._now(now)
        rid = str(request_id or "").strip()
        secret = str(poll_secret or "").strip()
        if not rid or not secret:
            raise PairingError("request_id and poll_secret are required", status=400, code="invalid_request")

        with self._lock:
            self._purge_expired_locked(now_ts)
            req = self._pending_requests.get(rid)
            if req is None:
                raise PairingError("Pairing request not found or expired.", status=404, code="not_found")
            if not hmac.compare_digest(secret, req["poll_secret"]):
                raise PairingError("Invalid pairing credentials.", status=401, code="unauthorized")

            status = req["status"]
            if status == "pending":
                return {"status": "pending"}
            if status == "denied":
                return {"status": "denied"}
            if status == "approved":
                token = req.get("device_token")
                if not token:
                    req["status"] = "consumed"
                    raise PairingError("Device token already collected.", status=410, code="token_consumed")
                req["device_token"] = None
                req["status"] = "consumed"
                return {
                    "status": "approved",
                    "device_id": req["device_id"],
                    "device_name": req["device_name"],
                    "device_token": token,
                }
            raise PairingError("Device token already collected.", status=410, code="token_consumed")


_DEFAULT_MANAGER = PairingManager()


def get_manager():
    """Return the process-wide PairingManager singleton."""
    return _DEFAULT_MANAGER


def authenticate_bearer(auth_header, source_ip=None):
    """Verify Authorization: Bearer <token>, check revocation and owner_user_id, and touch last_seen."""
    raw = str(auth_header or "").strip()
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None

    digest = hash_token(token)
    device = db.get_phone_device_by_token_hash(digest)
    if device is None:
        return None
    if device.get("revoked_at") is not None:
        return None

    device_owner = str(device.get("owner_user_id") or "").strip()
    current_owner = get_current_owner_user_id()
    if not device_owner or not current_owner or device_owner != current_owner:
        return None  # A phone works only while the account that paired it is signed in.

    now_iso = datetime.now(timezone.utc).isoformat()
    touched = db.touch_phone_device(device["id"], last_seen_at=now_iso, last_ip=source_ip)
    return touched or device


def list_devices(include_revoked=False):
    """List paired phone devices (never returning token hashes)."""
    return db.list_phone_devices(include_revoked=include_revoked)


def revoke_device(device_id):
    """Revoke a paired phone device by ID so its next request is rejected with 401."""
    did = str(device_id or "").strip()
    if not did:
        raise PairingError("device_id is required", status=400, code="invalid_request")
    existing = db.get_phone_device(did)
    if existing is None:
        raise PairingError("Phone device not found.", status=404, code="not_found")
    revoked = db.revoke_phone_device(did)
    db.log_activity("CEO", "phone", f"phone_revoked:{did}", existing["name"])
    _publish_phone_event("device_revoked", {"device_id": did})
    return revoked
