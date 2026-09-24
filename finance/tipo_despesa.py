"""O TIPO da despesa — fixa, eventual, investimento — separado do centro de custo.

Correção do dono em 24/09/2026, sobre a entrega 4:

    "fixa, eventual, investimento não é centro de custo, tem que ser separado
     para ter maior clareza no relatório do gestor"

Toda despesa passa a responder três perguntas independentes:

    plano de contas  -> o que é o gasto          (5.1.09 Diaristas)
    centro de custo  -> de qual área do negócio  (Buffet, Locação Espaço…)
    tipo de despesa  -> que tipo de gasto        (fixa | eventual | investimento)

Medido na Prime no mesmo dia: das 129 despesas da empresa, 51 tinham um dos três
"centros" DESPESA FIXA / EVENTUAL / INVESTIMENTO e só 5 um centro de área — o
campo único fazia escolher entre dizer o tipo e dizer a área. E o tipo não sai
do plano: "Diaristas" aparece como fixa, eventual e investimento.

Os 51 já classificados ganharam o tipo na migração 325 (opção A, escolhida pelo
dono), e o centro deles ficou como estava.
"""
from __future__ import annotations

import unicodedata
from datetime import date

TIPOS = ("fixa", "eventual", "investimento")
ROTULOS = {"fixa": "Fixa", "eventual": "Eventual", "investimento": "Investimento"}

#: O que alguém escreve (tela, agente, extrato) e o tipo que isso quer dizer.
_SINONIMOS = {"fixa": "fixa", "fixo": "fixa", "fixas": "fixa", "despesa fixa": "fixa",
              "eventual": "eventual", "eventuais": "eventual", "variavel": "eventual",
              "despesa eventual": "eventual", "investimento": "investimento",
              "investimentos": "investimento", "invest": "investimento"}


def _sem_acento(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(ch) != "Mn")


def normalizar(v) -> str | None:
    """'Fixa', 'DESPESA FIXA', 'investimentos' -> o tipo; vazio ou estranho -> None.
    Estranho vira None e não erro: nenhuma porta quebra por causa de um tipo mal
    escrito — o lançamento entra sem tipo e aparece no "sem tipo"."""
    t = " ".join(_sem_acento(str(v or "")).split())
    return _SINONIMOS.get(t)


def _forma(descricao: str) -> str:
    """A descrição sem data, número e pontuação: "DIARIA 18/09 MARIA" e "DIARIA
    25/09 MARIA" são a mesma forma. É por ela que a sugestão casa."""
    t = _sem_acento(descricao)
    out = []
    for p in "".join(ch if ch.isalnum() else " " for ch in t).split():
        if any(ch.isdigit() for ch in p) or len(p) < 2:
            continue
        out.append(p)
    return " ".join(out)


def _meses_ate(hoje: date, n: int) -> list[tuple[int, int]]:
    a, m = hoje.year, hoje.month
    out = []
    for _ in range(n):
        out.append((a, m))
        a, m = (a - 1, 12) if m == 1 else (a, m - 1)
    return list(reversed(out))


MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro")


def por_mes(pool, conta_id: int, meses: int = 2, hoje: date | None = None) -> list[dict]:
    """O quadro do gestor: as despesas de EMPRESA dos últimos `meses`, por tipo.

    `pct_com_tipo` é pelo VALOR, não pela quantidade — é o dinheiro que o
    relatório explica. Mês sem despesa nenhuma sai com total zero, e a tela
    decide não mostrar."""
    hoje = hoje or date.today()
    lista = _meses_ate(hoje, meses)
    ini = date(lista[0][0], lista[0][1], 1)
    a, m = hoje.year, hoje.month
    fim = date(a + 1, 1, 1) if m == 12 else date(a, m + 1, 1)
    with pool.connection() as c:
        rows = c.execute(
            """select extract(year from data)::int, extract(month from data)::int,
                      tipo_despesa, count(*), coalesce(sum(valor_centavos), 0)
                 from lancamentos
                where conta_id=%s and tipo='despesa' and natureza='empresa'
                  and data >= %s and data < %s
                group by 1, 2, 3""", (conta_id, ini, fim)).fetchall()
    out = []
    for ano, mes in lista:
        por = {t: {"n": 0, "centavos": 0} for t in TIPOS}
        sem = {"n": 0, "centavos": 0}
        for a_, m_, t, n, v in rows:
            if (a_, m_) != (ano, mes):
                continue
            alvo = por[t] if t in por else sem
            alvo["n"] += int(n)
            alvo["centavos"] += int(v)
        total = sum(x["centavos"] for x in por.values()) + sem["centavos"]
        com = total - sem["centavos"]
        out.append({"ano": ano, "mes": mes,
                    "rotulo": f"{MESES[mes - 1].capitalize()}/{ano}",
                    "por_tipo": [{"tipo": t, "rotulo": ROTULOS[t], **por[t]} for t in TIPOS],
                    "sem": sem, "total": total,
                    "pct_com_tipo": round(100 * com / total) if total else 0})
    return out


def sem_tipo(pool, conta_id: int, ano: int, mes: int, limite: int = 120) -> list[dict]:
    """As despesas de EMPRESA do mês ainda sem tipo, pra classificar com um toque.

    Cada uma vem com a SUGESTÃO, quando existe: o tipo do lançamento mais recente
    desta conta com a MESMA FORMA de descrição. É só sugestão — a tela destaca, o
    toque é que grava. Sem forma igual, sem sugestão: chutar pelo plano de contas
    erraria ("Diaristas" é dos três tipos na Prime)."""
    ini = date(ano, mes, 1)
    fim = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
    with pool.connection() as c:
        pend = c.execute(
            """select l.id, l.data, l.descricao, l.categoria, l.valor_centavos,
                      pc.codigo, pc.nome, cc.nome
                 from lancamentos l
                 left join plano_contas pc on pc.id = l.plano_conta_id
                 left join centros_custo cc on cc.id = l.centro_custo_id
                                            and cc.conta_id = l.conta_id
                where l.conta_id=%s and l.tipo='despesa' and l.natureza='empresa'
                  and l.tipo_despesa is null and l.data >= %s and l.data < %s
                order by l.valor_centavos desc, l.id desc
                limit %s""", (conta_id, ini, fim, limite)).fetchall()
        feitos = c.execute(
            """select descricao, tipo_despesa from lancamentos
                where conta_id=%s and tipo='despesa' and tipo_despesa is not null
                order by data desc, id desc limit 500""", (conta_id,)).fetchall()
    memoria: dict[str, str] = {}
    for desc, tipo in feitos:
        f = _forma(desc)
        if f and f not in memoria:
            memoria[f] = tipo
    return [{"id": r[0], "data": r[1], "descricao": r[2] or r[3] or "",
             "valor_centavos": int(r[4] or 0),
             "plano": f"{r[5]} {r[6]}" if r[5] else "", "centro": r[7] or "",
             "sugestao": memoria.get(_forma(r[2] or ""))} for r in pend]
