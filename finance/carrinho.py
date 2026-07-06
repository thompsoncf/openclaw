"""Carrinho avulso (compra item a item / delivery) do Zaq Fornecedor.

DIFERENTE de finance/pedidos.py: lá "pedido" = cesta da assinatura (cesta_semana).
AQUI "carrinho" = compra avulsa, o cliente escolhe produtos do catálogo item a item,
fecha e o fornecedor confirma/cobra (delivery). São dois fluxos distintos.

Estados do carrinho: rascunho -> aguardando (cliente enviou) -> confirmado
(fornecedor aceitou e cobra) -> em_entrega -> entregue (ou cancelado).

Reusa catalogo_produtos (preco_venda_centavos, saldo, disponivel). Preços são
SNAPSHOT no item. Tudo em centavos (bigint) pra não usar float em dinheiro.
"""
from __future__ import annotations

from decimal import Decimal


def _row_carrinho(c, carrinho_id: int) -> dict | None:
    """Helper: lê uma linha de carrinho e devolve dict."""
    r = c.execute(
        """select id, cliente_id, fornecedor_id, status, canal,
                  subtotal_centavos, taxa_centavos, total_centavos,
                  endereco_entrega, obs
           from carrinhos where id = %s""",
        (carrinho_id,),
    ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "cliente_id": r[1], "fornecedor_id": r[2], "status": r[3],
        "canal": r[4], "subtotal_centavos": int(r[5] or 0),
        "taxa_centavos": int(r[6] or 0), "total_centavos": int(r[7] or 0),
        "endereco_entrega": r[8], "obs": r[9],
    }


def _recalcular(c, carrinho_id: int) -> dict:
    """Recalcula subtotal (soma dos itens), aplica taxa de entrega e grava total."""
    r = c.execute(
        "select coalesce(sum(total_centavos),0) from carrinho_itens where carrinho_id = %s",
        (carrinho_id,),
    ).fetchone()
    subtotal = int(r[0] or 0)
    rf = c.execute(
        """select coalesce(co.taxa_entrega_centavos,0)
           from carrinhos ca join contas co on co.id = ca.fornecedor_id
           where ca.id = %s""",
        (carrinho_id,),
    ).fetchone()
    taxa = int(rf[0] or 0) if rf else 0
    total = subtotal + taxa
    c.execute(
        """update carrinhos set subtotal_centavos=%s, taxa_centavos=%s, total_centavos=%s
           where id=%s""",
        (subtotal, taxa, total, carrinho_id),
    )
    return {"subtotal_centavos": subtotal, "taxa_centavos": taxa, "total_centavos": total}


def obter_ou_criar(pool, cliente_id: int, fornecedor_id: int,
                   canal: str = "web") -> int:
    """Retorna o id do carrinho em RASCUNHO do cliente nesse fornecedor (o carrinho
    aberto). Se não existir, cria. Um carrinho aberto por cliente+fornecedor."""
    with pool.connection() as c:
        r = c.execute(
            """select id from carrinhos
               where cliente_id=%s and fornecedor_id=%s and status='rascunho'
               order by id desc limit 1""",
            (cliente_id, fornecedor_id),
        ).fetchone()
        if r:
            return int(r[0])
        canal = canal if canal in ("web", "whatsapp") else "web"
        rid = c.execute(
            """insert into carrinhos (cliente_id, fornecedor_id, status, canal)
               values (%s, %s, 'rascunho', %s) returning id""",
            (cliente_id, fornecedor_id, canal),
        ).fetchone()[0]
        c.commit()
        return int(rid)


def adicionar_item(pool, carrinho_id: int, produto_id: int,
                   quantidade: float = 1) -> dict:
    """Adiciona um produto ao carrinho (ou soma a quantidade se já estiver).
    Pega nome/unidade/preço do catálogo (snapshot). Só produto disponível e do
    mesmo fornecedor. Retorna {ok, totais} ou {ok:false, erro}."""
    if quantidade is None or float(quantidade) <= 0:
        return {"ok": False, "erro": "quantidade invalida"}
    with pool.connection() as c:
        car = _row_carrinho(c, carrinho_id)
        if not car:
            return {"ok": False, "erro": "carrinho nao encontrado"}
        if car["status"] != "rascunho":
            return {"ok": False, "erro": "carrinho nao esta mais em edicao"}
        p = c.execute(
            """select nome, unidade, preco_venda_centavos, disponivel
               from catalogo_produtos
               where id=%s and fornecedor_id=%s and ativo""",
            (produto_id, car["fornecedor_id"]),
        ).fetchone()
        if not p:
            return {"ok": False, "erro": "produto nao encontrado neste fornecedor"}
        nome, unidade, preco_unit, disponivel = p[0], p[1], int(p[2] or 0), p[3]
        if not disponivel:
            return {"ok": False, "erro": f"{nome} esta indisponivel"}
        ex = c.execute(
            "select id, quantidade from carrinho_itens where carrinho_id=%s and produto_id=%s",
            (carrinho_id, produto_id),
        ).fetchone()
        if ex:
            nova_qtd = Decimal(str(ex[1])) + Decimal(str(quantidade))
            total_item = int(round(float(nova_qtd) * preco_unit))
            c.execute(
                """update carrinho_itens set quantidade=%s, preco_unit_centavos=%s,
                       total_centavos=%s where id=%s""",
                (nova_qtd, preco_unit, total_item, ex[0]),
            )
        else:
            total_item = int(round(float(quantidade) * preco_unit))
            c.execute(
                """insert into carrinho_itens
                     (carrinho_id, produto_id, nome, unidade, quantidade,
                      preco_unit_centavos, total_centavos)
                   values (%s,%s,%s,%s,%s,%s,%s)""",
                (carrinho_id, produto_id, nome, unidade, Decimal(str(quantidade)),
                 preco_unit, total_item),
            )
        totais = _recalcular(c, carrinho_id)
        c.commit()
    return {"ok": True, "totais": totais}


def atualizar_quantidade(pool, carrinho_id: int, item_id: int,
                         quantidade: float) -> dict:
    """Muda a quantidade de um item. Quantidade 0 ou menos remove."""
    with pool.connection() as c:
        car = _row_carrinho(c, carrinho_id)
        if not car or car["status"] != "rascunho":
            return {"ok": False, "erro": "carrinho nao esta em edicao"}
        it = c.execute(
            "select preco_unit_centavos from carrinho_itens where id=%s and carrinho_id=%s",
            (item_id, carrinho_id),
        ).fetchone()
        if not it:
            return {"ok": False, "erro": "item nao encontrado"}
        if float(quantidade) <= 0:
            c.execute("delete from carrinho_itens where id=%s", (item_id,))
        else:
            preco_unit = int(it[0] or 0)
            total_item = int(round(float(quantidade) * preco_unit))
            c.execute(
                "update carrinho_itens set quantidade=%s, total_centavos=%s where id=%s",
                (Decimal(str(quantidade)), total_item, item_id),
            )
        totais = _recalcular(c, carrinho_id)
        c.commit()
    return {"ok": True, "totais": totais}


def remover_item(pool, carrinho_id: int, item_id: int) -> dict:
    """Remove um item do carrinho."""
    return atualizar_quantidade(pool, carrinho_id, item_id, 0)


def migrar_sessao(pool, cliente_id: int, fornecedor_id: int, itens) -> int:
    """Migra o carrinho de SESSAO (deslogado) pro carrinho do banco.
    Usado quando a pessoa enche o carrinho sem conta e depois entra/cadastra:
    nada se perde. Soma quantidades se o item ja existir. Retorna o carrinho_id."""
    cid = obter_ou_criar(pool, cliente_id, fornecedor_id)
    for it in (itens or []):
        try:
            adicionar_item(pool, cid, int(it["produto_id"]),
                           float(it.get("quantidade", 1)))
        except Exception:
            continue  # item indisponivel/removido: segue com o resto
    return cid


def registrar_pagamento(pool, carrinho_id: int, forma: str,
                        link_url: str | None = None) -> None:
    """Grava a forma de pagamento escolhida (entrega_pix / entrega_cartao /
    entrega_dinheiro / pagar_agora) e, se houver, o link de pagamento."""
    with pool.connection() as c:
        c.execute(
            """update carrinhos
               set forma_pagamento=%s,
                   pagamento_link_url=coalesce(%s, pagamento_link_url)
               where id=%s""",
            (forma, link_url, carrinho_id),
        )
        c.commit()


def marcar_pago(pool, carrinho_id: int) -> bool:
    """Marca o pedido como pago (webhook Asaas). Idempotente."""
    with pool.connection() as c:
        r = c.execute(
            "update carrinhos set pago=true, pago_em=now() "
            "where id=%s and not pago returning id",
            (carrinho_id,),
        ).fetchone()
        c.commit()
    return r is not None


def esvaziar(pool, carrinho_id: int) -> dict:
    """Remove TODOS os itens de uma vez (1 transacao, 1 roundtrip).
    Muito mais rapido que remover item a item."""
    with pool.connection() as c:
        c.execute("delete from carrinho_itens where carrinho_id = %s",
                  (carrinho_id,))
        tot = _recalcular(c, carrinho_id)
        c.commit()
    return tot


def ver(pool, carrinho_id: int) -> dict | None:
    """Retorna o carrinho com itens e totais. None se não existe."""
    with pool.connection() as c:
        car = _row_carrinho(c, carrinho_id)
        if not car:
            return None
        itens = c.execute(
            """select id, produto_id, nome, unidade, quantidade,
                      preco_unit_centavos, total_centavos
               from carrinho_itens where carrinho_id=%s order by id""",
            (carrinho_id,),
        ).fetchall()
        car["itens"] = [
            {"id": i[0], "produto_id": i[1], "nome": i[2], "unidade": i[3],
             "quantidade": float(i[4]), "preco_unit_centavos": int(i[5] or 0),
             "total_centavos": int(i[6] or 0)}
            for i in itens
        ]
        rf = c.execute(
            """select coalesce(co.pedido_minimo_centavos,0)
               from carrinhos ca join contas co on co.id=ca.fornecedor_id
               where ca.id=%s""",
            (carrinho_id,),
        ).fetchone()
        car["pedido_minimo_centavos"] = int(rf[0] or 0) if rf else 0
    return car


def fechar(pool, carrinho_id: int, endereco_entrega: str | None = None,
           obs: str | None = None, cep: str | None = None,
           bairro: str | None = None) -> dict:
    """Cliente fecha o carrinho (rascunho -> aguardando). Valida: tem itens E
    atinge o mínimo do fornecedor (mínimo vale sobre o SUBTOTAL, sem taxa).
    Retorna {ok, totais} ou {ok:false, erro, faltam_centavos}."""
    with pool.connection() as c:
        car = _row_carrinho(c, carrinho_id)
        if not car:
            return {"ok": False, "erro": "carrinho nao encontrado"}
        if car["status"] != "rascunho":
            return {"ok": False, "erro": "carrinho ja foi enviado"}
        n = c.execute(
            "select count(*) from carrinho_itens where carrinho_id=%s", (carrinho_id,)
        ).fetchone()[0]
        if not n:
            return {"ok": False, "erro": "carrinho vazio"}
        totais = _recalcular(c, carrinho_id)
        rf = c.execute(
            """select coalesce(co.pedido_minimo_centavos,0)
               from carrinhos ca join contas co on co.id=ca.fornecedor_id
               where ca.id=%s""",
            (carrinho_id,),
        ).fetchone()
        minimo = int(rf[0] or 0) if rf else 0
        if minimo and totais["subtotal_centavos"] < minimo:
            faltam = minimo - totais["subtotal_centavos"]
            return {"ok": False, "erro": "pedido abaixo do minimo",
                    "minimo_centavos": minimo, "faltam_centavos": faltam}
        try:
            c.execute(
                """update carrinhos set status='aguardando',
                       endereco_entrega=coalesce(%s, endereco_entrega),
                       obs=coalesce(%s, obs),
                       cep=coalesce(%s, cep),
                       bairro=coalesce(%s, bairro)
                   where id=%s""",
                (endereco_entrega, obs, cep, bairro, carrinho_id),
            )
        except Exception:
            # colunas cep/bairro ainda sem migracao: salva sem elas (degrada sem quebrar)
            c.rollback()
            c.execute(
                """update carrinhos set status='aguardando',
                       endereco_entrega=coalesce(%s, endereco_entrega),
                       obs=coalesce(%s, obs)
                   where id=%s""",
                (endereco_entrega, obs, carrinho_id),
            )
        c.commit()
    return {"ok": True, "totais": totais}


_ROTULO_STATUS = {
    "aguardando": "Aguardando confirmação",
    "confirmado": "Confirmado",
    "em_entrega": "Em entrega",
    "entregue": "Entregue",
    "cancelado": "Cancelado",
}


def listar_do_cliente(pool, cliente_id: int,
                      fornecedor_id: int | None = None) -> list[dict]:
    """Pedidos do CLIENTE (exclui o carrinho aberto/rascunho), mais novos primeiro.
    Traz fornecedor (nome/slug), contagem de itens e situacao de pagamento.
    Tolerante: se as colunas de pagamento (migracao 047) ainda nao existirem,
    cai pra consulta sem elas em vez de quebrar."""
    cond = "ca.cliente_id = %s and ca.status <> 'rascunho'"
    args: list = [cliente_id]
    if fornecedor_id is not None:
        cond += " and ca.fornecedor_id = %s"
        args.append(fornecedor_id)
    base = (
        "select ca.id, ca.status, ca.criado_em, ca.total_centavos, "
        "co.nome, co.fornecedor_slug, "
        "(select count(*) from carrinho_itens ci where ci.carrinho_id = ca.id){extra} "
        "from carrinhos ca join contas co on co.id = ca.fornecedor_id "
        f"where {cond} order by ca.criado_em desc limit 60"
    )
    pag = ", coalesce(ca.pago,false), ca.forma_pagamento, ca.pagamento_link_url"
    with pool.connection() as c:
        try:
            rows = c.execute(base.format(extra=pag), args).fetchall()
            tem_pag = True
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
            rows = c.execute(base.format(extra=""), args).fetchall()
            tem_pag = False
    out = []
    for r in rows:
        status = r[1]
        grupo = ("concluido" if status == "entregue"
                 else "cancelado" if status == "cancelado" else "em_aberto")
        rotulo = _ROTULO_STATUS.get(status, status or "")
        pago = bool(r[7]) if tem_pag else False
        forma = r[8] if tem_pag else None
        link = r[9] if tem_pag else None
        if pago:
            rotulo += " · Pago ✓"
        elif forma == "pagar_agora":
            rotulo += " · Aguardando pagamento"
        out.append({
            "id": r[0], "status": status, "grupo": grupo,
            "status_rotulo": rotulo, "criado_em": r[2],
            "total_centavos": int(r[3] or 0),
            "fornecedor_nome": r[4], "fornecedor_slug": r[5],
            "qtd_itens": int(r[6] or 0),
            "pago": pago, "forma_pagamento": forma,
            "pagamento_link_url": link,
        })
    return out


def pedidos_do_cliente(pool, cliente_id: int, fornecedor_id: int) -> list[dict]:
    """Formato compacto pra aba 'Meus pedidos' DENTRO da loja do fornecedor."""
    ped = listar_do_cliente(pool, cliente_id, fornecedor_id)[:5]
    out = []
    for p in ped:
        data = ""
        try:
            data = p["criado_em"].strftime("%d/%m") if p["criado_em"] else ""
        except Exception:
            data = str(p["criado_em"] or "")[:10]
        out.append({"code": str(p["id"]), "status": p["status_rotulo"],
                    "data": data, "total_centavos": p["total_centavos"]})
    return out


_FORMA_LABEL = {
    "entrega_pix": "Pix na entrega", "entrega_cartao": "Cartao na entrega",
    "entrega_dinheiro": "Dinheiro na entrega", "pagar_agora": "Pago online",
}


def listar_para_fornecedor(pool, fornecedor_id: int,
                           status: str | None = None) -> list[dict]:
    """Lista os carrinhos avulsos enviados ao fornecedor. Aguardando primeiro.
    Inclui bairro/forma de pagamento/pago quando as colunas existem (defensivo)."""
    def _monta(cols: str):
        q = (f"select {cols} from carrinhos ca "
             "join contas ct on ct.id = ca.cliente_id "
             "where ca.fornecedor_id=%s and ca.status != 'rascunho'")
        a = [fornecedor_id]
        if status:
            q += " and ca.status=%s"
            a.append(status)
        q += (" order by case ca.status when 'aguardando' then 0 "
              "when 'confirmado' then 1 when 'em_entrega' then 2 else 3 end, "
              "ca.criado_em desc")
        return q, tuple(a)

    base = ("ca.id, ca.cliente_id, ct.nome, ca.status, ca.canal, "
            "ca.subtotal_centavos, ca.taxa_centavos, ca.total_centavos, "
            "ca.endereco_entrega, ca.obs, ca.criado_em")
    extra = base + ", ca.bairro, ca.forma_pagamento, ca.pago"
    tem_extra = True
    with pool.connection() as c:
        try:
            q, a = _monta(extra)
            rows = c.execute(q, a).fetchall()
        except Exception:
            c.rollback()
            tem_extra = False
            q, a = _monta(base)
            rows = c.execute(q, a).fetchall()

    out = []
    for r in rows:
        d = {"id": r[0], "cliente_id": r[1], "cliente_nome": r[2], "status": r[3],
             "canal": r[4], "subtotal_centavos": int(r[5] or 0),
             "taxa_centavos": int(r[6] or 0), "total_centavos": int(r[7] or 0),
             "endereco_entrega": r[8], "obs": r[9], "criado_em": r[10]}
        if tem_extra:
            d["bairro"] = r[11]
            d["forma_pagamento"] = r[12]
            d["forma_label"] = _FORMA_LABEL.get(r[12], r[12] or "")
            d["pago"] = bool(r[13])
        else:
            d["bairro"] = None
            d["forma_pagamento"] = None
            d["forma_label"] = ""
            d["pago"] = False
        out.append(d)
    return out


def mudar_status(pool, fornecedor_id: int, carrinho_id: int,
                 novo_status: str) -> dict:
    """Fornecedor muda o status (confirmado/em_entrega/entregue/cancelado)."""
    validos = {"confirmado", "em_entrega", "entregue", "cancelado"}
    if novo_status not in validos:
        return {"ok": False, "erro": "status invalido"}
    with pool.connection() as c:
        r = c.execute(
            "select status from carrinhos where id=%s and fornecedor_id=%s",
            (carrinho_id, fornecedor_id),
        ).fetchone()
        if not r:
            return {"ok": False, "erro": "carrinho nao encontrado"}
        sets = ["status=%s"]
        args: list = [novo_status]
        if novo_status == "confirmado":
            sets.append("confirmado_em=now()")
        elif novo_status == "entregue":
            sets.append("entregue_em=now()")
        args.extend([carrinho_id, fornecedor_id])
        c.execute(
            f"update carrinhos set {', '.join(sets)} where id=%s and fornecedor_id=%s",
            tuple(args),
        )
        c.commit()
    return {"ok": True, "status": novo_status}


def definir_config_delivery(pool, fornecedor_id: int,
                            pedido_minimo_centavos: int | None = None,
                            taxa_entrega_centavos: int | None = None) -> dict:
    """Fornecedor define mínimo e taxa de entrega."""
    with pool.connection() as c:
        sets, args = [], []
        if pedido_minimo_centavos is not None:
            sets.append("pedido_minimo_centavos=%s")
            args.append(max(0, int(pedido_minimo_centavos)))
        if taxa_entrega_centavos is not None:
            sets.append("taxa_entrega_centavos=%s")
            args.append(max(0, int(taxa_entrega_centavos)))
        if not sets:
            return {"ok": False, "erro": "nada pra atualizar"}
        args.append(fornecedor_id)
        c.execute(f"update contas set {', '.join(sets)} where id=%s", tuple(args))
        c.commit()
    return {"ok": True}


def atualizar_endereco_entrega(pool, carrinho_id: int, cliente_id: int,
                               endereco: str, cep: str | None = None) -> dict:
    """Atualiza o endereco de entrega de um pedido, SO enquanto nao saiu pra
    entrega (status em rascunho/aguardando/confirmado). Valida que o pedido e'
    do proprio cliente. Retorna {"ok": bool, "erro"?: str}."""
    editavel = {"rascunho", "aguardando", "confirmado"}
    end = (endereco or "").strip()
    if not end:
        return {"ok": False, "erro": "endereco vazio"}
    with pool.connection() as c:
        r = c.execute(
            "select cliente_id, status from carrinhos where id=%s", (carrinho_id,)
        ).fetchone()
        if not r:
            return {"ok": False, "erro": "pedido nao encontrado"}
        if r[0] != cliente_id:
            return {"ok": False, "erro": "pedido de outro cliente"}
        if (r[1] or "") not in editavel:
            return {"ok": False, "erro": "pedido ja saiu pra entrega"}
        c.execute("update carrinhos set endereco_entrega=%s where id=%s", (end, carrinho_id))
        c.commit()
    return {"ok": True}


# ==========================================================
# FASES DO AVULSO (pipeline unificado) -- formato compativel
# com as funcoes de cesta em pedidos.py, pra a pagina mesclar.
# 'embalada_em' vem da migracao 050 (defensivo se ainda nao rodou).
# ==========================================================

def _tem_embalada_em(c) -> bool:
    try:
        c.execute("select embalada_em from carrinhos limit 0")
        return True
    except Exception:
        c.rollback()
        return False


def _itens_avulso(c, carrinho_ids: list[int]) -> dict:
    """{carrinho_id: [{produto_nome, grupo, quantidade, unidade}]}"""
    por = {i: [] for i in carrinho_ids}
    if not carrinho_ids:
        return por
    rows = c.execute(
        """select i.carrinho_id, i.nome,
                  coalesce(p.categoria, 'outros'),
                  i.quantidade, coalesce(i.unidade, 'und')
             from carrinho_itens i
             left join catalogo_produtos p on p.id = i.produto_id
            where i.carrinho_id = any(%s)
            order by p.categoria nulls last, i.nome""",
        (carrinho_ids,),
    ).fetchall()
    for cid, nome, grupo, qtd, un in rows:
        por[int(cid)].append({
            "produto_nome": nome, "grupo": grupo,
            "quantidade": float(qtd or 0), "unidade": un,
        })
    return por


def avulsos_novos(pool, fornecedor_id: int) -> list[dict]:
    """Avulsos aguardando aceite do fornecedor (fase Novos)."""
    with pool.connection() as c:
        rows = c.execute(
            """select ca.id, ca.cliente_id, ct.nome, ca.endereco_entrega,
                      ca.bairro, ca.total_centavos, ca.forma_pagamento, ca.criado_em,
                      (select count(*) from carrinho_itens i where i.carrinho_id = ca.id)
                 from carrinhos ca join contas ct on ct.id = ca.cliente_id
                where ca.fornecedor_id = %s and ca.status = 'aguardando'
                order by ca.criado_em""",
            (fornecedor_id,),
        ).fetchall()
    return [{
        "tipo": "avulso", "id": int(r[0]), "cliente_id": int(r[1]),
        "cliente_nome": r[2], "endereco": r[3] or "", "bairro": r[4] or "",
        "total_centavos": int(r[5] or 0),
        "forma_label": _FORMA_LABEL.get(r[6], r[6] or ""),
        "criado_em": r[7], "qtd_itens": int(r[8] or 0),
    } for r in rows]


def avulsos_para_embalagem(pool, fornecedor_id: int) -> list[dict]:
    """Avulsos confirmados (fase Embalagem), formato compativel com cesta."""
    with pool.connection() as c:
        tem = _tem_embalada_em(c)
        emb = "ca.embalada_em" if tem else "null::timestamptz as embalada_em"
        rows = c.execute(
            f"""select ca.id, ca.cliente_id, ct.nome, ca.endereco_entrega,
                       ca.bairro, ca.cep, ca.total_centavos, {emb}
                  from carrinhos ca join contas ct on ct.id = ca.cliente_id
                 where ca.fornecedor_id = %s and ca.status = 'confirmado'
                 order by {'ca.embalada_em nulls first, ' if tem else ''}ca.criado_em""",
            (fornecedor_id,),
        ).fetchall()
        itens = _itens_avulso(c, [int(r[0]) for r in rows])
    out = []
    for r in rows:
        embalada = r[7]
        out.append({
            "tipo": "avulso", "id": int(r[0]), "cliente_id": int(r[1]),
            "cliente_nome": r[2], "endereco": r[3] or "", "bairro": r[4] or "",
            "cep": r[5] or "", "tamanho_nome": None, "total_centavos": int(r[6] or 0),
            "data_entrega": None,
            "embalada_em": embalada.strftime("%d/%m %H:%M") if hasattr(embalada, "strftime") else None,
            "esta_embalada": embalada is not None,
            "itens": itens.get(int(r[0]), []),
        })
    return out


def avulsos_para_rotas(pool, fornecedor_id: int) -> list[dict]:
    """Avulsos a caminho/entregues (fase Rotas), formato compativel com cesta."""
    with pool.connection() as c:
        tem = _tem_embalada_em(c)
        emb = "ca.embalada_em" if tem else "null::timestamptz as embalada_em"
        rows = c.execute(
            f"""select ca.id, ca.cliente_id, ct.nome, ca.endereco_entrega,
                       ca.bairro, ca.cep, ca.total_centavos, {emb},
                       ca.entregue_em, ca.status,
                       (select count(*) from carrinho_itens i where i.carrinho_id = ca.id),
                       (select whatsapp_id from membros m
                          where m.conta_id = ca.cliente_id and m.papel = 'dono' limit 1)
                  from carrinhos ca join contas ct on ct.id = ca.cliente_id
                 where ca.fornecedor_id = %s
                   and ca.status in ('confirmado','em_entrega','entregue')
                 order by ct.nome""",
            (fornecedor_id,),
        ).fetchall()
    out = []
    for r in rows:
        embalada, entregue, status = r[7], r[8], r[9]
        esta_entregue = (status == "entregue") or (entregue is not None)
        out.append({
            "tipo": "avulso", "id": int(r[0]), "cliente_id": int(r[1]),
            "cliente_nome": r[2], "endereco": r[3] or "", "bairro": r[4] or "",
            "cep": r[5] or "", "tamanho_nome": None, "total_centavos": int(r[6] or 0),
            "data_entrega": None,
            "esta_embalada": embalada is not None,
            "esta_entregue": esta_entregue,
            "embalada_em": embalada.strftime("%d/%m %H:%M") if hasattr(embalada, "strftime") else None,
            "entregue_em": entregue.strftime("%d/%m %H:%M") if hasattr(entregue, "strftime") else None,
            "qtd_itens": int(r[10] or 0), "whatsapp": r[11],
        })
    return out


def avulsos_itens_separacao(pool, fornecedor_id: int) -> list[dict]:
    """Soma itens dos avulsos confirmados (entra na pick list agregada)."""
    with pool.connection() as c:
        rows = c.execute(
            """select i.nome, coalesce(p.categoria, 'outros') as grupo,
                      coalesce(p.unidade, i.unidade, 'und') as un,
                      sum(i.quantidade)
                 from carrinho_itens i
                 join carrinhos ca on ca.id = i.carrinho_id
                 left join catalogo_produtos p on p.id = i.produto_id
                where ca.fornecedor_id = %s and ca.status = 'confirmado'
                group by i.nome, grupo, un
                order by grupo, i.nome""",
            (fornecedor_id,),
        ).fetchall()
    return [{"produto_nome": r[0], "grupo": r[1], "unidade": r[2],
             "quantidade": float(r[3] or 0)} for r in rows]


def marcar_embalada(pool, fornecedor_id: int, carrinho_id: int) -> bool:
    """Marca um avulso como embalado (embalada_em=now()). Idempotente."""
    with pool.connection() as c:
        try:
            r = c.execute(
                """update carrinhos set embalada_em = coalesce(embalada_em, now())
                    where id=%s and fornecedor_id=%s and status='confirmado'
                    returning id""",
                (carrinho_id, fornecedor_id),
            ).fetchone()
            c.commit()
            return r is not None
        except Exception:
            c.rollback()
            return False


def desmarcar_embalada(pool, fornecedor_id: int, carrinho_id: int) -> bool:
    with pool.connection() as c:
        try:
            r = c.execute(
                "update carrinhos set embalada_em=null where id=%s and fornecedor_id=%s returning id",
                (carrinho_id, fornecedor_id),
            ).fetchone()
            c.commit()
            return r is not None
        except Exception:
            c.rollback()
            return False


def avulsos_pago_info(pool, fornecedor_id: int, ids: list[int]) -> dict:
    """{id: {"pago": bool, "forma": str}} -- enriquece a fase Rotas."""
    if not ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            """select id, coalesce(pago, false), coalesce(forma_pagamento, '')
                 from carrinhos where fornecedor_id = %s and id = any(%s)""",
            (fornecedor_id, list(ids)),
        ).fetchall()
    return {int(r[0]): {"pago": bool(r[1]), "forma": r[2]} for r in rows}


def registrar_recebimento(pool, fornecedor_id: int, carrinho_id: int,
                          forma: str, recebido: bool) -> bool:
    """Na entrega: marca entregue, grava a forma REAL do pagamento e o pago.
    forma vazia mantem a forma atual (caso "entregue mas nao recebi")."""
    with pool.connection() as c:
        try:
            sets = ["status = 'entregue'",
                    "entregue_em = coalesce(entregue_em, now())"]
            params: list = []
            if forma:
                sets.append("forma_pagamento = %s")
                params.append(forma)
            if recebido:
                sets.append("pago = true")
                sets.append("pago_em = coalesce(pago_em, now())")
            else:
                sets.append("pago = false")
            params += [carrinho_id, fornecedor_id]
            r = c.execute(
                "update carrinhos set " + ", ".join(sets) +
                " where id = %s and fornecedor_id = %s returning id",
                tuple(params),
            ).fetchone()
            c.commit()
            return r is not None
        except Exception:
            c.rollback()
            return False
