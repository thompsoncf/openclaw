"""Modelos do livro-caixa.

Aqui mora a "linguagem" do dinheiro: o que e' um lancamento, quais os tipos
e quais as categorias validas. Valores sao guardados em centavos (inteiro)
pra nunca ter erro de arredondamento de float.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum


class Tipo(str, Enum):
    DESPESA = "despesa"
    RECEITA = "receita"


CATEGORIAS_DESPESA = [
    "Mercado", "Restaurante", "Transporte", "Moradia", "Contas de casa",
    "Saúde", "Educação", "Lazer", "Compras", "Assinaturas", "Impostos",
    "Pet", "Outros",
]

CATEGORIAS_RECEITA = [
    "Salário", "Freela", "Investimentos", "Vendas", "Reembolso", "Outros",
]


def categorias_de(tipo: Tipo) -> list[str]:
    return CATEGORIAS_DESPESA if Tipo(tipo) == Tipo.DESPESA else CATEGORIAS_RECEITA


def normalizar_categoria(tipo: Tipo, categoria: str) -> str:
    """Casa a categoria ignorando maiusculas/minusculas. Se nao bater, cai em 'Outros'."""
    validas = categorias_de(tipo)
    alvo = (categoria or "").strip().casefold()
    for c in validas:
        if c.casefold() == alvo:
            return c
    return "Outros"


def reais_para_centavos(valor) -> int:
    d = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if d < 0:
        raise ValueError("valor nao pode ser negativo")
    return int((d * 100).to_integral_value())


def centavos_para_reais(centavos: int) -> Decimal:
    return (Decimal(centavos) / 100).quantize(Decimal("0.01"))


def formatar_brl(centavos: int) -> str:
    reais = centavos_para_reais(centavos)
    s = f"{reais:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


@dataclass
class Lancamento:
    tipo: Tipo
    valor_centavos: int
    categoria: str
    descricao: str = ""
    data: date = field(default_factory=date.today)
    pagamento: str = ""
    origem: str = "manual"
    comprovante: str = ""
    id: int | None = None

    @classmethod
    def criar(cls, tipo, valor_reais, categoria, descricao="", data=None,
              pagamento="", origem="manual", comprovante=""):
        tipo = Tipo(tipo)
        return cls(
            tipo=tipo,
            valor_centavos=reais_para_centavos(valor_reais),
            categoria=normalizar_categoria(tipo, categoria),
            descricao=descricao,
            data=data or date.today(),
            pagamento=pagamento,
            origem=origem,
            comprovante=comprovante,
        )
