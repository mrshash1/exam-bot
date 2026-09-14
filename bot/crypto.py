# -*- coding: utf-8 -*-
"""
Token-derived symmetric encryption for private data stored in the public repo.

The bot token never appears in the repository (it lives only in GitHub Actions
secrets), so files encrypted with a token-derived key are unreadable to anyone
who only has the public repo.  We use a SHA-256 based stream cipher plus an
HMAC integrity tag — plenty strong for keeping answer keys / results away from
students, without external crypto dependencies.
"""
import base64
import hashlib
import hmac
import json
import os

BLOCK = 32


def _key(secret: str, code: str) -> bytes:
    return hashlib.sha256((secret + "|" + code).encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_json(obj, secret: str, code: str = "") -> dict:
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(8)
    key = _key(secret, code)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(key, nonce, len(data))))
    mac = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:10]
    return {
        "n": base64.b64encode(nonce).decode(),
        "d": base64.b64encode(ct).decode(),
        "m": mac.hex(),
    }


def decrypt_json(box: dict, secret: str, code: str = ""):
    try:
        nonce = base64.b64decode(box["n"])
        ct = base64.b64decode(box["d"])
        key = _key(secret, code)
        mac = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:10]
        if not hmac.compare_digest(mac, bytes.fromhex(box.get("m", ""))):
            return None
        data = bytes(a ^ b for a, b in zip(ct, _keystream(key, nonce, len(ct))))
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


def short_sig(payload: str) -> str:
    """8-hex-char signature used to detect duplicate submissions."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
