"""Sugestão de categoria de hortifruti pelo nome do produto.

`categoria` é texto livre em catalogo_produtos (migração 032). A vitrine da loja
agrupa por rótulos canônicos: fruta | legume | verdura | tempero | ovos | outros.
Cair num desses garante que o produto aparece numa seção da loja.

`sugerir_categoria(nome)` dá um PALPITE por correspondência de palavra – serve só
pra pré-preencher o formulário. O fornecedor sempre pode trocar. Retorna None
quando não reconhece (aí o formulário deixa em branco e o usuário escolhe).
"""

from __future__ import annotations

import unicodedata

# Rótulos que a vitrine entende (ordem de exibição). "outros" é o fallback seguro.
CATEGORIAS_PADRAO: list[str] = ["fruta", "legume", "verdura", "tempero", "ovos", "outros"]

# Palavra-chave -> categoria. Todas as chaves SEM acento e minúsculas (ver _norm).
_MAPA: dict[str, str] = {
    # frutas
    "banana": "fruta", "maca": "fruta", "laranja": "fruta", "mamao": "fruta",
    "manga": "fruta", "abacaxi": "fruta", "melancia": "fruta", "melao": "fruta",
    "uva": "fruta", "morango": "fruta", "limao": "fruta", "abacate": "fruta",
    "goiaba": "fruta", "maracuja": "fruta", "caju": "fruta", "acerola": "fruta",
    "tangerina": "fruta", "mexerica": "fruta", "pera": "fruta", "kiwi": "fruta",
    "coco": "fruta", "ameixa": "fruta", "figo": "fruta", "pessego": "fruta",
    "caja": "fruta", "graviola": "fruta", "jaca": "fruta", "caqui": "fruta",
    "tamarindo": "fruta", "cupuacu": "fruta", "seriguela": "fruta", "fruta": "fruta",
    # legumes / raízes / tubérculos
    "tomate": "legume", "cenoura": "legume", "batata": "legume", "cebola": "legume",
    "abobora": "legume", "abobrinha": "legume", "chuchu": "legume", "beterraba": "legume",
    "pepino": "legume", "pimentao": "legume", "berinjela": "legume", "quiabo": "legume",
    "vagem": "legume", "milho": "legume", "mandioca": "legume", "macaxeira": "legume",
    "inhame": "legume", "gengibre": "legume", "alho": "legume", "ervilha": "legume",
    "jilo": "legume", "maxixe": "legume", "rabanete": "legume", "nabo": "legume",
    "cara": "legume", "legume": "legume",
    # verduras / folhas
    "alface": "verdura", "couve": "verdura", "rucula": "verdura", "espinafre": "verdura",
    "agriao": "verdura", "repolho": "verdura", "acelga": "verdura", "brocolis": "verdura",
    "couveflor": "verdura", "almeirao": "verdura", "mostarda": "verdura", "verdura": "verdura",
    # temperos / ervas
    "salsa": "tempero", "cebolinha": "tempero", "coentro": "tempero", "manjericao": "tempero",
    "hortela": "tempero", "alecrim": "tempero", "oregano": "tempero", "louro": "tempero",
    "tomilho": "tempero", "pimenta": "tempero", "tempero": "tempero", "cebolinho": "tempero",
    "cheiro": "tempero",
    # ovos
    "ovo": "ovos", "ovos": "ovos",
}


def _norm(s: str) -> str:
    """Minúsculas, sem acento, espaços nas pontas removidos."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def sugerir_categoria(nome: str) -> str | None:
    """Chuta a categoria pelo nome do produto. None se não reconhecer.

    Estratégia (previsível, sem falso-positivo de substring):
      1. nome inteiro normalizado bate uma chave do mapa.
      2. senão, palavra a palavra (hífen vira espaço) – 1ª que bater vence.

    Ex.: 'Tomate cereja' -> 'legume'; 'Banana prata' -> 'fruta';
         'Couve-flor' -> 'verdura'; 'Ovos caipira' -> 'ovos';
         'Whisky' -> None.
    """
    n = _norm(nome)
    if not n:
        return None
    if n in _MAPA:
        return _MAPA[n]
    for palavra in n.replace("-", " ").split():
        if palavra in _MAPA:
            return _MAPA[palavra]
    return None
