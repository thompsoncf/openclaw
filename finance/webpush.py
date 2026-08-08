"""Web Push (RFC 8291 aes128gcm + VAPID RFC 8292) só com `cryptography`.

Sem dependência externa (pywebpush/http-ece quebram no build em setuptools novo).
Aqui a gente monta o payload cifrado e o header VAPID na mão — é código pequeno e
bem especificado. Validado por round-trip (o teste cifra e decifra de volta).

Uso:
    from finance import webpush
    webpush.enviar(subscription, {"title": "...", "body": "..."})

`subscription` = {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}} (o que o
navegador devolve em pushManager.subscribe). As chaves VAPID vêm do ambiente:
    VAPID_PUBLIC   (application server key, base64url do ponto não-comprimido)
    VAPID_PRIVATE  (base64url dos 32 bytes crus da chave privada)
    VAPID_SUBJECT  (mailto:... ou https://... — contato do remetente)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric import ec, utils as _asym_utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization


class PushExpirado(Exception):
    """A assinatura morreu (404/410) — quem chama deve apagá-la do banco."""


def _b64u_dec(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _b64u_enc(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def configurado() -> bool:
    return bool(os.environ.get("VAPID_PUBLIC") and os.environ.get("VAPID_PRIVATE"))


def chave_publica() -> str:
    return os.environ.get("VAPID_PUBLIC", "")


# ---------------------------------------------------------------- HKDF (RFC 5869)
def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()          # extract
    out, t, i = b"", b"", 1                                       # expand
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


# ---------------------------------------------------------------- cifragem (RFC 8291/8188)
def _cifrar(plaintext: bytes, p256dh_b64: str, auth_b64: str) -> bytes:
    ua_public = _b64u_dec(p256dh_b64)          # chave pública do navegador (65 bytes)
    auth = _b64u_dec(auth_b64)                 # auth secret (16 bytes)

    as_priv = ec.generate_private_key(ec.SECP256R1())     # chave efêmera do servidor
    as_public = as_priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_priv.exchange(ec.ECDH(), ua_key)            # segredo compartilhado (32)

    # IKM: combina o ecdh com as chaves das duas pontas (RFC 8291 §3.4)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hkdf(auth, ecdh, key_info, 32)

    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    conteudo = plaintext + b"\x02"             # delimitador de último registro (sem padding)
    ciphertext = AESGCM(cek).encrypt(nonce, conteudo, None)

    rs = 4096                                  # record size (bem maior que a mensagem)
    cabecalho = salt + struct.pack(">I", rs) + bytes([len(as_public)]) + as_public
    return cabecalho + ciphertext


# ---------------------------------------------------------------- VAPID (RFC 8292)
def _vapid_auth(endpoint: str) -> str:
    priv_raw = _b64u_dec(os.environ["VAPID_PRIVATE"])
    priv = ec.derive_private_key(int.from_bytes(priv_raw, "big"), ec.SECP256R1())
    origem = "{0.scheme}://{0.netloc}".format(urlparse(endpoint))
    sub = os.environ.get("VAPID_SUBJECT", "mailto:contato@zaq-ia.com")
    header = _b64u_enc(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64u_enc(json.dumps({"aud": origem, "exp": int(time.time()) + 12 * 3600, "sub": sub},
                                  separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    der = priv.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = _asym_utils.decode_dss_signature(der)             # DER → R||S cru (64 bytes) do JWT
    sig = _b64u_enc(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    token = f"{header}.{claims}.{sig}"
    return f"vapid t={token}, k={os.environ['VAPID_PUBLIC']}"


# ---------------------------------------------------------------- envio
def enviar(subscription: dict, dados: dict, ttl: int = 3600) -> bool:
    """Manda uma notificação. Levanta PushExpirado em 404/410 (assinatura morta).
    Devolve True se a push service aceitou (201/200/202)."""
    endpoint = subscription["endpoint"]
    keys = subscription.get("keys") or {}
    corpo = _cifrar(json.dumps(dados, separators=(",", ":")).encode(), keys["p256dh"], keys["auth"])
    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Authorization": _vapid_auth(endpoint),
    }
    r = httpx.post(endpoint, content=corpo, headers=headers, timeout=15)
    if r.status_code in (404, 410):
        raise PushExpirado(endpoint)
    return r.status_code in (200, 201, 202)
