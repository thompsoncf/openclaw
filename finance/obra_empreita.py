"""O que já foi pago ao empreiteiro, por etapa (migração 371).

Desenho aprovado pelo dono em 25/09/2026 (docs/mockups/nicho_construcao.html,
seção 07): "paguei 10 mil pro empreiteiro, primeira etapa da casa 2" vira o
lançamento de mão de obra na Casa 2 E a marca de que fundação e estrutura estão
pagas.

O dinheiro continua sendo o LANÇAMENTO (o caixa e a DRE por obra já o contam).
Aqui fica só o "pelo quê": que etapas aquele pagamento fechou. Cruzando com as
etapas feitas, a ficha mostra as duas coisas que dão prejuízo em empreitada:

- PAGO E NÃO FEITO: adiantamento. Pode ser combinado — mas é o que vira calote
  quando o empreiteiro some;
- PAGO DUAS VEZES: a mesma etapa em dois pagamentos. Às vezes é parcela
  combinada, às vezes é erro. O sistema avisa e não impede.
E a que dá briga: FEITO E NÃO PAGO, que é o que o empreiteiro vai cobrar.

O VALOR: o pagamento que fecha várias etapas é dividido entre elas pelo peso
(o mesmo peso do andamento da obra), com o resto dos centavos na última. É uma
régua, não a verdade: quem quiser outro rateio lança dois pagamentos.
"""
from __future__ import annotations

from datetime import date

from . import obras as _ob

_COLS = ("id", "etapa_id", "lancamento_id", "valor_centavos", "pago_em", "obs")


def _parte_da_obra(c, conta_id: int, obra: dict, lancamento_id: int) -> int:
    """Quanto daquele lançamento é DESTA obra: o inteiro se ele está no centro da
    obra; a parte da divisão se foi dividido. ValueError se não é despesa da obra."""
    r = c.execute(
        """select l.tipo, l.centro_custo_id, l.valor_centavos,
                  (select r.valor_centavos from lancamento_rateio r
                    where r.lancamento_id = l.id and r.centro_custo_id = %s)
             from lancamentos l where l.id=%s and l.conta_id=%s""",
        (obra["centro_custo_id"], lancamento_id, conta_id)).fetchone()
    if not r:
        raise ValueError("Não achei esse lançamento.")
    tipo, centro, valor, parte = r
    if tipo != "despesa":
        raise ValueError("Esse lançamento não é um pagamento (é receita).")
    if parte is not None:
        return int(parte)
    if centro == obra["centro_custo_id"]:
        return int(valor)
    raise ValueError(f"Esse lançamento não está na {obra['nome']}. Ponha ele na obra "
                     "primeiro (por_na_obra) e depois marque as etapas.")


def _dividir(valor: int, etapas: list[dict]) -> list[int]:
    """O valor entre as etapas pelo peso; o resto dos centavos na última. Sem peso
    nenhum, em partes iguais."""
    pesos = [max(float(e["peso"]), 0.0) for e in etapas]
    total = sum(pesos)
    if total <= 0:
        pesos, total = [1.0] * len(etapas), float(len(etapas))
    partes = [int(valor * p // total) for p in pesos]
    partes[-1] += valor - sum(partes)
    return partes


def pagar_etapas(pool, conta_id: int, obra_id: int, etapas_ref: list, *,
                 lancamento_id: int, quando: date | None = None, obs: str = "") -> dict:
    """Marca as etapas como pagas por aquele lançamento. Devolve {obra, etapas:
    [{nome, valor_centavos}], adiantadas: [nomes], ja_pagas: [frases]}.
    ValueError com a frase pro usuário."""
    obra = _ob.obter_obra(pool, conta_id, obra_id)
    if not obra:
        raise ValueError("Obra não encontrada.")
    escolhidas = []
    for ref in etapas_ref or []:
        e = next((x for x in obra["etapas"] if x["id"] == ref), None) \
            if isinstance(ref, int) else _ob._achar_etapa(obra["etapas"], str(ref))
        if not e:
            nomes = ", ".join(x["nome"].lower() for x in obra["etapas"])
            raise ValueError(f"Não achei a etapa “{ref}” na {obra['nome']}. As etapas são: {nomes}.")
        if e not in escolhidas:
            escolhidas.append(e)
    if not escolhidas:
        raise ValueError("Diga quais etapas esse pagamento fecha.")
    with pool.connection() as c:
        usado = c.execute(
            """select string_agg(distinct e.nome, ', ') from obra_etapa_pagamentos p
                 join obra_etapas e on e.id = p.etapa_id
                where p.conta_id=%s and p.lancamento_id=%s""",
            (conta_id, lancamento_id)).fetchone()[0]
        if usado:
            raise ValueError(f"Esse lançamento já foi usado pra pagar: {usado.lower()}.")
        valor = _parte_da_obra(c, conta_id, obra, lancamento_id)
        anteriores = {r[0]: (r[1], r[2]) for r in c.execute(
            """select etapa_id, sum(valor_centavos), max(pago_em) from obra_etapa_pagamentos
                where conta_id=%s and obra_id=%s and etapa_id = any(%s) group by etapa_id""",
            (conta_id, obra_id, [e["id"] for e in escolhidas])).fetchall()}
        partes = _dividir(valor, escolhidas)
        for e, v in zip(escolhidas, partes):
            c.execute(
                """insert into obra_etapa_pagamentos (conta_id, obra_id, etapa_id, lancamento_id,
                                                      valor_centavos, pago_em, obs)
                   values (%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, obra_id, e["id"], lancamento_id, v, quando or date.today(),
                 (obs or "").strip()[:200]))
        c.commit()
    return {"obra": obra["nome"],
            "etapas": [{"nome": e["nome"], "valor_centavos": v} for e, v in zip(escolhidas, partes)],
            "adiantadas": [e["nome"] for e in escolhidas if not e["concluida_em"]],
            "ja_pagas": [f"{e['nome']} já tinha {_ob._brl(int(anteriores[e['id']][0]))} pago "
                         f"em {anteriores[e['id']][1].strftime('%d/%m')}"
                         for e in escolhidas if e["id"] in anteriores]}


def pagamentos(pool, conta_id: int, obra_id: int) -> list[dict]:
    try:
        with pool.connection() as c:
            rows = c.execute(f"""select {', '.join(_COLS)} from obra_etapa_pagamentos
                                  where conta_id=%s and obra_id=%s order by pago_em, id""",
                             (conta_id, obra_id)).fetchall()
    except Exception:  # noqa: BLE001 — sem a 371
        return []
    return [dict(zip(_COLS, r)) for r in rows]


def situacao(pool, conta_id: int, obra: dict) -> dict:
    """Por etapa, quanto foi pago; e as três listas que importam."""
    pags = pagamentos(pool, conta_id, obra["id"])
    por_etapa: dict[int, int] = {}
    for p in pags:
        por_etapa[p["etapa_id"]] = por_etapa.get(p["etapa_id"], 0) + int(p["valor_centavos"])
    etapas = [dict(e, pago_centavos=por_etapa.get(e["id"], 0)) for e in obra["etapas"]]
    return {
        "etapas": etapas,
        "pagamentos": pags,
        "total_pago": sum(por_etapa.values()),
        "feitas_nao_pagas": [e["nome"] for e in etapas if e["concluida_em"] and not e["pago_centavos"]],
        "pagas_nao_feitas": [e["nome"] for e in etapas if e["pago_centavos"] and not e["concluida_em"]],
        "tem": bool(pags),
    }


def desfazer(pool, conta_id: int, obra_id: int, lancamento_id: int) -> int:
    """Tira as marcas daquele pagamento (o lançamento continua no caixa)."""
    with pool.connection() as c:
        n = c.execute("""delete from obra_etapa_pagamentos
                          where conta_id=%s and obra_id=%s and lancamento_id=%s""",
                      (conta_id, obra_id, lancamento_id)).rowcount
        c.commit()
    return n


def resumo(obra: dict, sit: dict) -> str:
    """A empreitada da obra numa frase de WhatsApp. Vazio sem pagamento."""
    if not sit["tem"]:
        return ""
    txt = f"Mão de obra paga por etapa: {_ob._brl(sit['total_pago'])}."
    if sit["pagas_nao_feitas"]:
        txt += " ⚠️ Pago e ainda não feito (adiantamento): " + ", ".join(
            n.lower() for n in sit["pagas_nao_feitas"]) + "."
    if sit["feitas_nao_pagas"]:
        txt += " Feito e ainda não pago: " + ", ".join(
            n.lower() for n in sit["feitas_nao_pagas"]) + "."
    return txt
