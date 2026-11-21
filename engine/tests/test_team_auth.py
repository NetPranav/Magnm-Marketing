"""Tests for engine.team_auth and /api/account* + /auth/callback server routes."""

import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import paths
import server
import team_auth


class _FakeHttpResponse:
    """Minimal context-manager HTTP response stub for mocked urllib.request.urlopen."""

    def __init__(self, body_obj, status=200):
        self.status = status
        if body_obj is None:
            self._raw = b""
        elif isinstance(body_obj, (bytes, bytearray)):
            self._raw = bytes(body_obj)
        else:
            self._raw = json.dumps(body_obj).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(url, status, payload):
    raw = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(url, status, "Error", hdrs=None, fp=io.BytesIO(raw))


class TeamAuthTests(unittest.TestCase):
    """Verify PKCE, session persistence/refresh, invites, config overrides, and server routes."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        os.makedirs(os.path.join(self.tmpdir, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "state"), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir  # config/supabase.json is read from the bundled resources

        self._prev_env_url = os.environ.pop("MAGNM_SUPABASE_URL", None)
        self._prev_env_key = os.environ.pop("MAGNM_SUPABASE_PUBLISHABLE_KEY", None)
        self._prev_token = server.ENGINE_TOKEN

        team_auth.cancel_login()
        team_auth._set_last_error(None)

        self._write_config("https://example.supabase.co", "sb_publishable_test_key")

    def tearDown(self):
        server.ENGINE_TOKEN = self._prev_token
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        team_auth.cancel_login()
        team_auth._set_last_error(None)

        if self._prev_env_url is None:
            os.environ.pop("MAGNM_SUPABASE_URL", None)
        else:
            os.environ["MAGNM_SUPABASE_URL"] = self._prev_env_url

        if self._prev_env_key is None:
            os.environ.pop("MAGNM_SUPABASE_PUBLISHABLE_KEY", None)
        else:
            os.environ["MAGNM_SUPABASE_PUBLISHABLE_KEY"] = self._prev_env_key

        self._tmp_obj.cleanup()

    def _write_config(self, url, publishable_key):
        cfg_path = os.path.join(self.tmpdir, "config", "supabase.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"url": url, "publishable_key": publishable_key}, f)

    def _extract_state(self, authorize_url):
        parsed = urllib.parse.urlparse(authorize_url)
        qs = urllib.parse.parse_qs(parsed.query)
        redirect_to = qs["redirect_to"][0]
        r_qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_to).query)
        return r_qs["s"][0], qs

    def test_01_pkce_challenge_matches_rfc7636_appendix_b(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        self.assertEqual(team_auth.pkce_challenge(verifier), expected)

    def test_02_config_env_override_and_unconfigured_state(self):
        os.environ["MAGNM_SUPABASE_URL"] = "https://override.supabase.co/"
        os.environ["MAGNM_SUPABASE_PUBLISHABLE_KEY"] = "sb_override_key"
        cfg = team_auth.get_config()
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["url"], "https://override.supabase.co")
        self.assertEqual(cfg["publishable_key"], "sb_override_key")

        os.environ.pop("MAGNM_SUPABASE_URL", None)
        os.environ.pop("MAGNM_SUPABASE_PUBLISHABLE_KEY", None)
        os.remove(os.path.join(self.tmpdir, "config", "supabase.json"))

        cfg_missing = team_auth.get_config()
        self.assertFalse(cfg_missing["configured"])
        acct = team_auth.current_account()
        self.assertEqual(acct["configured"], False)
        self.assertEqual(acct["signed_in"], False)
        with self.assertRaises(team_auth.TeamAuthError) as ctx:
            team_auth.start_login(5050)
        self.assertIn("not configured", ctx.exception.message.lower())

    def test_03_state_mismatch_reuse_and_expiry_rejected(self):
        auth_url = team_auth.start_login(12345)
        state, qs = self._extract_state(auth_url)
        self.assertEqual(qs["provider"][0], "google")
        self.assertEqual(qs["code_challenge_method"][0], "s256")
        self.assertIn("http://127.0.0.1:12345/auth/callback", qs["redirect_to"][0])

        # Mismatch does not consume the active state.
        with self.assertRaises(team_auth.TeamAuthError):
            team_auth.finish_login({"s": "wrong-state", "code": "abc"})

        # Expiry rejects and consumes.
        with patch("team_auth.time.time", return_value=time.time() + team_auth.PKCE_TTL_S + 5):
            with self.assertRaises(team_auth.TeamAuthError) as exp_ctx:
                team_auth.finish_login({"s": state, "code": "abc"})
            self.assertIn("expired", exp_ctx.exception.message.lower())

        # Reuse after consumption is rejected.
        with self.assertRaises(team_auth.TeamAuthError):
            team_auth.finish_login({"s": state, "code": "abc"})

        # Starting a new login replaces older pending logins.
        url1 = team_auth.start_login(12345)
        state1, _ = self._extract_state(url1)
        url2 = team_auth.start_login(12345)
        state2, _ = self._extract_state(url2)
        self.assertNotEqual(state1, state2)
        with self.assertRaises(team_auth.TeamAuthError):
            team_auth.finish_login({"s": state1, "code": "abc"})

    def test_04_successful_exchange_writes_session_with_0600_mode(self):
        auth_url = team_auth.start_login(8787)
        state, _ = self._extract_state(auth_url)

        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeHttpResponse({
                "access_token": "tok-access-1",
                "refresh_token": "tok-refresh-1",
                "expires_in": 3600,
                "user": {"id": "user-uuid-1", "email": "Founder@Example.com"},
            })

        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            res = team_auth.finish_login({"s": state, "code": "auth-code-xyz"})

        self.assertEqual(res["email"], "founder@example.com")
        self.assertEqual(captured["url"], "https://example.supabase.co/auth/v1/token?grant_type=pkce")
        self.assertEqual(captured["headers"].get("Apikey"), "sb_publishable_test_key")
        self.assertEqual(captured["body"]["auth_code"], "auth-code-xyz")
        self.assertEqual(captured["timeout"], team_auth.REQUEST_TIMEOUT_S)

        session_file = paths.data("state", "team_session.json")
        self.assertTrue(os.path.isfile(session_file))
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(session_file).st_mode)
            self.assertEqual(mode, 0o600)

        # Replay of same state fails (one-time use).
        with self.assertRaises(team_auth.TeamAuthError):
            team_auth.finish_login({"s": state, "code": "auth-code-xyz"})

    def test_05_refresh_only_near_expiry_and_clears_session_on_400(self):
        session_file = paths.data("state", "team_session.json")
        team_auth._save_session({
            "access_token": "fresh-token",
            "refresh_token": "refresh-1",
            "expires_at": int(time.time()) + 300,
            "user_id": "uid-1",
            "email": "a@example.com",
        })

        with patch("team_auth.urllib.request.urlopen") as mock_open:
            tok = team_auth.get_access_token()
            self.assertEqual(tok, "fresh-token")
            mock_open.assert_not_called()

        # Near expiry (within 60s): triggers refresh and updates session.
        team_auth._save_session({
            "access_token": "expiring-token",
            "refresh_token": "refresh-1",
            "expires_at": int(time.time()) + 30,
            "user_id": "uid-1",
            "email": "a@example.com",
        })

        def fake_refresh_ok(req, timeout=0):
            self.assertIn("grant_type=refresh_token", req.full_url)
            return _FakeHttpResponse({
                "access_token": "rotated-token",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
                "user": {"id": "uid-1", "email": "a@example.com"},
            })

        with patch("team_auth.urllib.request.urlopen", side_effect=fake_refresh_ok):
            tok2 = team_auth.get_access_token()
            self.assertEqual(tok2, "rotated-token")

        # Force near-expiry again and return 400 -> clears session file.
        team_auth._save_session({
            "access_token": "bad-token",
            "refresh_token": "refresh-revoked",
            "expires_at": int(time.time()) + 10,
            "user_id": "uid-1",
            "email": "a@example.com",
        })

        with patch(
            "team_auth.urllib.request.urlopen",
            side_effect=_http_error("https://example.supabase.co/auth/v1/token", 400, {"error": "invalid_grant"}),
        ):
            with self.assertRaises(team_auth.TeamAuthError):
                team_auth.get_access_token()
        self.assertFalse(os.path.exists(session_file))

    def test_06_logout_deletes_session_even_when_network_fails(self):
        session_file = paths.data("state", "team_session.json")
        team_auth._save_session({
            "access_token": "tok-1",
            "refresh_token": "ref-1",
            "expires_at": int(time.time()) + 3600,
            "user_id": "uid-1",
            "email": "a@example.com",
        })
        self.assertTrue(os.path.isfile(session_file))

        with patch(
            "team_auth.urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            team_auth.logout()

        self.assertFalse(os.path.exists(session_file))

    def test_07_not_invited_error_maps_to_friendly_message(self):
        auth_url = team_auth.start_login(8787)
        state, _ = self._extract_state(auth_url)

        with self.assertRaises(team_auth.TeamAuthError) as ctx:
            team_auth.finish_login({
                "s": state,
                "error": "server_error",
                "error_description": "Database error saving new user",
            })
        self.assertEqual(ctx.exception.message, team_auth.NOT_INVITED_MESSAGE)
        self.assertEqual(team_auth.get_last_error(), team_auth.NOT_INVITED_MESSAGE)

    def test_08_current_account_active_and_deactivated(self):
        team_auth._save_session({
            "access_token": "tok-1",
            "refresh_token": "ref-1",
            "expires_at": int(time.time()) + 3600,
            "user_id": "uid-1",
            "email": "member@example.com",
        })

        with patch(
            "team_auth.urllib.request.urlopen",
            return_value=_FakeHttpResponse([
                {
                    "id": "uid-1",
                    "email": "member@example.com",
                    "display_name": "Alex",
                    "avatar_url": "https://example.com/a.png",
                    "role": "member",
                    "active": True,
                }
            ]),
        ):
            acct = team_auth.current_account()
            self.assertTrue(acct["configured"])
            self.assertTrue(acct["signed_in"])
            self.assertFalse(acct["deactivated"])
            self.assertEqual(acct["user"]["role"], "member")

        # Empty profile row -> deactivated.
        with patch("team_auth.urllib.request.urlopen", return_value=_FakeHttpResponse([])):
            deact = team_auth.current_account()
            self.assertTrue(deact["signed_in"])
            self.assertTrue(deact["deactivated"])

    def test_09_invite_validation_and_admin_only_mapping(self):
        team_auth._save_session({
            "access_token": "tok-1",
            "refresh_token": "ref-1",
            "expires_at": int(time.time()) + 3600,
            "user_id": "uid-1",
            "email": "admin@example.com",
        })

        for bad_email in ("", "not-an-email", "a@b", "user name@example.com"):
            with self.assertRaises(team_auth.TeamAuthError):
                team_auth.create_invite(bad_email, "member")

        # Normalizes email to lowercase and creates invite.
        seen_body = {}

        def fake_create(req, timeout=0):
            seen_body.update(json.loads(req.data.decode("utf-8")))
            return _FakeHttpResponse([{
                "email": "new.user@example.com",
                "role": "member",
                "created_at": "2026-11-21T00:00:00Z",
                "accepted_at": None,
                "revoked_at": None,
            }])

        with patch("team_auth.urllib.request.urlopen", side_effect=fake_create):
            created = team_auth.create_invite("  New.User@Example.COM  ", "member")
            self.assertEqual(seen_body, {"email": "new.user@example.com", "role": "member"})
            self.assertEqual(created["email"], "new.user@example.com")

        # 403 / empty PATCH maps to admin-only message.
        with patch(
            "team_auth.urllib.request.urlopen",
            side_effect=_http_error("https://example.supabase.co/rest/v1/invites", 403, {"message": "new row violates rls"}),
        ):
            with self.assertRaises(team_auth.TeamAuthError) as ctx:
                team_auth.create_invite("x@example.com", "member")
            self.assertEqual(ctx.exception.message, team_auth.ADMIN_ONLY_INVITES_MESSAGE)

        with patch("team_auth.urllib.request.urlopen", return_value=_FakeHttpResponse([])):
            with self.assertRaises(team_auth.TeamAuthError) as ctx2:
                team_auth.revoke_invite("x@example.com")
            self.assertEqual(ctx2.exception.message, team_auth.ADMIN_ONLY_INVITES_MESSAGE)

    def test_10_server_auth_token_and_callback_html_escaping(self):
        server.ENGINE_TOKEN = "secret-engine-token"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"

            # /api/account without token returns 401.
            with self.assertRaises(urllib.error.HTTPError) as unauth_ctx:
                urllib.request.urlopen(f"{base}/api/account", timeout=5)
            self.assertEqual(unauth_ctx.exception.code, 401)
            unauth_ctx.exception.close()

            # /api/account with token returns 200.
            req = urllib.request.Request(
                f"{base}/api/account",
                headers={"X-Magnm-Token": "secret-engine-token"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["configured"])
                self.assertFalse(data["signed_in"])

            # /auth/callback works WITHOUT engine token and escapes <script> in error text.
            with patch(
                "team_auth.finish_login",
                side_effect=team_auth.TeamAuthError("<script>alert('xss')</script>", status=400),
            ):
                with self.assertRaises(urllib.error.HTTPError) as cb_ctx:
                    urllib.request.urlopen(f"{base}/auth/callback?s=abc&code=123", timeout=5)
                err_resp = cb_ctx.exception
                self.assertEqual(err_resp.code, 400)
                self.assertEqual(err_resp.headers.get("Cache-Control"), "no-store")
                self.assertEqual(err_resp.headers.get("Referrer-Policy"), "no-referrer")
                html_body = err_resp.read().decode("utf-8")
                err_resp.close()
                self.assertIn("Sign-in failed", html_body)
                self.assertNotIn("<script>alert('xss')</script>", html_body)
                self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html_body)

            # /api/account/login/start uses real server port and respects open_browser.
            start_req = urllib.request.Request(
                f"{base}/api/account/login/start",
                data=json.dumps({"open_browser": True}).encode("utf-8"),
                headers={
                    "X-Magnm-Token": "secret-engine-token",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with patch("server.webbrowser.open") as mock_open:
                with urllib.request.urlopen(start_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    start_data = json.loads(resp.read().decode("utf-8"))
                    self.assertIn(f"127.0.0.1%3A{port}%2Fauth%2Fcallback", start_data["authorize_url"])
                    mock_open.assert_called_once_with(start_data["authorize_url"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
