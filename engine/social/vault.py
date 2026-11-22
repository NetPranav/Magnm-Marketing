"""Encrypted per-platform Playwright storage_state vault (AES-256-GCM + OS keychain)."""

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading

_SOCIAL_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_SOCIAL_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths
from social import SocialError, normalize_platform

_BLOB_MAGIC = b"MGMSOC1\x00"
_NONCE_LEN = 12
_KEY_LEN = 32


class VaultError(SocialError):
    """Error raised when loading, saving, or removing encrypted social sessions."""


class KeychainUnavailableError(VaultError):
    """Raised when no working OS keychain is available (plaintext fallback forbidden)."""


def _import_aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as exc:
        raise VaultError(
            "Social session encryption needs the 'cryptography' package (cryptography==50.0.2).",
            status=503,
            code="missing_cryptography",
        ) from exc


def _import_keyring():
    try:
        import keyring
        return keyring
    except ImportError as exc:
        raise KeychainUnavailableError(
            "Social session storage needs the 'keyring' package (keyring==25.7.0).",
            status=503,
            code="missing_keyring",
        ) from exc


_ALLOWED_KEYRING_BACKENDS = frozenset({
    ("keyring.backends.secretservice", "Keyring"),
    ("keyring.backends.libsecret", "Keyring"),
    ("keyring.backends.kwallet", "DBusKeyring"),
    ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    ("keyring.backends.macos", "Keyring"),
    ("keyring.backends.windows", "WinVaultKeyring"),
})


class KeyringKeyStore:
    """OS keychain adapter backed by python-keyring; allowlists real OS backends only."""

    def _check_backend(self, kr, depth=0):
        if kr is None or depth > 4:
            raise KeychainUnavailableError(
                "No OS keychain is available; refusing to store social session.",
                status=503,
                code="no_keychain",
            )

        try:
            if float(getattr(kr, "priority", 0)) <= 0:
                raise KeychainUnavailableError(
                    "OS keychain backend is non-persistent; refusing to save.",
                    status=503,
                    code="no_keychain",
                )
        except (TypeError, ValueError) as exc:
            raise KeychainUnavailableError(
                "OS keychain backend priority is invalid; refusing to save.",
                status=503,
                code="no_keychain",
            ) from exc

        cls_mod = (type(kr).__module__ or "").strip().lower()
        cls_name = (type(kr).__name__ or "").strip()

        if cls_mod == "keyring.backends.chainer" or cls_name == "ChainerBackend":
            chained = getattr(kr, "backends", None)
            if not chained:
                raise KeychainUnavailableError(
                    "Keyring chainer has no active OS keychain backend; refusing to save.",
                    status=503,
                    code="no_keychain",
                )
            # ChainerBackend.set_password writes to the first viable backend in kr.backends.
            self._check_backend(chained[0], depth=depth + 1)
            return kr

        if (cls_mod, cls_name) not in _ALLOWED_KEYRING_BACKENDS:
            raise KeychainUnavailableError(
                "Keyring backend is not an allowed OS keychain; refusing to save.",
                status=503,
                code="no_keychain",
            )
        return kr

    def _usable_backend(self, keyring_mod):
        try:
            kr = keyring_mod.get_keyring()
        except Exception as exc:
            raise KeychainUnavailableError(
                "No OS keychain is available; refusing to store social session.",
                status=503,
                code="no_keychain",
            ) from exc
        return self._check_backend(kr)

    def get_key(self, service, account):
        keyring_mod = _import_keyring()
        self._usable_backend(keyring_mod)
        try:
            encoded = keyring_mod.get_password(service, account)
        except Exception as exc:
            raise KeychainUnavailableError(
                "Failed to read key from OS keychain.",
                status=503,
                code="keychain_read_error",
            ) from exc
        if not encoded:
            return None
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except Exception as exc:
            raise VaultError(
                "Stored keychain key is malformed.",
                status=409,
                code="invalid_key",
            ) from exc
        if len(raw) != _KEY_LEN:
            raise VaultError(
                "Stored keychain key has unexpected length.",
                status=409,
                code="invalid_key",
            )
        return raw

    def set_key(self, service, account, key_bytes):
        if not isinstance(key_bytes, (bytes, bytearray)) or len(key_bytes) != _KEY_LEN:
            raise ValueError("Encryption key must be 32 bytes")
        keyring_mod = _import_keyring()
        self._usable_backend(keyring_mod)
        encoded = base64.b64encode(bytes(key_bytes)).decode("ascii")
        try:
            keyring_mod.set_password(service, account, encoded)
            read_back = keyring_mod.get_password(service, account)
        except Exception as exc:
            raise KeychainUnavailableError(
                "No OS keychain is available; refusing to save social session.",
                status=503,
                code="no_keychain",
            ) from exc
        if read_back != encoded:
            raise KeychainUnavailableError(
                "OS keychain did not persist the encryption key; refusing to save.",
                status=503,
                code="no_keychain",
            )

    def delete_key(self, service, account):
        try:
            keyring_mod = _import_keyring()
            self._usable_backend(keyring_mod)
        except KeychainUnavailableError:
            return False
        try:
            keyring_mod.delete_password(service, account)
            return True
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "delete" in name or "notfound" in name:
                return False
            return False


class InMemoryKeyStore:
    """In-memory key store for isolated tests (never touches OS keychain or disk)."""

    def __init__(self, available=True):
        self._lock = threading.Lock()
        self._keys = {}
        self.available = bool(available)

    def get_key(self, service, account):
        if not self.available:
            raise KeychainUnavailableError(
                "No OS keychain is available; refusing to save social session.",
                status=503,
                code="no_keychain",
            )
        with self._lock:
            return self._keys.get((service, account))

    def set_key(self, service, account, key_bytes):
        if not self.available:
            raise KeychainUnavailableError(
                "No OS keychain is available; refusing to save social session.",
                status=503,
                code="no_keychain",
            )
        if not isinstance(key_bytes, (bytes, bytearray)) or len(key_bytes) != _KEY_LEN:
            raise ValueError("Encryption key must be 32 bytes")
        with self._lock:
            self._keys[(service, account)] = bytes(key_bytes)

    def delete_key(self, service, account):
        if not self.available:
            return False
        with self._lock:
            return self._keys.pop((service, account), None) is not None


class SocialVault:
    """AES-256-GCM encrypted Playwright storage_state store bound to platform and data root."""

    def __init__(self, data_root=None, key_store=None):
        self.data_root = os.path.realpath(data_root or paths.DATA_ROOT)
        self.data_root_id = hashlib.sha256(self.data_root.encode("utf-8")).hexdigest()[:32]
        self._key_store = key_store
        self._lock = threading.Lock()
        self._generations = {}

    def get_generation(self, platform):
        """Return the current vault generation counter for platform."""
        plat = normalize_platform(platform)
        with self._lock:
            return self._generations.get(plat, 0)

    def bump_generation(self, platform):
        """Increment and return the vault generation counter so stale saves are refused."""
        plat = normalize_platform(platform)
        with self._lock:
            nxt = self._generations.get(plat, 0) + 1
            self._generations[plat] = nxt
            return nxt

    def _get_key_store(self):
        if self._key_store is not None:
            return self._key_store
        return KeyringKeyStore()

    @property
    def vault_dir(self):
        return os.path.join(self.data_root, "state", "social")

    def blob_path(self, platform):
        plat = normalize_platform(platform)
        return os.path.join(self.vault_dir, f"{plat}.bin")

    def _service_name(self):
        return f"magnm-studio-social-{self.data_root_id}"

    def _aad(self, platform):
        return f"magnm-social-v1:{platform}:{self.data_root_id}".encode("utf-8")

    def has_session(self, platform):
        """Return True if an encrypted session blob and its keychain key both exist."""
        plat = normalize_platform(platform)
        bpath = self.blob_path(plat)
        if not os.path.isfile(bpath):
            return False
        try:
            key = self._get_key_store().get_key(self._service_name(), plat)
            return key is not None and len(key) == _KEY_LEN
        except VaultError:
            return False

    def save_state(self, platform, state_dict, *, expected_generation=None):
        """Encrypt and persist Playwright storage_state dict (with IndexedDB) under state/social/<platform>.bin."""
        plat = normalize_platform(platform)
        if not isinstance(state_dict, dict):
            raise VaultError("storage_state must be a JSON object.", status=400, code="invalid_state")

        AESGCM = _import_aesgcm()
        key_store = self._get_key_store()
        service = self._service_name()

        with self._lock:
            if expected_generation is not None and self._generations.get(plat, 0) != expected_generation:
                raise VaultError(
                    "Session save refused because platform session was disabled or removed.",
                    status=409,
                    code="stale_session",
                )
            # Always verify keychain works before writing anything.
            key = key_store.get_key(service, plat)
            if key is None:
                key = os.urandom(_KEY_LEN)
                key_store.set_key(service, plat, key)

            plaintext = json.dumps(state_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            nonce = os.urandom(_NONCE_LEN)
            aad = self._aad(plat)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
            blob = _BLOB_MAGIC + nonce + ciphertext

            os.makedirs(self.vault_dir, mode=0o700, exist_ok=True)
            try:
                os.chmod(self.vault_dir, 0o700)
            except OSError:
                pass

            dest = self.blob_path(plat)
            fd, tmp_path = tempfile.mkstemp(prefix=f".{plat}_", suffix=".tmp", dir=self.vault_dir)
            try:
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass
                with os.fdopen(fd, "wb") as fh:
                    fh.write(blob)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, dest)
                try:
                    os.chmod(dest, 0o600)
                except OSError:
                    pass
            except Exception:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
                raise

    def load_state(self, platform):
        """Decrypt and return Playwright storage_state dict in memory; never touches disk with plaintext."""
        plat = normalize_platform(platform)
        bpath = self.blob_path(plat)
        if not os.path.isfile(bpath):
            raise VaultError(
                f"No saved session for {plat}. Sign in first.",
                status=404,
                code="no_session",
            )

        AESGCM = _import_aesgcm()
        key_store = self._get_key_store()
        key = key_store.get_key(self._service_name(), plat)
        if not key:
            raise VaultError(
                f"Encryption key for {plat} is missing from the keychain; remove and sign in again.",
                status=409,
                code="missing_key",
            )

        try:
            with open(bpath, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            raise VaultError(
                f"Could not read saved session for {plat}.",
                status=500,
                code="vault_read_error",
            ) from exc

        min_len = len(_BLOB_MAGIC) + _NONCE_LEN + 16
        if len(blob) < min_len or not blob.startswith(_BLOB_MAGIC):
            raise VaultError(
                f"Saved session for {plat} is invalid or corrupted.",
                status=409,
                code="invalid_blob",
            )

        nonce = blob[len(_BLOB_MAGIC) : len(_BLOB_MAGIC) + _NONCE_LEN]
        ciphertext = blob[len(_BLOB_MAGIC) + _NONCE_LEN :]
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, self._aad(plat))
        except Exception as exc:
            raise VaultError(
                f"Saved session for {plat} could not be decrypted (key or context mismatch).",
                status=409,
                code="decrypt_failed",
            ) from exc

        try:
            parsed = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise VaultError(
                f"Saved session for {plat} contains invalid JSON.",
                status=409,
                code="invalid_state",
            ) from exc
        if not isinstance(parsed, dict):
            raise VaultError(
                f"Saved session for {plat} is malformed.",
                status=409,
                code="invalid_state",
            )
        return parsed

    def remove(self, platform):
        """Delete both the encrypted session blob and the keychain key for platform."""
        plat = normalize_platform(platform)
        bpath = self.blob_path(plat)
        removed_blob = False
        with self._lock:
            self._generations[plat] = self._generations.get(plat, 0) + 1
            if os.path.exists(bpath):
                try:
                    os.remove(bpath)
                    removed_blob = True
                except OSError as exc:
                    raise VaultError(
                        f"Failed to remove saved session for {plat}.",
                        status=500,
                        code="remove_failed",
                    ) from exc
            removed_key = self._get_key_store().delete_key(self._service_name(), plat)
        return {"platform": plat, "removed": bool(removed_blob or removed_key)}
