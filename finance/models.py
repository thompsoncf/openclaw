"""Modelos do livro-caixa.

Aqui mora a "linguagem" do dinheiro: o que e' um lancamento, quais os tipos
e quais as categorias validas. Valores sao guardados em centavos (inteiro)
pra nunca ter erro de arredondamento de float.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import unicodedata


def normalizar_descricao(texto: str) -> str:
    """Normaliza descricao de produto pra busca: minuscula, sem acento, sem
    espacos extras. Ex: 'Coca-Cola 2L  ' -> 'coca-cola 2l'. Usada pra casar o
    mesmo produto entre cupons diferentes no banco de precos."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


# Abreviacoes COMUNS e NAO-AMBIGUAS de cupom -> nome legivel. Conservador de
# proposito: o Claude ja' expande na leitura; isto e' so' rede de seguranca pra
# manter consistencia. NAO incluir ambiguas (cond=condicionador/condimento etc).
_ABREVIACOES = {
    "fd": "Fralda", "fds": "Fraldas",
    "refri": "Refrigerante", "refrig": "Refrigerante",
    "achoc": "Achocolatado",
    "amac": "Amaciante",
    "det": "Detergente",
    "desod": "Desodorante",
    "shamp": "Shampoo",
    "marg": "Margarina",
    "bisc": "Biscoito",
    "iog": "Iogurte",
    "req": "Requeijao",
    "ajust": "Ajuste", "ajustt": "Ajuste",
}


def expandir_abreviacoes(texto: str) -> str:
    """Expande abreviacoes comuns de cupom no NOME DE EXIBICAO, token a token e
    sem distincao de caixa. Ex: 'FD Pampers Pants' -> 'Fralda Pampers Pants'.
    So' mexe em tokens que estao no dicionario; o resto fica como veio."""
    if not texto:
        return texto
    saida = []
    for tok in texto.split():
        chave = tok.lower().strip(".,;:/")
        saida.append(_ABREVIACOES.get(chave, tok))
    return " ".join(saida)


def limpar_nome_produto(texto: str) -> str:
    """Limpa o NOME DE EXIBICAO do produto (o que aparece pro usuario), tirando
    lixo comum de cupom: desconto grudado, parenteses de promo, codigos soltos,
    espacos duplos. NAO expande abreviacoes (isso e' outro problema). Preserva
    acentos e caixa (e' pra mostrar bonito, nao pra casar).

    Ex: 'Gefxfer 100mg 30ml (c/ desconto de R$ 13,85)' -> 'Gefxfer 100mg 30ml'
        'Arroz Camil  5kg ' -> 'Arroz Camil 5kg'
    """
    import re
    if not texto:
        return ""
    t = texto.strip()
    # remove trechos de desconto/promo entre parenteses: (c/ desconto...), (promo...)
    t = re.sub(r"\(\s*(c/|com)?\s*desconto[^)]*\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(\s*promo[^)]*\)", "", t, flags=re.IGNORECASE)
    # remove "desconto de R$ X,XX" solto (sem parenteses)
    t = re.sub(r"\bc/?\s*desconto\s*(de)?\s*r\$?\s*[\d.,]+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bdesconto\s*(de)?\s*r\$\s*[\d.,]+", "", t, flags=re.IGNORECASE)
    # remove parenteses vazios que sobraram
    t = re.sub(r"\(\s*\)", "", t)
    # colapsa espacos
    t = " ".join(t.split())
    # tira pontuacao solta no fim
    t = t.rstrip(" -,;.")
    # expande abreviacoes comuns (FD->Fralda) - rede de seguranca de exibicao
    t = expandir_abreviacoes(t)
    return t


class Tipo(str, Enum):
    DESPESA = "despesa"
    RECEITA = "receita"


CATEGORIAS_DESPESA = [
    "Mercado", "Restaurante", "Transporte", "Moradia", "Contas de casa",
    "Saude", "Educacao", "Lazer", "Compras", "Vestuario", "Beleza",
    "Presentes", "Servicos", "Assinaturas", "Impostos", "Pet",
    "Construcao", "Outros",
]

CATEGORIAS_RECEITA = [
    "Salario", "Honorarios", "Freela", "Consultoria", "Investimentos",
    "Vendas", "Aluguel", "Beneficio", "Presentes", "Reembolso", "Outros",
]

# Departamentos que APARECEM no raio-x do consumo (LISTA BRANCA). So' faz sentido
# detalhar item de cupom nesses. Tudo que nao estiver aqui fica fora do raio-x -
# no cliente E no admin. Editar aqui pra incluir/tirar departamento.
DEPARTAMENTOS_RAIOX = {"Mercado", "Saude", "Restaurante", "Pet", "Construcao"}


# Formas de pagamento CANONICAS. O que importa pro produto: separar debito de
# credito (credito pode ser parcelado -> gera previsao de fatura) e saber se foi
# pix/especie. "outro" e' a rede de seguranca. Editar aqui pra incluir/tirar.
FORMAS_PAGAMENTO = [
    "pix", "debito", "credito", "especie", "boleto", "transferencia", "outro",
]

# Sinonimos -> forma canonica. Casamos por SUBSTRING no texto normalizado (sem
# acento, minusculo), do mais especifico pro mais generico, pra "cartao de
# credito" bater em credito antes de "cartao" cair num default. Ordem importa.
_SINONIMOS_PAGAMENTO = [
    ("credito", "credito"), ("credit", "credito"),
    ("no cred", "credito"), ("cartao de cred", "credito"), ("cc", "credito"),
    ("debito", "debito"), ("debit", "debito"),
    ("no deb", "debito"), ("cartao de deb", "debito"),
    ("pix", "pix"),
    ("especie", "especie"), ("dinheiro", "especie"),
    ("cash", "especie"), ("vivo", "especie"),  # "no vivo" = em dinheiro (gíria)
    ("boleto", "boleto"),
    ("transfer", "transferencia"), ("ted", "transferencia"),
    ("doc", "transferencia"), ("deposito", "transferencia"),
    # "cartao" solto (sem credito/debito): default debito (a vista, o mais comum).
    ("cartao", "debito"), ("cartão", "debito"),
]


def canonizar_forma_pagamento(texto: str | None) -> str:
    """Normaliza uma forma de pagamento livre pra uma das FORMAS_PAGAMENTO.
    Ex: 'no crédito' -> 'credito', 'mandei um pix' -> 'pix', 'dinheiro' ->
    'especie', 'cartão' (sem dizer qual) -> 'debito'. Vazio/desconhecido -> ''.
    Guarda-se '' (nao 'outro') quando nada foi informado, pra distinguir
    "nao sei" de "forma exotica que o usuario chamou de outro"."""
    if not texto:
        return ""
    alvo = normalizar_descricao(texto)  # minuscula, sem acento
    if not alvo:
        return ""
    # ja' veio canonico?
    if alvo in FORMAS_PAGAMENTO:
        return alvo
    for chave, canon in _SINONIMOS_PAGAMENTO:
        if chave in alvo:
            return canon
    return "outro"


def categorias_de(tipo: Tipo) -> list[str]:
    return CATEGORIAS_DESPESA if Tipo(tipo) == Tipo.DESPESA else CATEGORIAS_RECEITA


def normalizar_categoria(tipo: Tipo, categoria: str) -> str:
    """Casa a categoria ignorando maiusculas/minusculas. Se nao batter, cai em 'Outros'."""
    validas = categorias_de(tipo)
    alvo = (categoria or "").strip().casefold()
    for c in validas:
        if c.casefold() == alvo:
            return c
    return "Outros"


def _sem_acento(s: str) -> str:
    """Remove acentos de uma string."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def canonizar_categoria(cat: str | None, tipo: str = "despesa") -> str:
    """Mapeia uma categoria (possivelmente com acento/caixa diferente, ex 'Saúde'
    vs 'Saude') para a categoria PADRAO da lista oficial. Se nao encontrar
    correspondencia, devolve 'Outros' (nunca cria categoria nova).

    Ex: 'Saude' -> 'Saude', 'saude' -> 'Saude', 'SAÚDE' -> 'Saude'.
    Assim o agrupamento junta tudo na categoria oficial, sem depender de como
    foi salvo. Nao altera os dados no banco - so' normaliza na leitura/exibicao.
    """
    if not cat:
        return "Outros"
    chave = _sem_acento(cat).strip().lower()
    mapa_despesa = {_sem_acento(c).lower(): c for c in CATEGORIAS_DESPESA}
    mapa_receita = {_sem_acento(c).lower(): c for c in CATEGORIAS_RECEITA}
    mapa = mapa_receita if tipo == "receita" else mapa_despesa
    return mapa.get(chave, "Outros")


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
    forma_pagamento: str = ""  # canonica: pix|debito|credito|especie|... (ou '')
    origem: str = "manual"
    comprovante: str = ""
    natureza: str | None = None  # 'pessoal' | 'empresa' | None (a definir)
    id: int | None = None

    @classmethod
    def criar(cls, tipo, valor_reais, categoria, descricao="", data=None,
              pagamento="", forma_pagamento="", origem="manual", comprovante="",
              natureza=None):
        tipo = Tipo(tipo)
        return cls(
            tipo=tipo,
            valor_centavos=reais_para_centavos(valor_reais),
            categoria=normalizar_categoria(tipo, categoria),
            descricao=descricao,
            data=data or date.today(),
            pagamento=pagamento,
            forma_pagamento=canonizar_forma_pagamento(forma_pagamento),
            origem=origem,
            comprovante=comprovante,
            natureza=natureza,
        )
