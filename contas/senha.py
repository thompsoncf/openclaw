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


def gerar_temporaria() -> str:
    """Senha provisória pro dono destravar alguém da equipe na hora.

    Legível pra ditar no WhatsApp: sem os pares que se confundem lidos em voz alta
    (0/O, 1/l/I) e sem símbolo, que o teclado do celular esconde. O comprimento
    compensa o alfabeto menor — 4 letras + 3 dígitos desse conjunto dão ~10^9
    combinações, e a senha vive minutos, até a pessoa entrar e trocar.

    `secrets`, nunca `random`: o módulo é o mesmo dos tokens de convite e de reset.
    """
    alfa, num = "abcdefghjkmnpqrstuvwxyz", "23456789"
    return ("Zaq"
            + "".join(secrets.choice(alfa) for _ in range(4))
            + "".join(secrets.choice(num) for _ in range(3)))


def verificar_senha(senha: str, guardado: str | None) -> bool:
    try:
        _alg, sal, hex_h = (guardado or "").split("$")
        h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
        return secrets.compare_digest(h.hex(), hex_h)
    except Exception:  # noqa: BLE001
        return False
