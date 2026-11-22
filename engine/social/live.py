"""In-memory live browser control (JPEG frames, bounded input, headed reopen) for login and challenges."""

from concurrent.futures import Future
import os
import queue
import sys
import threading
import urllib.parse

_SOCIAL_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_SOCIAL_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from social import SocialError, normalize_platform
from social.policy import SocialGuardProxy

VIEWPORT = {"width": 1280, "height": 800}
JPEG_QUALITY = 70
NAV_TIMEOUT_MS = 15000
MAX_INPUT_TEXT_CHARS = 200
MAX_SCROLL_DELTA = 2000.0
COMMAND_TIMEOUT_S = 15.0

ALLOWED_KEYS = frozenset({
    "Enter",
    "Tab",
    "Backspace",
    "Delete",
    "Escape",
    "Space",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
})

ALLOWED_CLICK_BUTTONS = frozenset({"left", "right"})

CHROMIUM_SECURITY_ARGS = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
    "--deny-permission-prompts",
]

LOGIN_PATHS = {
    "instagram": "/accounts/login/",
    "x": "/i/flow/login",
    "linkedin": "/login",
}

DEFAULT_BASE_URLS = {
    "instagram": "https://www.instagram.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
}


class BrowserNotInstalledError(SocialError):
    """Raised when Playwright or Chromium is not installed."""


def _import_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as exc:
        raise BrowserNotInstalledError(
            "Social browser requires Playwright and Chromium.",
            status=503,
            code="browser_not_installed",
        ) from exc


def is_playwright_available():
    """Return True if playwright.sync_api can be imported."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def login_url_for(platform, platform_hosts=None):
    """Return the fixed login URL for platform (or injected test host)."""
    plat = normalize_platform(platform)
    base = DEFAULT_BASE_URLS[plat]
    if platform_hosts and plat in platform_hosts:
        custom = str(platform_hosts[plat].get("base_url") or "").rstrip("/")
        if custom:
            base = custom
        login_path = str(platform_hosts[plat].get("login_path") or LOGIN_PATHS[plat])
    else:
        login_path = LOGIN_PATHS[plat]
    if not login_path.startswith("/"):
        login_path = "/" + login_path
    return f"{base}{login_path}"


def validate_live_input(payload):
    """Validate and normalize a bounded live-control input event; never logs payload."""
    if not isinstance(payload, dict):
        raise SocialError("Input payload must be an object.", status=400, code="invalid_input")

    action = str(payload.get("action") or payload.get("type") or "").strip().lower()
    if action == "click":
        try:
            x = float(payload.get("x"))
            y = float(payload.get("y"))
        except (TypeError, ValueError) as exc:
            raise SocialError("click requires numeric x and y.", status=400, code="invalid_input") from exc
        if not (0.0 <= x <= VIEWPORT["width"] and 0.0 <= y <= VIEWPORT["height"]):
            raise SocialError(
                f"click coordinates must be within 0..{VIEWPORT['width']} x 0..{VIEWPORT['height']}.",
                status=400,
                code="invalid_input",
            )
        button = str(payload.get("button") or "left").strip().lower()
        if button not in ALLOWED_CLICK_BUTTONS:
            raise SocialError("click button must be 'left' or 'right'.", status=400, code="invalid_input")
        return {"action": "click", "x": x, "y": y, "button": button}

    if action == "scroll":
        try:
            dx = float(payload.get("delta_x", 0))
            dy = float(payload.get("delta_y", 0))
        except (TypeError, ValueError) as exc:
            raise SocialError("scroll requires numeric delta_x and delta_y.", status=400, code="invalid_input") from exc
        if abs(dx) > MAX_SCROLL_DELTA or abs(dy) > MAX_SCROLL_DELTA:
            raise SocialError(
                f"scroll deltas must be within [-{int(MAX_SCROLL_DELTA)}, {int(MAX_SCROLL_DELTA)}].",
                status=400,
                code="invalid_input",
            )
        return {"action": "scroll", "delta_x": dx, "delta_y": dy}

    if action == "text":
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise SocialError("text action requires a non-empty string.", status=400, code="invalid_input")
        if len(text) > MAX_INPUT_TEXT_CHARS:
            raise SocialError(
                f"text length exceeds {MAX_INPUT_TEXT_CHARS} characters.",
                status=400,
                code="invalid_input",
            )
        if "\x00" in text:
            raise SocialError("text contains invalid NUL byte.", status=400, code="invalid_input")
        return {"action": "text", "text": text}

    if action == "key":
        key = payload.get("key")
        if not isinstance(key, str):
            raise SocialError("key action requires a string key name.", status=400, code="invalid_input")
        norm_key = "Space" if key == " " else key.strip()
        if norm_key not in ALLOWED_KEYS:
            raise SocialError(
                f"Key '{norm_key}' is not in the allowed key list.",
                status=400,
                code="invalid_input",
            )
        return {"action": "key", "key": norm_key}

    raise SocialError(
        "Unsupported live input action; expected click, scroll, text, or key.",
        status=400,
        code="invalid_input",
    )


class LiveBrowserHandle:
    """Thread-safe Playwright session handle keeping all browser calls on its owner thread."""

    def __init__(self, policy, *, headless=True, initial_storage_state=None):
        self.policy = policy
        self.headless = bool(headless)
        self._initial_storage_state = initial_storage_state
        self._cmd_queue = queue.Queue()
        self._closed = threading.Event()
        self._window_closed = threading.Event()
        self._reopening = False
        self._pw_cm = None
        self._pw = None
        self._proxy = None
        self._browser = None
        self._context = None
        self._page = None
        self._owner_thread_id = None

    def open_on_current_thread(self):
        """Launch GuardProxy, Chromium, and non-persistent context on the calling thread."""
        sync_playwright = _import_sync_playwright()
        self._owner_thread_id = threading.get_ident()
        self._proxy = SocialGuardProxy(self.policy)
        self._proxy.__enter__()
        try:
            self._pw_cm = sync_playwright()
            self._pw = self._pw_cm.__enter__()
            self._launch_browser_and_context(self.headless, self._initial_storage_state)
        except Exception as exc:
            self.close_on_current_thread()
            if isinstance(exc, SocialError):
                raise
            msg = str(exc).lower()
            if "executable doesn't exist" in msg or "playwright install" in msg:
                raise BrowserNotInstalledError(
                    "Chromium browser binary is not installed.",
                    status=503,
                    code="browser_not_installed",
                ) from exc
            raise BrowserNotInstalledError(
                "Failed to launch Chromium browser.",
                status=503,
                code="browser_launch_failed",
            ) from exc

    def _launch_browser_and_context(self, headless, storage_state):
        proxy_cfg = {"server": self._proxy.url, "bypass": "<-loopback>"}
        args = list(CHROMIUM_SECURITY_ARGS)
        if getattr(self._proxy, "spki_hash", None):
            args.append(f"--ignore-certificate-errors-spki-list={self._proxy.spki_hash}")
        self._browser = self._pw.chromium.launch(
            headless=bool(headless),
            proxy=proxy_cfg,
            args=args,
        )
        ctx_kwargs = {
            "viewport": dict(VIEWPORT),
            "accept_downloads": False,
            "service_workers": "block",
        }
        if storage_state is not None:
            ctx_kwargs["storage_state"] = storage_state
        self._context = self._browser.new_context(**ctx_kwargs)
        self.policy.install_on_context(self._context)
        self._page = self._context.new_page()
        self.policy.attach_page_guards(self._context, self._page)

        def _on_close(*_args):
            if not self._reopening:
                self._window_closed.set()

        self._page.on("close", _on_close)
        self._context.on("close", _on_close)
        self._browser.on("disconnected", _on_close)
        self.headless = bool(headless)

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    def is_window_closed(self):
        """Return True if the browser window, page, or handle has closed."""
        if self._closed.is_set() or self._window_closed.is_set():
            return True
        if threading.get_ident() == self._owner_thread_id and not self._reopening:
            if self._page is None:
                return True
            try:
                if self._page.is_closed():
                    self._window_closed.set()
                    return True
            except Exception:
                self._window_closed.set()
                return True
        return False

    def export_storage_state(self):
        """Return Playwright storage_state (including IndexedDB) in memory without touching disk."""
        if self._context is None:
            return None
        try:
            return self._context.storage_state(indexed_db=True)
        except Exception as exc:
            raise SocialError("Failed to read browser session state.", status=500, code="storage_state_failed") from exc

    def capture_frame_on_thread(self):
        """Capture a JPEG screenshot into memory bytes (never saved or logged)."""
        if self._page is None or self.is_window_closed():
            raise SocialError("No active live browser page.", status=409, code="no_live_session")
        try:
            return self._page.screenshot(type="jpeg", quality=JPEG_QUALITY)
        except Exception as exc:
            raise SocialError("Live browser frame capture failed.", status=500, code="frame_capture_failed") from exc

    def apply_input_on_thread(self, validated):
        """Apply validated bounded input to the active Playwright page."""
        if self._page is None or self.is_window_closed():
            raise SocialError("No active live browser page.", status=409, code="no_live_session")
        action = validated["action"]
        try:
            if action == "click":
                self._page.mouse.click(validated["x"], validated["y"], button=validated["button"])
            elif action == "scroll":
                self._page.mouse.wheel(validated["delta_x"], validated["delta_y"])
            elif action == "text":
                self._page.keyboard.type(validated["text"])
            elif action == "key":
                self._page.keyboard.press(validated["key"])
        except Exception as exc:
            raise SocialError("Live browser input failed.", status=500, code="live_input_failed") from exc

    def reopen_headed_on_thread(self):
        """Reopen the current context in a visible headed Chromium window, preserving in-memory state."""
        if self._context is None or self._page is None or self.is_window_closed():
            raise SocialError("No active browser session to reopen.", status=409, code="no_live_session")
        if not self.headless:
            return {"ok": True, "headless": False}

        state = self.export_storage_state()
        current_url = self._page.url or "about:blank"
        self._reopening = True
        try:
            try:
                self._context.close()
            except Exception:
                pass
            try:
                self._browser.close()
            except Exception:
                pass
            self._context = None
            self._page = None
            self._browser = None

            try:
                self._launch_browser_and_context(False, state)
            except Exception as exc:
                self._window_closed.set()
                raise SocialError(
                    "Could not open visible browser window.",
                    status=503,
                    code="headed_launch_failed",
                ) from exc
        finally:
            self._reopening = False

        parsed = urllib.parse.urlsplit(current_url)
        if parsed.scheme in ("http", "https"):
            ok, _reason = self.policy.check_request("GET", current_url, is_navigation=True, resource_type="document")
            if ok:
                try:
                    self._page.goto(current_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except Exception:
                    pass
        return {"ok": True, "headless": False}

    def discard_pending_commands(self):
        """Cancel and drain any queued live commands so stale inputs never execute later."""
        while True:
            try:
                _fn, fut = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fut.cancel()
            except Exception:
                pass

    def close_on_current_thread(self):
        """Close context, browser, Playwright, and GuardProxy cleanly."""
        self._closed.set()
        self._window_closed.set()
        self.discard_pending_commands()
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw_cm is not None:
            try:
                self._pw_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._pw_cm = None
            self._pw = None
        if self._proxy is not None:
            try:
                self._proxy.__exit__(None, None, None)
            except Exception:
                pass
            self._proxy = None
        self._page = None

    def run_on_owner(self, fn, timeout=COMMAND_TIMEOUT_S):
        """Schedule fn(handle) onto the Playwright owner thread and wait for its result."""
        if self._closed.is_set() or self._window_closed.is_set():
            raise SocialError("Live browser session has closed.", status=409, code="session_closed")
        if threading.get_ident() == self._owner_thread_id:
            return fn(self)
        fut = Future()
        self._cmd_queue.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except TimeoutError as exc:
            fut.cancel()
            raise SocialError("Live browser operation timed out.", status=504, code="live_timeout") from exc
        except Exception:
            fut.cancel()
            raise

    def pump_commands(self, max_wait_s=0.05):
        """Process owner-thread commands and pump Playwright's sync event loop; returns False if closed."""
        if self.is_window_closed():
            self.discard_pending_commands()
            return False

        while True:
            try:
                fn, fut = self._cmd_queue.get_nowait()
                self._execute_queued(fn, fut)
            except queue.Empty:
                break

        if self.is_window_closed():
            self.discard_pending_commands()
            return False

        wait_ms = max(10, min(int(float(max_wait_s) * 1000), 250))
        if self._page is not None and not self._closed.is_set():
            try:
                self._page.wait_for_timeout(wait_ms)
            except Exception:
                self._window_closed.set()
                self.discard_pending_commands()
                return False
        else:
            return False

        if self.is_window_closed():
            self.discard_pending_commands()
            return False

        while True:
            try:
                fn, fut = self._cmd_queue.get_nowait()
                self._execute_queued(fn, fut)
            except queue.Empty:
                break
        return not self.is_window_closed()

    def _execute_queued(self, fn, fut):
        if fut.set_running_or_notify_cancel():
            try:
                res = fn(self)
            except Exception as exc:
                if isinstance(exc, SocialError):
                    fut.set_exception(exc)
                else:
                    fut.set_exception(SocialError("Live browser operation failed.", status=500, code="live_error"))
            else:
                fut.set_result(res)


class LiveControlRegistry:
    """Tracks the single active live controller (either login or waiting_user challenge)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = None  # dict: {"kind": "login"|"challenge", "platform": ..., "run_id": ..., "call": fn}

    def register(self, kind, platform, call_fn, run_id=None):
        with self._lock:
            self._active = {
                "kind": kind,
                "platform": platform,
                "run_id": run_id,
                "call": call_fn,
            }

    def unregister(self, call_fn=None):
        with self._lock:
            if self._active is None:
                return
            if call_fn is None or self._active["call"] is call_fn:
                self._active = None

    def current(self):
        with self._lock:
            return dict(self._active) if self._active else None

    def capture_frame(self):
        active = self.current()
        if active is None:
            raise SocialError(
                "Live control is only available while waiting_user or logging in.",
                status=409,
                code="live_unavailable",
            )
        return active["call"]("frame", None)

    def send_input(self, payload):
        validated = validate_live_input(payload)
        active = self.current()
        if active is None:
            raise SocialError(
                "Live control is only available while waiting_user or logging in.",
                status=409,
                code="live_unavailable",
            )
        return active["call"]("input", validated)

    def open_full_window(self):
        active = self.current()
        if active is None:
            raise SocialError(
                "Live control is only available while waiting_user or logging in.",
                status=409,
                code="live_unavailable",
            )
        return active["call"]("open_window", None)
