"""Ferramentas do agente financeiro.

Cada ferramenta e' ligada ao livro-caixa de UM usuario. O agente chama elas
quando voce pede ("lanca 50 de mercado") ou quando le uma nota por foto.
"""
from datetime import date, datetime

from core.agent import Ferramenta
from .livro_caixa import LivroCaixa
from .models import (
    Lancamento, Tipo, CATEGORIAS_DESPESA, CATEGORIAS_RECEITA,
    formatar_brl,
)


def _parse_data(s: str | None) -> date:
    if not s:
        return date.today()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()


def construir_ferramentas(livro: LivroCaixa) -> list[Ferramenta]:
    def lancar(tipo: Tipo, entrada: dict) -> str:
        lanc = Lancamento.criar(
            tipo=tipo,
            valor_reais=entrada["valor"],
            categoria=entrada.get("categoria", "Outros"),
            descricao=entrada.get("descricao", ""),
            data=_parse_data(entrada.get("data")),
            pagamento=entrada.get("pagamento", ""),
            origem=entrada.get("origem", "manual"),
        )
        salvo = livro.adicionar(lanc)
        rotulo = "Despesa" if tipo == Tipo.DESPESA else "Receita"
        return (f"{rotulo} registrada: {formatar_brl(salvo.valor_centavos)} em "
                f"{salvo.categoria} ({salvo.data.strftime('%d/%m/%Y')}). id={salvo.id}")

    def lancar_despesa(entrada: dict) -> str:
        return lancar(Tipo.DESPESA, entrada)

    def lancar_receita(entrada: dict) -> str:
        return lancar(Tipo.RECEITA, entrada)

    def ver_saldo(_entrada: dict) -> str:
        return f"Saldo atual: {formatar_brl(livro.saldo_centavos())}"

    def relatorio_mes(entrada: dict) -> str:
        hoje = date.today()
        ano = int(entrada.get("ano") or hoje.year)
        mes = int(entrada.get("mes") or hoje.month)
        desp = livro.total_por_categoria(Tipo.DESPESA, mes=mes, ano=ano)
        total = sum(desp.values())
        if not desp:
            return f"Sem despesas em {mes:02d}/{ano}."
        linhas = [f"- {cat}: {formatar_brl(val)}" for cat, val in desp.items()]
        return (f"Despesas de {mes:02d}/{ano} (total {formatar_brl(total)}):\n"
                + "\n".join(linhas))

    valor_schema = {"type": "number", "description": "Valor em reais, ex: 87.40"}

    return [
        Ferramenta(
            nome="lancar_despesa",
            descricao="Registra uma despesa (saida de dinheiro) no livro-caixa.",
            parametros={
                "type": "object",
                "properties": {
                    "valor": valor_schema,
                    "categoria": {"type": "string", "enum": CATEGORIAS_DESPESA},
                    "descricao": {"type": "string"},
                    "data": {"type": "string", "description": "dd/mm/aaaa; vazio = hoje"},
                    "pagamento": {"type": "string", "description": "ex: Pix, cartao, dinheiro"},
                    "origem": {"type": "string", "enum": ["manual", "foto"]},
                },
                "required": ["valor", "categoria"],
            },
            executar=lancar_despesa,
        ),
        Ferramenta(
            nome="lancar_receita",
            descricao="Registra uma receita (entrada de dinheiro) no livro-caixa.",
            parametros={
                "type": "object",
                "properties": {
                    "valor": valor_schema,
                    "categoria": {"type": "string", "enum": CATEGORIAS_RECEITA},
                    "descricao": {"type": "string"},
                    "data": {"type": "string", "description": "dd/mm/aaaa; vazio = hoje"},
                    "origem": {"type": "string", "enum": ["manual", "foto"]},
                },
                "required": ["valor", "categoria"],
            },
            executar=lancar_receita,
        ),
        Ferramenta(
            nome="ver_saldo",
            descricao="Mostra o saldo atual (receitas menos despesas).",
            parametros={"type": "object", "properties": {}},
            executar=ver_saldo,
        ),
        Ferramenta(
            nome="relatorio_mes",
            descricao="Resumo de despesas por categoria de um mes.",
            parametros={
                "type": "object",
                "properties": {
                    "mes": {"type": "integer", "description": "1 a 12; vazio = mes atual"},
                    "ano": {"type": "integer", "description": "vazio = ano atual"},
                },
            },
            executar=relatorio_mes,
        ),
    ]
