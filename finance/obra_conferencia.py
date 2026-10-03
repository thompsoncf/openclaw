"""A conferência da nota na entrada do CD (PR 3a do CD das obras).

Desenho aprovado pelo dono em 03/10/2026 (docs/mockups/obras_cd_almoxarifado.html,
aba "Entradas"): o caminhão descarrega, o CD confere a nota contra o que desceu.
Bateu — um toque. Não bateu — digita quanto chegou.

O CD FICA COM O QUE CHEGOU: a conferência corrige a quantidade da própria
ENTRADA no `estoque_mov` (a que o leitor da nota gravou, `item_id` preso) e guarda
a quantidade da nota em `obra_conferencia_itens`. Corrigir a entrada — e não
lançar um ajuste solto — é o que mantém a nota inteira: se ela mudar de obra
depois (`obra_material.realocar_do_lancamento`), vai junto o que chegou.

A DIFERENÇA FICA CONTRA O FORNECEDOR: o texto da nota (o que o leitor anotou
como descrição — "Constrular") é guardado na conferência, e `divergencias`
conta por fornecedor. Desfazer devolve a quantidade da nota.

POR ENTRADA, NÃO POR NOTA: a nota lida em lotes (cupom grande, página 2 anexada
depois) ganha entradas novas depois da conferência — elas voltam pra "falta
conferir" e entram na MESMA conferência da nota.

O CD NUNCA FICA NEGATIVO: conferir pra menos (ou desfazer o que chegou a mais)
só tira do CD o que ainda está nele. Se o material já saiu pras obras, recusa —
a conferência é na chegada, antes de distribuir. E o desfazer recusa quando a
nota já foi pra uma obra (mexeria no estoque da obra).

SÓ O CD: a nota que foi direto pra uma obra é conferida no canteiro (o "recebi"
do pedido já faz esse papel). Aqui entram as entradas com `obra_id` vazio.
"""
from __future__ import annotations

from decimal import Decimal

from . import obra_material as _om


def _dec(v) -> Decimal:
    return _om.quantidade_br(v)


def _desde(dias: int):
    """O corte em dia de BRASÍLIA (CLAUDE.md, regra 7): o current_date do banco é
    UTC e, das 21h à meia-noite, já é amanhã."""
    from datetime import timedelta
    from .relogio import hoje
    return hoje() - timedelta(days=dias)


def pendentes(pool, conta_id: int, dias: int = 60) -> list[dict]:
    """As notas que deram entrada no CD nos últimos `dias` e não foram conferidas,
    a mais nova primeiro, com os itens (a quantidade da nota)."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select m.id, m.lancamento_id, l.descricao, l.data, m.produto_id, p.nome, p.unidade,
                          m.quantidade
                     from estoque_mov m
                     join lancamentos l on l.id = m.lancamento_id and l.conta_id = m.fornecedor_id
                     join catalogo_produtos p on p.id = m.produto_id and p.fornecedor_id = m.fornecedor_id
                    where m.fornecedor_id=%s and m.tipo='entrada' and m.obra_id is null
                      and m.item_id is not null
                      and l.data > %s
                      and not exists (select 1 from obra_conferencia_itens i
                                       where i.conta_id = m.fornecedor_id and i.mov_id = m.id)
                    order by l.data desc, m.lancamento_id desc, m.id""",
                (conta_id, _desde(dias))).fetchall()
    except Exception:  # noqa: BLE001 — sem a migração
        return []
    notas: dict = {}
    for mid, lid, desc, data, pid, nome, un, q in rows:
        n = notas.setdefault(lid, {"lancamento_id": lid, "fornecedor": (desc or "").strip() or "Nota sem nome",
                                   "data": data, "itens": []})
        n["itens"].append({"mov_id": mid, "produto_id": pid, "nome": nome, "unidade": un or "unidade",
                           "qtd": Decimal(q), "rotulo": _om.rotulo(q, un),
                           "valor_campo": _om._qtd(q)})          # "1,125", nunca "1.125" (= mil)
    return list(notas.values())


def conferir(pool, conta_id: int, lancamento_id: int, *, chegou: dict | None = None,
             por=None) -> dict:
    """Confere a nota. `chegou`: {mov_id: quantidade}; sem ele (ou igual à nota),
    "bateu". Devolve {frase, divergente, faltas}."""
    nota = next((n for n in pendentes(pool, conta_id, dias=3650) if n["lancamento_id"] == lancamento_id), None)
    if not nota:
        raise ValueError("Essa nota não está pra conferir (já conferida, ou não entrou no CD).")
    linhas, faltas = [], []
    for it in nota["itens"]:
        bruto = (chegou or {}).get(it["mov_id"], (chegou or {}).get(str(it["mov_id"])))
        q = it["qtd"] if bruto in (None, "") else _dec(bruto)
        linhas.append((it, q))
        if q != it["qtd"]:
            dif = q - it["qtd"]
            faltas.append(f"{it['nome']}: chegou {_om._qtd(q)} de {_om._qtd(it['qtd'])}"
                          if dif < 0 else f"{it['nome']}: chegou {_om._qtd(q)}, a nota diz {_om._qtd(it['qtd'])}")
    divergente = bool(faltas)
    tira: dict = {}                 # o que a conferência tira do CD, por material
    for it, q in linhas:
        if q < it["qtd"]:
            nome, menos = tira.get(it["produto_id"], (it["nome"], Decimal(0)))
            tira[it["produto_id"]] = (nome, menos + it["qtd"] - q)
    import psycopg
    try:
        with pool.connection() as c:
            # a conferência da nota: nasce aqui, ou (nota em lotes) é a que já existe.
            # O upsert trava a linha até o commit: duas conferências da mesma nota
            # fazem fila, e a segunda acha os itens já conferidos.
            cid = c.execute("""insert into obra_conferencias (conta_id, lancamento_id, fornecedor, divergente,
                                                              conferido_por)
                               values (%s,%s,%s,%s,%s)
                               on conflict (conta_id, lancamento_id)
                               do update set divergente = obra_conferencias.divergente or excluded.divergente
                               returning id""",
                            (conta_id, lancamento_id, nota["fornecedor"][:120], divergente, por)).fetchone()[0]
            if c.execute("select 1 from obra_conferencia_itens where conta_id=%s and mov_id = any(%s)",
                         (conta_id, [it["mov_id"] for it, _ in linhas])).fetchone():
                c.rollback()
                raise ValueError("Essa nota acabou de ser conferida por outra pessoa.")
            for pid, (nome, menos) in tira.items():
                saldo = _om._saldo_em(c, conta_id, pid, None)
                if saldo - menos < 0:
                    c.rollback()
                    raise ValueError(f"{nome}: o CD só tem {_om._qtd(max(saldo, Decimal(0)))} agora — já saiu "
                                     f"mais do que a diferença de {_om._qtd(menos)}. A conferência é na chegada: "
                                     "confira a nota antes de mandar o material pras obras.")
            for it, q in linhas:
                c.execute("""insert into obra_conferencia_itens (conta_id, conferencia_id, mov_id, produto_id,
                                                                 nota_qtd, chegou_qtd)
                             values (%s,%s,%s,%s,%s,%s)""",
                          (conta_id, cid, it["mov_id"], it["produto_id"], it["qtd"], q))
                if q != it["qtd"]:
                    c.execute("""update estoque_mov set quantidade=%s
                                  where id=%s and fornecedor_id=%s and tipo='entrada' and obra_id is null""",
                              (q, it["mov_id"], conta_id))
            c.commit()
    except psycopg.errors.UniqueViolation:
        raise ValueError("Essa nota acabou de ser conferida por outra pessoa.")
    if divergente:
        frase = f"Conferida com diferença ({nota['fornecedor']}): " + "; ".join(faltas) + ". O CD ficou com o que chegou."
    else:
        frase = f"Nota de {nota['fornecedor']} conferida: bateu. ✓"
    return {"frase": frase, "divergente": divergente, "faltas": faltas}


def conferidas(pool, conta_id: int, limite: int = 15) -> list[dict]:
    """As últimas conferências, com os itens que divergiram."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select x.id, x.lancamento_id, x.fornecedor, x.divergente, x.conferido_em,
                          coalesce(m.nome, 'Dono')
                     from obra_conferencias x
                     left join membros m on m.id = x.conferido_por and m.conta_id = x.conta_id
                    where x.conta_id=%s order by x.conferido_em desc, x.id desc limit %s""",
                (conta_id, limite)).fetchall()
            itens = c.execute(
                """select i.conferencia_id, p.nome, p.unidade, i.nota_qtd, i.chegou_qtd
                     from obra_conferencia_itens i
                     join catalogo_produtos p on p.id = i.produto_id and p.fornecedor_id = i.conta_id
                    where i.conta_id=%s and i.conferencia_id = any(%s) and i.nota_qtd <> i.chegou_qtd""",
                (conta_id, [r[0] for r in rows])).fetchall()
            # a nota que foi pra uma obra depois da conferência não desfaz mais
            na_obra = {r[0] for r in c.execute(
                """select i.conferencia_id from obra_conferencia_itens i
                     join estoque_mov m on m.id = i.mov_id and m.fornecedor_id = i.conta_id
                    where i.conta_id=%s and i.conferencia_id = any(%s) and m.obra_id is not null""",
                (conta_id, [r[0] for r in rows])).fetchall()}
    except Exception:  # noqa: BLE001
        return []
    difs: dict = {}
    for cid, nome, un, nq, cq in itens:
        difs.setdefault(cid, []).append(f"{nome}: chegou {_om._qtd(cq)} de {_om._qtd(nq)}")
    from .relogio import para_br
    return [{"id": r[0], "lancamento_id": r[1], "fornecedor": r[2], "divergente": r[3],
             "quando": para_br(r[4]), "quem": r[5], "difs": difs.get(r[0], []),
             "pode_desfazer": r[0] not in na_obra} for r in rows]


def desfazer(pool, conta_id: int, conferencia_id: int) -> str:
    """Errou a conferência: a entrada volta à quantidade da nota, e a nota volta
    pra "falta conferir". Recusa quando a nota já foi pra uma obra, ou quando o
    que chegou a mais já saiu do CD (o CD ficaria negativo)."""
    with pool.connection() as c:
        r = c.execute("select fornecedor from obra_conferencias where id=%s and conta_id=%s for update",
                      (conferencia_id, conta_id)).fetchone()
        if not r:
            raise ValueError("Conferência não encontrada.")
        itens = c.execute(
            """select i.mov_id, i.produto_id, i.nota_qtd, i.chegou_qtd, m.obra_id, p.nome
                 from obra_conferencia_itens i
                 join catalogo_produtos p on p.id = i.produto_id and p.fornecedor_id = i.conta_id
                 left join estoque_mov m on m.id = i.mov_id and m.fornecedor_id = i.conta_id
                where i.conferencia_id=%s and i.conta_id=%s""", (conferencia_id, conta_id)).fetchall()
        if any(mov_id and obra_id is not None for mov_id, _, _, _, obra_id, _ in itens):
            c.rollback()
            raise ValueError("Essa nota foi pra uma obra depois da conferência — desfazer mexeria no "
                             "estoque da obra. Se precisar, acerte por lá.")
        tira: dict = {}
        for mov_id, pid, nq, cq, _, nome in itens:
            if mov_id and nq < cq:
                menos = tira.get(pid, (nome, Decimal(0)))[1]
                tira[pid] = (nome, menos + cq - nq)
        for pid, (nome, menos) in tira.items():
            saldo = _om._saldo_em(c, conta_id, pid, None)
            if saldo - menos < 0:
                c.rollback()
                raise ValueError(f"{nome}: o que chegou a mais já saiu do CD (tem "
                                 f"{_om._qtd(max(saldo, Decimal(0)))}) — não dá pra desfazer.")
        for mov_id, _, nq, cq, _, _ in itens:
            if mov_id and nq != cq:
                c.execute("""update estoque_mov set quantidade=%s
                              where id=%s and fornecedor_id=%s and tipo='entrada' and obra_id is null""",
                          (nq, mov_id, conta_id))
        c.execute("delete from obra_conferencias where id=%s and conta_id=%s", (conferencia_id, conta_id))
        c.commit()
    return f"Conferência da nota de {r[0]} desfeita — voltou pra conferir."


def divergencias(pool, conta_id: int, dias: int = 30) -> list[dict]:
    """Por fornecedor, nos últimos `dias`: quantas notas conferidas e quantas com
    diferença — o número que vai pra conversa com ele."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select fornecedor, count(*), count(*) filter (where divergente)
                     from obra_conferencias
                    where conta_id=%s and conferido_em > now() - make_interval(days => %s)
                    group by fornecedor order by 3 desc, 2 desc""", (conta_id, dias)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"fornecedor": r[0], "notas": int(r[1]), "com_diferenca": int(r[2])} for r in rows]
