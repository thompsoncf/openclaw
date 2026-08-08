"""Web Push (finance/webpush) sem lib externa:

- a cifra aes128gcm (RFC 8291/8188) faz round-trip: o "navegador" decifra igual;
- o JWT VAPID (RFC 8292) tem assinatura ES256 válida e aud = origem do endpoint.

Prova que a implementação está correta sem depender de uma push service real.
"""
import base64
import hashlib
import hmac
import json
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from finance import webpush as wp

_PUB = "BFgivjimOMKrfAVR_oVj60h8I7xzuAKbzZCNniieGzJBQBpEciX1cbfc4X9YPogjaSOEnxewLX8kluH_m12H2BE"
_PRIV = "epBmEoYbA6hoBQWXCvbsQhEy2QA83UalUQAvcIsK0GQ"


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64ud(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf(salt, ikm, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def test_cifra_faz_roundtrip():
    # "navegador": par de chaves + auth secret
    ua_priv = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua_priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    msg = {"title": "🔥 Novo lead", "body": "toque pra atender", "url": "/cockpit"}

    body = wp._cifrar(json.dumps(msg).encode(), _b64u(ua_pub), _b64u(auth))

    salt = body[:16]
    idlen = body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    as_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub)
    ecdh = ua_priv.exchange(ec.ECDH(), as_key)
    ikm = _hkdf(auth, ecdh, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ct, None).rstrip(b"\x02")
    assert json.loads(plain) == msg


def test_vapid_jwt_valido(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC", _PUB)
    monkeypatch.setenv("VAPID_PRIVATE", _PRIV)
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:contato@zaq-ia.com")
    header = wp._vapid_auth("https://fcm.googleapis.com/fcm/send/abc")
    token = header.split("t=")[1].split(", k=")[0]
    h, c, s = token.split(".")
    claims = json.loads(_b64ud(c))
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "mailto:contato@zaq-ia.com"
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64ud(_PUB))
    sig = _b64ud(s)
    der = utils.encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    pub.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))   # levanta se inválida


def test_configurado(monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE", raising=False)
    assert wp.configurado() is False
    monkeypatch.setenv("VAPID_PUBLIC", _PUB)
    monkeypatch.setenv("VAPID_PRIVATE", _PRIV)
    assert wp.configurado() is True
