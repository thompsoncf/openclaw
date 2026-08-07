"""Validacao e classificacao de documento — CPF (11) e CNPJ (14).

Valida o DIGITO VERIFICADOR de verdade (nao so' o tamanho), rejeita sequencias
repetidas (000..., 111...) e oferece formatacao. Sem dependencia externa.
"""
from __future__ import annotations


def so_digitos(s) -> str:
    if not s:
        return ""
    return "".join(c for c in str(s) if c.isdigit())


def valida_cpf(cpf) -> bool:
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    s = sum(int(d[i]) * (10 - i) for i in range(9))
    r = (s * 10) % 11
    r = 0 if r == 10 else r
    if r != int(d[9]):
        return False
    s = sum(int(d[i]) * (11 - i) for i in range(10))
    r = (s * 10) % 11
    r = 0 if r == 10 else r
    return r == int(d[10])


def valida_cnpj(cnpj) -> bool:
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False

    def _dv(base: str) -> int:
        pesos = ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if len(base) == 12
                 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
        s = sum(int(base[i]) * pesos[i] for i in range(len(base)))
        r = s % 11
        return 0 if r < 2 else 11 - r

    if _dv(d[:12]) != int(d[12]):
        return False
    return _dv(d[:13]) == int(d[13])


def classificar(doc) -> tuple[str | None, str]:
    """(tipo, digitos) so' pelo tamanho — 11 -> 'pf', 14 -> 'pj', senao None."""
    d = so_digitos(doc)
    if len(d) == 11:
        return ("pf", d)
    if len(d) == 14:
        return ("pj", d)
    return (None, d)


def valida(doc) -> tuple[bool, str | None, str]:
    """Valida CPF ou CNPJ pelo tamanho. Retorna (ok, tipo, digitos)."""
    tipo, d = classificar(doc)
    if tipo == "pf":
        return (valida_cpf(d), "pf", d)
    if tipo == "pj":
        return (valida_cnpj(d), "pj", d)
    return (False, None, d)


def formatar(doc) -> str | None:
    d = so_digitos(doc)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return d or None
