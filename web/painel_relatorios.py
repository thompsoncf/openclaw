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
from finance import agenda as _ag
from finance import empresa as emp
from finance import vendas
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


def _col(chave, rotulo, num=False, brl=False, tag=False, flex=False):
    """`flex=True` marca a coluna ELÁSTICA da tabela — a que pode encolher quando
    falta largura. Só uma por relatório, e é sempre a de nome livre (descrição,
    cliente, contraparte).

    Existe porque o oposto quebrava a tela. A tabela tinha `nowrap` em TODA célula
    e `min-width:640px`: com seis colunas de nome longo nada cabia nos 720px do
    cartão, a tabela rolava pro lado e o que aparecia era o MEIO dela. No print de
    26/08 o cliente lia "ço Pelle Clínica" e "erson Venici" — não era truncagem, era
    a coluna Data e o começo do nome fora da área visível. Uma coluna elástica, que
    corta no FIM com reticências, mantém todas as outras no lugar e o começo do nome
    sempre visível — que é a parte que identifica o cliente."""
    return {"chave": chave, "rotulo": rotulo, "num": num, "brl": brl, "tag": tag,
            "flex": flex}


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
        "colunas": [_col("data", "Data"), _col("descricao", "Descrição", flex=True), _col("categoria", "Categoria"),
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
        "colunas": [_col("vencimento", "Vencimento"), _col("contraparte", rotulo_col, flex=True),
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
                    _col("contraparte", rotulo_col, flex=True), _col("categoria", "Categoria"),
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
        "colunas": [_col("vendedor", "Vendedor", flex=True), _col("vendas_centavos", "Recebido no período", num=True, brl=True),
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
        "colunas": [_col("numero", "Nº"), _col("cliente", "Cliente", flex=True),
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
        "colunas": [_col("numero", "Nº"), _col("cliente", "Cliente", flex=True),
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


AGENDA_STATUS_TAG = {
    "ativo": ("Confirmado", "ok"), "pre_reservado": ("Pré-reserva", "aviso"),
    "cancelado": ("Cancelado", "erro"),
}
AGENDA_STATUS_OPCOES = [
    ("", "Status: todos"), ("ativo", "Confirmado"),
    ("pre_reservado", "Pré-reserva"), ("cancelado", "Cancelado"),
]
AGENDA_DESFECHO_TAG = {
    "realizado": ("Realizado", "ok"), "nao_realizado": ("Não realizado", "erro"),
}
AGENDA_TIPO_ROTULO = {"pessoal": "Pessoal", "empresa": "Empresa", "fornecedor": "Fornecedor"}


def _dados_agenda(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """A Agenda (web/painel_agenda.py) só mostra o que vem — mês corrente e os
    próximos compromissos. Este relatório fecha o período: quantos eventos,
    quantos viraram presença, quantos não aconteceram e quantos foram
    cancelados. `status` (ativo/pré-reserva/cancelado) e `desfecho`
    (realizado/não realizado) já existem desde as migrações 098-179; nenhuma
    coluna nova.

    Cliente tem DUAS fontes, nenhuma delas nova: `orcamentos.evento_agenda_id`
    quando o compromisso nasceu de um orçamento aprovado (mesma regra `empresa
    or cliente` de `_dados_orcamentos`), e `eventos_agenda.prospeccao_id`
    quando nasceu de um lead — `finance.cockpit.agendar_visita` liga os dois
    assim que marca a visita, e é de onde vêm a maioria das linhas "Visita —
    Fulano"/"VISITA TÉCNICA..." que apareciam com Cliente vazio antes desta
    junção. O orçamento manda quando os dois existem (é o registro mais firme
    — o lead pode ter sido reatribuído, o orçamento não). Reunião interna e
    compromisso pessoal criados à mão (`finance.agenda_tools.marcar_evento`)
    não têm nenhum dos dois — o nome, se existe, está só no título digitado à
    mão, sem onde puxar; continuam "—", e é o esperado, não bug.

    "Sinal" é `eventos_agenda.sinal_centavos`, o valor que segura a DATA na
    própria agenda (163_evento_sinal_esperado) — só é gravado no "Só segurar a
    data" do formulário de novo compromisso (web/painel_agenda.py, checkbox
    `segurar`) ou na pré-reserva por orçamento (web/proposta._reservar_na_agenda).
    `agendar_visita` nunca passa esse campo — visita não segura data, então
    R$ 0,00 nessas linhas é o valor certo, não dado faltando. Receita cheia do
    orçamento (não só o sinal) exigiria o mesmo join que a aba Orçamentos já
    faz (`_VALOR_ORC`) e fica pra depois, se fizer falta.

    As métricas do topo ignoram o filtro de Status de propósito, igual em
    Orçamentos: mostram a distribuição inteira do período; o total da tabela é
    só do que está na tela. "Vendedor" no filtro é quem marcou o compromisso
    (`membro_id`), reaproveitando o mesmo seletor de membros da conta."""
    ini, fim = _intervalo(periodo)
    where = ["e.conta_id=%s"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("e.inicio::date >= %s and e.inicio::date <= %s")
        params += [ini, fim]
    if vendedor_sel:
        try:
            where.append("e.membro_id = %s")
            params.append(int(vendedor_sel))
        except (TypeError, ValueError):
            where.pop()
    join_orc = """left join lateral (
                    select coalesce(o.empresa, o.cliente) as nome
                      from orcamentos o
                     where o.evento_agenda_id = e.id
                     order by o.id desc limit 1
                  ) oc on true
                  left join prospeccao p on p.id = e.prospeccao_id"""
    if busca:
        where.append("coalesce(oc.nome, p.contato, p.empresa) ilike %s")
        params.append(f"%{busca}%")
    base_sql = " and ".join(where)

    with pool.connection() as c:
        agg = c.execute(
            f"""select count(*),
                       count(*) filter (where e.desfecho='realizado'),
                       count(*) filter (where e.desfecho='nao_realizado'),
                       count(*) filter (where e.status='cancelado'),
                       coalesce(sum(e.sinal_centavos), 0)
                  from eventos_agenda e
                  {join_orc}
                 where {base_sql}""", params).fetchone()
    n_total = agg[0] or 0
    n_realizado, n_nao_realizado, n_cancelado = agg[1] or 0, agg[2] or 0, agg[3] or 0
    sinal_total = int(agg[4] or 0)

    def _pct(n):
        return f"{n} · {round(n * 100 / n_total)}%" if n_total else f"{n} · 0%"

    where2, params2 = list(where), list(params)
    if status_sel in AGENDA_STATUS_TAG:
        where2.append("e.status = %s")
        params2.append(status_sel)
    where2_sql = " and ".join(where2)

    with pool.connection() as c:
        rows = c.execute(
            f"""select e.inicio, coalesce(e.tipo_evento, e.titulo),
                       coalesce(oc.nome, p.contato, p.empresa),
                       e.tipo, e.status, e.desfecho, e.convidados, e.sinal_centavos
                  from eventos_agenda e
                  {join_orc}
                 where {where2_sql}
                 order by e.inicio asc limit 300""",
            params2).fetchall()

    linhas = []
    for r in rows:
        st_rotulo, st_cor = AGENDA_STATUS_TAG.get(r[4], (r[4] or "—", "neutro"))
        df_rotulo, df_cor = AGENDA_DESFECHO_TAG.get(r[5], ("—", "neutro"))
        linhas.append({
            "inicio": r[0].strftime("%d/%m %H:%M") if r[0] else "—",
            "evento": r[1] or "—", "cliente": r[2] or "—",
            "tipo": AGENDA_TIPO_ROTULO.get(r[3], r[3] or "—"),
            "status": st_rotulo, "status_cor": st_cor,
            "desfecho": df_rotulo, "desfecho_cor": df_cor,
            "convidados": r[6] if r[6] is not None else "—",
            "sinal_centavos": int(r[7] or 0),
        })
    return {
        "label": "Agenda", "mock": False,
        # A elástica aqui é o EVENTO, não o cliente: o título é digitado à mão e
        # não tem teto ("Reunião de alinhamento sobre o contrato da Prefeitura"),
        # enquanto nome de cliente tem tamanho previsível. E este é o relatório
        # mais largo dos sete — oito colunas —, então é onde faltar a elástica
        # dói mais: sem ela a tabela rola pro lado e engole a Data e o começo do
        # título, que foi o defeito do print de 26/08.
        "colunas": [_col("inicio", "Data"), _col("evento", "Evento", flex=True),
                    _col("cliente", "Cliente"), _col("tipo", "Tipo"),
                    _col("status", "Status", tag=True),
                    _col("desfecho", "Desfecho", tag=True),
                    _col("convidados", "Convid.", num=True),
                    _col("sinal_centavos", "Sinal", num=True, brl=True)],
        "linhas": linhas, "col_total": "sinal_centavos",
        "total_centavos": _soma(linhas, "sinal_centavos"),
        "metricas": [("Eventos no período", str(n_total)),
                     ("Realizados", _pct(n_realizado)),
                     ("Não realizados", _pct(n_nao_realizado)),
                     ("Cancelados", _pct(n_cancelado)),
                     ("Sinal no período", _brl(sinal_total))],
        "filtro_extra": {
            "status_opcoes": AGENDA_STATUS_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


# ------------------------------------------------------------- LEADS DO CHIP
#
# O lead que chega pelo QR não tem número pra somar — tem TEMPO DE ESPERA. Este
# relatório existe porque o painel sabia quantas mensagens chegaram e não sabia
# quanto tempo alguém ficou sem resposta: em 26/08, na conta 34, sete pessoas
# tinham chamado o chip principal e ninguém tinha respondido. Nenhuma tela dizia.
#
# A redação (o texto da espera, o rótulo do chip, a mediana) mora em
# `finance/vendas.py`, testável sem banco. Aqui fica só a consulta.

#: os valores do filtro de chip que NÃO são um id. "" é todos; "principal" é o
#: chip da própria conta, que na conversa aparece como `chip_id` NULO.
CHIP_TODOS, CHIP_PRINCIPAL = "", "principal"

#: tom da função pura → classe do selo no template (.rel-tag.ok/.aviso/.erro/.neutro)
_TOM_TAG = {"ok": "ok", "ambar": "aviso", "coral": "erro", "neutro": "neutro"}


def _chips_da_conta(pool, conta_id: int) -> tuple[str, dict[int, str]]:
    """(nome do chip principal, {id_da_conta_chip: nome}) — pra dar nome ao número.

    Duas fontes porque são duas coisas diferentes: o chip PRINCIPAL é um canal
    (`canais_config`, identificador `qr:<conta>`), e o chip SECUNDÁRIO é uma conta
    inteira, ligada por `contas.chip_de`. Uma consulta só não daria conta, e
    inventar um mapa novo daria uma terceira versão da verdade pra divergir.
    """
    with pool.connection() as c:
        prin = c.execute(
            """select rotulo from canais_config
                where conta_id=%s and canal='whatsapp' and identificador=%s
                order by id desc limit 1""",
            (conta_id, f"qr:{conta_id}")).fetchone()
        secs = c.execute("select id, nome from contas where chip_de=%s order by id",
                         (conta_id,)).fetchall()
    return (prin[0] if prin and prin[0] else ""), {r[0]: r[1] or "" for r in secs}


def _dados_leads_chip(pool, conta_id, periodo, chip_sel, vendedor_sel, busca) -> dict:
    """Os leads que entraram por um chip de WhatsApp, e quanto cada um esperou.

    UMA LINHA POR LEAD, não por conversa: o mesmo lead pode ter mais de uma
    conversa, e contá-las duas vezes inflaria "leads recebidos" e a mediana.

    Três decisões que a consulta carrega, cada uma medida em produção:

    1. **`chip_id` NULO é o chip principal**, não é dado faltando (ver
       `vendas.rotulo_do_chip`). Filtrar por `chip_id = %s` esconderia 174 dos
       186 leads da conta 34.
    2. **Resposta é `out` de HUMANO.** Havia 18 mensagens de bot na conta 34;
       contá-las zeraria a espera de quem, na prática, continuou esperando gente.
    3. **"Última msg" vem da conversa, não de `prospeccao.ultimo_contato_em`.**
       Esse campo está vazio em 158 dos 174 leads do chip principal — que têm
       2.772 mensagens trocadas. Lido dali, o relatório anunciaria que quase
       ninguém foi atendido.
    """
    ini, fim = _intervalo(periodo)
    where = ["cv.conta_id=%s", "cv.prospeccao_id is not null"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("p.criado_em::date >= %s and p.criado_em::date <= %s")
        params += [ini, fim]
    if chip_sel == CHIP_PRINCIPAL:
        where.append("cv.chip_id is null")
    elif chip_sel:
        where.append("cv.chip_id = %s")
        params.append(int(chip_sel))
    if vendedor_sel:
        where.append("cv.responsavel_membro_id = %s")
        params.append(int(vendedor_sel))
    if busca:
        where.append("p.empresa ilike %s")
        params.append(f"%{busca}%")

    sql = f"""
    with conv as (
      select cv.id, cv.prospeccao_id, cv.chip_id, cv.criado_em, cv.ultima_msg_em,
             cv.responsavel_membro_id,
             (select min(m.criado_em) from mensagens m
               where m.conversa_id=cv.id and m.direcao='in') as prim_in,
             -- só HUMANO: o bot respondendo não é a empresa respondendo
             (select min(m.criado_em) from mensagens m
               where m.conversa_id=cv.id and m.direcao='out'
                 and m.autor='humano') as prim_resp,
             (select count(*) from mensagens m where m.conversa_id=cv.id) as msgs
        from conversas cv join prospeccao p on p.id = cv.prospeccao_id
       where {" and ".join(where)}
    ), por_lead as (
      select prospeccao_id as lead_id,
             -- o chip da PRIMEIRA conversa: é por ele que o lead entrou.
             -- `array_agg` e não `min` porque o nulo aqui tem significado (chip
             -- principal) e `min` o descartaria em favor de um id qualquer.
             (array_agg(chip_id order by criado_em))[1] as chip_id,
             min(prim_in) as prim_in, min(prim_resp) as prim_resp,
             sum(msgs) as msgs, max(ultima_msg_em) as ultima_msg,
             (array_agg(responsavel_membro_id
                        order by ultima_msg_em desc nulls last))[1] as memb
        from conv group by prospeccao_id
    )
    select p.id, p.empresa, l.chip_id, l.prim_in, l.prim_resp, l.msgs,
           l.ultima_msg, coalesce(mb.nome, '—'), o.numero
      from por_lead l
      join prospeccao p on p.id = l.lead_id
      left join membros mb on mb.id = l.memb
      left join orcamentos o on o.id = p.orcamento_id
     order by l.prim_in desc nulls last, p.id desc
     limit 300"""
    with pool.connection() as c:
        rows = c.execute(sql, params).fetchall()

    nome_prin, rot_secs = _chips_da_conta(pool, conta_id)
    linhas, esperas = [], []
    n_nunca = n_orc = 0
    for r in rows:
        esp = vendas.espera_do_lead(r[3], r[4])
        if esp["minutos"] is not None:
            esperas.append(esp["minutos"])
        if esp["texto"] == "nunca respondido":
            n_nunca += 1
        if r[8]:
            n_orc += 1
        linhas.append({
            "lead": r[1] or "—",
            "chip": vendas.rotulo_do_chip(r[2], rotulos=rot_secs,
                                          nome_principal=nome_prin),
            "entrou": _fmt_hora(r[3]),
            "esperou": esp["texto"], "esperou_cor": _TOM_TAG[esp["tom"]],
            "msgs": int(r[5] or 0),
            "vendedor": r[7],
            "ultima": _fmt_hora(r[6]),
            "orcamento": f"nº {r[8]}" if r[8] else "—",
        })

    med = vendas.mediana(esperas)
    return {
        "label": "Leads do chip", "mock": False,
        # o NOME DO LEAD é a coluna elástica: é a única de texto livre aqui, e as
        # outras (chip, hora, espera, contagem) têm largura previsível. Sem uma
        # marcada, a tabela volta a rolar pro lado e engole o começo do nome.
        "colunas": [_col("lead", "Lead", flex=True), _col("chip", "Chip"),
                    _col("entrou", "Entrou em"),
                    _col("esperou", "Esperou", tag=True),
                    _col("msgs", "Msgs", num=True),
                    _col("vendedor", "Vendedor"), _col("ultima", "Última msg"),
                    _col("orcamento", "Orçamento")],
        "linhas": linhas,
        # SEM total: `valor_estimado_centavos` é zero nos 675 leads da base, e uma
        # linha "Total R$ 0,00" seria exatamente o ruído que o funil acabou de
        # tirar. O template pula a linha quando `col_total` é nulo.
        "col_total": None, "total_centavos": 0,
        "metricas": [("Leads recebidos", str(len(linhas))),
                     ("Nunca respondidos", str(n_nunca)),
                     ("Espera (mediana)", vendas.duracao_curta(med)),
                     ("Viraram orçamento", str(n_orc))],
        "filtro_extra": {
            "chips": _opcoes_de_chip(nome_prin, rot_secs),
            "chip_sel": str(chip_sel or ""),
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


def _opcoes_de_chip(nome_prin: str, rot_secs: dict[int, str]) -> list[tuple[str, str]]:
    """As opções do filtro. Só oferece escolha quando há mais de um chip — numa
    conta com chip único o seletor seria uma pergunta de resposta única."""
    if not rot_secs:
        return []
    return ([(CHIP_TODOS, "Chip: todos"),
             (CHIP_PRINCIPAL, nome_prin or "Chip principal")]
            + [(str(i), n or f"Chip {i}") for i, n in sorted(rot_secs.items())])


def _fmt_hora(d) -> str:
    """dd/mm HH:MM no fuso de Brasília — a hora importa aqui (a espera é medida em
    minutos), e `_fmt` só mostra a data."""
    return d.astimezone(_ag.BRT).strftime("%d/%m %H:%M") if d else "—"


# ----------------------------------------------------------- FUNIL COMERCIAL
#
# Os quatro indicadores que o consultor da Prime pediu, cada um com a COBERTURA
# do dado ao lado. A régua está em `finance/vendas.py`; aqui fica só a consulta.
#
# A visita é identificada por três coisas juntas, e nenhuma delas é palpite:
#   * `titulo ilike 'visita%'` — é como o Cockpit batiza (`finance/cockpit.py`,
#     "Visita — {quem}") e como o time batiza na mão ("VISITA TÉCNICA - PEDRO");
#   * `status <> 'cancelado'` — visita cancelada não foi agendada pra valer, e
#     contá-la inflaria o degrau de cima sem inflar nenhum de baixo;
#   * `tipo_evento is null` — quando esse campo vem preenchido (Casamento,
#     Locação...) o compromisso é a FESTA do cliente, não a visita dele ao espaço.

#: o degrau "leads" do funil sai da mesma fonte da aba Leads do chip: quem chegou
#: por uma conversa de WhatsApp. Somar a base garimpada no Maps aqui misturaria
#: duas espécies de lead e faria a taxa de conversão despencar por artifício.
_SQL_VISITAS = """
    select e.id,
           coalesce(p.empresa, replace(e.titulo, 'Visita — ', '')) as lead,
           (e.prospeccao_id is not null) as ligado,
           coalesce(mb.nome, '—') as vendedor,
           e.inicio, (e.inicio < now()) as passou, e.desfecho,
           -- quanto o lead esperou da primeira mensagem dele até a visita ser
           -- marcada. Só existe pra visita ligada a um lead — nas soltas não há
           -- de quem medir.
           case when p.id is null then null else
             (select min(m.criado_em) from mensagens m
                join conversas cv on cv.id = m.conversa_id
               where cv.prospeccao_id = p.id and m.direcao='in') end as lead_chegou,
           e.criado_em
      from eventos_agenda e
      left join prospeccao p on p.id = e.prospeccao_id and p.conta_id = e.conta_id
      left join membros mb on mb.id = e.membro_id
     where e.conta_id = %s
       and e.titulo ilike 'visita%%'
       and coalesce(e.status,'') <> 'cancelado'
       and e.tipo_evento is null
"""


def _dados_funil(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """O funil comercial: lead → visita agendada → aconteceu → respondida → sinal.

    Cada taxa vem com a cobertura porque, sem ela, o relatório mente por omissão:
    em 26/08, na conta 34, "2 de 3 apareceram" pareceria 67% de comparecimento —
    mas 8 visitas já tinham acontecido e 5 estavam sem resposta nenhuma.
    """
    ini, fim = _intervalo(periodo)
    where, params = "", [conta_id]
    if periodo != "todos":
        where += " and e.inicio::date >= %s and e.inicio::date <= %s"
        params += [ini, fim]
    if vendedor_sel:
        where += " and e.membro_id = %s"
        params.append(int(vendedor_sel))
    if busca:
        where += " and (p.empresa ilike %s or e.titulo ilike %s)"
        params += [f"%{busca}%", f"%{busca}%"]
    if status_sel == "sem_resposta":
        where += " and e.inicio < now() and e.desfecho is null"
    elif status_sel == "respondidas":
        where += " and e.desfecho is not null"
    elif status_sel == "sem_lead":
        where += " and e.prospeccao_id is null"

    with pool.connection() as c:
        rows = c.execute(_SQL_VISITAS + where + " order by e.inicio desc limit 300",
                         params).fetchall()
        # os leads que entraram por conversa — o topo do funil. Fora do filtro de
        # vendedor de propósito: o lead chega antes de ter dono, e recortar por
        # vendedor aqui daria uma taxa de conversão sobre um universo que o
        # vendedor nunca teve a chance de atender.
        p2: list = [conta_id]
        sql_leads = ("select count(distinct cv.prospeccao_id) from conversas cv "
                     "where cv.conta_id=%s and cv.prospeccao_id is not null")
        if periodo != "todos":
            sql_leads += " and cv.criado_em::date >= %s and cv.criado_em::date <= %s"
            p2 += [ini, fim]
        n_leads = c.execute(sql_leads, p2).fetchone()[0] or 0
        n_sinal = c.execute(
            "select count(*) from orcamentos where conta_id=%s and sinal_pago_em is not null",
            (conta_id,)).fetchone()[0] or 0
        n_orc = c.execute("select count(*) from orcamentos where conta_id=%s",
                          (conta_id,)).fetchone()[0] or 0

    linhas, esperas = [], []
    n_agendadas = n_ligadas = n_passou = n_respondidas = n_apareceu = 0
    for r in rows:
        n_agendadas += 1
        if r[2]:
            n_ligadas += 1
        d = vendas.desfecho_da_visita(r[6], bool(r[5]))
        if r[5]:
            n_passou += 1
        if d["conta_no_comparecimento"]:
            n_respondidas += 1
            if r[6] == vendas.VISITA_APARECEU:
                n_apareceu += 1
        espera = None
        if r[7] and r[8]:
            espera = max(0, int((r[8] - r[7]).total_seconds() // 60))
            esperas.append(espera)
        linhas.append({
            "lead": r[1] or "—",
            "vendedor": r[3],
            "marcada": _fmt_hora(r[4]),
            "esperou": vendas.duracao_curta(espera) if espera is not None else "sem lead",
            "desfecho": d["texto"], "desfecho_cor": _TOM_TAG[d["tom"]],
        })

    # AS TAXAS. `base` é o que revela o buraco — ver vendas.taxa_com_cobertura.
    t_agendou = vendas.taxa_com_cobertura(n_agendadas, n_leads) if n_leads else \
        vendas.taxa_com_cobertura(0, 0)
    t_compareceu = vendas.taxa_com_cobertura(n_apareceu, n_respondidas, base=n_passou)
    t_sinal = vendas.taxa_com_cobertura(n_sinal, n_orc) if n_orc else \
        vendas.taxa_com_cobertura(0, 0)
    med = vendas.mediana(esperas)

    return {
        "label": "Funil", "mock": False,
        "colunas": [_col("lead", "Lead", flex=True), _col("vendedor", "Vendedor"),
                    _col("marcada", "Visita marcada"),
                    _col("esperou", "Esperou p/ agendar"),
                    _col("desfecho", "O cliente apareceu?", tag=True)],
        "linhas": linhas,
        # sem dinheiro nesta aba — mesma razão da aba Leads do chip
        "col_total": None, "total_centavos": 0,
        "metricas": [
            ("Leads → visita agendada",
             f"{t_agendou['texto']} · {n_agendadas} de {n_leads}"),
            ("Compareceram",
             f"{t_compareceu['texto']} · "
             + vendas.texto_da_cobertura(n_respondidas, n_passou, o_que="respondidas")),
            ("Viraram sinal pago",
             f"{t_sinal['texto']} · {n_sinal} de {n_orc} orçamentos"),
            ("Lead → agendamento (mediana)", vendas.duracao_curta(med)),
        ],
        # o AVISO é o achado, não rodapé: enquanto a maior parte das visitas que já
        # aconteceram estiver sem resposta, a taxa de comparecimento acima não
        # sustenta decisão nenhuma, e a tela tem que dizer isso antes da tabela.
        "aviso_config": (
            f"{n_passou - n_respondidas} das {n_passou} visitas que já aconteceram "
            "estão sem resposta — ninguém marcou se o cliente apareceu. Enquanto "
            "isso, a taxa de comparecimento sai de uma amostra pequena demais pra "
            "decidir. O vendedor responde pelo Cockpit, no bloco “Precisa de "
            "resposta”."
        ) if (n_passou - n_respondidas) > 0 else "",
        "filtro_extra": {
            "status_opcoes": FUNIL_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


FUNIL_OPCOES = [
    ("", "Todas as visitas"), ("sem_resposta", "— Sem resposta —"),
    ("respondidas", "Respondidas"), ("sem_lead", "Sem lead ligado"),
]


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
    "agenda": {"label": "Agenda", "montar": lambda pool, cid, per, **f: _dados_agenda(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
    # o filtro de chip viaja no MESMO parâmetro `status` das outras abas, de
    # propósito: o template já tem esse select e a rota já o repassa. Um
    # parâmetro novo obrigaria a mexer nos dois pra não ganhar nada.
    "leads_chip": {"label": "Leads do chip", "montar": lambda pool, cid, per, **f: _dados_leads_chip(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
    "funil": {"label": "Funil", "montar": lambda pool, cid, per, **f: _dados_funil(
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
