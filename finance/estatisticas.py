"""Estatisticas de uso das categorias em TODAS as contas (visao do dono do SaaS).

Serve pra DECIDIR o que mexer na lista oficial (finance/models.py):
- Categoria "Outros" com % alto  -> falta categoria; vale criar uma nova.
- Categoria oficial com ZERO uso  -> candidata a remover.

So' leitura: nenhum dado e' alterado. Junta grafias (ex 'Saude'/'Saúde') via
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
    (itens_lancamento), nao de lancamento. Ordenado por nº de itens (desc).
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
      - top_produtos / top_lojas: onde o banco e' mais forte
    Fallback: se precos_observados está vazio (incidente de perda), le de precos_vigentes
    (os agregados consolidados de ~85 preços que sobreviveram).
    """
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

        # Se NAO ha observacoes cruas (ex: so' restaram os vigentes agregados),
        # o painel le' de precos_vigentes pra nao mostrar "0" com dados existindo.
        usar_vigentes = (int(tot_obs or 0) == 0)

        if usar_vigentes:
            vg = conn.execute(
                """select count(*), count(distinct descricao_norm), count(distinct loja_id)
                   from precos_vigentes"""
            ).fetchone()
            tot_obs, tot_prod, tot_lojas = vg  # reusa os contadores com os vigentes
            mais = conn.execute(
                """select coalesce(l.nome, nullif(v.mercado,''), v.regiao, '-') as loja,
                          v.descricao_exemplo as produto,
                          v.valor_mediana_centavos as preco, v.data_mais_recente,
                          v.n_observacoes as vezes
                   from precos_vigentes v
                   left join lojas l on l.id = v.loja_id
                   order by v.n_observacoes desc, v.data_mais_recente desc
                   limit 60"""
            ).fetchall()
            top_prod = conn.execute(
                """select descricao_exemplo, sum(n_observacoes)
                   from precos_vigentes group by descricao_exemplo, descricao_norm
                   order by sum(n_observacoes) desc limit 15"""
            ).fetchall()
            top_lojas = conn.execute(
                """select coalesce(l.nome, nullif(v.mercado,''), v.regiao, '-'),
                          sum(v.n_observacoes), count(distinct v.descricao_norm)
                   from precos_vigentes v
                   left join lojas l on l.id = v.loja_id
                   group by coalesce(l.nome, nullif(v.mercado,''), v.regiao, '-')
                   order by sum(v.n_observacoes) desc limit 15"""
            ).fetchall()
        else:
            mais = conn.execute(
                """select coalesce(l.nome, nullif(po.mercado,''), po.regiao, '-') as loja,
                          min(po.descricao_original) as produto,
                          po.valor_unitario_centavos as preco, po.data_compra,
                          count(*) as vezes
                   from precos_observados po
                   left join lojas l on l.id = po.loja_id
                   group by coalesce(l.nome, nullif(po.mercado,''), po.regiao, '-'),
                            po.descricao_norm, po.valor_unitario_centavos, po.data_compra
                   order by vezes desc, po.data_compra desc
                   limit 60"""
            ).fetchall()
            top_prod = conn.execute(
                """select min(descricao_original), count(*)
                   from precos_observados group by descricao_norm
                   order by count(*) desc limit 15"""
            ).fetchall()
            top_lojas = conn.execute(
                """select coalesce(l.nome, nullif(po.mercado,''), po.regiao, '-'),
                          count(*), count(distinct po.descricao_norm)
                   from precos_observados po
                   left join lojas l on l.id = po.loja_id
                   group by coalesce(l.nome, nullif(po.mercado,''), po.regiao, '-')
                   order by count(*) desc limit 15"""
            ).fetchall()
    return {
        "tot_observacoes": int(tot_obs or 0),
        "tot_produtos": int(tot_prod or 0),
        "tot_lojas": int(tot_lojas or 0),
        "tot_cupons": int(tot_cupons or 0),
        "fonte": "vigentes" if usar_vigentes else "observados",
        "mais_confirmados": [{"loja": r[0], "produto": r[1], "preco": int(r[2]),
                              "data": r[3], "vezes": int(r[4])} for r in mais],
        "top_produtos": [{"produto": r[0], "obs": int(r[1])} for r in top_prod],
        "top_lojas": [{"loja": r[0], "obs": int(r[1]), "produtos": int(r[2])} for r in top_lojas],
    }


def estatisticas_funil(pool) -> dict:
    """Funil de aquisição pro painel admin: visitante -> testou -> cadastrou -> pagou.

    Cruza a tabela `leads` (test-drive da landing) com `contas` (status de pagamento).
    "Pagou" = conta com status='ativa' (o ciclo trial->ativa ja' existe via ct.ativar).

    Estrutura:
      {
        "geral": {
          "visitantes", "testaram", "cadastraram", "pagaram",
          "pct_testou", "pct_cadastrou", "pct_pagou"   # % sobre visitantes
        },
        "por_canal": [
          {"canal", "visitantes", "testaram", "cadastraram", "pagaram"}, ...
        ],
        "contas_status": [{"status", "qtd"}, ...],       # saude das contas reais
        "trials_vencendo": [{"id","nome","plano","vencimento"}, ...],  # <=3 dias
        "tem_tabela_leads": bool,   # False se a migracao 023 ainda nao rodou
      }
    """
    out: dict = {
        "geral": {"visitantes": 0, "testaram": 0, "cadastraram": 0, "pagaram": 0,
                  "pct_testou": 0.0, "pct_cadastrou": 0.0, "pct_pagou": 0.0},
        "por_canal": [],
        "contas_status": [],
        "trials_vencendo": [],
        "tem_tabela_leads": False,
    }

    with pool.connection() as conn:
        # a tabela leads pode ainda nao existir (antes da migracao 023)
        existe = conn.execute(
            "select to_regclass('public.leads') is not null"
        ).fetchone()[0]
        out["tem_tabela_leads"] = bool(existe)

        if existe:
            g = conn.execute(
                """select
                     count(*),
                     count(*) filter (where l.gastos_usados > 0),
                     count(*) filter (where l.virou_conta),
                     count(*) filter (where c.status = 'ativa')
                   from leads l
                   left join contas c on c.id = l.conta_id"""
            ).fetchone()
            vis, test, cad, pag = (int(g[0]), int(g[1]), int(g[2]), int(g[3]))
            pct = lambda n: round(100.0 * n / vis, 1) if vis else 0.0
            out["geral"] = {
                "visitantes": vis, "testaram": test,
                "cadastraram": cad, "pagaram": pag,
                "pct_testou": pct(test), "pct_cadastrou": pct(cad), "pct_pagou": pct(pag),
            }

            for canal, v, t, ca, pa in conn.execute(
                """select l.canal,
                          count(*),
                          count(*) filter (where l.gastos_usados > 0),
                          count(*) filter (where l.virou_conta),
                          count(*) filter (where c.status = 'ativa')
                   from leads l
                   left join contas c on c.id = l.conta_id
                   group by l.canal order by count(*) desc"""
            ).fetchall():
                out["por_canal"].append({
                    "canal": canal, "visitantes": int(v), "testaram": int(t),
                    "cadastraram": int(ca), "pagaram": int(pa),
                })

        # saude das contas reais (ignora a conta-degustacao)
        for status, qtd in conn.execute(
            """select status, count(*) from contas
               where nome <> '__degustacao_visitantes__'
               group by status order by count(*) desc"""
        ).fetchall():
            out["contas_status"].append({"status": status, "qtd": int(qtd)})

        # trials vencendo nos proximos 3 dias (agir antes de perder)
        for cid, nome, plano, venc in conn.execute(
            """select id, nome, plano, vencimento from contas
               where status = 'trial'
                 and vencimento between current_date and current_date + 3
                 and nome <> '__degustacao_visitantes__'
               order by vencimento"""
        ).fetchall():
            out["trials_vencendo"].append({
                "id": int(cid), "nome": nome, "plano": plano,
                "vencimento": venc.isoformat() if venc else None,
            })

    return out


def estatisticas_custo(pool, desde_iso: str | None = None, ate_iso: str | None = None) -> dict:
    """Resumo de custo de API pro painel admin, com período escolhível.

    desde_iso/ate_iso no formato 'AAAA-MM-DD' (inclusive). Se ambos None, usa o mês corrente.

    Estrutura:
      {
        "periodo": {"desde", "ate"},
        "geral": {
          "chamadas", "fotos", "textos",
          "custo_total_reais",
          "custo_fotos_reais", "custo_textos_reais",
          "contas_ativas",
          "custo_medio_por_conta_reais",
          "custo_medio_foto_reais", "custo_medio_texto_reais",
        },
        "por_conta": [
          {"conta_id", "nome", "chamadas", "fotos", "custo_reais"}, ...
        ],
        "tem_tabela": bool,
      }
    """
    out = {"periodo": {"desde": desde_iso, "ate": ate_iso}, "tem_tabela": False,
           "geral": {}, "por_conta": []}

    with pool.connection() as conn:
        existe = conn.execute("select to_regclass('public.uso_api') is not null").fetchone()[0]
        out["tem_tabela"] = bool(existe)
        if not existe:
            return out

        if desde_iso and ate_iso:
            filtro = "u.criado_em::date between %s and %s"
            args = (desde_iso, ate_iso)
        else:
            filtro = "date_trunc('month', u.criado_em) = date_trunc('month', current_date)"
            args = ()
        out["periodo"] = {"desde": desde_iso or "(início do mês)", "ate": ate_iso or "hoje"}

        deg = conn.execute(
            "select id from contas where nome='__degustacao_visitantes__' limit 1"
        ).fetchone()
        deg_id = int(deg[0]) if deg else -1

        g = conn.execute(
            f"""select
                  count(*),
                  count(*) filter (where u.eh_foto),
                  count(*) filter (where not u.eh_foto),
                  coalesce(sum(u.custo_centavos),0),
                  coalesce(sum(u.custo_centavos) filter (where u.eh_foto),0),
                  coalesce(sum(u.custo_centavos) filter (where not u.eh_foto),0),
                  count(distinct u.conta_id) filter (where u.conta_id <> %s)
                from uso_api u where {filtro}""",
            (deg_id, *args),
        ).fetchone()
        chamadas, fotos, textos, ctot, cfoto, ctexto, contas_ativas = (
            int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), int(g[6]))

        reais = lambda cent: round(cent / 100.0, 2)
        out["geral"] = {
            "chamadas": chamadas, "fotos": fotos, "textos": textos,
            "custo_total_reais": reais(ctot),
            "custo_fotos_reais": reais(cfoto),
            "custo_textos_reais": reais(ctexto),
            "contas_ativas": contas_ativas,
            "custo_medio_por_conta_reais": reais(ctot / contas_ativas) if contas_ativas else 0.0,
            "custo_medio_foto_reais": reais(cfoto / fotos) if fotos else 0.0,
            "custo_medio_texto_reais": reais(ctexto / textos) if textos else 0.0,
        }

        for cid, nome, ch, ft, cc in conn.execute(
            f"""select u.conta_id,
                       coalesce(c.nome,'(conta '||u.conta_id||')'),
                       count(*), count(*) filter (where u.eh_foto),
                       coalesce(sum(u.custo_centavos),0)
                from uso_api u
                left join contas c on c.id = u.conta_id
                where {filtro}
                group by u.conta_id, c.nome
                order by sum(u.custo_centavos) desc
                limit 30""",
            args,
        ).fetchall():
            out["por_conta"].append({
                "conta_id": int(cid), "nome": nome, "chamadas": int(ch),
                "fotos": int(ft), "custo_reais": reais(int(cc)),
            })

    return out


# ===========================================================================
# GATILHO DA FASE B + ASSERTIVIDADE DO QR (so' leitura; visao admin)
# Adicionadas pra acompanhar o enchimento do banco de ouro e a qualidade da
# leitura de QR por tipo de midia. Calibravel via dict 'gatilhos'.
# ===========================================================================

# Numeros calibrados pra realidade de Teresina (ajustaveis):
GATILHOS_FASE_B = {
    "observacoes": 1200,       # observacoes cruas em precos_observados
    "produtos": 400,           # produtos distintos (descricao_norm)
    "lojas": 6,                # lojas distintas (comparacao precisa de mercados diferentes)
    "pct_confirmado": 0.35,    # % de produtos com >=2 leituras (preco confirmado, nao unico)
    "cobertura_gtin": 0.40,    # % de observacoes com GTIN (ponto forte do fluxo)
}


def _metrica(nome: str, valor, gatilho, unidade: str = "") -> dict:
    """Monta uma linha de metrica pronta pra barra de progresso no painel."""
    try:
        pct = int(round(100 * float(valor) / float(gatilho))) if gatilho else 100
    except (TypeError, ValueError, ZeroDivisionError):
        pct = 0
    return {
        "nome": nome, "valor": valor, "gatilho": gatilho, "unidade": unidade,
        "pct": max(0, min(pct, 100)), "ok": (valor or 0) >= gatilho,
    }


def pronto_para_fase_b(pool, gatilhos: dict | None = None) -> dict:
    """Diz se o banco de precos ja' encheu o suficiente pra liberar a Fase B
    (raio-x opt-in + Haiku no cupom simples). So' leitura.

    Retorna:
      - veredito: 'LIBERADA' | 'BLOQUEADA'
      - metricas: lista pronta pra barra de progresso (nome/valor/gatilho/pct/ok)
      - faltam: nomes das metricas que ainda nao bateram
      - contexto: cupons com QR, obs/cupom, produtos confirmados, produtos
        multi-loja (o "ouro" de verdade), estimativa de cupons faltando, data
        da observacao mais recente.
    """
    G = gatilhos or GATILHOS_FASE_B
    with pool.connection() as conn:
        tot_obs = conn.execute("select count(*) from precos_observados").fetchone()[0] or 0
        tot_prod = conn.execute(
            "select count(distinct descricao_norm) from precos_observados").fetchone()[0] or 0
        tot_lojas = conn.execute(
            "select count(distinct loja_id) from precos_observados where loja_id is not null"
        ).fetchone()[0] or 0
        com_gtin = conn.execute(
            "select count(*) from precos_observados where gtin is not null").fetchone()[0] or 0
        prod_confirmados = conn.execute(
            "select count(*) from (select 1 from precos_observados "
            "group by descricao_norm having count(*) >= 2) t").fetchone()[0] or 0
        prod_multiloja = conn.execute(
            "select count(*) from (select 1 from precos_observados where loja_id is not null "
            "group by descricao_norm having count(distinct loja_id) >= 2) t").fetchone()[0] or 0
        try:
            cupons = conn.execute(
                "select count(distinct chave) from qr_leituras where chave is not null"
            ).fetchone()[0] or 0
        except Exception:  # noqa: BLE001
            cupons = 0
        try:
            data_recente = conn.execute(
                "select max(data_compra) from precos_observados").fetchone()[0]
        except Exception:  # noqa: BLE001
            data_recente = None

    pct_conf = (prod_confirmados / tot_prod) if tot_prod else 0.0
    pct_gtin = (com_gtin / tot_obs) if tot_obs else 0.0
    obs_por_cupom = (tot_obs / cupons) if cupons else 0.0

    metricas = [
        _metrica("Observacoes cruas", tot_obs, G["observacoes"]),
        _metrica("Produtos distintos", tot_prod, G["produtos"]),
        _metrica("Lojas distintas", tot_lojas, G["lojas"]),
        _metrica("% confirmado (>=2 leituras)", round(pct_conf * 100),
                 round(G["pct_confirmado"] * 100), unidade="%"),
        _metrica("Cobertura GTIN", round(pct_gtin * 100),
                 round(G["cobertura_gtin"] * 100), unidade="%"),
    ]
    liberada = all(m["ok"] for m in metricas)
    faltam = [m["nome"] for m in metricas if not m["ok"]]

    cupons_faltando = None
    if obs_por_cupom > 0 and tot_obs < G["observacoes"]:
        cupons_faltando = int(round((G["observacoes"] - tot_obs) / obs_por_cupom))

    return {
        "veredito": "LIBERADA" if liberada else "BLOQUEADA",
        "liberada": liberada,
        "faltam": faltam,
        "metricas": metricas,
        "contexto": {
            "cupons_com_qr": cupons,
            "obs_por_cupom": round(obs_por_cupom, 1),
            "produtos_confirmados": prod_confirmados,
            "produtos_multiloja": prod_multiloja,
            "cupons_faltando_estimado": cupons_faltando,
            "data_mais_recente": (data_recente.isoformat()
                                  if hasattr(data_recente, "isoformat") else data_recente),
        },
    }


def estatisticas_leituras_qr(pool) -> dict:
    """Assertividade da leitura de QR por TIPO de midia (Foto vs PDF). So' leitura.

    Pra cada tipo: total de leituras, quantas acertaram o QR (leu=true), a taxa
    de acerto, e o tamanho medio (KB) das que ACERTARAM vs FALHARAM â pra ver se
    foto pequena/borrada e' o que derruba a leitura. Ajuda a decidir ajustes no
    decoder e a calibrar o roteamento (sem chave -> Haiku).
    """
    with pool.connection() as conn:
        linhas = conn.execute(
            """select
                 case when media_type ilike 'application/pdf' then 'PDF'
                      when media_type ilike 'image/%' then 'Foto'
                      else coalesce(media_type, '?') end as tipo,
                 count(*) as total,
                 count(*) filter (where leu) as acertos,
                 avg(img_bytes) filter (where leu) as bytes_ok,
                 avg(img_bytes) filter (where not leu) as bytes_falha
               from qr_leituras
               group by 1
               order by total desc"""
        ).fetchall()

    tipos = []
    tot = ok = 0
    for tipo, total, acertos, bytes_ok, bytes_falha in linhas:
        total = total or 0
        acertos = acertos or 0
        tot += total
        ok += acertos
        tipos.append({
            "tipo": tipo,
            "total": total,
            "acertos": acertos,
            "falhas": total - acertos,
            "acertividade_pct": int(round(100 * acertos / total)) if total else 0,
            "kb_medio_acerto": int((bytes_ok or 0) / 1024),
            "kb_medio_falha": int((bytes_falha or 0) / 1024),
        })
    return {
        "tipos": tipos,
        "total_leituras": tot,
        "acertividade_geral_pct": int(round(100 * ok / tot)) if tot else 0,
    }


def resumo_fase_b_texto(pool, gatilhos: dict | None = None) -> str:
    """Resumo legivel (Markdown do Telegram) do progresso da Fase B.
    Bom pro alerta diario ao admin ou um comando '/banco' no painel."""
    d = pronto_para_fase_b(pool, gatilhos)

    def _barra(pct: int) -> str:
        cheio = max(0, min(10, int(round((pct or 0) / 10))))
        return "█" * cheio + "░" * (10 - cheio)

    linhas = [f"*Banco de ouro – Fase B: {d['veredito']}*", ""]
    for m in d["metricas"]:
        marca = "✅" if m["ok"] else "⏳"
        u = m["unidade"]
        linhas.append(
            f"{marca} {m['nome']}: {m['valor']}{u}/{m['gatilho']}{u} "
            f"`{_barra(m['pct'])}` {m['pct']}%"
        )
    ctx = d["contexto"]
    linhas += [
        "",
        f"🏆 Produtos multi-loja (ouro): *{ctx['produtos_multiloja']}*",
        f"📊 Confirmados (≥2 leituras): *{ctx['produtos_confirmados']}*",
        f"🧾 Cupons com QR: *{ctx['cupons_com_qr']}* (~{ctx['obs_por_cupom']} itens/cupom)",
    ]
    if ctx.get("cupons_faltando_estimado") is not None:
        linhas.append(f"🎯 Faltam ~*{ctx['cupons_faltando_estimado']}* cupons pro volume")
    if d["faltam"]:
        linhas.append("")
        linhas.append("Ainda falta: " + ", ".join(d["faltam"]))
    return "\n".join(linhas)
