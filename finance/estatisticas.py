"""Estatisticas de uso das categorias em TODAS as contas (visao do dono do SaaS).

Serve pra DECIDIR o que mexer na lista oficial (finance/models.py):
- Categoria "Outros" com % alto  -> falta categoria; vale criar uma nova.
- Categoria oficial com ZERO uso  -> candidata a remover.

So' leitura: nenhum dado e' alterado. Junta grafias (ex 'Saude'/'SaÃºde') via
canonizar_categoria, igual a' exibicao do app. Como a canonizacao sempre cai
numa categoria oficial (ou 'Outros'), qualquer categoria solta/antiga e'
contabilizada dentro de 'Outros' automaticamente.
"""
from __future__ import annotations

from .models import CATEGORIAS_DESPESA, CATEGORIAS_RECEITA, canonizar_categoria


def estatisticas_categorias(pool) -> dict:
    """Retorna o uso por categoria, separado em despesa/receita.

    Estrutura:
      {
        "despesa": [{"categoria", "qtd", "pct", "total"}, ...]  # ordenado por qtd desc
        "receita": [...],
        "pct_outros_despesa": float, "pct_outros_receita": float,
        "total_despesa": int, "total_receita": int,
      }
    Valores monetarios em CENTAVOS (usar brl() na exibicao).
    """
    with pool.connection() as conn:
        rows = conn.execute(
            """select tipo, categoria, count(*), coalesce(sum(valor_centavos), 0)
               from lancamentos group by tipo, categoria"""
        ).fetchall()

    # agrega por (tipo, categoria_canonizada): junta grafias e categorias soltas
    agg: dict[tuple[str, str], dict] = {}
    for tipo, cat, qtd, soma in rows:
        t = tipo if tipo in ("despesa", "receita") else "despesa"
        cat_p = canonizar_categoria(cat, t)
        a = agg.setdefault((t, cat_p), {"qtd": 0, "total": 0})
        a["qtd"] += int(qtd)
        a["total"] += int(soma)

    def _monta(tipo: str, oficiais: list[str]):
        total_qtd = sum(v["qtd"] for (t, _), v in agg.items() if t == tipo)
        vistas, linhas = set(), []
        for (t, cat_p), v in agg.items():
            if t != tipo:
                continue
            vistas.add(cat_p)
            linhas.append({"categoria": cat_p, "qtd": v["qtd"], "total": v["total"]})
        # garante que TODA categoria oficial aparece (mesmo com 0 = candidata a remover)
        for c in oficiais:
            if c not in vistas:
                linhas.append({"categoria": c, "qtd": 0, "total": 0})
        for l in linhas:
            l["pct"] = round(100 * l["qtd"] / total_qtd, 1) if total_qtd else 0.0
        linhas.sort(key=lambda x: x["qtd"], reverse=True)
        pct_outros = next((l["pct"] for l in linhas if l["categoria"] == "Outros"), 0.0)
        return linhas, pct_outros

    desp, pct_outros_desp = _monta("despesa", CATEGORIAS_DESPESA)
    rec, pct_outros_rec = _monta("receita", CATEGORIAS_RECEITA)

    return {
        "despesa": desp,
        "receita": rec,
        "pct_outros_despesa": pct_outros_desp,
        "pct_outros_receita": pct_outros_rec,
        "total_despesa": sum(l["qtd"] for l in desp),
        "total_receita": sum(l["qtd"] for l in rec),
    }


def estatisticas_raiox(pool) -> list[dict]:
    """Itens de cupom por DEPARTAMENTO, em TODAS as contas (visao admin do raio-x).

    Mostra EXATAMENTE o que o cliente ve: so' os departamentos da lista branca
    (finance/models.py: DEPARTAMENTOS_RAIOX) que tem item de cupom. Nivel de ITEM
    (itens_lancamento), nao de lancamento. Ordenado por nÂº de itens (desc).
    """
    # fallback igual a' lista branca, caso models.py atrase no deploy
    try:
        from .models import DEPARTAMENTOS_RAIOX
    except ImportError:
        DEPARTAMENTOS_RAIOX = {"Mercado", "Saude", "Restaurante", "Pet"}

    with pool.connection() as conn:
        rows = conn.execute(
            """select l.categoria, count(*) as itens,
                      count(distinct l.id) as cupons,
                      coalesce(sum(i.valor_total_centavos), 0) as total
               from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
               group by l.categoria"""
        ).fetchall()

    agg: dict[str, dict] = {}
    for cat, itens, cupons, total in rows:
        cat_p = canonizar_categoria(cat, "despesa")
        if cat_p not in DEPARTAMENTOS_RAIOX:
            continue   # so' a lista branca
        a = agg.setdefault(cat_p, {"itens": 0, "cupons": 0, "total": 0})
        a["itens"] += int(itens)
        a["cupons"] += int(cupons)
        a["total"] += int(total)

    linhas = [{"departamento": k, **v} for k, v in agg.items() if v["itens"] > 0]
    linhas.sort(key=lambda x: x["itens"], reverse=True)
    return linhas


def estatisticas_precos(pool) -> dict:
    """Painel do BANCO DE PRECOS (visao admin). So' leitura. Mostra:
      - contadores: observacoes, produtos distintos, lojas, cupons com QR lido
      - mais_confirmados: 1 linha por (loja, produto, preco, dia) com "visto N vezes"
        (a ideia do contador: o mesmo preco no mesmo dia/loja nao polui, vira N)
      - top_produtos / top_lojas: onde o banco e' mais forte
    Mantem TODAS as observacoes cruas (sao a confianca da mediana); aqui so' agrega
    pra exibir."""
    with pool.connection() as conn:
        tot_obs, tot_prod, tot_lojas = conn.execute(
            """select count(*), count(distinct descricao_norm), count(distinct loja_id)
               from precos_observados"""
        ).fetchone()
        try:
            tot_cupons = conn.execute(
                "select count(distinct chave) from qr_leituras where chave is not null"
            ).fetchone()[0]
        except Exception:  # noqa: BLE001
            tot_cupons = 0
        mais = conn.execute(
            """select coalesce(nullif(mercado,''), regiao, '-') as loja,
                      min(descricao_original) as produto,
                      valor_unitario_centavos as preco, data_compra, count(*) as vezes
               from precos_observados
               group by coalesce(nullif(mercado,''), regiao, '-'),
                        descricao_norm, valor_unitario_centavos, data_compra
               order by vezes desc, data_compra desc
               limit 60"""
        ).fetchall()
        top_prod = conn.execute(
            """select min(descricao_original), count(*)
               from precos_observados group by descricao_norm
               order by count(*) desc limit 15"""
        ).fetchall()
        top_lojas = conn.execute(
            """select coalesce(nullif(mercado,''), regiao, '-'), count(*),
                      count(distinct descricao_norm)
               from precos_observados
               group by coalesce(nullif(mercado,''), regiao, '-')
               order by count(*) desc limit 15"""
        ).fetchall()
    return {
        "tot_observacoes": int(tot_obs or 0),
        "tot_produtos": int(tot_prod or 0),
        "tot_lojas": int(tot_lojas or 0),
        "tot_cupons": int(tot_cupons or 0),
        "mais_confirmados": [{"loja": r[0], "produto": r[1], "preco": int(r[2]),
                              "data": r[3], "vezes": int(r[4])} for r in mais],
        "top_produtos": [{"produto": r[0], "obs": int(r[1])} for r in top_prod],
        "top_lojas": [{"loja": r[0], "obs": int(r[1]), "produtos": int(r[2])} for r in top_lojas],
    }
