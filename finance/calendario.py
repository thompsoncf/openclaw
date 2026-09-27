"""O DIA DA SEMANA QUE A IA NÃO PODE CHUTAR.

O CASO (27/09/2026). No "Testar comigo" do resgate, o dono respondeu "06 07 2028" pra
data da formatura e a IA repetiu, confiante: "06/07/2028 (sábado)". É quinta-feira. Na
Prime o dia da semana escolhe o PACOTE ("Segunda a Quinta" × "Sábado"): com os preços
liberados, a IA cotaria o pacote de fim de semana pra uma quinta — o mesmo tipo de erro
do pacote de 2027 cotado pra uma festa de 2026 (ver o prompt em finance/agente.py).

O modelo não calcula calendário; ele adivinha. Então as duas pontas ficam com o código:

  * ENTRADA: `bloco(textos)` acha as datas escritas na conversa e entrega a lista
    pronta ("06/07/2028 é quinta-feira"), com a ordem de nunca calcular de cabeça.
  * SAÍDA: `corrigir(texto)` troca o dia da semana errado que vier colado a uma data
    ("06/07/2028 (sábado)", "06/07/2028, sábado", "sábado, 06/07/2028") pelo certo,
    antes de a mensagem sair pro cliente.

Puro, sem banco: o teste fixa as duas pontas sem depender de modelo nenhum.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_BRT = timezone(timedelta(hours=-3))

DIAS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira",
        "sábado", "domingo")

MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
         "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
         "novembro": 11, "dezembro": 12}

#: "06/07/2028", "6-7-28", "06.07.2028", "06 07 2028", "6/7" — e "06072028" colado
_RE_NUM = re.compile(
    r"(?<![\d/.\-])(\d{1,2})\s*[/.\-]\s*(\d{1,2})(?:\s*[/.\-]\s*(\d{2}|\d{4}))?(?![\d/.\-])"
    r"|(?<!\d)(\d{1,2}) (\d{1,2}) (\d{4})(?!\d)"
    r"|(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")
#: "6 de julho de 2028", "6 de julho"
_RE_EXTENSO = re.compile(
    r"(?<!\d)(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")(?:\s+de\s+(\d{4}))?", re.I)

#: um dia da semana escrito pelo modelo (com ou sem "-feira", com ou sem acento)
_DIA = (r"(segunda|ter[çc]a|quarta|quinta|sexta)(?:[\s-]*feira)?|s[áa]bado|domingo")


def _hoje() -> date:
    return datetime.now(_BRT).date()


def _data(d, m, a, hoje: date) -> date | None:
    try:
        d, m = int(d), int(m)
        if a:
            a = int(a)
            a = a + 2000 if a < 100 else a
            return date(a, m, d)
        # sem ano: a próxima vez que essa data acontece (festa é sempre pra frente)
        x = date(hoje.year, m, d)
        return x if x >= hoje else date(hoje.year + 1, m, d)
    except ValueError:
        return None


def datas(texto: str, hoje: date | None = None) -> list[date]:
    """As datas escritas no texto, na ordem, sem repetir. Só as de 2 anos pra trás a
    10 pra frente — "10 20 2030" não é data, e telefone não vira festa."""
    hoje = hoje or _hoje()
    out: list[date] = []
    for m in _RE_NUM.finditer(texto or ""):
        g = m.groups()
        if g[0]:
            x = _data(g[0], g[1], g[2], hoje)
        elif g[3]:
            x = _data(g[3], g[4], g[5], hoje)
        else:
            x = _data(g[6], g[7], g[8], hoje)
        if x and x not in out and hoje.year - 2 <= x.year <= hoje.year + 10:
            out.append(x)
    for m in _RE_EXTENSO.finditer(texto or ""):
        x = _data(m.group(1), MESES[m.group(2).lower()], m.group(3), hoje)
        if x and x not in out and hoje.year - 2 <= x.year <= hoje.year + 10:
            out.append(x)
    return out


def dia(x: date) -> str:
    return DIAS[x.weekday()]


def bloco(*textos: str, hoje: date | None = None) -> str:
    """O calendário pronto das datas da conversa, pro prompt. Vazio sem data."""
    hoje = hoje or _hoje()
    todas: list[date] = []
    for t in textos:
        for x in datas(t or "", hoje):
            if x not in todas:
                todas.append(x)
    if not todas:
        return ""
    linhas = "; ".join(f"{x:%d/%m/%Y} é {dia(x)}" for x in todas[:12])
    return (f"\n\nCALENDÁRIO (conferido pelo sistema — use SEMPRE estes dias da semana, "
            f"nunca calcule de cabeça; hoje é {hoje:%d/%m/%Y}, {dia(hoje)}): {linhas}. "
            "O dia da semana decide o pacote do catálogo.")


def corrigir(texto: str, hoje: date | None = None) -> str:
    """Troca o dia da semana ERRADO colado a uma data pelo certo. Só mexe onde o dia
    está grudado na data (parênteses, vírgula ou traço, antes ou depois); o resto do
    texto fica como veio."""
    if not texto:
        return texto
    hoje = hoje or _hoje()
    data_re = (r"(\d{1,2}/\d{1,2}(?:/\d{2,4})?)")

    def _certo(txt_data: str) -> str | None:
        xs = datas(txt_data, hoje)
        return dia(xs[0]) if xs else None

    def _como(original: str, certo: str) -> str:
        return certo.capitalize() if original[:1].isupper() else certo

    def _depois(m):          # grupos: 1 data · 2 separador · 3 dia
        certo = _certo(m.group(1))
        if not certo:
            return m.group(0)
        ini, fim = m.start(3) - m.start(0), m.end(3) - m.start(0)
        return m.group(0)[:ini] + _como(m.group(3), certo) + m.group(0)[fim:]

    def _antes(m):           # grupos: 1 dia · 2 (dentro do dia) · 3 separador · 4 data
        certo = _certo(m.group(4))
        if not certo:
            return m.group(0)
        return _como(m.group(1), certo) + m.group(0)[m.end(1) - m.start(0):]

    # "06/07/2028 (sábado)" · "06/07/2028, sábado" · "06/07/2028 - sábado"
    texto = re.sub(data_re + r"(\s*[(,\-–]\s*)(" + _DIA + r")", _depois, texto, flags=re.I)
    # "sábado, 06/07/2028" · "sábado (06/07/2028)" · "sábado dia 06/07/2028"
    texto = re.sub(r"(" + _DIA + r")(\s*[,(]?\s*(?:dia\s+)?)" + data_re, _antes, texto, flags=re.I)
    return texto
