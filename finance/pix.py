"""Pix copia e cola (BR Code estático) com a chave da PRÓPRIA empresa.

Pra que serve: a parcela da reforma que a etapa liberou (finance/obra_reforma.py)
vai pro cliente com o Pix pronto. Decisão do dono em 26/09/2026: o dinheiro cai
DIRETO na conta da empresa, pela chave dela — nada de passar pelo Asaas da ZAQ
(o "cobrar via Pix" dos títulos, finance/asaas.py, recebe na conta da ZAQ e
repassa na mão; pra dinheiro de cliente de obra, isso é a ZAQ segurando o que não
é dela). O preço disso: a baixa não é automática — a empresa avisa que recebeu.

O FORMATO é o do Manual de Padrões para Iniciação do Pix (BACEN): campos EMV
"id + tamanho(2 dígitos) + valor", a chave dentro do campo 26 (GUI
br.gov.bcb.pix), o valor no 54, nome e cidade do recebedor no 59/60 (ASCII, até
25 e 15 caracteres), o identificador no 62-05 e o CRC16-CCITT no 63. Qualquer
app de banco lê.

A CHAVE: e-mail, CPF, CNPJ, celular (+55...) ou aleatória (EVP). Onze dígitos
são ambíguos (CPF ou celular sem DDI): com dígito verificador de CPF válido, é
CPF; senão, celular. É o mesmo desempate que os bancos fazem na tela de cadastro.
"""
from __future__ import annotations

import re
import unicodedata

_GUI = "br.gov.bcb.pix"
_EVP = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIPOS = {"email": "E-mail", "cpf": "CPF", "cnpj": "CNPJ", "celular": "Celular",
         "aleatoria": "Chave aleatória"}


def _digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _cpf_valido(d: str) -> bool:
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def _cnpj_valido(d: str) -> bool:
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n in (12, 13):
        pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if n == 12 else [6] + [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        soma = sum(int(d[i]) * pesos[i] for i in range(n))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if dv != int(d[n]):
            return False
    return True


def normalizar_chave(chave: str) -> tuple[str, str]:
    """(tipo, chave no formato do DICT). ValueError com a frase pro usuário."""
    bruta = (chave or "").strip()
    if not bruta:
        raise ValueError("Escreva a chave Pix.")
    if "@" in bruta:
        if not _EMAIL.match(bruta):
            raise ValueError("Esse e-mail não parece uma chave Pix.")
        return "email", bruta.lower()
    if _EVP.match(bruta.lower()):
        return "aleatoria", bruta.lower()
    d = _digitos(bruta)
    if bruta.startswith("+"):
        if len(d) in (12, 13) and d.startswith("55"):
            return "celular", "+" + d
        raise ValueError("Celular como chave Pix vai com DDI e DDD: +55 99 99999-9999.")
    if len(d) == 14:
        if not _cnpj_valido(d):
            raise ValueError("Esse CNPJ não fecha (dígito verificador).")
        return "cnpj", d
    if len(d) == 11 and _cpf_valido(d):
        return "cpf", d
    if len(d) in (10, 11):
        return "celular", "+55" + d
    if len(d) in (12, 13) and d.startswith("55"):
        return "celular", "+" + d
    raise ValueError("Não reconheci a chave. Vale e-mail, CPF, CNPJ, celular ou a chave aleatória.")


def _ascii(txt: str, maximo: int) -> str:
    """Nome e cidade do recebedor: sem acento, só o que o EMV aceita, cortado."""
    t = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9 .\-]", "", t)
    return " ".join(t.split()).upper()[:maximo].strip()


def _campo(id_: str, valor: str) -> str:
    return f"{id_}{len(valor):02d}{valor}"


def crc16(payload: str) -> str:
    """CRC16-CCITT (polinômio 0x1021, início 0xFFFF), em 4 dígitos hex maiúsculos."""
    crc = 0xFFFF
    for b in payload.encode("utf-8"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def copia_e_cola(chave: str, valor_centavos: int | None, nome: str, cidade: str,
                 txid: str = "***") -> str:
    """O BR Code estático. `txid`: até 25 letras/números ('***' = sem)."""
    _tipo, k = normalizar_chave(chave)
    nome_r = _ascii(nome, 25) or "RECEBEDOR"
    cidade_r = _ascii(cidade, 15) or "BRASIL"
    tx = re.sub(r"[^A-Za-z0-9]", "", txid or "")[:25] or "***"
    p = _campo("00", "01")
    p += _campo("26", _campo("00", _GUI) + _campo("01", k))
    p += _campo("52", "0000") + _campo("53", "986")
    if valor_centavos:
        p += _campo("54", f"{int(valor_centavos) / 100:.2f}")
    p += _campo("58", "BR") + _campo("59", nome_r) + _campo("60", cidade_r)
    p += _campo("62", _campo("05", tx))
    p += "6304"
    return p + crc16(p)


def qr_svg(payload: str) -> str | None:
    """O QR do copia e cola em SVG (pra pôr direto no HTML). None sem o segno —
    a página continua com o copia e cola, que é o que mais se usa no celular."""
    try:
        import segno
    except Exception:  # noqa: BLE001
        return None
    import io
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=5, border=2,
                                        xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")


# ── a chave da empresa (migração 367) ─────────────────────────────────────
def da_conta(pool, conta_id: int) -> dict | None:
    """{'chave', 'tipo', 'rotulo_tipo', 'recebedor', 'cidade'} ou None sem chave
    (ou sem a 367). Recebedor e cidade caem pros dados da empresa."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select pix_chave, pix_tipo,
                          coalesce(nullif(pix_recebedor, ''), nullif(razao_social, ''),
                                   nullif(nome_fantasia, ''), nome, ''),
                          coalesce(nullif(pix_cidade, ''), nullif(cidade, ''), '')
                     from contas where id=%s""", (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — banco sem a 367
        return None
    if not r or not r[0]:
        return None
    return {"chave": r[0], "tipo": r[1], "rotulo_tipo": TIPOS.get(r[1] or "", ""),
            "recebedor": r[2], "cidade": r[3]}


def salvar(pool, conta_id: int, chave: str, recebedor: str = "", cidade: str = "") -> dict:
    """Grava a chave normalizada. Chave vazia APAGA (a cobrança volta a sair sem
    Pix). ValueError com a frase pro usuário se a chave não fechar."""
    if not (chave or "").strip():
        with pool.connection() as c:
            c.execute("""update contas set pix_chave=null, pix_tipo=null, pix_recebedor=null,
                                           pix_cidade=null where id=%s""", (conta_id,))
            c.commit()
        return {}
    tipo, k = normalizar_chave(chave)
    with pool.connection() as c:
        c.execute("""update contas set pix_chave=%s, pix_tipo=%s, pix_recebedor=%s, pix_cidade=%s
                      where id=%s""",
                  (k, tipo, (recebedor or "").strip()[:60] or None,
                   (cidade or "").strip()[:40] or None, conta_id))
        c.commit()
    return da_conta(pool, conta_id) or {}
