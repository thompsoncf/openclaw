"""Configuração de NICHOS de venda (Zaq Vendas / Fornecedor).

Cada nicho define o VOCABULÁRIO que o lojista vê ao cadastrar produtos:
- unidades  -> como o produto é medido/vendido (kg, peça, caixa, ml...)
- categorias -> como o produto é agrupado (fruta, masculino, medicamento...)

Isto é config EM CÓDIGO de propósito: enquanto nós definimos os nichos, um mapa
Python é rápido, versionado no Git e sob controle total. Se um dia quisermos que
o admin/cliente gerencie nichos pela tela sem deploy, migra-se para uma tabela —
as funções abaixo (unidades_do_nicho / categorias_do_nicho / ...) viram a única
coisa a trocar, sem mexer em quem as consome.

O nicho de uma conta é guardado em contas.nicho_id (FK -> nichos). A tabela
`nichos` (migração 031) tem: id, nome, slug, tipo ('produto'|'servico'). Este
mapa é indexado pelo SLUG do nicho, casando com a tabela.

IMPORTANTE: a primeira unidade e a primeira categoria de cada nicho são tratadas
como o PADRÃO (default) quando o lojista não escolhe.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# O MAPA: slug do nicho -> {label, unidades, categorias}
# ---------------------------------------------------------------------------
# 'label' é só um nome amigável pra UI. 'unidades' e 'categorias' são as listas
# que aparecem nos selects do cadastro de produto. A ordem importa: [0] é o padrão.
NICHOS: dict[str, dict] = {
    "hortifruti": {
        "label": "Hortifrúti",
        "unidades": ["kg", "duzia", "maco", "unidade", "bandeja", "litro", "pacote"],
        "categorias": ["fruta", "verdura", "legume", "tempero", "outro"],
    },
    "vestuario": {
        "label": "Vestuário / Acessórios",
        "unidades": ["peca", "par", "unidade"],
        "categorias": ["masculino", "feminino", "infantil", "calcado", "acessorio", "outro"],
    },
    "minimercado": {
        "label": "Minimercado / Mercearia",
        "unidades": ["unidade", "kg", "pacote", "litro", "caixa", "fardo"],
        "categorias": ["alimento", "bebida", "limpeza", "higiene", "outro"],
    },
    "alimentacao": {
        "label": "Alimentação / Lanche",
        "unidades": ["unidade", "porcao", "prato", "litro"],
        "categorias": ["salgado", "doce", "bebida", "prato", "combo", "outro"],
    },
    "farmacia": {
        "label": "Farmácia / Saúde",
        "unidades": ["caixa", "unidade", "frasco", "ml", "comprimido"],
        "categorias": ["medicamento", "higiene", "infantil", "beleza", "outro"],
    },
    "beleza": {
        "label": "Beleza / Cosméticos",
        "unidades": ["unidade", "frasco", "kit"],
        "categorias": ["cabelo", "pele", "maquiagem", "perfume", "unha", "outro"],
    },
    "generico": {
        "label": "Outro / Genérico",
        "unidades": ["unidade", "kg", "caixa", "pacote", "litro"],
        "categorias": [],  # livre: o lojista digita a categoria que quiser
    },
}

# União de TODAS as unidades de todos os nichos. Serve pra validação frouxa no
# banco (o check foi removido; aqui garantimos que uma unidade conhecida passa).
TODAS_UNIDADES: set[str] = {
    u for cfg in NICHOS.values() for u in cfg["unidades"]
}

# Nicho usado quando a conta não tem nicho definido (fallback seguro).
NICHO_PADRAO = "generico"


# ---------------------------------------------------------------------------
# HELPERS de leitura (é isto que o resto do sistema usa)
# ---------------------------------------------------------------------------
def nicho_existe(slug: str) -> bool:
    return (slug or "").strip().lower() in NICHOS


def config_do_nicho(slug: str | None) -> dict:
    """Devolve a config do nicho (ou a do genérico se não achar)."""
    s = (slug or "").strip().lower()
    return NICHOS.get(s, NICHOS[NICHO_PADRAO])


def unidades_do_nicho(slug: str | None) -> list[str]:
    """Lista de unidades válidas para o nicho."""
    return list(config_do_nicho(slug)["unidades"])


def categorias_do_nicho(slug: str | None) -> list[str]:
    """Lista de categorias sugeridas para o nicho (pode ser vazia = livre)."""
    return list(config_do_nicho(slug)["categorias"])


def unidade_padrao(slug: str | None) -> str:
    """Primeira unidade do nicho = o padrão."""
    us = unidades_do_nicho(slug)
    return us[0] if us else "unidade"


def unidade_valida_para(slug: str | None, unidade: str) -> bool:
    """A unidade pertence ao vocabulário do nicho?"""
    u = (unidade or "").strip().lower()
    return u in {x.lower() for x in unidades_do_nicho(slug)}


def label_do_nicho(slug: str | None) -> str:
    return config_do_nicho(slug)["label"]


def lista_nichos() -> list[dict]:
    """Todos os nichos pra montar um select: [{slug, label}, ...]."""
    return [{"slug": s, "label": cfg["label"]} for s, cfg in NICHOS.items()]


# ---------------------------------------------------------------------------
# LABELS amigáveis de unidade (pra exibição). Mantém curto; cai no próprio
# nome quando não houver tradução.
# ---------------------------------------------------------------------------
_UNIDADE_LABEL = {
    "kg": "kg", "duzia": "dúzia", "maco": "maço", "unidade": "un",
    "bandeja": "bandeja", "litro": "litro", "pacote": "pacote",
    "peca": "peça", "par": "par", "caixa": "caixa", "fardo": "fardo",
    "porcao": "porção", "prato": "prato", "frasco": "frasco", "ml": "ml",
    "comprimido": "comprimido", "kit": "kit",
}


def label_unidade(unidade: str | None) -> str:
    u = (unidade or "").strip().lower()
    return _UNIDADE_LABEL.get(u, u or "un")
