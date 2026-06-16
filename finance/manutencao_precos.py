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
    Mediana por (descricao_norm, loja_id, regiao, unidade). Retorna nº de linhas
    vigentes. Substitui o conteudo (recalculo completo - simples e correto)."""
    with pool.connection() as conn:
        # mediana no proprio Postgres com percentile_cont(0.5)
        # agrupa por produto+loja+regiao+unidade; pega tambem um nome de exemplo,
        # contagem e data mais recente.
        conn.execute("delete from precos_vigentes")
        conn.execute(
            """insert into precos_vigentes
                 (descricao_norm, descricao_exemplo, loja_id, mercado, regiao,
                  unidade, valor_mediana_centavos, n_observacoes, data_mais_recente)
               select
                  po.descricao_norm,
                  (array_agg(po.descricao_original order by po.data_compra desc))[1] as exemplo,
                  po.loja_id,
                  (array_agg(po.mercado order by po.data_compra desc))[1] as mercado,
                  po.regiao,
                  po.unidade,
                  round(percentile_cont(0.5) within group (order by po.valor_unitario_centavos))::int as mediana,
                  count(*) as n,
                  max(po.data_compra) as recente
                 from precos_observados po
                where po.data_compra >= (now() - (%s || ' days')::interval)::date
                  and po.valor_unitario_centavos > 0
                group by po.descricao_norm, po.loja_id, po.regiao, po.unidade
               on conflict (descricao_norm, loja_id, regiao, unidade) do update set
                  descricao_exemplo = excluded.descricao_exemplo,
                  mercado = excluded.mercado,
                  valor_mediana_centavos = excluded.valor_mediana_centavos,
                  n_observacoes = excluded.n_observacoes,
                  data_mais_recente = excluded.data_mais_recente,
                  atualizado_em = now()""",
            (dias,),
        )
        n = conn.execute("select count(*) from precos_vigentes").fetchone()[0]
        conn.commit()
    _log.info("recalcular_vigentes: %s linhas vigentes (ultimos %s dias)", n, dias)
    return int(n)
