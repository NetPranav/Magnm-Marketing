"""Encrypted per-platform posting token vault (AES-256-GCM under state/posting/ + OS keychain)."""

import hashlib
import json
import os
import sys
import tempfile
import threading

_POSTING_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_POSTING_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths
from posting import PostingError, normalize_platform
from social import vault as social_vault

_BLOB_MAGIC = b"MGMPST1\x00"
_NONCE_LEN = 12
_KEY_LEN = 32

KeyringKeyStore = social_vault.KeyringKeyStore
InMemoryKeyStore = social_vault.InMemoryKeyStore


class PostingVaultError(PostingError):
    """Error raised when reading, writing, or deleting encrypted posting tokens."""


def _import_aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as exc:
        raise PostingVaultError(
            "Posting token encryption needs the 'cryptography' package (cryptography==50.0.2).",
            status=503,
            code="missing_cryptography",
        ) from exc


class PostingVault:
    """AES-256-GCM token store under state/posting/<platform>.bin with its own keychain service and AAD."""

    def __init__(self, data_root=None, key_store=None):
        self.data_root = os.path.realpath(data_root or paths.DATA_ROOT)
        self.data_root_id = hashlib.sha256(self.data_root.encode("utf-8")).hexdigest()[:32]
        self._key_store = key_store
        self._lock = threading.Lock()

    def _get_key_store(self):
        if self._key_store is not None:
            return self._key_store
        return KeyringKeyStore()

    @property
    def vault_dir(self):
        return os.path.join(self.data_root, "state", "posting")

    def blob_path(self, platform):
        plat = normalize_platform(platform)
        return os.path.join(self.vault_dir, f"{plat}.bin")

    def _service_name(self):
        return f"magnm-studio-posting-{self.data_root_id}"

    def _aad(self, platform):
        return f"magnm-posting-v1:{platform}:{self.data_root_id}".encode("utf-8")

    def has_token(self, platform):
        """Return True if an encrypted token blob and its keychain key both exist."""
        plat = normalize_platform(platform)
        bpath = self.blob_path(plat)
        if not os.path.isfile(bpath):
            return False
        try:
            key = self._get_key_store().get_key(self._service_name(), plat)
            return key is not None and len(key) == _KEY_LEN
        except social_vault.VaultError:
            return False

    def save_token(self, platform, token_dict):
        """Encrypt and persist token_dict under state/posting/<platform>.bin with mode 0600."""
        plat = normalize_platform(platform)
        if not isinstance(token_dict, dict):
            raise PostingVaultError("Token payload must be a JSON object.", status=400, code="invalid_token")

        AESGCM = _import_aesgcm()
        key_store = self._get_key_store()
        service = self._service_name()

        with self._lock:
            try:
                key = key_store.get_key(service, plat)
                if key is None:
                    key = os.urandom(_KEY_LEN)
                    key_store.set_key(service, plat, key)
            except social_vault.VaultError as exc:
                raise PostingVaultError(exc.message, status=exc.status, code=exc.code) from exc

            plaintext = json.dumps(token_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            nonce = os.urandom(_NONCE_LEN)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, self._aad(plat))
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

    def load_token(self, platform):
        """Decrypt and return token_dict in memory; never touches disk with plaintext."""
        plat = normalize_platform(platform)
        bpath = self.blob_path(plat)
        if not os.path.isfile(bpath):
            raise PostingVaultError(
                f"No connected {plat} account. Connect it in Settings first.",
                status=404,
                code="not_connected",
            )

        AESGCM = _import_aesgcm()
        try:
            key = self._get_key_store().get_key(self._service_name(), plat)
        except social_vault.VaultError as exc:
            raise PostingVaultError(exc.message, status=exc.status, code=exc.code) from exc

        if not key:
            raise PostingVaultError(
                f"Encryption key for {plat} is missing from the keychain; reconnect in Settings.",
                status=409,
                code="missing_key",
            )

        try:
            with open(bpath, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            raise PostingVaultError(
                f"Could not read saved {plat} token.",
                status=500,
                code="vault_read_error",
            ) from exc

        min_len = len(_BLOB_MAGIC) + _NONCE_LEN + 16
        if len(blob) < min_len or not blob.startswith(_BLOB_MAGIC):
            raise PostingVaultError(
                f"Saved {plat} token is invalid or corrupted.",
                status=409,
                code="invalid_blob",
            )

        nonce = blob[len(_BLOB_MAGIC) : len(_BLOB_MAGIC) + _NONCE_LEN]
        ciphertext = blob[len(_BLOB_MAGIC) + _NONCE_LEN :]
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, self._aad(plat))
        except Exception as exc:
            raise PostingVaultError(
                f"Saved {plat} token could not be decrypted (key or context mismatch).",
                status=409,
                code="decrypt_failed",
            ) from exc

        try:
            parsed = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise PostingVaultError(
                f"Saved {plat} token contains invalid JSON.",
                status=409,
                code="invalid_token",
            ) from exc
        if not isinstance(parsed, dict):
            raise PostingVaultError(
                f"Saved {plat} token is malformed.",
                status=409,
                code="invalid_token",
            )
        return parsed

    def remove(self, platform):
        """Delete both the encrypted token blob and the keychain key for platform."""
        plat = normalize_platform(platform)
        bpath = self.blob_path(plat)
        removed_blob = False
        with self._lock:
            if os.path.exists(bpath):
                try:
                    os.remove(bpath)
                    removed_blob = True
                except OSError as exc:
                    raise PostingVaultError(
                        f"Failed to remove saved {plat} token.",
                        status=500,
                        code="remove_failed",
                    ) from exc
            removed_key = self._get_key_store().delete_key(self._service_name(), plat)
        return {"platform": plat, "removed": bool(removed_blob or removed_key)}
