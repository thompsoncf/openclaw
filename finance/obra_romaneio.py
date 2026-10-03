"""O romaneio do CD e o "onde" de cada material (PR 3c do CD das obras).

Desenho aprovado pelo dono em 03/10/2026 (docs/mockups/obras_cd_almoxarifado.html,
aba "Saídas e romaneio" e a coluna "Onde" do estoque): cada viagem do caminhão
é um ROMANEIO — o que vai pra cada casa. Imprime, ou manda pro motorista pelo
WhatsApp (wa.me, do WhatsApp de quem despacha — nada de canal do Zaq).

A VIAGEM junta os pedidos que saíram juntos: `despachar` marca todos como
"saiu" de uma vez (tudo ou nada) e grava o motorista e a hora. O "Saiu" de um
pedido sozinho também vira viagem (de um pedido só): toda saída tem romaneio. O
estoque NÃO se mexe aqui — continua sendo o "recebi" do mestre
(`obra_pedidos.receber`) que tira do CD e põe na casa.

O ONDE é texto livre e opcional ("Baia 1", "Prateleira A2", "Pátio"), em
`catalogo_produtos.onde`: aparece pra quem separa (no pedido), no romaneio e na
contagem do dia.
"""
from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote

from . import obra_material as _om
from . import obra_pedidos as _op

DIAS = 7


# ── o onde ────────────────────────────────────────────────────────────────
def ondes(pool, conta_id: int) -> dict[int, str]:
    """{produto_id: onde} dos materiais com lugar marcado ({} sem a migração)."""
    try:
        with pool.connection() as c:
            rows = c.execute("""select id, onde from catalogo_produtos
                                 where fornecedor_id=%s and coalesce(onde, '') <> ''""",
                             (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: r[1] for r in rows}


def salvar_onde(pool, conta_id: int, produto_id: int, onde: str | None) -> None:
    onde = " ".join((onde or "").split())[:40]
    with pool.connection() as c:
        c.execute("""update catalogo_produtos set onde=%s
                      where id=%s and fornecedor_id=%s and categoria is distinct from 'ferramenta'""",
                  (onde or None, produto_id, conta_id))
        c.commit()


# ── a viagem ──────────────────────────────────────────────────────────────
def despachar(pool, conta_id: int, pedido_ids, *, motorista: str = "", fone: str = "",
              por=None) -> dict:
    """Os pedidos marcados saem juntos numa viagem. Tudo ou nada: se um deles já
    saiu (ou foi fechado), nenhum sai. Devolve {viagem_id, frase}."""
    from .obra_acesso import fone_br
    ids = sorted({int(i) for i in (pedido_ids or [])})
    if not ids:
        raise ValueError("Marque os pedidos que vão nesta viagem.")
    motorista = " ".join((motorista or "").split())[:60]
    fone = fone_br(fone)
    if fone and len(fone) < 12:
        raise ValueError("O WhatsApp do motorista parece incompleto — DDD e número.")
    with pool.connection() as c:
        rows = c.execute("""select p.id, p.status, o.nome from obra_pedidos p
                              join obras o on o.id = p.obra_id and o.conta_id = p.conta_id
                             where p.conta_id=%s and p.id = any(%s)
                             order by p.id for update of p""", (conta_id, ids)).fetchall()
        if len(rows) != len(ids):
            c.rollback()
            raise ValueError("Pedido não encontrado.")
        presos = [f"{r[2]} ({_op.ROTULO[r[1]]})" for r in rows if r[1] not in ("pedido", "separando")]
        if presos:
            c.rollback()
            raise ValueError("Já saiu ou foi fechado: " + ", ".join(presos) + ". Nada foi despachado.")
        vid = c.execute("""insert into obra_viagens (conta_id, motorista, fone, despachou)
                           values (%s,%s,%s,%s) returning id""",
                        (conta_id, motorista, fone, por)).fetchone()[0]
        c.execute("""update obra_pedidos set status='saiu', saiu_em=now(), viagem_id=%s
                      where conta_id=%s and id = any(%s)""", (vid, conta_id, ids))
        c.commit()
    casas = ", ".join(dict.fromkeys(r[2] for r in rows))
    quem = f" com {motorista}" if motorista else ""
    return {"viagem_id": vid,
            "frase": f"Saiu{quem}: {casas}. O romaneio está em Saídas e romaneio — agora é com o “recebi” do mestre."}


def _carregar(pool, conta_id: int, *, viagem_id: int | None = None, dias: int = DIAS) -> list[dict]:
    try:
        with pool.connection() as c:
            if viagem_id is not None:
                vs = c.execute("""select id, motorista, fone, saiu_em from obra_viagens
                                   where conta_id=%s and id=%s""", (conta_id, viagem_id)).fetchall()
            else:
                vs = c.execute("""select id, motorista, fone, saiu_em from obra_viagens
                                   where conta_id=%s and saiu_em > now() - make_interval(days => %s)
                                   order by saiu_em desc, id desc""", (conta_id, dias)).fetchall()
            if not vs:
                return []
            ps = c.execute("""select p.id, p.viagem_id, p.obra_id, o.nome, p.status, p.recebido_em, p.foto_id
                                from obra_pedidos p
                                join obras o on o.id = p.obra_id and o.conta_id = p.conta_id
                               where p.conta_id=%s and p.viagem_id = any(%s)
                               order by o.nome, p.id""", (conta_id, [v[0] for v in vs])).fetchall()
            its = c.execute("""select i.pedido_id, p.nome, p.unidade, i.quantidade, i.quantidade_recebida, p.onde
                                 from obra_pedido_itens i
                                 join catalogo_produtos p on p.id = i.produto_id and p.fornecedor_id = i.conta_id
                                where i.conta_id=%s and i.pedido_id = any(%s)
                                order by i.id""", (conta_id, [p[0] for p in ps])).fetchall()
    except Exception:  # noqa: BLE001 — sem a migração
        return []
    from .relogio import para_br
    itens: dict = {}
    for pid, nome, un, q, rec, onde in its:
        itens.setdefault(pid, []).append({
            "nome": nome, "unidade": un or "unidade", "qtd": Decimal(q), "rotulo": _om.rotulo(q, un),
            "onde": onde or "", "recebida": rec,
            "texto": f"{_om.rotulo(q, un)} de {nome}"})
    pedidos: dict = {}
    for pid, vid, oid, obra, st, rec_em, foto in ps:
        pedidos.setdefault(vid, []).append({
            "id": pid, "obra_id": oid, "obra": obra, "status": st, "foto_id": foto,
            "recebido_em": para_br(rec_em) if rec_em else None, "itens": itens.get(pid, []),
            "itens_txt": " · ".join(i["texto"] for i in itens.get(pid, [])),
            "faltou": any(i["recebida"] is not None and Decimal(i["recebida"]) < i["qtd"]
                          for i in itens.get(pid, []))})
    out = []
    for vid, motorista, fone, saiu in vs:
        v = {"id": vid, "motorista": motorista or "", "fone": fone or "", "quando": para_br(saiu),
             "pedidos": pedidos.get(vid, [])}
        v["texto"] = texto(v)
        v["link"] = (f"https://wa.me/{v['fone']}?text={quote(v['texto'])}" if v["fone"]
                     else f"https://wa.me/?text={quote(v['texto'])}")
        v["recebidos"] = sum(1 for p in v["pedidos"] if p["status"] == "recebido")
        out.append(v)
    return out


def viagens(pool, conta_id: int, dias: int = DIAS) -> list[dict]:
    """As viagens dos últimos `dias`, a mais nova primeiro, com os pedidos."""
    return _carregar(pool, conta_id, dias=dias)


def romaneio(pool, conta_id: int, viagem_id: int) -> dict | None:
    """Uma viagem (de qualquer data), pro romaneio impresso."""
    vs = _carregar(pool, conta_id, viagem_id=viagem_id)
    return vs[0] if vs else None


def texto(v: dict) -> str:
    """O romaneio em texto — o que vai pro WhatsApp do motorista."""
    linhas = [f"🚚 Romaneio — viagem de {v['quando'].strftime('%d/%m %H:%M')}"
              + (f" · {v['motorista']}" if v["motorista"] else "")]
    for p in v["pedidos"]:
        linhas += ["", f"*{p['obra']}*"]
        linhas += [f"• {i['texto']}" + (f" (pegar em {i['onde']})" if i["onde"] else "") for i in p["itens"]]
    linhas += ["", "Na chegada, o mestre confirma no app da obra (Recebi + foto)."]
    return "\n".join(linhas)


# ── o que voltou pro CD ───────────────────────────────────────────────────
def devolucoes(pool, conta_id: int, dias: int = 30) -> list[dict]:
    """As sobras que voltaram das casas pro CD nos últimos `dias` (o "devolver"
    da casa pronta), por casa, a mais nova primeiro."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select m.obra_id, o.nome, p.nome, p.unidade, sum(m.quantidade), max(m.criado_em)
                     from estoque_mov m
                     join obras o on o.id = m.obra_id and o.conta_id = m.fornecedor_id
                     join catalogo_produtos p on p.id = m.produto_id and p.fornecedor_id = m.fornecedor_id
                    where m.fornecedor_id=%s and m.tipo='saida' and m.motivo='devolução ao CD'
                      and m.criado_em > now() - make_interval(days => %s)
                    group by 1, 2, 3, 4""", (conta_id, dias)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    from .relogio import para_br
    por: dict = {}
    for oid, obra, nome, un, q, quando in rows:
        d = por.setdefault(oid, {"obra_id": oid, "obra": obra, "quando": quando, "itens": []})
        d["quando"] = max(d["quando"], quando)
        d["itens"].append(f"{_om.rotulo(q, un)} de {nome}")
    out = sorted(por.values(), key=lambda d: d["quando"], reverse=True)
    for d in out:
        d["quando"] = para_br(d["quando"])
        d["itens_txt"] = " · ".join(d["itens"])
    return out
