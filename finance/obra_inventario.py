"""O inventário rotativo do CD, a curva ABC e os indicadores (PR 3b do CD das obras).

Desenho aprovado pelo dono em 03/10/2026 (docs/mockups/obras_cd_almoxarifado.html,
aba "Inventário"): em vez de parar o galpão pra contar tudo, conta-se POUCO TODO
DIA, começando pelos itens classe A. A diferença vira ajuste com motivo.

A CURVA ABC é calculada, sem cadastro: pelo valor que SAIU do CD nos últimos 90
dias (quantidade × último preço de nota, `obra_pedidos._precos`). Classe A = os
que somam os primeiros 80% do valor; B até 95%; C o resto. CD novo, sem saída
nenhuma ainda: pelo valor parado nele. Sem preço de nota, o material é C.

A CONTAGEM DO DIA: cada classe tem o seu intervalo (`INTERVALO`: A toda semana,
B a cada 15 dias, C a cada 30). Vence o material com saldo no CD que passou do
intervalo desde a última contagem (nunca contado = vencido). A lista do dia leva
até `POR_DIA`, A primeiro e o mais atrasado primeiro; o que já foi contado hoje
continua nela, com o resultado.

CONTAR: o sistema é lido NA HORA, com a linha do material travada (`for update`)
— clique duplo ou duas pessoas contando o mesmo material não ajustam duas vezes:
a segunda vê o sistema já ajustado e bate. A diferença vira `ajuste` no
`estoque_mov` (com sinal, como no motor do fornecedor, `catalogo.movimentar`),
no CD (`obra_id` vazio), e o motivo é obrigatório. O valor da diferença fica
gravado pelo preço de nota DA HORA.

OS INDICADORES (últimos 30 dias): acuracidade (contagens que bateram ÷
contagens), perdas (diferença pra menos com motivo quebra/perda/furto),
divergências de fornecedor (`obra_conferencia`), pedido → recebido (média) e o
giro do material que mais pesa na curva.
"""
from __future__ import annotations

from decimal import Decimal

from . import obra_conferencia as _conf
from . import obra_material as _om
from . import obra_pedidos as _op

DIAS_ABC = 90
INTERVALO = {"A": 7, "B": 15, "C": 30}
POR_DIA = 5
MOTIVOS = {"quebra": "quebra", "perda": "perda / estragou", "furto": "furto",
           "erro": "erro de lançamento", "achado": "achou a mais"}
_PERDA = ("quebra", "perda", "furto")


def _dec(v) -> Decimal:
    """O que foi contado, como se digita no Brasil ("1.000" é mil, "1,5" um e meio)."""
    return _om.quantidade_br(v)


# ── a curva ABC ───────────────────────────────────────────────────────────
def curva(pool, conta_id: int, linhas: list[dict] | None = None) -> tuple[dict[int, str], list[int]]:
    """({produto_id: 'A'|'B'|'C'}, os produtos com valor em ordem, o maior primeiro)."""
    linhas = _om.deposito(pool, conta_id) if linhas is None else linhas
    precos = _op._precos(pool, conta_id)
    por_dia = _op._saida_por_dia(pool, conta_id, DIAS_ABC)
    base = {r["produto_id"]: por_dia.get(r["produto_id"], Decimal(0)) * precos.get(r["produto_id"], 0)
            for r in linhas}
    if not any(base.values()):                      # CD novo: pelo valor parado
        base = {r["produto_id"]: max(r["saldo"], Decimal(0)) * precos.get(r["produto_id"], 0)
                for r in linhas}
    total = sum(base.values(), Decimal(0))
    classes, ordem, acum = {}, [], Decimal(0)
    for pid in sorted(base, key=lambda p: -base[p]):
        if total <= 0 or base[pid] <= 0:
            classes[pid] = "C"
            continue
        antes = acum / total
        classes[pid] = "A" if antes < Decimal("0.8") else ("B" if antes < Decimal("0.95") else "C")
        acum += base[pid]
        ordem.append(pid)
    return classes, ordem


# ── a contagem do dia ─────────────────────────────────────────────────────
def _contagens(pool, conta_id: int, dias: int) -> list[dict]:
    """As contagens dos últimos `dias`, a mais nova primeiro."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select k.id, k.produto_id, p.nome, p.unidade, k.sistema, k.contado, k.motivo,
                          k.valor_centavos, k.contado_em, coalesce(m.nome, 'Dono')
                     from obra_contagens k
                     join catalogo_produtos p on p.id = k.produto_id and p.fornecedor_id = k.conta_id
                     left join membros m on m.id = k.contado_por and m.conta_id = k.conta_id
                    where k.conta_id=%s and k.contado_em > now() - make_interval(days => %s)
                    order by k.contado_em desc, k.id desc""", (conta_id, dias)).fetchall()
    except Exception:  # noqa: BLE001 — sem a migração
        return []
    from .relogio import dia_br, para_br
    out = []
    for kid, pid, nome, un, sis, cont, mot, valor, quando, quem in rows:
        sis, cont = Decimal(sis), Decimal(cont)
        dif = cont - sis
        out.append({"id": kid, "produto_id": pid, "nome": nome, "unidade": un or "unidade",
                    "sistema": sis, "contado": cont, "dif": dif, "motivo": mot,
                    "rotulo_motivo": MOTIVOS.get(mot or "", ""), "valor": valor,
                    "quando": para_br(quando), "dia": dia_br(quando), "quem": quem,
                    "rotulo_dif": ("ok" if dif == 0 else
                                   ("+" if dif > 0 else "−") + _om._qtd(abs(dif))
                                   + (f" · {MOTIVOS.get(mot or '', '')}" if mot else ""))})
    return out


def contagem_do_dia(pool, conta_id: int, *, linhas: list[dict] | None = None,
                    abc: dict | None = None, n: int = POR_DIA) -> dict:
    """{itens, faltam, vencidos, recentes}: a lista de hoje — o que já foi contado
    hoje (com o resultado) e os vencidos que completam `n`, A primeiro."""
    from .relogio import hoje
    linhas = _om.deposito(pool, conta_id) if linhas is None else linhas
    abc = curva(pool, conta_id, linhas)[0] if abc is None else abc
    recentes = _contagens(pool, conta_id, 40)
    dia = hoje()
    ultima: dict[int, object] = {}
    for k in recentes:
        ultima.setdefault(k["produto_id"], k["dia"])
    ja = {}
    for k in recentes:
        if k["dia"] == dia:
            ja.setdefault(k["produto_id"], k)         # a mais nova do dia
    # desfazer só a última contagem de cada material (a de cima)
    mais_nova = {}
    for k in recentes:
        mais_nova.setdefault(k["produto_id"], k["id"])
    itens = []
    for pid, k in ja.items():
        itens.append({"produto_id": pid, "nome": k["nome"], "unidade": k["unidade"],
                      "classe": abc.get(pid, "C"), "sistema": k["sistema"],
                      "contagem": dict(k, pode_desfazer=mais_nova.get(pid) == k["id"])})
    vencidos = []
    for r in linhas:
        pid = r["produto_id"]
        if pid in ja or r["saldo"] == 0:
            continue
        classe = abc.get(pid, "C")
        ult = ultima.get(pid)
        atraso = None if ult is None else (dia - ult).days
        if atraso is None or atraso >= INTERVALO[classe]:
            vencidos.append((classe, -(atraso if atraso is not None else 10**6), r["nome"], r, ult))
    vencidos.sort(key=lambda t: (t[0], t[1], t[2]))
    vagas = max(0, n - len(itens))
    for classe, _, _, r, ult in vencidos[:vagas]:
        itens.append({"produto_id": r["produto_id"], "nome": r["nome"], "unidade": r["unidade"],
                      "classe": classe, "sistema": r["saldo"], "ultima": ult, "contagem": None})
    faltam = [i for i in itens if i["contagem"] is None]
    return {"itens": itens, "faltam": len(faltam),
            "faltam_a": sum(1 for i in faltam if i["classe"] == "A"),
            "vencidos": len(vencidos), "recentes": recentes[:15]}


# ── contar e desfazer ─────────────────────────────────────────────────────
def contar(pool, conta_id: int, produto_id: int, contado, *, motivo: str | None = None,
           por=None) -> dict:
    """Conta um material do CD. Bateu: só registra. Não bateu: o motivo é
    obrigatório e a diferença vira `ajuste` no CD. Devolve {frase, dif}."""
    q = _dec(contado)
    motivo = (motivo or "").strip() or None
    preco = _op._precos(pool, conta_id).get(produto_id)
    with pool.connection() as c:
        p = c.execute("""select nome, unidade from catalogo_produtos
                          where id=%s and fornecedor_id=%s and categoria is distinct from 'ferramenta'
                          for update""",
                      (produto_id, conta_id)).fetchone()
        if not p:
            raise ValueError("Material não encontrado.")
        nome, un = p
        sistema = _om._saldo_em(c, conta_id, produto_id, None)
        dif = q - sistema
        mov_id = valor = None
        if dif != 0:
            if motivo not in MOTIVOS:
                c.rollback()
                raise ValueError(f"{nome}: o sistema diz {_om.rotulo(sistema, un)} e você contou "
                                 f"{_om._qtd(q)}. Escolha o motivo da diferença.")
            if dif > 0 and motivo in _PERDA:
                c.rollback()
                raise ValueError(f"{nome}: contou MAIS do que o sistema — o motivo é "
                                 "“achou a mais” ou “erro de lançamento”.")
            if dif < 0 and motivo == "achado":
                c.rollback()
                raise ValueError(f"{nome}: contou MENOS do que o sistema — “achou a mais” não serve.")
            mov_id = _om._mov(c, conta_id, produto_id, "ajuste", dif,
                              motivo=f"inventário: {MOTIVOS[motivo]}")
            valor = int(dif * preco) if preco else None
        else:
            motivo = None
        c.execute("""insert into obra_contagens (conta_id, produto_id, sistema, contado, motivo,
                                                 mov_id, valor_centavos, contado_por)
                     values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (conta_id, produto_id, sistema, q, motivo, mov_id, valor, por))
        c.commit()
    if dif == 0:
        return {"frase": f"{nome}: bateu ({_om.rotulo(q, un)}). ✓", "dif": dif}
    sinal = "+" if dif > 0 else "−"
    return {"frase": f"{nome}: contou {_om._qtd(q)}, o sistema dizia {_om._qtd(sistema)} — "
                     f"ajuste de {sinal}{_om._qtd(abs(dif))} ({MOTIVOS[motivo]}).", "dif": dif}


def desfazer(pool, conta_id: int, contagem_id: int) -> str:
    """Errou a contagem (digitou 360 em vez de 306): apaga a contagem e o ajuste.
    Só a de hoje e só a última daquele material — depois dela o sistema já andou."""
    from .relogio import dia_br, hoje
    with pool.connection() as c:
        r = c.execute("select produto_id from obra_contagens where id=%s and conta_id=%s",
                      (contagem_id, conta_id)).fetchone()
        if not r:
            raise ValueError("Contagem não encontrada.")
        # a mesma ordem de trava do `contar`: o material, depois a contagem
        c.execute("select 1 from catalogo_produtos where id=%s and fornecedor_id=%s for update",
                  (r[0], conta_id))
        k = c.execute("""select k.produto_id, k.mov_id, k.contado_em, p.nome
                           from obra_contagens k
                           join catalogo_produtos p on p.id = k.produto_id and p.fornecedor_id = k.conta_id
                          where k.id=%s and k.conta_id=%s for update of k""",
                      (contagem_id, conta_id)).fetchone()
        if not k:
            c.rollback()
            raise ValueError("Contagem não encontrada.")
        pid, mov_id, quando, nome = k
        if dia_br(quando) != hoje():
            c.rollback()
            raise ValueError("Só dá pra desfazer a contagem de hoje. Conte de novo — a nova corrige.")
        if c.execute("select 1 from obra_contagens where conta_id=%s and produto_id=%s and id > %s",
                     (conta_id, pid, contagem_id)).fetchone():
            c.rollback()
            raise ValueError(f"{nome} foi contado de novo depois. Conte de novo — a nova corrige.")
        c.execute("delete from obra_contagens where id=%s and conta_id=%s", (contagem_id, conta_id))
        if mov_id:
            c.execute("delete from estoque_mov where id=%s and fornecedor_id=%s and tipo='ajuste'",
                      (mov_id, conta_id))
        c.commit()
    return f"Contagem de {nome} desfeita — o estoque voltou ao que era."


# ── os indicadores ────────────────────────────────────────────────────────
def _duracao(seg: float) -> str:
    h = seg / 3600
    if h < 1:
        return f"{max(1, round(seg / 60))} min"
    if h < 48:
        return f"{round(h)} h"
    return f"{round(h / 24)} dias"


def indicadores(pool, conta_id: int, *, linhas: list[dict] | None = None, ordem: list | None = None,
                dias: int = 30) -> dict:
    """Os números de "Como o CD está indo" (últimos `dias`). `ordem` é a da
    `curva` (pra não calcular de novo)."""
    linhas = _om.deposito(pool, conta_id) if linhas is None else linhas
    ordem = curva(pool, conta_id, linhas)[1] if ordem is None else ordem
    ks = _contagens(pool, conta_id, dias)
    bateram = sum(1 for k in ks if k["dif"] == 0)
    perdas = [k for k in ks if k["dif"] < 0 and k["motivo"] in _PERDA]
    por_item: dict = {}
    for k in perdas:
        x = por_item.setdefault((k["produto_id"], k["motivo"]), {"nome": k["nome"], "unidade": k["unidade"],
                                                                 "qtd": Decimal(0), "valor": 0,
                                                                 "motivo": MOTIVOS[k["motivo"]]})
        x["qtd"] += -k["dif"]
        x["valor"] += -(k["valor"] or 0)
    maiores = sorted(por_item.values(), key=lambda x: (-x["valor"], -x["qtd"]))
    div = _conf.divergencias(pool, conta_id, dias)
    com_dif = [d for d in div if d["com_diferenca"]]
    ped = None
    try:
        with pool.connection() as c:
            r = c.execute("""select count(*), avg(extract(epoch from recebido_em - criado_em))
                               from obra_pedidos
                              where conta_id=%s and status='recebido' and recebido_em is not null
                                and recebido_em > now() - make_interval(days => %s)""",
                          (conta_id, dias)).fetchone()
        if r and r[0]:
            ped = {"pedidos": int(r[0]), "media": _duracao(float(r[1] or 0))}
    except Exception:  # noqa: BLE001 — sem a 670
        ped = None
    return {"contagens": len(ks), "bateram": bateram,
            "acuracidade": round(100 * bateram / len(ks)) if ks else None,
            "perdas_valor": sum(x["valor"] for x in maiores),
            "perdas": [dict(x, rotulo=_om.rotulo(x["qtd"], x["unidade"])) for x in maiores[:3]],
            "divergentes": sum(d["com_diferenca"] for d in com_dif),
            "fornecedor_divergente": com_dif[0]["fornecedor"] if com_dif else "",
            "pedido_recebido": ped, "giro": _giro(pool, conta_id, linhas, ordem, dias)}


def _giro(pool, conta_id: int, linhas: list[dict], ordem: list, dias: int) -> dict | None:
    """O giro do material que mais pesa na curva: o que saiu do CD no período ÷
    o estoque médio (o saldo de hoje e o do começo do período, pela média)."""
    if not ordem:
        return None
    pid = ordem[0]
    r = next((x for x in linhas if x["produto_id"] == pid), None)
    if r is None:
        return None
    try:
        with pool.connection() as c:
            saiu, liquido = c.execute(
                """select coalesce(sum(case when tipo in ('saida','perda') then quantidade else 0 end), 0),
                          coalesce(sum(case when tipo in ('entrada','ajuste') then quantidade
                                            else -quantidade end), 0)
                     from estoque_mov
                    where fornecedor_id=%s and produto_id=%s and obra_id is null
                      and criado_em > now() - make_interval(days => %s)""",
                (conta_id, pid, dias)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    saiu, liquido = Decimal(str(saiu)), Decimal(str(liquido))
    medio = (r["saldo"] + (r["saldo"] - liquido)) / 2
    if saiu <= 0 or medio <= 0:
        return {"nome": r["nome"], "vezes": None}
    return {"nome": r["nome"], "vezes": (saiu / medio).quantize(Decimal("0.1"))}
