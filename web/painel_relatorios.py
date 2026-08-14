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


TIPOS = {
    "vendas": {"label": "Vendas", "montar": lambda pool, cid, per: _dados_vendas(pool, cid, per)},
    "contas_pagar": {"label": "Contas a pagar", "montar": lambda pool, cid, per: _dados_titulos_abertos(pool, cid, "pagar")},
    "contas_receber": {"label": "Contas a receber", "montar": lambda pool, cid, per: _dados_titulos_abertos(pool, cid, "receber")},
    "pagas": {"label": "Contas pagas", "montar": lambda pool, cid, per: _dados_titulos_pagos(pool, cid, "pagar", per)},
    "comissao": {"label": "Comissão", "montar": lambda pool, cid, per: _dados_comissao(pool, cid, per)},
    "recebidas": {"label": "Contas recebidas", "montar": lambda pool, cid, per: _dados_titulos_pagos(pool, cid, "receber", per)},
}


def _contexto(conta_id: int, tipo: str, periodo: str):
    tipo = tipo if tipo in TIPOS else "vendas"
    periodo = periodo if periodo in _PERIODO_ROTULO else "mes"
    dados = TIPOS[tipo]["montar"](get_pool(), conta_id, periodo)
    return tipo, periodo, dados


@router.get("/painel/relatorios", response_class=HTMLResponse)
def painel_relatorios(request: Request, tipo: str = "vendas", periodo: str = "mes"):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    tipo, periodo, dados = _contexto(conta[0], tipo, periodo)
    return _render("relatorios", request, tipos=TIPOS, tipo=tipo, periodo=periodo, periodos=PERIODOS,
                   periodo_rotulo=_PERIODO_ROTULO[periodo], dados=dados)


@router.get("/painel/relatorios/pdf", response_class=HTMLResponse)
def painel_relatorios_pdf(request: Request, tipo: str = "vendas", periodo: str = "mes"):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    tipo, periodo, dados = _contexto(conta[0], tipo, periodo)
    from datetime import datetime
    return HTMLResponse(_env.get_template("relatorio_pdf").render(
        dados=dados, tipo=tipo, periodo=periodo, periodo_rotulo=_PERIODO_ROTULO[periodo],
        gerado_em=datetime.now().strftime("%d/%m/%Y %H:%M"),
        **_letterhead(pool, conta),
    ))
