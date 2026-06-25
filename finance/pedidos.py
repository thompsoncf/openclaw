"""finance/pedidos.py — visão de PEDIDOS do fornecedor.

Um "pedido" aqui é uma `cesta_semana` (uma assinatura × uma semana). Esta camada
NÃO cria pedidos — eles nascem do montador (Fase 4) através da janela semanal
(Fase 6). Aqui só CONSULTAMOS o que já existe, pro fornecedor enxergar e operar.

Quatro sub-abas estão planejadas no painel do fornecedor:
  1. Lista       — gestão (este arquivo, agora)
  2. Separação   — consolidado de itens da semana (futuro)
  3. Embalagem   — pack-list por cliente + etiqueta (futuro)
  4. Rotas       — agrupamento por bairro/CEP (futuro)

Fronteira (decisão do dono):
  - Este arquivo (assistente): consulta read-only de `cesta_semana`/`cesta_itens`.
  - Web (Claude Code): rota /painel/fornecedor/pedidos e template.

⚠️ PRIVACIDADE: nunca devolver `custo_unit_centavos`/`custo_total_centavos` em
respostas que vão pro cliente. O fornecedor PODE ver o próprio custo
(é dele); mas a aba Lista do PEDIDO não precisa dele — mostra só preço e
status. Esta função respeita isso (não retorna custo).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


# status que o template renderiza com cor/ícone próprio
_STATUS_VALIDOS = {
    "sugerida", "em_ajuste", "confirmada",
    "cobrada", "entregue", "cancelada",
}


def _intervalo_do_periodo(periodo: str | None) -> tuple[date | None, date | None]:
    """Traduz atalhos de período em (data_de, data_ate). Devolve (None, None) se
    o atalho for desconhecido ou ausente — o template não trava por isso.

    Atalhos suportados:
      - 'proxima'      : próxima entrega (hoje em diante)
      - 'esta_semana'  : entrega entre hoje e domingo desta semana
      - 'proxima_semana': segunda a domingo da semana que vem
      - 'mes'          : hoje + 30 dias
      - 'passadas'     : últimos 30 dias (entregas já realizadas)
    """
    if not periodo:
        return None, None
    hoje = date.today()
    # weekday: segunda=0 ... domingo=6
    dias_ate_domingo = 6 - hoje.weekday()
    if periodo == "proxima":
        return hoje, None
    if periodo == "esta_semana":
        return hoje, hoje + timedelta(days=dias_ate_domingo)
    if periodo == "proxima_semana":
        prox_seg = hoje + timedelta(days=dias_ate_domingo + 1)
        return prox_seg, prox_seg + timedelta(days=6)
    if periodo == "mes":
        return hoje, hoje + timedelta(days=30)
    if periodo == "passadas":
        return hoje - timedelta(days=30), hoje - timedelta(days=1)
    return None, None


def listar_pedidos(
    pool,
    fornecedor_id: int,
    *,
    status: str | None = None,
    periodo: str | None = None,
    data_de: date | None = None,
    data_ate: date | None = None,
    busca_cliente: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Lista pedidos (cestas da semana) deste fornecedor, do mais recente.

    Filtros (todos opcionais):
      - status: 'sugerida'/'em_ajuste'/'confirmada'/'cobrada'/'entregue'/'cancelada'.
        Aceita também 'em_aberto' (= sugerida OU em_ajuste OU confirmada) como
        atalho pra "ainda não entregou".
      - periodo: atalho — 'proxima'/'esta_semana'/'proxima_semana'/'mes'/'passadas'.
        Se passado, sobrescreve data_de/data_ate.
      - data_de / data_ate: filtra por `data_entrega` (inclusive). Use pra range
        personalizado quando 'periodo' não cobrir.
      - busca_cliente: substring case-insensitive no nome do cliente.
      - limit: teto pra não estourar tela; default 200 cobre semanas grandes.

    Retorna lista de dicts já formatados pro template (sem custo do fornecedor).
    Campos: id, cliente_id, cliente_nome, bairro, endereco, cep, fornecedor_nome,
            tamanho_nome, data_entrega, status, qtd_itens, preco_reais,
            status_pagamento, assinatura_id, criada_em.
    """
    # atalho de período traduz pra data_de/data_ate (sem mexer no SQL)
    if periodo:
        data_de, data_ate = _intervalo_do_periodo(periodo)
    where = ["cs.fornecedor_id = %s"]
    params: list[Any] = [fornecedor_id]

    if status:
        if status == "em_aberto":
            where.append("cs.status in ('sugerida','em_ajuste','confirmada')")
        elif status in _STATUS_VALIDOS:
            where.append("cs.status = %s")
            params.append(status)
        # se vier status inválido, simplesmente ignora (não trava o painel)

    if data_de is not None:
        where.append("cs.data_entrega >= %s")
        params.append(data_de)
    if data_ate is not None:
        where.append("cs.data_entrega <= %s")
        params.append(data_ate)
    if busca_cliente:
        where.append("c.nome ilike %s")
        params.append(f"%{busca_cliente}%")

    sql = f"""
        select
            cs.id,
            cs.cliente_id,
            coalesce(c.nome, '(cliente '||cs.cliente_id||')')          as cliente_nome,
            c.endereco                                                  as endereco,
            c.cep                                                       as cep,
            coalesce(f.nome, '')                                        as fornecedor_nome,
            coalesce(ct.nome, '?')                                      as tamanho_nome,
            cs.data_entrega,
            cs.status,
            (select count(*) from cesta_itens ci where ci.cesta_id = cs.id) as qtd_itens,
            cs.preco_centavos,
            cs.assinatura_id,
            a.status                                                    as status_assinatura,
            cs.criada_em
        from cesta_semana cs
        left join contas c            on c.id = cs.cliente_id
        left join contas f            on f.id = cs.fornecedor_id
        left join assinaturas a       on a.id = cs.assinatura_id
        left join cesta_tamanhos ct   on ct.id = a.tamanho_id
        where {' and '.join(where)}
        order by cs.data_entrega desc nulls last, cs.id desc
        limit %s
    """
    params.append(int(limit))

    out: list[dict[str, Any]] = []
    with pool.connection() as c:
        for row in c.execute(sql, params).fetchall():
            (cesta_id, cli_id, cli_nome, endereco, cep, forn_nome, tam_nome,
             data_ent, st, qtd, preco_c, assin_id, st_assin, criada) = row
            out.append({
                "id": int(cesta_id),
                "cliente_id": int(cli_id),
                "cliente_nome": cli_nome,
                "bairro": _extrair_bairro(endereco),
                "endereco": endereco,
                "cep": cep,
                "fornecedor_nome": forn_nome,
                "tamanho_nome": tam_nome,
                "data_entrega": data_ent.isoformat() if data_ent else None,
                "status": st,
                "qtd_itens": int(qtd or 0),
                "preco_reais": round(int(preco_c or 0) / 100, 2),
                "status_pagamento": _status_pagamento(st, st_assin),
                "assinatura_id": int(assin_id),
                "criada_em": criada.isoformat() if isinstance(criada, datetime) else str(criada),
            })
    return out


def contar_por_status(pool, fornecedor_id: int) -> dict[str, int]:
    """Conta pedidos por status (pros 'chips' resumo no topo da aba Lista).

    Considera só pedidos com data_entrega >= hoje OU sem data (em rascunho).
    Pedidos antigos entregues/cancelados não inflam a contagem.
    """
    sql = """
        select status, count(*)
          from cesta_semana
         where fornecedor_id = %s
           and (data_entrega is null or data_entrega >= current_date - interval '60 days')
         group by status
    """
    out: dict[str, int] = {s: 0 for s in _STATUS_VALIDOS}
    with pool.connection() as c:
        for st, n in c.execute(sql, (fornecedor_id,)).fetchall():
            if st in out:
                out[st] = int(n)
    # totais úteis pra UI
    out["em_aberto"] = out["sugerida"] + out["em_ajuste"] + out["confirmada"]
    out["total"] = sum(out[s] for s in _STATUS_VALIDOS)
    return out


def detalhe_pedido(pool, fornecedor_id: int, cesta_id: int) -> dict[str, Any] | None:
    """Carrega um pedido inteiro pra tela de detalhe (cabeçalho + itens).

    Valida que a cesta é deste fornecedor (segurança multi-tenant).
    Retorna None se não existir ou não for dele.
    """
    with pool.connection() as c:
        cab = c.execute(
            """select cs.id, cs.cliente_id,
                      coalesce(cli.nome, '(cliente '||cs.cliente_id||')'),
                      cli.endereco, cli.cep,
                      coalesce(ct.nome, '?'), cs.data_entrega, cs.status,
                      cs.preco_centavos, a.status, cs.assinatura_id, cs.criada_em
                 from cesta_semana cs
                 left join contas cli on cli.id = cs.cliente_id
                 left join assinaturas a on a.id = cs.assinatura_id
                 left join cesta_tamanhos ct on ct.id = a.tamanho_id
                where cs.id = %s and cs.fornecedor_id = %s""",
            (cesta_id, fornecedor_id),
        ).fetchone()
        if cab is None:
            return None
        (cid, cli_id, cli_nome, endereco, cep, tam_nome, data_ent, st,
         preco_c, st_assin, assin_id, criada) = cab

        itens_raw = c.execute(
            """select ci.id, ci.produto_id,
                      coalesce(p.nome, '(produto '||ci.produto_id||')'),
                      ci.grupo, ci.quantidade, p.unidade,
                      ci.preco_unit_centavos
                 from cesta_itens ci
                 left join catalogo_produtos p on p.id = ci.produto_id
                where ci.cesta_id = %s
                order by ci.grupo nulls last, p.nome""",
            (cesta_id,),
        ).fetchall()

    itens: list[dict[str, Any]] = []
    for iid, prod_id, prod_nome, grupo, qtd, unidade, preco_unit_c in itens_raw:
        itens.append({
            "id": int(iid),
            "produto_id": int(prod_id),
            "produto_nome": prod_nome,
            "grupo": grupo or "outros",
            "quantidade": float(qtd or 0),
            "unidade": unidade or "und",
            "preco_unit_reais": round(int(preco_unit_c or 0) / 100, 2),
        })

    return {
        "id": int(cid),
        "cliente_id": int(cli_id),
        "cliente_nome": cli_nome,
        "endereco": endereco,
        "cep": cep,
        "bairro": _extrair_bairro(endereco),
        "tamanho_nome": tam_nome,
        "data_entrega": data_ent.isoformat() if data_ent else None,
        "status": st,
        "preco_reais": round(int(preco_c or 0) / 100, 2),
        "status_pagamento": _status_pagamento(st, st_assin),
        "assinatura_id": int(assin_id),
        "criada_em": criada.isoformat() if isinstance(criada, datetime) else str(criada),
        "itens": itens,
        "qtd_itens": len(itens),
    }


def consolidar_separacao(
    pool,
    fornecedor_id: int,
    *,
    periodo: str = "proxima_semana",
    data_de: date | None = None,
    data_ate: date | None = None,
) -> dict[str, Any]:
    """Pick list — o que o fornecedor precisa SEPARAR/COMPRAR pra entregar.

    Soma quantidades de cada produto nas cestas CONFIRMADAS do período, agrupadas
    por grupo (fruta/legume/verdura/tempero). Cestas em 'sugerida'/'em_ajuste'
    NÃO entram na soma (cliente ainda pode mexer); são contadas separadamente
    pra UI alertar "X cestas ainda em ajuste — pode mudar".

    Cancelada/entregue/cobrada não interessam aqui (já saíram do "vou entregar").

    periodo: atalho — default 'proxima_semana' (seg a dom da próxima semana).
             pode ser 'esta_semana'/'proxima'/'mes'. Se data_de/data_ate forem
             passados, sobrescrevem o periodo.

    Retorna:
      {
        "periodo": "proxima_semana",
        "data_de": "2026-06-29", "data_ate": "2026-07-05",
        "qtd_cestas_confirmadas": 5,
        "qtd_cestas_em_ajuste": 2,        # alerta — podem virar confirmadas
        "qtd_cestas_total": 7,
        "valor_total_reais": 600.0,        # soma do preço das confirmadas
        "grupos": [
          {"grupo": "fruta", "itens": [
              {"produto_nome": "Banana prata", "unidade": "kg", "quantidade": 4.5,
               "categoria": "fruta", "saldo_atual": 30.0, "suficiente": True}, ...]},
          ...
        ],
        "total_itens_distintos": 8,
      }
    """
    if periodo and (data_de is None and data_ate is None):
        data_de, data_ate = _intervalo_do_periodo(periodo)

    where_data = ""
    params_data: list[Any] = []
    if data_de is not None:
        where_data += " and cs.data_entrega >= %s"
        params_data.append(data_de)
    if data_ate is not None:
        where_data += " and cs.data_entrega <= %s"
        params_data.append(data_ate)

    with pool.connection() as c:
        # 1) cabeçalho — quantas cestas confirmadas vs em ajuste; valor total
        cab = c.execute(
            f"""select
                  count(*) filter (where cs.status = 'confirmada')                 as conf,
                  count(*) filter (where cs.status in ('sugerida','em_ajuste'))    as ajuste,
                  count(*) filter (where cs.status in ('confirmada','sugerida','em_ajuste')) as total,
                  coalesce(sum(cs.preco_centavos) filter (where cs.status='confirmada'), 0) as valor_c
                from cesta_semana cs
                where cs.fornecedor_id = %s{where_data}""",
            (fornecedor_id, *params_data),
        ).fetchone()
        qtd_conf, qtd_ajuste, qtd_total, valor_c = cab

        # 2) consolidado — só status='confirmada' (rigoroso)
        sql_itens = f"""
            select
              ci.produto_id,
              coalesce(p.nome, '(produto ' || ci.produto_id || ')')  as produto_nome,
              coalesce(ci.grupo, p.categoria, 'outros')               as grupo,
              coalesce(p.unidade, 'und')                              as unidade,
              p.categoria,
              coalesce(p.saldo, 0)                                    as saldo_atual,
              sum(ci.quantidade)                                      as qtd_total
            from cesta_itens ci
            join cesta_semana cs on cs.id = ci.cesta_id
            left join catalogo_produtos p on p.id = ci.produto_id
            where cs.fornecedor_id = %s
              and cs.status = 'confirmada'
              {where_data}
            group by ci.produto_id, p.nome, ci.grupo, p.categoria, p.unidade, p.saldo
            order by grupo, produto_nome
        """
        rows = c.execute(sql_itens, (fornecedor_id, *params_data)).fetchall()

    # agrupa por grupo pro template
    grupos_dict: dict[str, list[dict[str, Any]]] = {}
    total_itens = 0
    for prod_id, prod_nome, grupo, unidade, categoria, saldo, qtd in rows:
        qtd_f = float(qtd or 0)
        saldo_f = float(saldo or 0)
        grupos_dict.setdefault(grupo, []).append({
            "produto_id": int(prod_id),
            "produto_nome": prod_nome,
            "grupo": grupo,
            "unidade": unidade,
            "categoria": categoria or grupo,
            "quantidade": round(qtd_f, 3),
            "saldo_atual": round(saldo_f, 3),
            "suficiente": saldo_f >= qtd_f,
            "falta": round(max(0.0, qtd_f - saldo_f), 3),
            "sobra_apos": round(saldo_f - qtd_f, 3),
        })
        total_itens += 1

    # ordem natural dos grupos (fruta/legume/verdura/tempero/outros)
    ordem = ["fruta", "legume", "verdura", "tempero", "outros"]
    grupos_lista = []
    for nome in ordem:
        if nome in grupos_dict:
            grupos_lista.append({"grupo": nome, "itens": grupos_dict.pop(nome)})
    # grupos não previstos vão no fim
    for nome, itens in grupos_dict.items():
        grupos_lista.append({"grupo": nome, "itens": itens})

    return {
        "periodo": periodo,
        "data_de": data_de.isoformat() if data_de else None,
        "data_ate": data_ate.isoformat() if data_ate else None,
        "qtd_cestas_confirmadas": int(qtd_conf or 0),
        "qtd_cestas_em_ajuste": int(qtd_ajuste or 0),
        "qtd_cestas_total": int(qtd_total or 0),
        "valor_total_reais": round(int(valor_c or 0) / 100, 2),
        "grupos": grupos_lista,
        "total_itens_distintos": total_itens,
    }


def listar_para_embalagem(
    pool,
    fornecedor_id: int,
    *,
    periodo: str = "proxima_semana",
    incluir_embaladas: bool = True,
) -> dict[str, Any]:
    """Pack list — cestas individuais pro fornecedor montar uma por uma.

    Diferente da Separação (que CONSOLIDA itens), aqui é por CESTA. Cada cesta
    vira um cartão com cliente, endereço, lista de itens, e botão "embalada".

    Só inclui cestas em status 'confirmada' (em sugerida/ajuste o cliente
    ainda pode mexer — não vale embalar).

    Retorna {
      periodo, data_de, data_ate,
      qtd_total, qtd_embaladas, qtd_falta_embalar,
      cestas: [
        {id, cliente_nome, endereco, bairro, cep, tamanho_nome, data_entrega,
         embalada_em (None ou ISO), itens:[{produto_nome, grupo, quantidade, unidade}]}
      ]
    }
    """
    data_de, data_ate = _intervalo_do_periodo(periodo)

    where_data = ""
    params_data: list[Any] = []
    if data_de is not None:
        where_data += " and cs.data_entrega >= %s"
        params_data.append(data_de)
    if data_ate is not None:
        where_data += " and cs.data_entrega <= %s"
        params_data.append(data_ate)

    where_emb = "" if incluir_embaladas else " and cs.embalada_em is null"

    with pool.connection() as c:
        # 1) busca as cestas confirmadas do período
        cestas_raw = c.execute(
            f"""select cs.id, cs.cliente_id,
                       coalesce(cli.nome, '(cliente '||cs.cliente_id||')'),
                       cli.endereco, cli.cep,
                       coalesce(ct.nome, '?'),
                       cs.data_entrega, cs.embalada_em
                 from cesta_semana cs
                 left join contas cli on cli.id = cs.cliente_id
                 left join assinaturas a on a.id = cs.assinatura_id
                 left join cesta_tamanhos ct on ct.id = a.tamanho_id
                where cs.fornecedor_id = %s
                  and cs.status = 'confirmada'
                  {where_data}{where_emb}
                order by cs.embalada_em nulls first, cs.data_entrega, cli.nome""",
            (fornecedor_id, *params_data),
        ).fetchall()

        # 2) busca itens de todas de uma vez (evita N+1)
        ids = [int(r[0]) for r in cestas_raw]
        itens_por_cesta: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
        if ids:
            rows = c.execute(
                """select ci.cesta_id,
                          coalesce(p.nome, '(produto '||ci.produto_id||')'),
                          coalesce(ci.grupo, p.categoria, 'outros'),
                          ci.quantidade,
                          coalesce(p.unidade, 'und')
                     from cesta_itens ci
                     left join catalogo_produtos p on p.id = ci.produto_id
                    where ci.cesta_id = any(%s)
                    order by ci.grupo nulls last, p.nome""",
                (ids,),
            ).fetchall()
            for cid, nome, grupo, qtd, un in rows:
                itens_por_cesta[int(cid)].append({
                    "produto_nome": nome,
                    "grupo": grupo,
                    "quantidade": float(qtd or 0),
                    "unidade": un,
                })

    cestas = []
    qtd_embaladas = 0
    for (cid, cli_id, cli_nome, end, cep, tam, data_ent, embalada) in cestas_raw:
        if embalada is not None:
            qtd_embaladas += 1
        cestas.append({
            "id": int(cid),
            "cliente_id": int(cli_id),
            "cliente_nome": cli_nome,
            "endereco": end,
            "bairro": _extrair_bairro(end),
            "cep": cep,
            "tamanho_nome": tam,
            "data_entrega": data_ent.isoformat() if data_ent else None,
            "embalada_em": (
                embalada.strftime("%d/%m %H:%M")
                if isinstance(embalada, datetime) else None
            ),
            "esta_embalada": embalada is not None,
            "itens": itens_por_cesta.get(int(cid), []),
        })

    return {
        "periodo": periodo,
        "data_de": data_de.isoformat() if data_de else None,
        "data_ate": data_ate.isoformat() if data_ate else None,
        "qtd_total": len(cestas),
        "qtd_embaladas": qtd_embaladas,
        "qtd_falta_embalar": len(cestas) - qtd_embaladas,
        "cestas": cestas,
    }


def marcar_embalada(pool, fornecedor_id: int, cesta_id: int) -> bool:
    """Marca uma cesta como embalada (gravando `embalada_em = now()`).

    Idempotente: re-marcar não atualiza o timestamp (preserva o original).
    Valida que a cesta é do fornecedor (segurança multi-tenant).
    Retorna True se atualizou, False se a cesta não existe ou não é dele.
    """
    with pool.connection() as c:
        r = c.execute(
            """update cesta_semana
                  set embalada_em = coalesce(embalada_em, now())
                where id = %s and fornecedor_id = %s
                  and status = 'confirmada'
                returning id""",
            (cesta_id, fornecedor_id),
        ).fetchone()
        c.commit()
    return r is not None


def desmarcar_embalada(pool, fornecedor_id: int, cesta_id: int) -> bool:
    """Desfaz a marcação de embalada (volta `embalada_em` pra NULL).

    Útil se o fornecedor marcou por engano. Valida fornecedor.
    """
    with pool.connection() as c:
        r = c.execute(
            """update cesta_semana
                  set embalada_em = null
                where id = %s and fornecedor_id = %s
                returning id""",
            (cesta_id, fornecedor_id),
        ).fetchone()
        c.commit()
    return r is not None


def dados_etiqueta(pool, fornecedor_id: int, cesta_id: int) -> dict[str, Any] | None:
    """Dados pra renderizar a etiqueta imprimível de UMA cesta.

    Mesma estrutura básica que `detalhe_pedido`, mas otimizada pra impressão
    (sem itens detalhados — etiqueta é cliente + endereço + tamanho + data).
    Valida fornecedor.
    """
    with pool.connection() as c:
        row = c.execute(
            """select cs.id,
                      coalesce(cli.nome, '(cliente '||cs.cliente_id||')'),
                      cli.endereco, cli.cep,
                      coalesce(ct.nome, '?'),
                      cs.data_entrega,
                      coalesce(f.nome, '') as fornecedor_nome,
                      f.fornecedor_slug
                 from cesta_semana cs
                 left join contas cli on cli.id = cs.cliente_id
                 left join contas f on f.id = cs.fornecedor_id
                 left join assinaturas a on a.id = cs.assinatura_id
                 left join cesta_tamanhos ct on ct.id = a.tamanho_id
                where cs.id = %s and cs.fornecedor_id = %s""",
            (cesta_id, fornecedor_id),
        ).fetchone()
    if row is None:
        return None
    (cid, cli_nome, end, cep, tam, data_ent, forn_nome, forn_slug) = row
    return {
        "id": int(cid),
        "cliente_nome": cli_nome,
        "endereco": end or "",
        "cep": cep or "",
        "bairro": _extrair_bairro(end),
        "tamanho_nome": tam,
        "data_entrega": data_ent.isoformat() if data_ent else None,
        "fornecedor_nome": forn_nome,
        "fornecedor_slug": forn_slug,
    }


# ---------- helpers privados ----------

def listar_para_rotas(
    pool,
    fornecedor_id: int,
    *,
    periodo: str = "proxima_semana",
    incluir_entregues: bool = True,
) -> dict[str, Any]:
    """Rotas — cestas agrupadas por bairro pra entregar.

    TODAS as confirmadas entram (embalada ou não). As que ainda não estão
    embaladas vêm marcadas pra alertar o fornecedor ("⚠ não embalada").

    Grupos formados pelo bairro extraído do endereço; clientes sem bairro
    reconhecível vão num grupo "(sem bairro)" no fim.

    Dentro de cada grupo, ordem por embalada (não-embaladas primeiro — alerta)
    e depois por nome do cliente.

    Retorna {
      periodo, data_de, data_ate,
      qtd_total, qtd_entregues, qtd_pendentes, qtd_nao_embaladas,
      grupos: [
        {bairro, qtd, cestas:[{id, cliente_nome, endereco, bairro, cep,
                                tamanho_nome, data_entrega,
                                esta_embalada, esta_entregue,
                                embalada_em, entregue_em,
                                qtd_itens, whatsapp}]}
      ]
    }
    """
    data_de, data_ate = _intervalo_do_periodo(periodo)

    where_data = ""
    params_data: list[Any] = []
    if data_de is not None:
        where_data += " and cs.data_entrega >= %s"
        params_data.append(data_de)
    if data_ate is not None:
        where_data += " and cs.data_entrega <= %s"
        params_data.append(data_ate)

    where_entr = "" if incluir_entregues else " and cs.entregue_em is null"

    with pool.connection() as c:
        rows = c.execute(
            f"""select cs.id, cs.cliente_id,
                       coalesce(cli.nome, '(cliente '||cs.cliente_id||')'),
                       cli.endereco, cli.cep,
                       coalesce(ct.nome, '?'),
                       cs.data_entrega, cs.embalada_em, cs.entregue_em,
                       (select count(*) from cesta_itens ci where ci.cesta_id = cs.id),
                       (select whatsapp_id from membros m
                          where m.conta_id = cs.cliente_id and m.papel = 'dono'
                          limit 1)
                 from cesta_semana cs
                 left join contas cli on cli.id = cs.cliente_id
                 left join assinaturas a on a.id = cs.assinatura_id
                 left join cesta_tamanhos ct on ct.id = a.tamanho_id
                where cs.fornecedor_id = %s
                  and cs.status in ('confirmada','cobrada','entregue')
                  {where_data}{where_entr}
                order by cs.data_entrega, cli.nome""",
            (fornecedor_id, *params_data),
        ).fetchall()

    grupos_dict: dict[str, list[dict[str, Any]]] = {}
    qtd_total = 0
    qtd_entregues = 0
    qtd_nao_embaladas = 0
    for (cid, cli_id, cli_nome, end, cep, tam, data_ent,
         embalada, entregue, qtd_itens, wpp) in rows:
        bairro = _extrair_bairro(end) or "(sem bairro)"
        esta_embalada = embalada is not None
        esta_entregue = entregue is not None
        qtd_total += 1
        if esta_entregue:
            qtd_entregues += 1
        if not esta_embalada and not esta_entregue:
            qtd_nao_embaladas += 1

        grupos_dict.setdefault(bairro, []).append({
            "id": int(cid),
            "cliente_id": int(cli_id),
            "cliente_nome": cli_nome,
            "endereco": end or "",
            "bairro": bairro,
            "cep": cep or "",
            "tamanho_nome": tam,
            "data_entrega": data_ent.isoformat() if data_ent else None,
            "esta_embalada": esta_embalada,
            "esta_entregue": esta_entregue,
            "embalada_em": (
                embalada.strftime("%d/%m %H:%M")
                if isinstance(embalada, datetime) else None
            ),
            "entregue_em": (
                entregue.strftime("%d/%m %H:%M")
                if isinstance(entregue, datetime) else None
            ),
            "qtd_itens": int(qtd_itens or 0),
            "whatsapp": wpp,
        })

    # ordena dentro de cada grupo: pendentes primeiro, embaladas depois, entregues no fim
    def _ordem(c: dict[str, Any]) -> tuple[int, str]:
        if c["esta_entregue"]:
            return (2, c["cliente_nome"])
        if c["esta_embalada"]:
            return (1, c["cliente_nome"])
        return (0, c["cliente_nome"])

    grupos_lista = []
    # bairros conhecidos primeiro (alfabético), "(sem bairro)" no fim
    bairros = sorted(k for k in grupos_dict.keys() if k != "(sem bairro)")
    if "(sem bairro)" in grupos_dict:
        bairros.append("(sem bairro)")
    for b in bairros:
        cestas_b = sorted(grupos_dict[b], key=_ordem)
        grupos_lista.append({
            "bairro": b,
            "qtd": len(cestas_b),
            "qtd_pendentes": sum(1 for x in cestas_b if not x["esta_entregue"]),
            "cestas": cestas_b,
        })

    return {
        "periodo": periodo,
        "data_de": data_de.isoformat() if data_de else None,
        "data_ate": data_ate.isoformat() if data_ate else None,
        "qtd_total": qtd_total,
        "qtd_entregues": qtd_entregues,
        "qtd_pendentes": qtd_total - qtd_entregues,
        "qtd_nao_embaladas": qtd_nao_embaladas,
        "qtd_grupos": len(grupos_lista),
        "grupos": grupos_lista,
    }


def marcar_entregue(pool, fornecedor_id: int, cesta_id: int) -> bool:
    """Marca uma cesta como entregue: status='entregue' + entregue_em=now().

    Idempotente. Só atualiza se a cesta é do fornecedor E está em
    confirmada/cobrada/entregue (não permite entregar uma cancelada).
    """
    with pool.connection() as c:
        r = c.execute(
            """update cesta_semana
                  set status = 'entregue',
                      entregue_em = coalesce(entregue_em, now())
                where id = %s and fornecedor_id = %s
                  and status in ('confirmada','cobrada','entregue')
                returning id""",
            (cesta_id, fornecedor_id),
        ).fetchone()
        c.commit()
    return r is not None


def desmarcar_entregue(pool, fornecedor_id: int, cesta_id: int) -> bool:
    """Desfaz a entrega: volta status pra 'confirmada' e entregue_em=NULL.

    Útil pra correção de erro humano (marcou cliente errado, etc).
    """
    with pool.connection() as c:
        r = c.execute(
            """update cesta_semana
                  set status = 'confirmada',
                      entregue_em = null
                where id = %s and fornecedor_id = %s
                  and status = 'entregue'
                returning id""",
            (cesta_id, fornecedor_id),
        ).fetchone()
        c.commit()
    return r is not None


# ---------- helpers privados ----------

def _status_pagamento(status_cesta: str, status_assinatura: str | None) -> str:
    """Resume o "estado do dinheiro" do pedido pro fornecedor.

    Regra prática (não enche de status — só 4 buckets úteis pra UI):
      - cobrada/entregue          -> 'pago'         (já tem dinheiro do Asaas)
      - cancelada                  -> 'cancelado'    (não vai mais)
      - assinatura inadimplente    -> 'atrasado'
      - resto (sugerida/em_ajuste/confirmada com assinatura ok) -> 'aguardando'
    """
    if status_cesta == "cancelada":
        return "cancelado"
    if status_cesta in ("cobrada", "entregue"):
        return "pago"
    if status_assinatura == "inadimplente":
        return "atrasado"
    return "aguardando"


def _extrair_bairro(endereco: str | None) -> str:
    """Heurística leve: tenta achar 'bairro' num endereço livre.

    Endereços vêm como texto livre ('Rua X, 123, Bairro Y, Cidade - UF, CEP').
    Pra aba Lista isso basta — a aba Rotas (futura) vai usar CEP de verdade.
    Sem bairro reconhecível, retorna ''.
    """
    if not endereco:
        return ""
    partes = [p.strip() for p in endereco.split(",") if p.strip()]
    # tenta encontrar a parte que mais parece um bairro (não é número, não é UF)
    for parte in partes:
        baixa = parte.lower()
        if any(t in baixa for t in ("bairro", "centro", "zona")):
            return parte
    # fallback: 3ª parte costuma ser o bairro em endereços brasileiros padrão
    if len(partes) >= 3:
        return partes[2]
    return ""
