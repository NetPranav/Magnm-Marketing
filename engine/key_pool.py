#!/usr/bin/env python3
"""Thread-safe NVIDIA NIM API key pool with LRU rotation, rate-limit tracking, and cooldowns."""

from collections import deque
import os
import sys
import threading
import time

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

RATE_LIMIT_PER_MIN = 38
WINDOW_SECONDS = 60.0
DEFAULT_COOLDOWN_SECONDS = 60.0
ACQUIRE_TIMEOUT_SECONDS = 20.0
MAX_NUMBERED_KEYS = 20

_KNOWN_ROLE_SUFFIXES = (
    "CMO",
    "STRATEGIST",
    "ADVISOR",
    "RESEARCHER",
    "BLOG_WRITER",
    "WRITER",
    "DESIGNER",
    "VIDEO_PRODUCER",
    "REVIEWER",
    "ANALYST",
    "LISTENER",
    "COMMUNITY",
    "GROWTH",
)


class AINotConnected(RuntimeError):
    """Raised when no configured AI provider or local Ollama instance can answer."""


class AIAuthError(RuntimeError):
    """Raised when an AI provider rejects authentication credentials (HTTP 401/403)."""


def mask_key(val):
    """Return masked form (first 6 + last 3 chars) that never exposes the full key."""
    if not val:
        return ""
    if len(val) <= 9:
        return "*" * len(val)
    return f"{val[:6]}...{val[-3:]}"


def _role_env_name(role):
    norm = (role or "").strip().upper().replace("-", "_")
    if not norm:
        return ""
    return f"NVIDIA_NIM_API_KEY_{norm}"


class _KeyEntry:
    """Mutable per-key state tracked inside KeyPool."""

    __slots__ = (
        "label",
        "key",
        "masked",
        "is_pool",
        "order",
        "last_used",
        "last_used_seq",
        "requests",
        "cooldown_until",
        "disabled_reason",
        "ok",
        "errors",
    )

    def __init__(self, label, key, is_pool, order):
        self.label = label
        self.key = key
        self.masked = mask_key(key)
        self.is_pool = is_pool
        self.order = order
        self.last_used = None
        self.last_used_seq = 0
        self.requests = deque()
        self.cooldown_until = None
        self.disabled_reason = None
        self.ok = 0
        self.errors = 0


class KeyPool:
    """In-process, thread-safe pool of NVIDIA NIM API keys."""

    def __init__(
        self,
        clock=None,
        sleep=None,
        env_loader=None,
        environ=None,
        rate_limit_per_min=RATE_LIMIT_PER_MIN,
        acquire_timeout_s=ACQUIRE_TIMEOUT_SECONDS,
    ):
        self._clock = clock
        self._sleep = sleep
        self._env_loader = env_loader
        self._environ = environ
        self._rate_limit = int(rate_limit_per_min)
        self._acquire_timeout_s = float(acquire_timeout_s)
        self._lock = threading.Lock()
        self._entries = {}
        self._pool_labels = []
        self._role_preferred_label = {}
        self._extra_roles = set()
        self._seq = 0

    def _now(self):
        return float(self._clock() if self._clock is not None else time.monotonic())

    def _do_sleep(self, seconds, sleep_fn=None):
        fn = sleep_fn if sleep_fn is not None else (self._sleep if self._sleep is not None else time.sleep)
        fn(seconds)

    def _load_file_env(self):
        if self._env_loader is not None:
            return self._env_loader() or {}
        try:
            import ai_gateway
            return ai_gateway.load_env() or {}
        except Exception:
            return {}

    def _get_environ(self):
        return self._environ if self._environ is not None else os.environ

    def _role_env_names(self):
        names = [f"NVIDIA_NIM_API_KEY_{suffix}" for suffix in _KNOWN_ROLE_SUFFIXES]
        seen = set(names)
        for role in sorted(self._extra_roles):
            env_name = _role_env_name(role)
            if env_name and env_name not in seen:
                names.append(env_name)
                seen.add(env_name)
        return names

    def _sync_locked(self, role=None):
        if role:
            self._extra_roles.add((role or "").lower().strip())

        file_env = self._load_file_env()
        env_map = self._get_environ()

        def get_val(name):
            val = file_env.get(name)
            if val is not None and str(val).strip():
                return str(val).strip()
            env_val = env_map.get(name)
            if env_val is not None and str(env_val).strip():
                return str(env_val).strip()
            return ""

        pool_vars = (
            ["NVIDIA_NIM_API_KEY"]
            + [f"NVIDIA_NIM_API_KEY{i}" for i in range(1, MAX_NUMBERED_KEYS + 1)]
            + ["NVIDIA_API_KEY"]
        )

        desired = []
        seen_values = {}
        pool_labels = []
        order = 0

        for var_name in pool_vars:
            val = get_val(var_name)
            if not val or val in seen_values:
                continue
            seen_values[val] = var_name
            desired.append((var_name, val, True, order))
            pool_labels.append(var_name)
            order += 1

        role_preferred = {}
        try:
            import ai_gateway
            role_items = [(r, spec["key_env"]) for r, spec in ai_gateway.ROLE_MODEL_SPEC.items()]
        except Exception:
            role_items = [(s.lower(), f"NVIDIA_NIM_API_KEY_{s}") for s in _KNOWN_ROLE_SUFFIXES]

        known_role_keys = {k for _, k in role_items}
        for extra_role in sorted(self._extra_roles):
            env_k = _role_env_name(extra_role)
            if env_k and env_k not in known_role_keys:
                role_items.append((extra_role, env_k))
                known_role_keys.add(env_k)

        for role_id, var_name in role_items:
            val = get_val(var_name)
            if not val:
                continue
            if val in seen_values:
                role_preferred[role_id] = seen_values[val]
                continue
            seen_values[val] = var_name
            desired.append((var_name, val, False, order))
            role_preferred[role_id] = var_name
            order += 1

        new_entries = {}
        for label, val, is_pool, idx in desired:
            existing = self._entries.get(label)
            if existing is not None and existing.key == val:
                existing.is_pool = is_pool
                existing.order = idx
                new_entries[label] = existing
            else:
                new_entries[label] = _KeyEntry(label=label, key=val, is_pool=is_pool, order=idx)

        self._entries = new_entries
        self._pool_labels = pool_labels
        self._role_preferred_label = role_preferred

    def sync_from_env(self, role=None):
        """Refresh configured keys from .env and environment while keeping existing key state."""
        with self._lock:
            self._sync_locked(role=role)

    def reset(self):
        """Clear all tracked key state."""
        with self._lock:
            self._entries.clear()
            self._pool_labels = []
            self._role_preferred_label = {}
            self._extra_roles.clear()
            self._seq = 0

    def _prune_requests(self, entry, now):
        q = entry.requests
        while q and (now - q[0]) >= WINDOW_SECONDS:
            q.popleft()

    def _is_healthy(self, entry, now):
        if entry.disabled_reason is not None:
            return False
        if entry.cooldown_until is not None and now < entry.cooldown_until:
            return False
        self._prune_requests(entry, now)
        return len(entry.requests) < self._rate_limit

    def _candidate_entries_locked(self, role=None):
        role_norm = (role or "").lower().strip()
        pref_label = self._role_preferred_label.get(role_norm)
        pref_entry = self._entries.get(pref_label) if pref_label else None
        pool_entries = [
            self._entries[lbl]
            for lbl in self._pool_labels
            if lbl in self._entries and lbl != pref_label
        ]
        return pref_entry, pool_entries

    def _pick_healthy_locked(self, pref_entry, pool_entries, now, exclude_labels=None):
        excluded = set(exclude_labels or ())
        if pref_entry is not None and pref_entry.label not in excluded:
            if self._is_healthy(pref_entry, now):
                return pref_entry

        healthy_pool = [
            e for e in pool_entries
            if e.label not in excluded and self._is_healthy(e, now)
        ]
        if not healthy_pool:
            return None

        return min(
            healthy_pool,
            key=lambda e: (
                -1.0 if e.last_used is None else e.last_used,
                e.last_used_seq,
                e.order,
            ),
        )

    def _record_use_locked(self, entry, now):
        self._seq += 1
        entry.last_used = now
        entry.last_used_seq = self._seq
        entry.requests.append(now)

    def pool_count(self):
        """Return the number of deduplicated pool keys currently configured."""
        with self._lock:
            self._sync_locked()
            return len(self._pool_labels)

    def has_keys(self, role=None):
        """Return True if at least one key is configured for the pool or this role."""
        with self._lock:
            self._sync_locked(role=role)
            pref_entry, pool_entries = self._candidate_entries_locked(role)
            return pref_entry is not None or bool(pool_entries)

    def has_any_keys(self):
        """Return True if at least one pool or role key is configured."""
        with self._lock:
            self._sync_locked()
            return bool(self._entries)

    def configured_keys(self):
        """Return deduplicated raw key strings currently configured in pool or role env vars."""
        with self._lock:
            self._sync_locked()
            seen = set()
            out = []
            for entry in sorted(self._entries.values(), key=lambda e: e.order):
                val = (entry.key or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    out.append(val)
            return out

    def first_pool_key(self):
        """Return the first configured pool key value (for backward compat), or empty string."""
        with self._lock:
            self._sync_locked()
            if not self._pool_labels:
                return ""
            first = self._entries.get(self._pool_labels[0])
            return first.key if first else ""

    def peek_healthy_key(self, role=None):
        """Return a healthy key string without consuming quota, or fallback configured key."""
        with self._lock:
            self._sync_locked(role=role)
            now = self._now()
            pref_entry, pool_entries = self._candidate_entries_locked(role)
            chosen = self._pick_healthy_locked(pref_entry, pool_entries, now)
            if chosen is not None:
                return chosen.key
            if pref_entry is not None and pref_entry.disabled_reason is None:
                return pref_entry.key
            for e in pool_entries:
                if e.disabled_reason is None:
                    return e.key
            if pref_entry is not None:
                return pref_entry.key
            return pool_entries[0].key if pool_entries else ""

    def try_acquire_nowait(self, role=None, exclude_labels=None):
        """Immediately acquire a healthy (key, label) or return None without waiting."""
        with self._lock:
            self._sync_locked(role=role)
            now = self._now()
            pref_entry, pool_entries = self._candidate_entries_locked(role)
            chosen = self._pick_healthy_locked(pref_entry, pool_entries, now, exclude_labels=exclude_labels)
            if chosen is None:
                return None
            self._record_use_locked(chosen, now)
            return chosen.key, chosen.label

    def acquire(self, role=None, exclude_labels=None, sleep_fn=None):
        """Acquire a healthy (key, label) using role preference then LRU rotation.

        Waits up to 20s if keys are busy or cooling down, then raises AINotConnected.
        Raises AIAuthError if every eligible key has been disabled by 401/403.
        """
        start = self._now()
        slept_total = 0.0

        while True:
            with self._lock:
                self._sync_locked(role=role)
                now = self._now()
                pref_entry, pool_entries = self._candidate_entries_locked(role)
                all_candidates = ([pref_entry] if pref_entry is not None else []) + pool_entries
                if not all_candidates:
                    raise AINotConnected("No NVIDIA NIM API keys configured.")

                chosen = self._pick_healthy_locked(
                    pref_entry, pool_entries, now, exclude_labels=exclude_labels
                )
                if chosen is not None:
                    self._record_use_locked(chosen, now)
                    return chosen.key, chosen.label

                excluded = set(exclude_labels or ())
                eligible = [e for e in all_candidates if e.label not in excluded]
                if not eligible or all(e.disabled_reason is not None for e in eligible):
                    raise AIAuthError("All NVIDIA NIM API keys were rejected (HTTP 401/403).")

                ready_times = []
                for e in eligible:
                    if e.disabled_reason is not None:
                        continue
                    self._prune_requests(e, now)
                    cd_ready = e.cooldown_until if (e.cooldown_until is not None and now < e.cooldown_until) else now
                    rpm_ready = (e.requests[0] + WINDOW_SECONDS) if len(e.requests) >= self._rate_limit else now
                    ready_times.append(max(cd_ready, rpm_ready))

                soonest = min(ready_times) if ready_times else (now + self._acquire_timeout_s)
                elapsed = max(now - start, slept_total)
                remaining = self._acquire_timeout_s - elapsed
                if remaining <= 0:
                    raise AINotConnected(
                        "All NVIDIA keys are busy or rate-limited right now. Try again in a minute."
                    )

                wait_s = min(max(0.001, soonest - now), remaining)

            self._do_sleep(wait_s, sleep_fn=sleep_fn)
            slept_total += wait_s

    def report(self, label, outcome, retry_after=None):
        """Record request outcome ('ok', 'rate_limited', 'auth_failed', 'error') for a key label."""
        with self._lock:
            entry = self._entries.get(label)
            if entry is None:
                return
            now = self._now()
            if outcome == "ok":
                entry.ok += 1
            elif outcome == "rate_limited":
                entry.errors += 1
                cooldown = DEFAULT_COOLDOWN_SECONDS
                if retry_after is not None:
                    try:
                        parsed = float(retry_after)
                        if parsed >= 0:
                            cooldown = parsed
                    except (TypeError, ValueError):
                        pass
                until = now + cooldown
                entry.cooldown_until = max(entry.cooldown_until or 0.0, until)
            elif outcome == "auth_failed":
                entry.errors += 1
                entry.disabled_reason = "key rejected (HTTP 401/403)"
            elif outcome == "error":
                entry.errors += 1
            else:
                raise ValueError(f"Unknown key_pool outcome: {outcome!r}")

    def status(self):
        """Return safe per-key status dicts without any raw secret values."""
        with self._lock:
            self._sync_locked()
            now = self._now()
            items = []
            for entry in self._entries.values():
                self._prune_requests(entry, now)
                is_cooling = entry.cooldown_until is not None and now < entry.cooldown_until
                cooling_s = round(max(0.0, entry.cooldown_until - now), 1) if is_cooling else 0.0
                reqs = len(entry.requests)
                healthy = (
                    entry.disabled_reason is None
                    and not is_cooling
                    and reqs < self._rate_limit
                )
                items.append({
                    "label": entry.label,
                    "masked": entry.masked,
                    "healthy": healthy,
                    "cooling_down_s": cooling_s,
                    "disabled_reason": entry.disabled_reason,
                    "requests_last_min": reqs,
                    "ok": entry.ok,
                    "errors": entry.errors,
                })
            return items


_default_pool = KeyPool()


def get_pool():
    """Return the shared process-wide KeyPool instance."""
    return _default_pool


def acquire(role=None):
    """Acquire a healthy (key, label) from the shared pool."""
    return _default_pool.acquire(role=role)


def report(label, outcome, retry_after=None):
    """Report a request outcome for a key label to the shared pool."""
    return _default_pool.report(label, outcome, retry_after=retry_after)


def status():
    """Return safe status entries for all configured keys in the shared pool."""
    return _default_pool.status()


def reset():
    """Reset the shared pool state."""
    _default_pool.reset()
