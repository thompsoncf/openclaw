"""Manutencao do banco de precos (escala).

(A) arquivar_antigos: move precos com mais de N meses pra tabela historico, pra
    a tabela operacional (precos_observados) nao inchar. O comparador so' usa os
    ultimos 90 dias mesmo; o historico fica guardado pra analises futuras.

(B) recalcular_vigentes: a partir das observacoes dos ultimos 90 dias, calcula a
    MEDIANA do preco por (produto_norm + loja + regiao + unidade) e grava em
    precos_vigentes (1 linha por produto+loja). Mediana ignora outlier (erro de
    digitacao). O comparador le' daqui: rapido e robusto.

Uso (no Render Shell):
    python -m scripts.manutencao_precos                 # roda A e B
    python -m scripts.manutencao_precos --so-arquivar
    python -m scripts.manutencao_precos --so-vigentes
"""
from __future__ import annotations

import logging

_log = logging.getLogger("openclaw.precos")


def arquivar_antigos(pool, meses: int = 6) -> int:
    """Move precos com mais de `meses` pra precos_observados_historico.
    Retorna quantos foram arquivados. Seguro: copia e so' depois apaga."""
    with pool.connection() as conn:
        # copia os antigos pro historico (ignora os que ja' estao la')
        conn.execute(
            """insert into precos_observados_historico
                 (id, descricao_norm, descricao_original, valor_unitario_centavos,
                  mercado, regiao, gtin, data_compra, conta_id, item_id, fonte,
                  loja_id, unidade, criado_em)
               select id, descricao_norm, descricao_original, valor_unitario_centavos,
                      mercado, regiao, gtin, data_compra, conta_id, item_id, fonte,
                      loja_id, unidade, criado_em
                 from precos_observados
                where data_compra < (now() - (%s || ' months')::interval)::date
               on conflict (id) do nothing""",
            (meses,),
        )
        # apaga da operacional o que foi arquivado
        cur = conn.execute(
            """delete from precos_observados
                where data_compra < (now() - (%s || ' months')::interval)::date""",
            (meses,),
        )
        n = cur.rowcount
        conn.commit()
    _log.info("arquivar_antigos: %s precos movidos pro historico (> %s meses)", n, meses)
    return n


def recalcular_vigentes(pool, dias: int = 90) -> int:
    """Recalcula precos_vigentes a partir das observacoes dos ultimos `dias`.
    Mediana por (produto + loja + regiao + unidade), onde produto = coalesce(gtin, descricao_norm).
    Assim mesmo GTIN com nome diferente fica junto (agregado), mas GTINs diferentes ficam separados.
    Retorna nº de linhas vigentes. Substitui o conteudo (recalculo completo)."""
    with pool.connection() as conn:
        # mediana no proprio Postgres com percentile_cont(0.5)
        # agrupa por PRODUTO (gtin se existe, senao nome) + loja+regiao+unidade
        conn.execute("delete from precos_vigentes")
        conn.execute(
            """insert into precos_vigentes
                 (descricao_norm, descricao_exemplo, loja_id, mercado, regiao,
                  unidade, gtin, valor_mediana_centavos, n_observacoes, data_mais_recente)
               select
                  (array_agg(po.descricao_norm order by po.data_compra desc))[1] as descricao_norm,
                  (array_agg(po.descricao_original order by po.data_compra desc))[1] as exemplo,
                  po.loja_id,
                  (array_agg(po.mercado order by po.data_compra desc))[1] as mercado,
                  po.regiao,
                  po.unidade,
                  max(po.gtin) as gtin,
                  round(percentile_cont(0.5) within group (order by po.valor_unitario_centavos))::int as mediana,
                  count(*) as n,
                  max(po.data_compra) as recente
                 from precos_observados po
                where po.data_compra >= (now() - (%s || ' days')::interval)::date
                  and po.valor_unitario_centavos > 0
                group by coalesce(po.gtin, po.descricao_norm), po.loja_id, po.regiao, po.unidade""",
            (dias,),
        )
        n = conn.execute("select count(*) from precos_vigentes").fetchone()[0]
        conn.commit()
    _log.info("recalcular_vigentes: %s linhas vigentes (ultimos %s dias, agregado por GTIN)", n, dias)
    return int(n)
