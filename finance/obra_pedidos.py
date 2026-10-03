"""O CD das obras: o pedido da obra, o "recebi" e a sobra (migração 670).

Desenho aprovado pelo dono em 03/10/2026 (docs/mockups/obras_cd_almoxarifado.html,
"segue as recomendações"), PR 1 de 3:
  1. o pedido NÃO precisa de aprovação: o CD separa direto (o dono vê e cancela);
  3. o "dinheiro parado" no CD aparece só pra dono e gestor (a tela decide);
  4. o "recebi" do mestre é a prova de entrega: é ELE que move o material do CD
     pra obra. O "levei" direto de antes continua valendo pra quem não pede;
  6. um CD por empresa — o depósito de sempre (`estoque_mov.obra_id` vazio).

O FLUXO: pedido → separando → saiu → recebido (ou cancelado). O estoque só se
mexe no "recebi", com a mesma transferência pareada do "levei"
(`obra_material.mover`); o pedido dá dono, hora e prova ao movimento, e guarda
o que chegou de verdade ("pediu 20, recebeu 18").

A SOBRA: casa pronta com material ainda nela é dinheiro parado no lugar errado.
`sobras` lista, `devolver` traz tudo de volta pro CD (transferência pareada ao
contrário).
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

from . import obra_material as _om
from . import obras as _ob

STATUS = ("pedido", "separando", "saiu", "recebido", "cancelado")
ROTULO = {"pedido": "📝 Pedido", "separando": "📦 Separando", "saiu": "🚚 Saiu",
          "recebido": "✅ Recebido", "cancelado": "Cancelado"}
_ANTES = {"separando": ("pedido",), "saiu": ("pedido", "separando"),
          "recebido": ("pedido", "separando", "saiu")}
_PRONTA = ("pronta", "vendida", "entregue")


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v).replace(",", "."))
    except Exception:  # noqa: BLE001
        raise ValueError("Quantidade inválida.")


# ── o pedido ──────────────────────────────────────────────────────────────
def criar(pool, conta_id: int, obra_id: int, *, itens: list[tuple], pedido_por=None,
          urgente: bool = False, prazo: date | None = None, recado: str = "") -> dict:
    """`itens`: [(material como a pessoa falou, quantidade)]. Material ambíguo ou
    desconhecido pergunta, e nada é gravado (é o CD que vai separar: tem que
    ser o material certo)."""
    limpos = []
    for ref, qtd in itens:
        if not (str(ref or "").strip()) and not str(qtd or "").strip():
            continue
        q = _dec(qtd)
        if q <= 0:
            raise ValueError("Quantidade tem que ser maior que zero.")
        p = _om.achar_produto(pool, conta_id, str(ref or ""))
        if p is not None and "ambiguo" in p:
            raise ValueError(f"Qual “{ref}”? " + " · ".join(p["ambiguo"]))
        if p is None:
            raise ValueError(f"Não conheço “{ref}” no CD. Escolha da lista.")
        limpos.append((p["id"], q))
    if not limpos:
        raise ValueError("Diga o material e a quantidade.")
    with pool.connection() as c:
        if not c.execute("select 1 from obras where id=%s and conta_id=%s",
                         (obra_id, conta_id)).fetchone():
            raise ValueError("Obra não encontrada.")
        pid = c.execute("""insert into obra_pedidos (conta_id, obra_id, pedido_por, urgente, prazo, recado)
                           values (%s,%s,%s,%s,%s,%s) returning id""",
                        (conta_id, obra_id, pedido_por, bool(urgente), prazo,
                         " ".join((recado or "").split())[:200])).fetchone()[0]
        for produto_id, q in limpos:
            c.execute("""insert into obra_pedido_itens (conta_id, pedido_id, produto_id, quantidade)
                         values (%s,%s,%s,%s)""", (conta_id, pid, produto_id, q))
        c.commit()
    return {"id": pid, "n": len(limpos)}


def _status(c, conta_id: int, pedido_id: int):
    r = c.execute("select status, obra_id from obra_pedidos where id=%s and conta_id=%s",
                  (pedido_id, conta_id)).fetchone()
    if not r:
        raise ValueError("Pedido não encontrado.")
    return r


def avancar(pool, conta_id: int, pedido_id: int, para: str) -> None:
    """separando ou saiu. O recebido é só pelo `receber` (que move o estoque)."""
    if para not in ("separando", "saiu"):
        raise ValueError("Passo inválido.")
    with pool.connection() as c:
        st, _ = _status(c, conta_id, pedido_id)
        if st not in _ANTES[para]:
            raise ValueError(f"O pedido já está em “{ROTULO[st]}”.")
        c.execute(f"update obra_pedidos set status=%s, {para}_em=now() where id=%s and conta_id=%s",
                  (para, pedido_id, conta_id))
        c.commit()


def cancelar(pool, conta_id: int, pedido_id: int) -> None:
    with pool.connection() as c:
        st, _ = _status(c, conta_id, pedido_id)
        if st in ("recebido", "cancelado"):
            raise ValueError("Esse pedido já foi fechado.")
        c.execute("""update obra_pedidos set status='cancelado', cancelado_em=now()
                      where id=%s and conta_id=%s""", (pedido_id, conta_id))
        c.commit()


def receber(pool, conta_id: int, pedido_id: int, *, recebido_por=None,
            quantidades: dict | None = None, foto: tuple | None = None, subir=None) -> dict:
    """O "recebi": move do CD pra obra o que chegou (`quantidades` {item_id:
    qtd}; sem ele, tudo o que foi pedido) e fecha o pedido. A foto é a prova.
    Devolve {frase, faltou}."""
    with pool.connection() as c:
        st, obra_id = _status(c, conta_id, pedido_id)
        if st not in _ANTES["recebido"]:
            raise ValueError(f"O pedido já está em “{ROTULO[st]}”.")
        itens = c.execute("""select i.id, i.produto_id, i.quantidade, p.nome, p.unidade
                               from obra_pedido_itens i
                               join catalogo_produtos p on p.id = i.produto_id and p.fornecedor_id = i.conta_id
                              where i.pedido_id=%s and i.conta_id=%s order by i.id""",
                          (pedido_id, conta_id)).fetchall()
    foto_id = None
    if foto and foto[0]:
        from . import obra_fotos as _of
        foto_id = _of.guardar(pool, conta_id, obra_id, foto[0], foto[1], legenda=f"Recebido do CD — pedido {pedido_id}",
                              origem="campo", membro_id=recebido_por, subir=subir)["id"]
    faltou, partes = [], []
    for iid, produto_id, pedida, nome, un in itens:
        q = _dec(quantidades.get(iid, quantidades.get(str(iid), pedida))) if quantidades else Decimal(pedida)
        if q < 0:
            raise ValueError("Quantidade inválida.")
        ids = []
        if q > 0:
            ids = _om.mover(pool, conta_id, acao="levei", produto_id=produto_id, quantidade=q,
                            obra_id=obra_id, por=f"pedido {pedido_id}")["mov_ids"]
            partes.append(f"{_om.rotulo(q, un)} de {nome}")
        if q < Decimal(pedida):
            faltou.append(f"{nome}: chegou {_om._qtd(q)} de {_om._qtd(pedida)}")
        with pool.connection() as c:
            c.execute("""update obra_pedido_itens set quantidade_recebida=%s, mov_ids=%s
                          where id=%s and conta_id=%s""", (q, json.dumps(ids), iid, conta_id))
            c.commit()
    with pool.connection() as c:
        c.execute("""update obra_pedidos set status='recebido', recebido_em=now(), recebido_por=%s,
                            foto_id=%s where id=%s and conta_id=%s""",
                  (recebido_por, foto_id, pedido_id, conta_id))
        c.commit()
    frase = "Recebido: " + (", ".join(partes) if partes else "nada") + "."
    if faltou:
        frase += " ⚠️ Faltou — " + "; ".join(faltou) + "."
    return {"frase": frase, "faltou": faltou}


def pedidos(pool, conta_id: int, *, obra_ids=None, dias_recebidos: int = 3) -> list[dict]:
    """Os pedidos abertos e os recebidos dos últimos dias, com os itens e o que
    tem no CD de cada um (pra quem separa ver o "tem só 8")."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select p.id, p.obra_id, o.nome, p.status, p.urgente, p.prazo, p.recado,
                          p.criado_em, coalesce(m.nome, 'Dono'), p.foto_id, p.recebido_em
                     from obra_pedidos p
                     join obras o on o.id = p.obra_id and o.conta_id = p.conta_id
                     left join membros m on m.id = p.pedido_por and m.conta_id = p.conta_id
                    where p.conta_id=%s
                      and (p.status in ('pedido','separando','saiu')
                           or (p.status='recebido' and p.recebido_em > now() - make_interval(days => %s)))
                    order by p.urgente desc, p.criado_em""", (conta_id, dias_recebidos)).fetchall()
            itens = c.execute(
                """select i.pedido_id, i.id, i.produto_id, p.nome, p.unidade, i.quantidade, i.quantidade_recebida
                     from obra_pedido_itens i
                     join catalogo_produtos p on p.id = i.produto_id and p.fornecedor_id = i.conta_id
                    where i.conta_id=%s and i.pedido_id = any(%s) order by i.id""",
                (conta_id, [r[0] for r in rows])).fetchall()
    except Exception:  # noqa: BLE001 — sem a 670
        return []
    no_cd = {r["produto_id"]: r["saldo"] for r in _om.deposito(pool, conta_id)}
    por_pedido: dict = {}
    for pid, iid, prod, nome, un, q, rec in itens:
        por_pedido.setdefault(pid, []).append({
            "id": iid, "produto_id": prod, "nome": nome, "unidade": un or "unidade",
            "qtd": Decimal(q), "rotulo": _om.rotulo(q, un), "recebida": rec,
            "texto": f"{_om.rotulo(q, un)} de {nome}",
            "rotulo_recebida": _om.rotulo(rec, un) if rec is not None else "",
            "no_cd": no_cd.get(prod, Decimal(0)),
            "falta_no_cd": no_cd.get(prod, Decimal(0)) < Decimal(q)})
    from .relogio import para_br
    out = []
    for r in rows:
        if obra_ids is not None and r[1] not in obra_ids:
            continue
        out.append({"id": r[0], "obra_id": r[1], "obra": r[2], "status": r[3], "urgente": r[4],
                    "prazo": r[5], "recado": r[6], "quando": para_br(r[7]), "quem": r[8],
                    "foto_id": r[9], "itens": por_pedido.get(r[0], []),
                    "faltou": any(i["recebida"] is not None and Decimal(i["recebida"]) < i["qtd"]
                                  for i in por_pedido.get(r[0], []))})
    return out


# ── a sobra das casas prontas ─────────────────────────────────────────────
def sobras(pool, conta_id: int) -> list[dict]:
    """As casas prontas que ainda têm material (saldo > 0) — volta pro CD?"""
    prontas = {o["id"]: o for o in _ob.listar_obras(pool, conta_id, com_custos=False)
               if o["pct"] == 100 or o["status"] in _PRONTA}
    if not prontas:
        return []
    out = []
    for oid, linhas in _om.por_obra(pool, conta_id).items():
        if oid not in prontas:
            continue
        com = [r for r in linhas if r["saldo"] > 0]
        if com:
            out.append({"obra_id": oid, "obra": prontas[oid]["nome"],
                        "itens": [{"nome": r["nome"], "rotulo": _om.rotulo(r["saldo"], r["unidade"]),
                                   "texto": f"{_om.rotulo(r['saldo'], r['unidade'])} de {r['nome']}"}
                                  for r in com]})
    return out


def devolver(pool, conta_id: int, obra_id: int, membro_id=None) -> str:
    """Traz TODO o saldo da obra de volta pro CD (transferência pareada)."""
    linhas = [r for r in _om.quadro_da_obra(pool, conta_id, obra_id) if r["saldo"] > 0]
    if not linhas:
        raise ValueError("Essa obra não tem material pra devolver.")
    with pool.connection() as c:
        for r in linhas:
            t = uuid.uuid4().hex[:12]
            _om._mov(c, conta_id, r["produto_id"], "saida", r["saldo"], obra_id=obra_id,
                     transf_id=t, motivo="devolução ao CD")
            _om._mov(c, conta_id, r["produto_id"], "entrada", r["saldo"], transf_id=t,
                     motivo="devolução ao CD")
        c.commit()
    return "Voltou pro CD: " + ", ".join(f"{_om.rotulo(r['saldo'], r['unidade'])} de {r['nome']}"
                                        for r in linhas) + "."


# ── os números do CD (a visão geral) ──────────────────────────────────────
def _precos(pool, conta_id: int) -> dict[int, int]:
    """O último preço unitário de NOTA de cada material (centavos)."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select distinct on (m.produto_id) m.produto_id, i.valor_unitario_centavos
                     from estoque_mov m join itens_lancamento i on i.id = m.item_id
                    where m.fornecedor_id=%s and i.valor_unitario_centavos > 0
                    order by m.produto_id, m.id desc""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: int(r[1]) for r in rows}


def _saida_por_dia(pool, conta_id: int, dias: int = 28) -> dict[int, Decimal]:
    """O que sai do CD por dia, pela média dos últimos `dias` (pras obras ou
    gasto direto no CD)."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select produto_id, sum(quantidade) from estoque_mov
                    where fornecedor_id=%s and obra_id is null and tipo in ('saida','perda')
                      and criado_em > now() - make_interval(days => %s)
                    group by produto_id""", (conta_id, dias)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: Decimal(str(r[1])) / dias for r in rows}


def estoque_cd(pool, conta_id: int) -> list[dict]:
    """A tabela do CD: saldo, mínimo, sai por semana, cobertura (dias) e valor."""
    precos, por_dia = _precos(pool, conta_id), _saida_por_dia(pool, conta_id)
    out = []
    for r in _om.deposito(pool, conta_id):
        dia = por_dia.get(r["produto_id"], Decimal(0))
        cobertura = int(r["saldo"] / dia) if dia > 0 and r["saldo"] > 0 else None
        preco = precos.get(r["produto_id"])
        out.append(dict(r, semana=dia * 7, rotulo_semana=_om.rotulo((dia * 7).quantize(Decimal("0.1")), r["unidade"]) if dia else "—",
                        cobertura=cobertura,
                        valor=int(r["saldo"] * preco) if preco and r["saldo"] > 0 else None))
    return out


def visao(pool, conta_id: int) -> dict:
    est = estoque_cd(pool, conta_id)
    peds = pedidos(pool, conta_id)
    abertos = [p for p in peds if p["status"] in ("pedido", "separando", "saiu")]
    return {"itens": sum(1 for r in est if r["saldo"] > 0),
            "dinheiro": sum(r["valor"] or 0 for r in est),
            "abertos": len(abertos), "urgentes": sum(1 for p in abertos if p["urgente"]),
            "abaixo": [r for r in est if r.get("abaixo")],
            "curtos": sorted([r for r in est if r["cobertura"] is not None and r["cobertura"] < 7],
                             key=lambda r: r["cobertura"]),
            "estoque": est, "pedidos": peds}


def entradas(pool, conta_id: int, limite: int = 20) -> list[dict]:
    """O que entrou no CD (nota sem obra, "chegou", devolução), o mais novo primeiro."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select m.criado_em, p.nome, p.unidade, m.quantidade, m.motivo, l.descricao
                     from estoque_mov m
                     join catalogo_produtos p on p.id = m.produto_id
                     left join lancamentos l on l.id = m.lancamento_id and l.conta_id = m.fornecedor_id
                    where m.fornecedor_id=%s and m.obra_id is null and m.tipo='entrada'
                    order by m.id desc limit %s""", (conta_id, limite)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    from .relogio import para_br
    return [{"quando": para_br(r[0]), "nome": r[1], "rotulo": _om.rotulo(r[3], r[2]),
             "origem": (r[5] or ("nota" if r[4] == "nota" else (r[4] or "entrada")))} for r in rows]
