"""Hash de senha (scrypt, stdlib — zero dependência extra).

Mesmo esquema usado no login por conta (web.portal). Fica aqui num módulo leve
(sem FastAPI) pra o login de membro (contas.equipe) reusar sem puxar o portal.
Formato guardado: "scrypt$<sal_hex>$<hash_hex>".
"""
import hashlib
import secrets


def hash_senha(senha: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
    return f"scrypt${sal}${h.hex()}"


def verificar_senha(senha: str, guardado: str | None) -> bool:
    try:
        _alg, sal, hex_h = (guardado or "").split("$")
        h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
        return secrets.compare_digest(h.hex(), hex_h)
    except Exception:  # noqa: BLE001
        return False
