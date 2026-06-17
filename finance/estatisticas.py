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

    Diferente de estatisticas_categorias: aqui o nivel e' o ITEM de cupom
    (itens_lancamento), nao o lancamento. Mostra onde ha' dado de cupom (alimenta
    o banco de precos) e onde nao ha'. Marca os departamentos excluidos do raio-x
    do cliente. Retorna lista ordenada por nÂº de itens (desc).
    """
    from .models import CATEGORIAS_DESPESA
    try:
        from .models import DEPARTAMENTOS_FORA_RAIOX
    except ImportError:
        DEPARTAMENTOS_FORA_RAIOX = set()

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
        a = agg.setdefault(cat_p, {"itens": 0, "cupons": 0, "total": 0})
        a["itens"] += int(itens)
        a["cupons"] += int(cupons)
        a["total"] += int(total)

    linhas = [{"departamento": cat_p, "excluido": cat_p in DEPARTAMENTOS_FORA_RAIOX, **v}
              for cat_p, v in agg.items()]
    # departamentos de despesa que NUNCA receberam item (nao aparecem no raio-x)
    vistas = set(agg.keys())
    for c in CATEGORIAS_DESPESA:
        if c not in vistas:
            linhas.append({"departamento": c, "itens": 0, "cupons": 0, "total": 0,
                           "excluido": c in DEPARTAMENTOS_FORA_RAIOX})
    linhas.sort(key=lambda x: x["itens"], reverse=True)
    return linhas
