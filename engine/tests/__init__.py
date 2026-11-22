"""Engine test package: isolates every test run from the developer's real data, keys and network.

The engine's default data folder is the repo itself, where a developer's real database, sign-in and .env live.
Tests used to reach them (migrating the real database, refreshing the real sign-in), so before any engine module
is imported this package:
- points MAGNM_HOME at a throwaway folder and drops provider keys and proxies from the environment;
- turns the scheduler and cloud sync off (child processes inherit all of this);
- refuses, in this process, internet connections and any access to the real .env, database and sign-in file.

Always run the suite as a package: python3 -m unittest discover -s engine/tests -t .
"""
import atexit
import os
import re
import shutil
import socket
import sys
import tempfile

_REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
_HOME = tempfile.mkdtemp(prefix="magnm-tests-")
atexit.register(shutil.rmtree, _HOME, True)

_PRIVATE_ENV_RE = re.compile(
    r"(NVIDIA_|SUPABASE_|MAGNM_|OPENAI_|ANTHROPIC_|GEMINI_|GROQ_).*|.*(_API_KEY|_TOKEN)|(HTTPS?|ALL)_PROXY",
    re.IGNORECASE,
)
for _name in list(os.environ):
    if _PRIVATE_ENV_RE.fullmatch(_name):
        del os.environ[_name]

os.environ["MAGNM_HOME"] = _HOME
os.environ["MAGNM_DISABLE_SCHEDULER"] = "1"
os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "1"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"

# Seed the throwaway folder exactly as the app seeds a new install (config, agents, playbooks).
sys.path.insert(0, os.path.join(_REPO, "engine"))
import paths  # noqa: E402  (must be imported after MAGNM_HOME is set)

paths.ensure_data_root()

_PRIVATE_FILES = frozenset(
    os.path.join(_REPO, *parts)
    for parts in (
        (".env",),
        ("state", "magnm.db"),
        ("state", "team_session.json"),
    )
)
_LOCAL_HOSTS = frozenset(("", "127.0.0.1", "localhost", "::1", "0.0.0.0", "::"))


def _guard(event, args):
    if event in ("open", "sqlite3.connect"):
        target = args[0]
        if isinstance(target, bytes):
            target = os.fsdecode(target)
        if isinstance(target, str):
            path = os.path.abspath(target.removeprefix("file:").split("?", 1)[0])
            if path in _PRIVATE_FILES:
                raise PermissionError(f"Engine tests must not touch real user data: {path}")
    elif event in ("socket.getaddrinfo", "socket.connect"):
        host = args[0] if event == "socket.getaddrinfo" else args[1]
        if isinstance(host, tuple):
            host = host[0]
        elif event == "socket.connect":
            return  # Unix socket
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host is not None and str(host).lower() not in _LOCAL_HOSTS:
            raise socket.gaierror(socket.EAI_NONAME, f"Engine tests have no internet access: {host}")


sys.addaudithook(_guard)
