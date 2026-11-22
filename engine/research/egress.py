"""Outbound connections for research: only to public addresses, checked on the address actually connected to.

Checking a URL beforehand is not enough: DNS can answer differently a moment later, and Chromium follows redirects
and opens WebSockets without asking Playwright's route handler. So urllib connects through create_public_connection,
and the research browser sends all traffic through GuardProxy.
"""

import http.client
import ipaddress
import selectors
import socket
import socketserver
import threading
import urllib.parse
import urllib.request

from research import fetch

CONNECT_TIMEOUT_S = 15
IDLE_TIMEOUT_S = 30
MAX_HEADER_BYTES = 64 * 1024


def create_public_connection(address, timeout=CONNECT_TIMEOUT_S, source_address=None):
    """Resolve once, refuse if any answer is non-public, and connect to exactly one of those answers."""
    host, port = address
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # A name: judge what it resolves to.
    else:
        if fetch.is_disallowed_address(host):
            raise fetch.SSRFError(f"Blocked non-public IP address {host}")
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = [str(info[4][0]).split("%", 1)[0] for info in infos]
    if not ips:
        raise fetch.FetchError(f"DNS resolution returned no addresses for {host}")
    for ip in ips:
        if fetch.is_disallowed_address(ip):
            raise fetch.SSRFError(f"Blocked non-public IP address {ip} for host {host}")
    last_error = None
    for ip in ips:
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError as exc:
            last_error = exc
    raise last_error


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_public_connection


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_public_connection


class PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req)


def _splice(client, upstream):
    with selectors.DefaultSelector() as sel:
        sel.register(client, selectors.EVENT_READ, upstream)
        sel.register(upstream, selectors.EVENT_READ, client)
        while True:
            ready = sel.select(IDLE_TIMEOUT_S)
            if not ready:
                return
            for key, _ in ready:
                data = key.fileobj.recv(65536)
                if not data:
                    return
                key.data.sendall(data)


class _ProxyHandler(socketserver.BaseRequestHandler):
    def _refuse(self, status):
        self.request.sendall(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))

    def handle(self):
        try:
            self.request.settimeout(CONNECT_TIMEOUT_S)
            self._proxy()
        except (OSError, ValueError):
            pass  # Client or site went away, or sent something that isn't HTTP.

    def _proxy(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.request.recv(65536)
            if not chunk or len(buf) > MAX_HEADER_BYTES:
                return
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        tunnel = method.upper() == "CONNECT"
        url = urllib.parse.urlsplit("//" + target if tunnel else target)
        if not url.hostname or (not tunnel and url.scheme.lower() != "http"):
            return self._refuse("400 Bad Request")
        try:
            upstream = create_public_connection((url.hostname, url.port or (443 if tunnel else 80)))
        except fetch.SSRFError:
            return self._refuse("403 Forbidden")
        except (fetch.FetchError, OSError):
            return self._refuse("502 Bad Gateway")
        with upstream:
            if tunnel:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                path = urllib.parse.urlunsplit(("", "", url.path or "/", url.query, ""))
                upgrade = any(line.lower().startswith("upgrade:") for line in lines[1:])
                dropped = ("proxy-",) if upgrade else ("proxy-", "connection:", "keep-alive:")
                kept = [line for line in lines[1:] if not line.lower().startswith(dropped)]
                if not upgrade:
                    kept.append("Connection: close")
                upstream.sendall("\r\n".join([f"{method} {path} {version}", *kept, "", ""]).encode("latin-1"))
            if rest:
                upstream.sendall(rest)
            _splice(self.request, upstream)


class GuardProxy:
    """A loopback proxy, alive for one browser context, that can only reach public addresses."""

    def __enter__(self):
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ProxyHandler, bind_and_activate=False)
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self._server.server_bind()
        self._server.server_activate()
        self._thread = threading.Thread(target=self._server.serve_forever, name="magnm-research-proxy", daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
