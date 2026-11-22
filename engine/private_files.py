"""Files agents must never read or send to a model, whatever folder they were asked to look in.

Agents read untrusted web pages and repos, so treat every path they pass as chosen by an attacker.
"""

import fnmatch
import os

import paths

_PRIVATE_DIRS = frozenset((".git", ".ssh", ".aws", ".gnupg", ".kube", ".docker"))
_PRIVATE_NAMES = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", "credentials*", "service-account*.json", "secrets.*", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "team_session*", "engine_endpoint.json",
)
_PUBLIC_EXAMPLES = frozenset((".env.example", ".env.sample", ".env.template"))


def is_private(real_path, root):
    """Return True if real_path (already resolved, inside root) is secret-like or is the engine's private state."""
    parts = os.path.relpath(real_path, os.path.realpath(root)).lower().replace("\\", "/").split("/")
    if any(part in _PRIVATE_DIRS for part in parts):
        return True
    state_dir = os.path.realpath(paths.data("state"))
    if os.path.normcase(real_path + os.sep).startswith(os.path.normcase(state_dir + os.sep)):
        return True
    name = parts[-1]
    if name in _PUBLIC_EXAMPLES:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in _PRIVATE_NAMES)


def is_image(real_path):
    """Return True if the file's first bytes are a PNG, JPEG, WebP or GIF."""
    try:
        with open(real_path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return (
        head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )
