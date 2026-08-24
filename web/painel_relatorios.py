"""Módulo "Relatórios" — Vendas, Contas a pagar, Contas a receber, Contas
pagas, Comissão e Contas recebidas, com resultado na tela e exportação em PDF
(impressão do navegador, mesmo padrão já usado no holerite e na separação de
pedidos).

Dados reais, reaproveitando o que já existe:
  - Vendas          -> lancamentos (tipo='receita', natureza='empresa')
  - Contas a pagar   -> titulos (tipo='pagar',   status='aberto')  [finance.empresa]
  - Contas a receber -> titulos (tipo='receber', status='aberto')  [finance.empresa]
  - Contas pagas     -> titulos (tipo='pagar',   status='pago', filtrado por pago_em)
  - Contas recebidas -> titulos (tipo='receber', status='pago', filtrado por pago_em)
  - Comissão         -> lancamentos de vendas agrupados por membro_id, aplicando o
                        membros.comissao_pct de cada um (migração 137). Vendedor sem
                        % configurada aparece com comissão R$ 0,00 e um aviso na tela
                        apontando pra Equipe.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from contas import equipe as eq
from db.conexao import get_pool
from finance import empresa as emp
from web.portal import _render, _env, conta_logada, brl as _brl, _mascara_cnpj

router = APIRouter()

PERIODOS = [
    ("mes", "Este mês"),
    ("mes_passado", "Mês passado"),
    ("90d", "Últimos 90 dias"),
    ("ano", "Este ano"),
    ("todos", "Todo o período"),
]
_PERIODO_ROTULO = dict(PERIODOS)


def _pode_ver(request: Request):
    """Só quem tem a capacidade financeiro (dono ou membro com o papel) entra —
    mesmo gate usado pra Empresa/DRE, já que relatório financeiro é módulo PJ."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if not conta[11]:  # tem_pj
        return None, RedirectResponse("/painel", status_code=303)
    caps = eq.caps_do_papel(request.session.get("papel", "dono"))
    if not caps["financeiro"]:
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _intervalo(periodo: str) -> tuple[date, date]:
    hoje = date.today()
    if periodo == "todos":
        return date(2000, 1, 1), hoje
    if periodo == "mes_passado":
        fim = hoje.replace(day=1) - timedelta(days=1)
        return fim.replace(day=1), fim
    if periodo == "90d":
        return hoje - timedelta(days=90), hoje
    if periodo == "ano":
        return date(hoje.year, 1, 1), hoje
    return hoje.replace(day=1), hoje  # "mes"


def _fmt(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _letterhead(pool, conta) -> dict:
    """Marca (logo/cor) + dados cadastrais da empresa pro timbre do PDF — mesmo
    kit que holerite e recibo do PDV já usam (finance.marca/empresa.marca_empresa)."""
    d = emp.obter_dados_empresa(pool, conta[0])
    endereco = ", ".join(p for p in (
        d["endereco"], d["bairro"],
        f"{d['cidade']}/{d['uf']}" if d["cidade"] else d["uf"],
        f"CEP {d['cep']}" if d["cep"] else "",
    ) if p)
    return {
        "marca": emp.marca_empresa(pool, conta[0]),
        "empresa_nome": d["razao_social"] or d["nome_fantasia"] or conta[2] or "",
        "cnpj_fmt": _mascara_cnpj(d["documento"]),
        "endereco_fmt": endereco,
    }


def _soma(linhas, chave):
    return sum(int(r[chave]) for r in linhas)


def _col(chave, rotulo, num=False, brl=False, tag=False):
    return {"chave": chave, "rotulo": rotulo, "num": num, "brl": brl, "tag": tag}


def _dados_vendas(pool, conta_id, periodo):
    ini, fim = _intervalo(periodo)
    with pool.connection() as c:
        rows = c.execute(
            """select l.data, l.descricao, l.categoria, l.forma_pagamento,
                      coalesce(m.nome, '-') as vendedor, l.valor_centavos
                 from lancamentos l left join membros m on m.id = l.membro_id
                where l.conta_id=%s and l.tipo='receita' and l.natureza='empresa'
                  and l.data >= %s and l.data <= %s
                order by l.data desc, l.id desc limit 300""",
            (conta_id, ini, fim),
        ).fetchall()
    linhas = [{"data": _fmt(r[0]), "descricao": r[1] or "—", "categoria": r[2] or "—",
               "forma": r[3] or "—", "vendedor": r[4], "valor_centavos": int(r[5] or 0)}
              for r in rows]
    total = _soma(linhas, "valor_centavos")
    n = len(linhas)
    hoje_str = _fmt(date.today())
    vendido_hoje = _soma([r for r in linhas if r["data"] == hoje_str], "valor_centavos")
    return {
        "label": "Vendas", "mock": False,
        "colunas": [_col("data", "Data"), _col("descricao", "Descrição"), _col("categoria", "Categoria"),
                    _col("forma", "Forma"), _col("vendedor", "Vendedor"),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total vendido", _brl(total)), ("Nº de vendas", str(n)),
                     ("Ticket médio", _brl(total // n if n else 0)), ("Vendido hoje", _brl(vendido_hoje))],
    }


def _dados_titulos_abertos(pool, conta_id, tipo):
    """Contas a pagar/receber: SEMPRE mostra tudo que está em aberto — período não
    se aplica aqui (uma conta aberta continua aberta até ser paga, não "expira")."""
    hoje = date.today()
    tits = emp.listar_titulos(pool, conta_id, status="aberto", tipo=tipo, limite=300)
    linhas = []
    for t in tits:
        if t["atrasado"]:
            status, cor = "Vencida", "erro"
        else:
            dias = (t["vencimento"] - hoje).days if t["vencimento"] else None
            status, cor = "A vencer", ("aviso" if dias is not None and dias <= 7 else "ok")
        linhas.append({
            "vencimento": _fmt(t["vencimento"]),
            "contraparte": t["cliente_nome"] or t["contraparte"] or "—",
            "categoria": t["categoria"] or "—", "status": status, "status_cor": cor,
            "valor_centavos": t["valor_centavos"],
        })
    total = _soma(linhas, "valor_centavos")
    vencidas = [r for r in linhas if r["status"] == "Vencida"]
    a_vencer_7d = [r for r in linhas if r["status_cor"] == "aviso"]
    rotulo_col = "Fornecedor" if tipo == "pagar" else "Cliente"
    label = "Contas a pagar" if tipo == "pagar" else "Contas a receber"
    return {
        "label": label, "mock": False, "sem_periodo": True,
        "colunas": [_col("vencimento", "Vencimento"), _col("contraparte", rotulo_col),
                    _col("categoria", "Categoria"), _col("status", "Status", tag=True),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total em aberto", _brl(total)),
                     ("Vencidas", f"{len(vencidas)} · {_brl(_soma(vencidas, 'valor_centavos'))}"),
                     ("A vencer em 7 dias", _brl(_soma(a_vencer_7d, "valor_centavos")))],
    }


def _dados_titulos_pagos(pool, conta_id, tipo, periodo):
    ini, fim = _intervalo(periodo)
    tits = emp.listar_titulos(pool, conta_id, status="pago", tipo=tipo, limite=500)
    linhas = []
    for t in tits:
        pg = t["pago_em"]
        if pg is None or pg < ini or pg > fim:
            continue
        linhas.append({
            "data": _fmt(pg), "contraparte": t["cliente_nome"] or t["contraparte"] or "—",
            "categoria": t["categoria"] or "—", "valor_centavos": t["valor_centavos"],
        })
    total = _soma(linhas, "valor_centavos")
    maior = max((r["valor_centavos"] for r in linhas), default=0)
    rotulo_col = "Fornecedor" if tipo == "pagar" else "Cliente"
    label = "Contas pagas" if tipo == "pagar" else "Contas recebidas"
    verbo = "pago" if tipo == "pagar" else "recebido"
    return {
        "label": label, "mock": False,
        "colunas": [_col("data", "Pagamento" if tipo == "pagar" else "Recebimento"),
                    _col("contraparte", rotulo_col), _col("categoria", "Categoria"),
                    _col("valor_centavos", f"Valor {verbo}", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [(f"Total {verbo} no período", _brl(total)), ("Nº de registros", str(len(linhas))),
                     ("Maior valor", _brl(maior))],
    }


def _dados_comissao(pool, conta_id, periodo):
    """Comissão do período por vendedor.

    A conta vive em finance/comissao.py — o MESMO lugar que o Cockpit consulta.
    Antes cada tela fazia a sua e os números não batiam: aqui somava
    `lancamentos`, lá somava o valor estimado do lead."""
    from finance import comissao as com
    ini, fim = _intervalo(periodo)
    linhas = []
    sem_config = 0
    sem_vendedor = 0
    for r in com.por_vendedor(pool, conta_id, ini, fim):
        if r["sem_vendedor"]:
            sem_vendedor += 1
        elif not r["configurada"]:
            sem_config += 1
        linhas.append({
            "vendedor": r["vendedor"], "vendas_centavos": r["recebido_centavos"],
            "percentual": (f"{r['comissao_pct']:g}%" if r["configurada"]
                           else ("— sem vendedor" if r["sem_vendedor"] else "— não configurada")),
            "comissao_centavos": r["comissao_centavos"],
        })
    total = _soma(linhas, "comissao_centavos")
    vendas_totais = _soma(linhas, "vendas_centavos")
    destaque = max(linhas, key=lambda r: r["comissao_centavos"])["vendedor"] if linhas else "—"
    dados = {
        "label": "Comissão", "mock": False,
        "colunas": [_col("vendedor", "Vendedor"), _col("vendas_centavos", "Recebido no período", num=True, brl=True),
                    _col("percentual", "% comissão", num=True), _col("comissao_centavos", "Comissão a pagar", num=True, brl=True)],
        "linhas": linhas, "col_total": "comissao_centavos", "total_centavos": total,
        "metricas": [("Total de comissões", _brl(total)), ("Vendedor destaque", destaque),
                     ("Recebido pela equipe", _brl(vendas_totais))],
    }
    avisos = []
    if sem_config:
        plural = "es" if sem_config != 1 else ""
        avisos.append(
            f"{sem_config} vendedor{plural} sem % de comissão configurada (mostrando R$ 0,00 pra eles) — "
            "configure em Equipe, no botão “% comis.” de cada um.")
    if sem_vendedor:
        # não é frescura: é venda entrando sem dono, e comissão que ninguém recebe
        avisos.append(
            "Há recebimento sem vendedor atribuído. Isso acontece quando a venda "
            "entrou por fora do PDV e do funil — o valor conta no caixa, mas não "
            "gera comissão pra ninguém.")
    if avisos:
        dados["aviso_config"] = " ".join(avisos)
    return dados


# ---- Orçamentos e Contratos: uma aba por tabela, tudo visível, filtro corta ----
#
# Status em grupo ("fechados"/"abertos"/"assinados") ou específico — a mesma
# lista dá as opções do <select> e a tradução pra `status = any(%s)` na query.
# "" (todos) não entra no dict de propósito: ausência = sem filtro de status.
ORC_STATUS_TAG = {
    "rascunho": ("Rascunho", "aviso"), "enviado": ("Enviado", "aviso"),
    "negociando": ("Negociando", "aviso"), "aprovada": ("Aprovada", "ok"),
    "fechado": ("Fechado", "neutro"), "perdido": ("Perdido", "erro"),
}
ORC_STATUS_OPCOES = [
    ("", "Status: todos"), ("fechados", "— Fechados —"), ("abertos", "— Em aberto —"),
    ("rascunho", "Rascunho"), ("enviado", "Enviado"), ("negociando", "Negociando"),
    ("aprovada", "Aprovada"), ("perdido", "Perdido"),
]
ORC_STATUS_FILTROS = {
    "fechados": ["fechado"],
    # "perdido" fica de fora de propósito: é o próprio bucket "Perdidos" das
    # métricas, separado de "Em aberto" — juntar os dois faria o filtro mentir
    # sobre o que a métrica já mostra ao lado.
    "abertos": ["rascunho", "enviado", "negociando", "aprovada"],
    "rascunho": ["rascunho"], "enviado": ["enviado"], "negociando": ["negociando"],
    "aprovada": ["aprovada"], "perdido": ["perdido"],
}

CT_STATUS_TAG = {
    "rascunho": ("Rascunho", "aviso"), "enviado": ("Enviado", "aviso"),
    "assinado": ("Assinado", "ok"), "cumprido": ("Cumprido", "info"),
    "rescindido": ("Rescindido", "erro"),
}
CT_STATUS_OPCOES = [
    ("", "Status: todos"), ("assinados", "— Assinados —"),
    ("rascunho", "Rascunho"), ("enviado", "Enviado"), ("assinado", "Assinado"),
    ("cumprido", "Cumprido"), ("rescindido", "Rescindido"),
]
CT_STATUS_FILTROS = {
    "assinados": ["assinado", "cumprido"],
    "rascunho": ["rascunho"], "enviado": ["enviado"], "assinado": ["assinado"],
    "cumprido": ["cumprido"], "rescindido": ["rescindido"],
}

_VALOR_ORC = "coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0)"


def _vendedores_da_conta(pool, conta_id: int) -> list[tuple[int, str]]:
    with pool.connection() as c:
        rows = c.execute("select id, nome from membros where conta_id=%s order by nome",
                          (conta_id,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _dados_orcamentos(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """TODOS os orçamentos, sem fatiar por aba — Status corta em grupo (fechados/
    em aberto) ou específico, Vendedor e busca por cliente cortam junto. As
    métricas do topo ignoram o filtro de Status de propósito (mostram a
    distribuição inteira do período); o total da tabela é só do que está na tela.

    "Aprovada em" é `aprovada_em`: o instante em que o CLIENTE assinou a
    proposta pública (web/proposta.py). Não tem coluna "Fechado em" — o
    status "Fechado" já aparece na etiqueta de Status, e a data
    (`atualizado_em`) some quase toda vazia num relatório onde a maioria dos
    orçamentos ainda está em aberto (relato do dono: "não tem sentido").

    Cliente é UM nome só, não dois campos: o formulário troca o rótulo de
    `empresa` pra "Nome completo" quando o cliente é pessoa física, mas
    continua gravando na mesma coluna — então pra pessoa física o nome de
    verdade mora em `empresa`, e `cliente` (pensado como "Contato/responsável"
    de uma empresa) fica vazio a maior parte do tempo. Mesma regra que
    `_espelhar_cliente` (web/painel_servicos.py) já usa: `empresa or
    cliente`."""
    ini, fim = _intervalo(periodo)
    where = ["o.conta_id=%s"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("o.criado_em::date >= %s and o.criado_em::date <= %s")
        params += [ini, fim]
    if vendedor_sel:
        where.append("o.criado_por = %s")
        params.append(str(vendedor_sel))
    if busca:
        where.append("(o.empresa ilike %s or o.cliente ilike %s)")
        params += [f"%{busca}%", f"%{busca}%"]
    base_sql = " and ".join(where)

    with pool.connection() as c:
        por_status = c.execute(
            f"select o.status, count(*), sum({_VALOR_ORC}) from orcamentos o "
            f"where {base_sql} group by o.status", params).fetchall()

    def _grupo(quais):
        n = sum(int(r[1]) for r in por_status if r[0] in quais)
        v = sum(int(r[2] or 0) for r in por_status if r[0] in quais)
        return n, v

    n_fechado, v_fechado = _grupo({"fechado"})
    n_perdido, v_perdido = _grupo({"perdido"})
    n_aberto, v_aberto = _grupo({"rascunho", "enviado", "negociando", "aprovada"})

    where2, params2 = list(where), list(params)
    quais = ORC_STATUS_FILTROS.get(status_sel)
    if quais:
        where2.append("o.status = any(%s)")
        params2.append(quais)
    where2_sql = " and ".join(where2)

    with pool.connection() as c:
        rows = c.execute(
            f"""select o.numero, o.cliente, o.empresa, o.status, o.criado_em,
                       o.aprovada_em,
                       -- criado_por guarda o id do membro OU a palavra 'dono' (quem
                       -- abriu a conta, sem vendedor específico — mesma leitura de
                       -- web/proposta.py). Sem o 2º ramo, esses ficavam "—", como se
                       -- não tivessem dono nenhum.
                       coalesce(m.nome, case when o.criado_por = 'dono' then ct.nome end, '—'),
                       {_VALOR_ORC}, o.token
                  from orcamentos o
                  left join membros m on m.id::text = o.criado_por and m.conta_id = o.conta_id
                  left join contas ct on ct.id = o.conta_id
                 where {where2_sql}
                 order by o.criado_em desc limit 300""",
            params2).fetchall()

    linhas = []
    for r in rows:
        rotulo, cor = ORC_STATUS_TAG.get(r[3], (r[3] or "—", "neutro"))
        linhas.append({
            "numero": r[0], "cliente": r[2] or r[1] or "—",
            "status": rotulo, "status_cor": cor,
            "criado_em": _fmt(r[4]), "aprovada_em": _fmt(r[5]),
            "vendedor": r[6], "valor_centavos": int(r[7] or 0),
            "acao_href": f"/proposta/{r[8]}" if r[8] else None,
        })
    return {
        "label": "Orçamentos", "mock": False, "acao": True,
        "acao_rotulo": "Ver / imprimir proposta",
        "colunas": [_col("numero", "Nº"), _col("cliente", "Cliente"),
                    _col("status", "Status", tag=True), _col("criado_em", "Criado em"),
                    _col("aprovada_em", "Aprovada em"),
                    _col("vendedor", "Vendedor"),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": _soma(linhas, "valor_centavos"),
        "metricas": [("Total geral", _brl(v_fechado + v_aberto + v_perdido)),
                     ("Fechados", f"{n_fechado} · {_brl(v_fechado)}"),
                     ("Em aberto", f"{n_aberto} · {_brl(v_aberto)}"),
                     ("Perdidos", f"{n_perdido} · {_brl(v_perdido)}")],
        "filtro_extra": {
            "status_opcoes": ORC_STATUS_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


def _dados_contratos(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """TODOS os contratos vivos (sem os substituídos por aditivo — mesma trava de
    `finance/contrato.por_orcamento`). Vendedor vem do orçamento de origem: o
    contrato quase nunca grava `criado_por` (ver finance/contrato.py). Cliente
    também vem do orçamento de origem, com a mesma regra `empresa or cliente`
    de `_dados_orcamentos` — mesmo formulário, mesma confusão de campo."""
    ini, fim = _intervalo(periodo)
    where = ["c.conta_id=%s", "c.substitui_id is null"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("c.criado_em::date >= %s and c.criado_em::date <= %s")
        params += [ini, fim]
    if vendedor_sel:
        where.append("o.criado_por = %s")
        params.append(str(vendedor_sel))
    if busca:
        where.append("(o.empresa ilike %s or o.cliente ilike %s)")
        params += [f"%{busca}%", f"%{busca}%"]
    base_sql = " and ".join(where)
    join_sql = "left join orcamentos o on o.id = c.orcamento_id"

    with pool.connection() as c:
        por_status = c.execute(
            f"select c.status, count(*), sum(coalesce(c.valor_centavos,0)) "
            f"from contratos c {join_sql} where {base_sql} group by c.status",
            params).fetchall()

    def _grupo(quais):
        n = sum(int(r[1]) for r in por_status if r[0] in quais)
        v = sum(int(r[2] or 0) for r in por_status if r[0] in quais)
        return n, v

    n_assinado, v_assinado = _grupo({"assinado", "cumprido"})
    n_aguardando, v_aguardando = _grupo({"rascunho", "enviado"})
    n_rescindido, v_rescindido = _grupo({"rescindido"})

    where2, params2 = list(where), list(params)
    quais = CT_STATUS_FILTROS.get(status_sel)
    if quais:
        where2.append("c.status = any(%s)")
        params2.append(quais)
    where2_sql = " and ".join(where2)

    with pool.connection() as c:
        rows = c.execute(
            f"""select c.numero, coalesce(o.empresa, o.cliente, '—'), c.status, c.criado_em,
                       c.assinado_em,
                       -- mesma leitura de _dados_orcamentos: criado_por é o id do
                       -- membro OU a palavra 'dono'.
                       coalesce(m.nome, case when o.criado_por = 'dono' then ct.nome end, '—'),
                       coalesce(c.valor_centavos, 0), c.token
                  from contratos c {join_sql}
                  left join membros m on m.id::text = o.criado_por and m.conta_id = c.conta_id
                  left join contas ct on ct.id = c.conta_id
                 where {where2_sql}
                 order by c.criado_em desc limit 300""",
            params2).fetchall()

    linhas = []
    for r in rows:
        rotulo, cor = CT_STATUS_TAG.get(r[2], (r[2] or "—", "neutro"))
        linhas.append({
            "numero": r[0], "cliente": r[1], "status": rotulo, "status_cor": cor,
            "criado_em": _fmt(r[3]), "assinado_em": _fmt(r[4]),
            "vendedor": r[5], "valor_centavos": int(r[6] or 0),
            "acao_href": f"/contrato/{r[7]}" if r[7] else None,
        })
    return {
        "label": "Contratos", "mock": False, "acao": True,
        "acao_rotulo": "Ver / imprimir contrato",
        "colunas": [_col("numero", "Nº"), _col("cliente", "Cliente"),
                    _col("status", "Status", tag=True), _col("criado_em", "Criado em"),
                    _col("assinado_em", "Assinado em"), _col("vendedor", "Vendedor"),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": _soma(linhas, "valor_centavos"),
        "metricas": [("Total geral", _brl(v_assinado + v_aguardando + v_rescindido)),
                     ("Assinados", f"{n_assinado} · {_brl(v_assinado)}"),
                     ("Aguardando assinatura", f"{n_aguardando} · {_brl(v_aguardando)}"),
                     ("Rescindidos", f"{n_rescindido} · {_brl(v_rescindido)}")],
        "filtro_extra": {
            "status_opcoes": CT_STATUS_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


TIPOS = {
    "vendas": {"label": "Vendas", "montar": lambda pool, cid, per, **f: _dados_vendas(pool, cid, per)},
    "contas_pagar": {"label": "Contas a pagar", "montar": lambda pool, cid, per, **f: _dados_titulos_abertos(pool, cid, "pagar")},
    "contas_receber": {"label": "Contas a receber", "montar": lambda pool, cid, per, **f: _dados_titulos_abertos(pool, cid, "receber")},
    "pagas": {"label": "Contas pagas", "montar": lambda pool, cid, per, **f: _dados_titulos_pagos(pool, cid, "pagar", per)},
    "comissao": {"label": "Comissão", "montar": lambda pool, cid, per, **f: _dados_comissao(pool, cid, per)},
    "recebidas": {"label": "Contas recebidas", "montar": lambda pool, cid, per, **f: _dados_titulos_pagos(pool, cid, "receber", per)},
    "orcamentos": {"label": "Orçamentos", "montar": lambda pool, cid, per, **f: _dados_orcamentos(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
    "contratos": {"label": "Contratos", "montar": lambda pool, cid, per, **f: _dados_contratos(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
}


def _contexto(conta_id: int, tipo: str, periodo: str, status: str = "", vendedor: str = "", q: str = ""):
    tipo = tipo if tipo in TIPOS else "vendas"
    periodo = periodo if periodo in _PERIODO_ROTULO else "mes"
    dados = TIPOS[tipo]["montar"](get_pool(), conta_id, periodo, status=status, vendedor=vendedor, q=q)
    return tipo, periodo, dados


@router.get("/painel/relatorios", response_class=HTMLResponse)
def painel_relatorios(request: Request, tipo: str = "vendas", periodo: str = "mes",
                      status: str = "", vendedor: str = "", q: str = ""):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    tipo, periodo, dados = _contexto(conta[0], tipo, periodo, status, vendedor, q)
    return _render("relatorios", request, tipos=TIPOS, tipo=tipo, periodo=periodo, periodos=PERIODOS,
                   periodo_rotulo=_PERIODO_ROTULO[periodo], dados=dados)


@router.get("/painel/relatorios/pdf", response_class=HTMLResponse)
def painel_relatorios_pdf(request: Request, tipo: str = "vendas", periodo: str = "mes",
                          status: str = "", vendedor: str = "", q: str = ""):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    tipo, periodo, dados = _contexto(conta[0], tipo, periodo, status, vendedor, q)
    from datetime import datetime
    return HTMLResponse(_env.get_template("relatorio_pdf").render(
        dados=dados, tipo=tipo, periodo=periodo, periodo_rotulo=_PERIODO_ROTULO[periodo],
        gerado_em=datetime.now().strftime("%d/%m/%Y %H:%M"),
        **_letterhead(pool, conta),
    ))
