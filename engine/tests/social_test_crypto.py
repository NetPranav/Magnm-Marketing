"""For environments without the optional cryptography package: skip crypto tests, stub it where it is only setup."""

import hashlib
import hmac
import importlib.util
import unittest
from unittest.mock import patch

HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
requires_cryptography = unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography not installed")


class _SetupOnlyAEAD:
    """Test-only AES-GCM stand-in: hides and authenticates, so canary checks still mean something."""

    def __init__(self, key):
        self.key = key

    def _xor(self, nonce, data):
        stream = hashlib.shake_256(self.key + nonce).digest(len(data))
        return bytes(a ^ b for a, b in zip(data, stream))

    def _tag(self, nonce, body, aad):
        return hmac.new(self.key, nonce + (aad or b"") + body, hashlib.sha256).digest()[:16]

    def encrypt(self, nonce, data, aad):
        body = self._xor(nonce, data)
        return body + self._tag(nonce, body, aad)

    def decrypt(self, nonce, blob, aad):
        body, tag = blob[:-16], blob[-16:]
        if not hmac.compare_digest(tag, self._tag(nonce, body, aad)):
            raise ValueError("authentication failed")
        return self._xor(nonce, body)


def stub_vault_if_missing(test, vault_module):
    """Swap in the stand-in for this test, only when cryptography is absent."""
    if not HAVE_CRYPTOGRAPHY:
        patcher = patch.object(vault_module, "_import_aesgcm", return_value=_SetupOnlyAEAD)
        patcher.start()
        test.addCleanup(patcher.stop)
