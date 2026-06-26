from __future__ import annotations

import base64
import os
from pathlib import Path
from client.core.config import SCREENSHOT_ENCRYPTION_KEY

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEY_DIR  = Path("storage") / "keys"
KEY_FILE = KEY_DIR / "device.key"

AES_KEY_LENGTH = 32
NONCE_LENGTH   = 12


def _load_or_create_key() -> bytes:
    env_key = SCREENSHOT_ENCRYPTION_KEY

    if env_key:
        key = base64.b64decode(env_key)

        if len(key) != AES_KEY_LENGTH:
            raise RuntimeError(
        f"Encryption key must be {AES_KEY_LENGTH} bytes"
    )
        return key

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        raw = KEY_FILE.read_bytes()
        if len(raw) == AES_KEY_LENGTH:
            return raw
        KEY_FILE.unlink()
    key = os.urandom(AES_KEY_LENGTH)
    KEY_FILE.write_bytes(key)
    return key

def _get_aesgcm() -> AESGCM:
    return AESGCM(_load_or_create_key())


class CryptoEngine:

    @staticmethod
    def encrypt_bytes(plaintext: bytes) -> bytes:
        aesgcm = _get_aesgcm()
        nonce  = os.urandom(NONCE_LENGTH)
        ct     = aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ct

    @staticmethod
    def decrypt_bytes(blob: bytes) -> bytes:
        if len(blob) <= NONCE_LENGTH:
            raise ValueError(f"Blob too short: {len(blob)} bytes")
        nonce = blob[:NONCE_LENGTH]
        ct    = blob[NONCE_LENGTH:]
        try:
            return _get_aesgcm().decrypt(nonce, ct, None)
        except Exception as exc:
            raise ValueError(f"AES-GCM decryption failed: {exc}") from exc

    @staticmethod
    def save_encrypted(plaintext: bytes, dest_path: str | Path) -> Path:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(CryptoEngine.encrypt_bytes(plaintext))
        return dest

    @staticmethod
    def load_decrypted(src_path: str | Path) -> bytes:
        return CryptoEngine.decrypt_bytes(Path(src_path).read_bytes())

    @staticmethod
    def encrypt_file(plain_path: str | Path) -> Path:
        plain_path = Path(plain_path)
        enc_path   = plain_path.with_suffix(".bin")
        CryptoEngine.save_encrypted(plain_path.read_bytes(), enc_path)
        plain_path.unlink(missing_ok=True)
        return enc_path

    @staticmethod
    def encrypt_token(plaintext_token: str) -> str:
        blob = CryptoEngine.encrypt_bytes(plaintext_token.encode("utf-8"))
        return base64.b64encode(blob).decode("ascii")

    @staticmethod
    def decrypt_token(encrypted_b64: str) -> str:
        blob = base64.b64decode(encrypted_b64.encode("ascii"))
        return CryptoEngine.decrypt_bytes(blob).decode("utf-8")
