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
