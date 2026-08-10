"""Módulo "Relatórios" — MOCKUP visual (dados de exemplo, sem ligação com o banco).

Objetivo desta etapa: validar layout e navegação dos 6 relatórios pedidos —
Vendas, Contas a pagar, Contas a receber, (Contas) pagas, Comissão e Contas
recebidas — com resultado na tela e exportação em PDF (impressão do navegador,
mesmo padrão já usado no holerite e na separação de pedidos).

Depois de aprovado o layout, os dados de exemplo daqui entram trocados por
consultas reais (livro_caixa/plano_contas/vendas), mantendo a mesma tela.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from contas import equipe as eq
from web.portal import _render, _env, conta_logada, brl as _brl

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


def _soma(linhas, chave):
    return sum(int(r[chave]) for r in linhas)


def _col(chave, rotulo, num=False, brl=False, tag=False):
    return {"chave": chave, "rotulo": rotulo, "num": num, "brl": brl, "tag": tag}


def _dados_vendas():
    linhas = [
        {"data": "10/08", "cliente": "Mercado Bom Preço Ltda", "descricao": "Hortifruti variado (32 itens)", "forma": "Pix", "vendedor": "Ana Souza", "valor_centavos": 84250},
        {"data": "10/08", "cliente": "Restaurante Sabor Caseiro", "descricao": "Cesta semanal", "forma": "Boleto", "vendedor": "Carlos Lima", "valor_centavos": 123000},
        {"data": "09/08", "cliente": "Padaria Trigo Dourado", "descricao": "Frutas e verduras", "forma": "Dinheiro", "vendedor": "Ana Souza", "valor_centavos": 45680},
        {"data": "08/08", "cliente": "Empório da Vila", "descricao": "Reposição semanal", "forma": "Crédito", "vendedor": "Carlos Lima", "valor_centavos": 98750},
        {"data": "07/08", "cliente": "Hortifruti Popular", "descricao": "Pedido avulso", "forma": "Pix", "vendedor": "Bruna Ferreira", "valor_centavos": 32900},
        {"data": "06/08", "cliente": "Restaurante Sabor Caseiro", "descricao": "Cesta semanal", "forma": "Boleto", "vendedor": "Carlos Lima", "valor_centavos": 118000},
        {"data": "05/08", "cliente": "Mercado Bom Preço Ltda", "descricao": "Hortifruti variado", "forma": "Pix", "vendedor": "Ana Souza", "valor_centavos": 79900},
        {"data": "04/08", "cliente": "Empório da Vila", "descricao": "Reposição semanal", "forma": "Débito", "vendedor": "Bruna Ferreira", "valor_centavos": 91200},
    ]
    total = _soma(linhas, "valor_centavos")
    hoje = _soma([r for r in linhas if r["data"] == "10/08"], "valor_centavos")
    n = len(linhas)
    return {
        "label": "Vendas",
        "colunas": [_col("data", "Data"), _col("cliente", "Cliente"), _col("descricao", "Descrição"),
                    _col("forma", "Forma"), _col("vendedor", "Vendedor"),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total vendido", _brl(total)), ("Nº de vendas", str(n)),
                     ("Ticket médio", _brl(total // n if n else 0)), ("Vendido hoje", _brl(hoje))],
    }


def _dados_contas_pagar():
    linhas = [
        {"vencimento": "05/08", "fornecedor": "Contabilidade Silva & Assoc.", "categoria": "Serviços", "forma": "Transferência", "status": "Vencida", "status_cor": "erro", "valor_centavos": 35000},
        {"vencimento": "08/08", "fornecedor": "Energisa (energia)", "categoria": "Utilidades", "forma": "Débito automático", "status": "Vencida", "status_cor": "erro", "valor_centavos": 45680},
        {"vencimento": "12/08", "fornecedor": "Distribuidora Frutas & Cia", "categoria": "Mercadoria", "forma": "Boleto", "status": "A vencer", "status_cor": "aviso", "valor_centavos": 320000},
        {"vencimento": "14/08", "fornecedor": "Transportadora Rota Certa", "categoria": "Frete", "forma": "Pix", "status": "A vencer", "status_cor": "aviso", "valor_centavos": 62000},
        {"vencimento": "15/08", "fornecedor": "Imobiliária Central (aluguel)", "categoria": "Aluguel", "forma": "Boleto", "status": "A vencer", "status_cor": "aviso", "valor_centavos": 280000},
        {"vencimento": "20/08", "fornecedor": "Fornecedor Grãos do Vale", "categoria": "Mercadoria", "forma": "Boleto", "status": "A vencer", "status_cor": "ok", "valor_centavos": 154000},
    ]
    total = _soma(linhas, "valor_centavos")
    vencidas = [r for r in linhas if r["status"] == "Vencida"]
    a_vencer_7d = [r for r in linhas if r["status_cor"] == "aviso"]
    return {
        "label": "Contas a pagar",
        "colunas": [_col("vencimento", "Vencimento"), _col("fornecedor", "Fornecedor"), _col("categoria", "Categoria"),
                    _col("forma", "Forma prevista"), _col("status", "Status", tag=True),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total em aberto", _brl(total)),
                     ("Vencidas", f"{len(vencidas)} · {_brl(_soma(vencidas, 'valor_centavos'))}"),
                     ("A vencer em 7 dias", _brl(_soma(a_vencer_7d, "valor_centavos")))],
    }


def _dados_contas_receber():
    linhas = [
        {"vencimento": "03/08", "cliente": "Hortifruti Popular", "descricao": "Boleto #955", "status": "Vencida", "status_cor": "erro", "valor_centavos": 32900},
        {"vencimento": "09/08", "cliente": "Restaurante Sabor Caseiro", "descricao": "Boleto #988", "status": "Vencida", "status_cor": "erro", "valor_centavos": 61000},
        {"vencimento": "11/08", "cliente": "Mercado Bom Preço Ltda", "descricao": "Venda a prazo #1042", "status": "A vencer", "status_cor": "aviso", "valor_centavos": 84250},
        {"vencimento": "12/08", "cliente": "Restaurante Sabor Caseiro", "descricao": "Venda a prazo #1061", "status": "A vencer", "status_cor": "aviso", "valor_centavos": 118000},
        {"vencimento": "18/08", "cliente": "Empório da Vila", "descricao": "Venda a prazo #1050", "status": "A vencer", "status_cor": "ok", "valor_centavos": 98750},
        {"vencimento": "25/08", "cliente": "Padaria Trigo Dourado", "descricao": "Nota #1077", "status": "A vencer", "status_cor": "ok", "valor_centavos": 45680},
    ]
    total = _soma(linhas, "valor_centavos")
    vencidas = [r for r in linhas if r["status"] == "Vencida"]
    a_vencer_7d = [r for r in linhas if r["status_cor"] == "aviso"]
    return {
        "label": "Contas a receber",
        "colunas": [_col("vencimento", "Vencimento"), _col("cliente", "Cliente"), _col("descricao", "Documento"),
                    _col("status", "Status", tag=True), _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total a receber", _brl(total)),
                     ("Vencidas", f"{len(vencidas)} · {_brl(_soma(vencidas, 'valor_centavos'))}"),
                     ("A vencer em 7 dias", _brl(_soma(a_vencer_7d, "valor_centavos")))],
    }


def _dados_pagas():
    linhas = [
        {"data": "10/08", "fornecedor": "Distribuidora Frutas & Cia", "categoria": "Mercadoria", "forma": "Pix", "valor_centavos": 298000},
        {"data": "08/08", "fornecedor": "Energisa (energia)", "categoria": "Utilidades", "forma": "Débito automático", "valor_centavos": 45680},
        {"data": "05/08", "fornecedor": "Contabilidade Silva & Assoc.", "categoria": "Serviços", "forma": "Transferência", "valor_centavos": 35000},
        {"data": "01/08", "fornecedor": "Imobiliária Central (aluguel)", "categoria": "Aluguel", "forma": "Boleto", "valor_centavos": 280000},
        {"data": "30/07", "fornecedor": "Transportadora Rota Certa", "categoria": "Frete", "forma": "Pix", "valor_centavos": 58000},
        {"data": "28/07", "fornecedor": "Fornecedor Grãos do Vale", "categoria": "Mercadoria", "forma": "Boleto", "valor_centavos": 154000},
    ]
    total = _soma(linhas, "valor_centavos")
    maior = max(linhas, key=lambda r: r["valor_centavos"])
    return {
        "label": "Contas pagas",
        "colunas": [_col("data", "Pagamento"), _col("fornecedor", "Fornecedor"), _col("categoria", "Categoria"),
                    _col("forma", "Forma"), _col("valor_centavos", "Valor pago", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total pago no período", _brl(total)), ("Nº de pagamentos", str(len(linhas))),
                     ("Maior pagamento", _brl(maior["valor_centavos"]))],
    }


def _dados_comissao():
    linhas = [
        {"vendedor": "Ana Souza", "vendas_centavos": 1284500, "percentual": "5%", "comissao_centavos": 64225},
        {"vendedor": "Carlos Lima", "vendas_centavos": 985000, "percentual": "4%", "comissao_centavos": 39400},
        {"vendedor": "Bruna Ferreira", "vendas_centavos": 621000, "percentual": "5%", "comissao_centavos": 31050},
    ]
    total = _soma(linhas, "comissao_centavos")
    destaque = max(linhas, key=lambda r: r["comissao_centavos"])
    vendas_totais = _soma(linhas, "vendas_centavos")
    return {
        "label": "Comissão",
        "colunas": [_col("vendedor", "Vendedor"), _col("vendas_centavos", "Vendas no período", num=True, brl=True),
                    _col("percentual", "% comissão", num=True), _col("comissao_centavos", "Comissão a pagar", num=True, brl=True)],
        "linhas": linhas, "col_total": "comissao_centavos", "total_centavos": total,
        "metricas": [("Total de comissões", _brl(total)), ("Vendedor destaque", destaque["vendedor"]),
                     ("Vendas da equipe", _brl(vendas_totais))],
    }


def _dados_recebidas():
    linhas = [
        {"data": "09/08", "cliente": "Mercado Bom Preço Ltda", "descricao": "Boleto #1030", "forma": "Pix", "valor_centavos": 79900},
        {"data": "07/08", "cliente": "Restaurante Sabor Caseiro", "descricao": "Venda a prazo #1055", "forma": "Boleto", "valor_centavos": 118000},
        {"data": "05/08", "cliente": "Empório da Vila", "descricao": "Nota #1041", "forma": "Transferência", "valor_centavos": 91200},
        {"data": "02/08", "cliente": "Hortifruti Popular", "descricao": "Boleto #1022", "forma": "Pix", "valor_centavos": 45680},
        {"data": "30/07", "cliente": "Padaria Trigo Dourado", "descricao": "Venda a prazo #1015", "forma": "Dinheiro", "valor_centavos": 62000},
    ]
    total = _soma(linhas, "valor_centavos")
    maior = max(linhas, key=lambda r: r["valor_centavos"])
    return {
        "label": "Contas recebidas",
        "colunas": [_col("data", "Recebimento"), _col("cliente", "Cliente"), _col("descricao", "Documento"),
                    _col("forma", "Forma"), _col("valor_centavos", "Valor recebido", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total recebido no período", _brl(total)), ("Nº de recebimentos", str(len(linhas))),
                     ("Maior recebimento", _brl(maior["valor_centavos"]))],
    }


TIPOS = {
    "vendas": {"label": "Vendas", "montar": _dados_vendas},
    "contas_pagar": {"label": "Contas a pagar", "montar": _dados_contas_pagar},
    "contas_receber": {"label": "Contas a receber", "montar": _dados_contas_receber},
    "pagas": {"label": "Contas pagas", "montar": _dados_pagas},
    "comissao": {"label": "Comissão", "montar": _dados_comissao},
    "recebidas": {"label": "Contas recebidas", "montar": _dados_recebidas},
}


def _contexto(tipo: str, periodo: str):
    tipo = tipo if tipo in TIPOS else "vendas"
    periodo = periodo if periodo in _PERIODO_ROTULO else "mes"
    dados = TIPOS[tipo]["montar"]()
    return tipo, periodo, dados


@router.get("/painel/relatorios", response_class=HTMLResponse)
def painel_relatorios(request: Request, tipo: str = "vendas", periodo: str = "mes"):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    tipo, periodo, dados = _contexto(tipo, periodo)
    return _render("relatorios", request, tipos=TIPOS, tipo=tipo, periodo=periodo, periodos=PERIODOS,
                   periodo_rotulo=_PERIODO_ROTULO[periodo], dados=dados)


@router.get("/painel/relatorios/pdf", response_class=HTMLResponse)
def painel_relatorios_pdf(request: Request, tipo: str = "vendas", periodo: str = "mes"):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    tipo, periodo, dados = _contexto(tipo, periodo)
    from datetime import datetime
    return HTMLResponse(_env.get_template("relatorio_pdf").render(
        dados=dados, tipo=tipo, periodo=periodo, periodo_rotulo=_PERIODO_ROTULO[periodo],
        conta_nome=conta[2] or "", gerado_em=datetime.now().strftime("%d/%m/%Y %H:%M"),
    ))
